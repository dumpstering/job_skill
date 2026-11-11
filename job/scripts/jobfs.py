"""Shared file, time and settings helpers for job_skill v1.2.

Every script that touches the JobSearch folder goes through this module:
- VERSION and EVENT_SCHEMA;
- today() and now_iso() in the [time] timezone (the sandbox clock is UTC);
- settings() and search_profile(): TOML over in-code defaults, with a
  last-good fallback when a hand-edited file stops parsing;
- read_json() and loads_json(), the only way a script reads a JSON file from
  runner/in/, claude/, state/, queue/, applications/ or upload/ (a size limit
  per caller, nesting at most 64 deep, one plain BadJson error, a lone UTF-16
  surrogate back as U+FFFD), and read_json_or() for a reader that treats a
  bad file as missing;
- atomic_write_json() (adds _rev and _sha256), read_json_verified(),
  atomic_write_text(), create_only(), append_jsonl(), read_jsonl();
- RunLock, the runner's self-lock (an OS lock on an open file);
- the Posting record, ascii_name(), upload_name() and Folder(root), which names
  every path in the folder layout.

Stdlib only (zoneinfo needs the tzdata package on Windows; without it the US
zones fall back to built-in rules).
"""
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

VERSION = "1.3.1"
EVENT_SCHEMA = 1
DEFAULT_TZ = "America/Los_Angeles"

SETTINGS_DEFAULTS = {
    "daily": {"target": 20, "queue_factor": 1.5, "tier_a_per_day": 4, "batch_size": 10,
              "new_account_tenants_per_day": 3, "account_required_per_day": 6, "usajobs_per_day": 1,
              "questions_per_day": 6, "easy_apply_list_max": 5, "more_size": 10},
    "runner": {"mode": "L", "experiment_mode_w": False},
    "alerts": {"read_linkedin": True, "read_indeed_family": False, "read_ziprecruiter": False,
               "senders": []},
    "sources": {"adzuna": False, "usajobs": False, "workable_search": False, "muse": False,
                "dayforce": True, "r3_sitemaps": False},
    "referrals": {"enabled": True, "asks_per_day": 3, "hold_days": 0, "nudge_after_days": 5},
    "followups": {"tiers": ["A", "referral"], "days": 7},
    "apply": {"browser": "auto", "try_chrome_upload": True, "fill_mode": "sequential", "rounds_size": 5,
              "sensitive_confirm": "per_job", "screener": False, "mygreenhouse_autofill": False},
    # the opt-in LinkedIn browser mode; events.linkedin_settings checks and bounds these values
    "linkedin": {"browser": False, "per_day": 25, "min_seconds_between_actions": 4, "stop_on_warning": True},
    "caps": {"per_company_30d": 5, "per_family_per_company_30d": 2, "reject_reapply_days": 90,
             "ashby_one_open": True, "per_company_per_day": 2, "per_unit_30d": 2, "per_institution_30d": 15,
             "unit_segments": ["academic", "institute", "hospital", "government"],
             "employer": [{"name": "Google", "match": ["google"], "per_30d": 3,
                           "same_job_reapply_days": 90}]},
    "worksearch": {"claiming": False, "state": "CA", "claim_effective": "", "de8405_received": ""},
    "time": {"timezone": DEFAULT_TZ},
    "schedule": {"weekly_day": "monday"},
    "experience": {"years": "", "industry_years": ""},  # R6: seniority override (gates.experience)
}

# The keys are fixed; research presets and setup fill the values (amendment 2).
SEARCH_PROFILE_DEFAULTS = {
    "version": 1,
    "home": {"label": "", "zip": "", "radius_miles": 40, "places": [], "second_ring": [], "states": [],
             "remote": "us", "relocate": False},
    "gates": {"exclude_title_words": ["Director", "Vice President", "VP", "Intern", "Internship", "Chief"],
              "employment_types": ["full-time", "contract", "temporary"],
              "undated_policy": "first_seen_after_2_polls"},
    "knockout": [],
    "family": [],
    "widen": {"enabled": True, "steps": ["adjacent_families", "remote_us", "second_ring"]},
}

MAX_PATTERN = 200
# A quantified group that itself holds a quantifier, e.g. (a+)+ or (\w*){2,}.
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d+,?\d*\})")
PATTERN_KEYS = ("required_patterns", "preferred_patterns", "title_patterns")


class NotReady(Exception):
    """A verified JSON file is missing, cut short or fails its hash.
    .missing is True when the file (and its .prev.json) is simply not there."""

    def __init__(self, msg, missing=False):
        super().__init__(msg)
        self.missing = missing


class BadJson(ValueError):
    """A JSON file or text that is too big, nested too deep, not UTF-8 or not
    JSON. .kind is "size", "depth", "utf8" or "json"; the first two never
    change by waiting (a file still arriving can only be "utf8" or "json")."""

    def __init__(self, msg, kind="json"):
        super().__init__(msg)
        self.kind = kind

    @property
    def permanent(self):
        return self.kind in ("size", "depth")


class LockBusy(Exception):
    """Another runner holds the lock; .info is the holder's {pid, started, cmd}."""

    def __init__(self, path, info):
        super().__init__(f"{path} held by {info}")
        self.info = info


class ConfigError(Exception):
    """A config file that does not parse or breaks a rule."""


# ---------- time ----------

def utcnow():
    """The current UTC time. Tests replace this, or set JOB_SKILL_NOW (ISO)."""
    forced = os.environ.get("JOB_SKILL_NOW")
    if forced:
        t = datetime.fromisoformat(forced.replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _nth_sunday(year, month, n):
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


class USRules(tzinfo):
    """US standard time plus the 2007 DST rule, used only when tzdata is missing."""

    def __init__(self, name, std_hours, dst=True):
        self.name, self.std, self.dst_on = name, timedelta(hours=std_hours), dst

    def _is_dst(self, dt):
        if not self.dst_on:
            return False
        naive = dt.replace(tzinfo=None)
        start = datetime.combine(_nth_sunday(naive.year, 3, 2), datetime.min.time()) + timedelta(hours=2)
        end = datetime.combine(_nth_sunday(naive.year, 11, 1), datetime.min.time()) + timedelta(hours=1)
        return start <= naive < end

    def utcoffset(self, dt):
        return self.std + self.dst(dt)

    def dst(self, dt):
        return timedelta(hours=1) if dt is not None and self._is_dst(dt) else timedelta(0)

    def tzname(self, dt):
        return self.name

    def fromutc(self, dt):
        local = dt.replace(tzinfo=None) + self.std
        # The DST test uses wall-clock standard time; one hour shift at most.
        return (local + (timedelta(hours=1) if self._is_dst(local) else timedelta(0))).replace(tzinfo=self)


US_FALLBACK = {"America/Los_Angeles": -8, "America/Denver": -7, "America/Chicago": -6,
               "America/New_York": -5, "America/Anchorage": -9}
US_NO_DST = {"America/Phoenix": -7, "Pacific/Honolulu": -10}


def zone(name=None):
    """A tzinfo for an IANA name; US zones work even without tzdata."""
    name = name or DEFAULT_TZ
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, ValueError, or no zoneinfo at all
        if name in US_FALLBACK:
            return USRules(name, US_FALLBACK[name])
        if name in US_NO_DST:
            return USRules(name, US_NO_DST[name], dst=False)
        raise ConfigError(f"unknown time zone {name!r} (install tzdata)")


def _tz_of(root):
    if root is None:
        return zone(DEFAULT_TZ)
    return zone(settings(root)["time"]["timezone"])


def now(root=None):
    return utcnow().astimezone(_tz_of(root))


def today(root=None):
    """Today's date in the configured time zone (never the sandbox's UTC date)."""
    return now(root).date()


def now_iso(root=None):
    """Local time as ISO 8601 with offset, to the second."""
    return now(root).replace(microsecond=0).isoformat()


def stamp():
    """UTC compact stamp for event and request names: 20261112T171503Z."""
    return utcnow().strftime("%Y%m%dT%H%M%SZ")


def new_id():
    """<stamp>_<8hex>: the id of an event or request, also its file-name stem."""
    return f"{stamp()}_{secrets.token_hex(4)}"


def iso_date(d):
    return d.isoformat() if isinstance(d, (date, datetime)) else str(d)


# ---------- settings and search profile ----------

def deep_merge(base, over):
    """Defaults overlaid with a file's values; tables merge, everything else replaces."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(dict):
    """A loaded config. .source is file, last_good or defaults; .error says why not file."""

    source = "defaults"
    error = ""


def check_pattern(p):
    """Config regex is untrusted: at most 200 characters, no nested quantifiers."""
    if not isinstance(p, str):
        raise ConfigError(f"pattern is not text: {p!r}")
    if len(p) > MAX_PATTERN:
        raise ConfigError(f"pattern over {MAX_PATTERN} characters: {p[:40]}...")
    if NESTED_QUANTIFIER.search(p):
        raise ConfigError(f"nested quantifier in pattern: {p[:60]}")
    try:
        re.compile(p)
    except re.error as e:
        raise ConfigError(f"bad pattern {p[:60]}: {e}")


def check_search_profile(data):
    for ko in data.get("knockout") or []:
        for key in PATTERN_KEYS:
            for p in ko.get(key) or []:
                check_pattern(p)


def load_config(root, name, defaults, validate=None, keep_last_good=False):
    """config/<name> over defaults. A file that fails to parse or validate gives
    state/last_good/<name> (or the defaults) with .error set. keep_last_good
    (the runner only: state/ is its folder) copies a good file there."""
    folder = Folder(root)
    path, good = folder.config / name, folder.last_good(name)
    cfg = Config(copy.deepcopy(defaults))
    if not path.exists():
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        if validate:
            validate(data)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ConfigError) as e:
        cfg.error = f"{name}: {e}"
        try:
            data = tomllib.loads(good.read_text(encoding="utf-8-sig"))
            if validate:
                validate(data)
            cfg.update(deep_merge(defaults, data))
            cfg.source = "last_good"
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ConfigError):
            pass
        return cfg
    cfg.update(deep_merge(defaults, data))
    cfg.source = "file"
    if keep_last_good:
        good.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(good, path.read_text(encoding="utf-8-sig"))
    return cfg


def settings(root=None, keep_last_good=False):
    """config/settings.toml over SETTINGS_DEFAULTS (the defaults alone without a root)."""
    if root is None:
        return Config(copy.deepcopy(SETTINGS_DEFAULTS))
    return load_config(root, "settings.toml", SETTINGS_DEFAULTS, keep_last_good=keep_last_good)


def search_profile(root=None, keep_last_good=False):
    """config/search_profile.toml over SEARCH_PROFILE_DEFAULTS, regex-checked."""
    if root is None:
        return Config(copy.deepcopy(SEARCH_PROFILE_DEFAULTS))
    return load_config(root, "search_profile.toml", SEARCH_PROFILE_DEFAULTS, check_search_profile,
                       keep_last_good=keep_last_good)


# ---------- safe JSON input (shared decision R3) ----------

JSON_DEPTH_MAX = 64
# Size limits, one per kind of caller (bytes). A file Claude or another
# program drops (an event, a request, an args file) is small; a job folder's
# files and the runner's small state files are bigger; the pool and the
# queue files hold posting texts.
MAX_INBOX = 64 * 1024
MAX_FILE = 8 << 20
MAX_STATE = 256 << 20

# One bracket at a time, left to right: everything up to the next bracket
# outside a string is skipped inside the regex engine (possessive, so it never
# backtracks), then an opening or closing bracket, or a lone quote (a string
# with no end: the text is not JSON, and the scan stops there). Linear on any
# input.
_JSON_TOKENS = re.compile(r'(?:[^"\[\]{}]++|"[^"\\]*+(?:\\.[^"\\]*+)*+")*+(?:([\[{])|([\]}])|(")|\Z)', re.S)


def _no_constant(name):
    raise ValueError(f"{name} is not a JSON number")


# Half of a UTF-16 pair (a lone surrogate): valid in a JSON escape ("\ud83d",
# a server that cut an emoji in two) but no UTF-8 writer takes it. JSON joins a
# whole escaped pair into one character, so only halves are left after parsing.
_SURROGATE = re.compile("[\ud800-\udfff]")
_SURROGATE_HINT = re.compile(r"\\u[dD][89a-fA-F]|[\ud800-\udfff]")
_SURROGATE_HINT_BYTES = re.compile(rb"\\u[dD][89a-fA-F]")


def surrogate_hint(text):
    """True when JSON text may hold a lone surrogate (an escape like "\\ud83d"
    or the code point itself), so the parsed value needs clean_value."""
    return bool(_SURROGATE_HINT.search(text)) if isinstance(text, str) else \
        bool(_SURROGATE_HINT_BYTES.search(text))


def clean_text(s):
    """s with every lone UTF-16 surrogate as U+FFFD (a whole pair held as two
    code points becomes its one character). Text with none is returned as is."""
    if not isinstance(s, str) or not _SURROGATE.search(s):
        return s
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def clean_value(value):
    """value (a JSON value: dict, list, str, ...) with clean_text applied to
    every string, dict keys included, in place for dicts and lists (iterative:
    no depth limit of its own). Returns the cleaned value."""
    if isinstance(value, str):
        return clean_text(value)
    if not isinstance(value, (dict, list)):
        return value
    stack = [value]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            items = list(obj.items())
            if any(isinstance(k, str) and _SURROGATE.search(k) for k, _ in items):
                obj.clear()
                for k, v in items:
                    obj[clean_text(k)] = v
                items = list(obj.items())
            for k, v in items:
                if isinstance(v, str):
                    if _SURROGATE.search(v):
                        obj[k] = clean_text(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        else:
            for i, v in enumerate(obj):
                if isinstance(v, str):
                    if _SURROGATE.search(v):
                        obj[i] = clean_text(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
    return value


def json_depth_ok(text, limit=JSON_DEPTH_MAX):
    """True when no array or object in text nests deeper than limit. Linear in
    the text; brackets inside strings do not count."""
    if text.count("[") + text.count("{") <= limit:
        return True
    depth = 0
    for m in _JSON_TOKENS.finditer(text):
        if m.group(1):
            depth += 1
            if depth > limit:
                return False
        elif m.group(2):
            depth -= 1
        elif m.group(3):
            return True  # an unterminated string: json.loads refuses the text there, before any deeper bracket
    return True


def loads_json(text, allow_nan=True, max_depth=JSON_DEPTH_MAX, strict=True, clean=True):
    """The JSON value in text (str, or bytes read as UTF-8 with or without a
    BOM). Nesting deeper than max_depth is refused before parsing; a decode,
    parse or recursion error is one BadJson (a ValueError). strict=False lets
    a string hold control characters (some job-site APIs send them). Every
    lone UTF-16 surrogate in a string or key ("\\ud83d", half an emoji) comes
    back as U+FFFD (clean_value), so no writer later fails on it; clean=False
    keeps the value as parsed (a hash check over the bytes as written)."""
    value, text = _parse_json(text, allow_nan, max_depth, strict)
    if clean and surrogate_hint(text):
        value = clean_value(value)
    return value


def _parse_json(text, allow_nan, max_depth, strict):
    """(value, the text as str) for loads_json."""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise BadJson(f"not UTF-8 ({e.reason} at byte {e.start})", "utf8")
    elif isinstance(text, str) and text.startswith("\ufeff"):
        text = text[1:]
    if not isinstance(text, str):
        raise BadJson("not text", "json")
    if not json_depth_ok(text, max_depth):
        raise BadJson(f"nested deeper than {max_depth}", "depth")
    try:
        if allow_nan:
            return json.loads(text, strict=strict), text
        return json.loads(text, parse_constant=_no_constant, strict=strict), text
    except RecursionError:
        raise BadJson("nested too deep to read", "depth")
    except MemoryError:
        raise BadJson("too big to read", "size")
    except ValueError as e:  # JSONDecodeError, a number over the digit limit, NaN refused
        raise BadJson(f"not JSON ({str(e)[:120]})", "json")


def read_json(path, max_bytes, allow_nan=True, strict=True, clean=True):
    """The JSON value in the file at path, through loads_json, read only up to
    max_bytes (more is BadJson "size"). A missing or unreadable file raises
    OSError (FileNotFoundError when it is not there): that case is the
    caller's, since a missing file is often normal."""
    return loads_json(_read_capped(path, max_bytes), allow_nan=allow_nan, strict=strict, clean=clean)


def _read_capped(path, max_bytes):
    """The bytes of the file at path; BadJson "size" past max_bytes."""
    too_big = BadJson(f"over {max_bytes // 1024} KB", "size")
    with open(path, "rb") as f:
        try:
            size = os.fstat(f.fileno()).st_size
        except OSError:
            size = 0
        if size > max_bytes:
            raise too_big
        # Sized from the file (never a max_bytes buffer), in 1 MB steps after
        # that should the file grow while it is read; never past max_bytes.
        chunks, total, want = [], 0, size + 1
        while True:
            part = f.read(min(want, max_bytes + 1 - total))
            if not part:
                break
            chunks.append(part)
            total += len(part)
            if total > max_bytes:
                raise too_big
            want = 1 << 20
    return b"".join(chunks)


_BAD_SEEN = set()
_BAD_EMIT = [print]


def set_bad_file_emit(fn):
    """Where report_bad's ERROR lines go (the runner sets its own output and log)."""
    _BAD_EMIT[0] = fn or print


def report_bad(path, why, emit=None):
    """One ERROR line per bad file per process (through emit, else the one
    set_bad_file_emit chose): the file is treated as missing."""
    key = str(path)
    if key in _BAD_SEEN:
        return
    _BAD_SEEN.add(key)
    p = Path(path)
    shown = "/".join(p.parts[-3:]) if len(p.parts) > 3 else p.as_posix()
    line = re.sub(r"[\r\n]+", " ", f"ERROR bad file {shown}: {why} (treated as missing)")
    try:
        (emit or _BAD_EMIT[0])(line)
    except Exception:  # an output that fails must not turn a bad file into a stopped run
        pass


def read_json_or(path, max_bytes, default, emit=None):
    """read_json, or `default` when the file is missing (silently), bad (one
    ERROR line, see report_bad) or holds another type than default."""
    try:
        data = read_json(path, max_bytes)
    except OSError:
        return default
    except BadJson as e:
        report_bad(path, e, emit)
        return default
    if default is not None and not isinstance(data, type(default)):
        return default
    return data


# ---------- files ----------

def body_sha256(obj):
    """sha256 over the canonical JSON of obj without its _sha256 key."""
    body = {k: v for k, v in obj.items() if k != "_sha256"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


def prev_path(path):
    path = Path(path)
    return path.with_name(path.stem + ".prev" + path.suffix)


def _rename(src, dst, op=None):
    """os.replace (or os.rename) that waits out a Windows sharing violation:
    an antivirus scanner or the indexer often holds a file for a moment just
    after it was written, and the runner rewrites some state files (the robots
    cache) many times a run."""
    op = op or os.replace
    for attempt in range(12):
        try:
            return op(src, dst)
        except PermissionError:
            if os.name != "nt" or attempt == 11:
                raise
            time.sleep(0.02 * (attempt + 1))


def _temp_beside(path):
    return path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")


def _write_temp(path, data):
    tmp = _temp_beside(path)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def atomic_write_text(path, text):
    """Write text (UTF-8, LF) to a temp file beside path, then rename over it.
    A lone surrogate is written as U+FFFD (clean_text), never an error."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _write_temp(path, clean_text(text).encode("utf-8"))
    try:
        _rename(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path, obj, rev=None):
    """Write obj with _rev (the old file's plus one, unless given) and _sha256.
    The file being replaced is kept as <name>.prev.json for readers that catch
    a half-copied file. Returns the dict as written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if rev is None:
        rev = 1
        try:
            rev = int(read_json(path, MAX_STATE).get("_rev", 0)) + 1
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    out = {k: v for k, v in obj.items() if k not in ("_rev", "_sha256")}
    out["_rev"] = rev
    out["_sha256"] = body_sha256(out)
    data = (json.dumps(out, indent=1, ensure_ascii=True) + "\n").encode("ascii")
    tmp = _write_temp(path, data)
    try:
        if path.exists():
            prev, prev_tmp = prev_path(path), _temp_beside(prev_path(path))
            shutil.copyfile(path, prev_tmp)
            _rename(prev_tmp, prev)
        _rename(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return out


def _read_verified_once(path):
    try:
        raw = _read_capped(path, MAX_STATE)
        # The hash covers the value as written: checked before lone surrogates
        # (a file written before they were cleaned) become U+FFFD.
        data = loads_json(raw, clean=False)
    except FileNotFoundError:
        raise NotReady(f"{path}: missing", missing=True)
    except (OSError, ValueError) as e:  # BadJson too: too big, too deep, not UTF-8, not JSON
        raise NotReady(f"{path}: {e}")
    if not isinstance(data, dict):
        raise NotReady(f"{path}: not a JSON object")
    try:
        ok = "_sha256" not in data or data["_sha256"] == body_sha256(data)
    except (TypeError, ValueError):  # a value json.dumps cannot write back
        ok = False
    if not ok:
        raise NotReady(f"{path}: hash mismatch")
    return clean_value(data) if surrogate_hint(raw) else data


def read_json_verified(path, retries=1, wait=5.0, fallback=True, sleep=time.sleep):
    """A JSON object whose _sha256 matches (files with no hash just have to parse).
    Retries after `wait` seconds, then reads <name>.prev.json; raises NotReady.
    A file that is there but bad (and no good .prev.json) gets one ERROR line
    (report_bad): the caller treats it as missing."""
    last = None
    for attempt in range(retries + 1):
        try:
            return _read_verified_once(path)
        except NotReady as e:
            last = e
            if attempt < retries:
                sleep(wait)
    if fallback:
        try:
            return _read_verified_once(prev_path(path))
        except NotReady:
            pass
    if not last.missing:
        report_bad(path, str(last).replace(f"{path}: ", "", 1))
    raise last


def create_only(path, data):
    """Create a new file whole, once; FileExistsError if the name is taken.
    data is str, bytes, or a dict (written as JSON). The content lands in a temp
    file first, so a reader never sees half a file under the final name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=True, indent=1) + "\n"
    if isinstance(data, str):
        data = clean_text(data).encode("utf-8")  # a lone surrogate as U+FFFD, never an error
    if path.exists():
        raise FileExistsError(str(path))
    tmp = _write_temp(path, data)
    try:
        if os.name == "nt":
            _rename(tmp, path, os.rename)  # refuses an existing target on Windows
        else:
            os.link(tmp, path)  # refuses an existing target on POSIX
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def append_jsonl(path, record):
    """Append one JSON line. A cut last line gets a newline first, so it stays
    the only bad line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
    with open(path, "ab+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        f.write(line.encode("ascii"))
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    """Every parseable record in a JSONL file; malformed lines (a cut last line,
    or one nested too deep to parse) are skipped. A missing file is an empty list."""
    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return []
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            rec = loads_json(line)
        except ValueError:  # BadJson: a cut line, or one nested too deep
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# ---------- runner lock ----------

def runner_home():
    """%LOCALAPPDATA%\\JobRunner (JOBRUNNER_HOME overrides, for tests)."""
    if os.environ.get("JOBRUNNER_HOME"):
        return Path(os.environ["JOBRUNNER_HOME"])
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "JobRunner"


class RunLock:
    """The runner's self-lock: an OS lock on an open file (msvcrt.locking on
    Windows, fcntl.flock elsewhere). The OS releases it when the holder's
    process ends, however it ends (killed by a time limit, a shutdown), so a
    lock is never "stale" and is never taken from a live holder. The file also
    holds {pid, started, cmd} for messages; a clean release empties and
    removes it.

    with RunLock(path, cmd="daily"): ...   raises LockBusy while another holds it.
    acquire(wait=s) keeps trying for up to s seconds.
    .left_by is the {pid, started, cmd} a holder that died left in the file.
    """

    # Windows locks are mandatory: the locked byte sits past the info, so other
    # processes can still read who holds the lock.
    LOCK_OFFSET = 1 << 20

    def __init__(self, path=None, cmd=""):
        self.path = Path(path) if path else runner_home() / "run.lock"
        self.cmd = cmd
        self.held = False
        self.left_by = None
        self._fd = None

    def _holder(self):
        try:
            data = read_json(self.path, 4096)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _lock(self, fd):
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, self.LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(self, fd):
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, self.LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def _try(self):
        """One attempt; True when this call took the lock."""
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        except OSError:  # Windows: the last holder is removing the file this moment
            return False
        if not self._lock(fd):
            os.close(fd)
            return False
        if os.name != "nt":
            # The last holder removes the file before it unlocks: a lock on the
            # removed file is no lock, so try the new file.
            try:
                same = os.path.samestat(os.fstat(fd), os.stat(self.path))
            except OSError:
                same = False
            if not same:
                self._unlock(fd)
                os.close(fd)
                return False
        os.lseek(fd, 0, os.SEEK_SET)
        old = os.read(fd, 4096)
        try:
            left = loads_json(old) if old.strip() else None
        except ValueError:
            left = {"pid": "?", "started": "?", "cmd": "?"}
        self.left_by = left if isinstance(left, dict) else None
        info = {"pid": os.getpid(), "started": utcnow().replace(microsecond=0).isoformat(), "cmd": self.cmd}
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(info).encode("ascii"))
        self._fd = fd
        self.held = True
        return True

    def acquire(self, wait=0, poll=1.0, sleep=time.sleep):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0, wait)
        while True:
            for _ in range(3):  # a file removed under us is retried at once
                if self._try():
                    return self
            if time.monotonic() >= deadline:
                raise LockBusy(self.path, self._holder())
            sleep(poll)

    def release(self):
        if not self.held:
            return
        fd, self._fd, self.held = self._fd, None, False
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        if os.name != "nt":
            try:
                self.path.unlink()  # before the unlock, so a waiter never locks a removed file
            except OSError:
                pass
        self._unlock(fd)
        os.close(fd)
        if os.name == "nt":
            try:
                self.path.unlink()  # fails while a waiting run has it open; that run takes it next
            except OSError:
                pass

    def __enter__(self):
        return self if self.held else self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


# ---------- the posting record (2.5) ----------

@dataclass
class Posting:
    job_key: str = ""
    ats: str = ""
    board: str = ""
    company: str = ""
    company_norm: str = ""
    title: str = ""
    title_norm: str = ""
    location: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    remote: str = "unknown"          # onsite | hybrid | remote | unknown
    us_eligible: object = None       # True | False | None
    posted: object = None            # YYYY-MM-DD | None
    posted_field: str = ""
    first_seen: str = ""
    url: str = ""
    apply_url: str = ""
    req_ids: list = field(default_factory=list)
    text: str = ""
    salary: object = None            # {min, max, period, currency} | None
    questions: bool = False
    employer_site: str = ""
    lane: str = ""
    lead_ids: list = field(default_factory=list)
    simhash: str = ""                # 16 hex
    closed: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


# ---------- names ----------

# Windows-unsafe characters, plus $ and ` so a job folder path stays inert inside
# double quotes on a command line (bash and PowerShell expand only those there).
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*$`\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def ascii_name(text, max_len=60, alnum=False):
    """An ASCII, Windows-safe name: accents folded, unsafe characters dropped.
    alnum=True keeps letters and digits only (upload-name parts)."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    if alnum:
        return re.sub(r"[^A-Za-z0-9]", "", s)[:max_len]
    s = UNSAFE_NAME.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()[:max_len].rstrip(" .")
    if s.split(".")[0].lower() in RESERVED:
        s = "_" + s
    return s


UPLOAD_KINDS = {"resume": ("", "Resume", "pdf"), "cover": ("c", "Cover", "pdf"), "word": ("w", "Resume", "docx")}
UPLOAD_MAX = 40


def upload_name(n, last, employer, kind="resume", diy=False):
    """NN_<Last>_Resume_<Employer12>.pdf (2.5): NNc_ for a cover letter, NNw_ for
    a Word-only form, E<n>_ for a do-yourself item. ASCII, at most 40 characters."""
    suffix, word, ext = UPLOAD_KINDS[kind]
    prefix = (f"E{int(n)}" if diy else f"{int(n):02d}") + suffix
    emp = ascii_name(employer, 12, alnum=True) or "Employer"
    fixed = len(prefix) + len(word) + len(emp) + len(ext) + 4  # three underscores and the dot
    last = ascii_name(last, max(1, UPLOAD_MAX - fixed), alnum=True) or "Applicant"
    return f"{prefix}_{last}_{word}_{emp}.{ext}"


def job_folder_name(day, company, role):
    """applications/<YYYY-MM-DD> <Company> - <Role> (v1.1 layout)."""
    return ascii_name(f"{iso_date(day)} {ascii_name(company, 40)} - {ascii_name(role, 60)}", 110)


# ---------- the folder (2.4) ----------

class Folder:
    """Every path in the JobSearch folder. Dates are date objects or YYYY-MM-DD."""

    def __init__(self, root):
        self.root = Path(root)
        r = self.root
        self.master_resume = r / "master_resume.md"
        self.profile = r / "profile.md"
        self.library = r / "achievement_library.md"
        self.answers = r / "answers.md"
        self.publications = r / "publications.md"
        self.techniques = r / "techniques.md"
        self.tracker = r / "applications.xlsx"
        self.config = r / "config"
        self.settings_toml = self.config / "settings.toml"
        self.search_profile_toml = self.config / "search_profile.toml"
        self.watchlist_csv = self.config / "watchlist.csv"
        self.boards_csv = self.config / "boards.csv"
        self.variants = r / "variants"
        self.runner_in = r / "runner" / "in"
        self.events = self.runner_in / "events"
        self.leads = self.runner_in / "leads"
        self.requests = self.runner_in / "requests"
        self.runner_out = r / "runner" / "out"
        self.tracker_json = self.runner_out / "tracker.json"
        self.status_json = self.runner_out / "status.json"
        self.needs_search_json = self.runner_out / "needs_search.json"
        self.results = self.runner_out / "results"
        self.events_rejected = self.runner_out / "events_rejected"
        self.claude = r / "claude"
        self.session_json = self.claude / "session.json"
        self.queue = r / "queue"
        self.state = r / "state"
        self.pool_json = self.state / "pool.json"
        self.company_ats_json = self.state / "company_ats.json"
        self.probe_cache_csv = self.state / "probe_cache.csv"
        self.robots_cache_json = self.state / "robots_cache.json"
        self.board_health_csv = self.state / "board_health.csv"
        self.watchlist_auto_csv = self.state / "watchlist_auto.csv"
        self.queue_state_json = self.state / "queue_state.json"
        self.weights_json = self.state / "weights.json"
        self.applications = r / "applications"
        self.upload = r / "upload"
        self.network = r / "network"
        self.network_raw = self.network / "raw"
        self.network_csv = self.network / "network.csv"
        self.reports = r / "reports"
        self.funnel_csv = self.reports / "funnel.csv"
        self.usage_csv = self.reports / "usage.csv"
        self.worksearch = r / "worksearch"
        self.logs = r / "logs"
        self.backups = r / "backups"

    # config, variants and state
    def config_file(self, name):
        return self.config / name

    def variant(self, family, version=None):
        base = self.variants / family
        return base / f"v{int(version)}" if version is not None else base

    def variant_current(self, family):
        return self.variants / family / "current.txt"

    def seen(self, month):
        """state/seen/<YYYY-MM>.jsonl; month is 'YYYY-MM' or a date."""
        m = month.strftime("%Y-%m") if isinstance(month, (date, datetime)) else str(month)[:7]
        return self.state / "seen" / f"{m}.jsonl"

    def last_good(self, name):
        return self.state / "last_good" / name

    # runner in and out
    def event(self, ev_id, ev_type):
        return self.events / f"{ev_id}_{ev_type}.json"

    def lead_file(self, day, hhmm, source):
        return self.leads / f"{iso_date(day)}_{hhmm}_{source}.jsonl"

    def request(self, req_id, op):
        return self.requests / f"{req_id}_{op}.json"

    def result(self, req_id):
        return self.results / f"{req_id}.json"

    def events_done(self, day):
        return self.runner_out / "events_done" / iso_date(day)

    # claude
    def day_marker(self, day, step):
        return self.claude / f"day_{iso_date(day)}" / f"{step}.done"

    def morning_json(self, day):
        return self.claude / iso_date(day) / "morning.json"

    # queue
    def queue_dir(self, day):
        return self.queue / iso_date(day)

    def queue_json(self, day):
        return self.queue_dir(day) / "queue.json"

    def addendum(self, day, hhmm):
        return self.queue_dir(day) / f"addendum_{hhmm}.json"

    def today_md(self, day):
        return self.queue_dir(day) / "today.md"

    def review_html(self, day):
        return self.queue_dir(day) / "review.html"

    def discards_csv(self, day):
        return self.queue_dir(day) / "discards.csv"

    def diy_md(self, day):
        return self.queue_dir(day) / "diy.md"

    # applications and uploads
    def job_folder(self, day, company, role):
        return self.applications / job_folder_name(day, company, role)

    def not_sent(self, month):
        m = month.strftime("%Y-%m") if isinstance(month, (date, datetime)) else str(month)[:7]
        return self.applications / "_not_sent" / m

    def diy_dir(self, day):
        return self.applications / "_diy" / iso_date(day)

    def upload_file(self, n, last, employer, kind="resume", diy=False):
        return self.upload / upload_name(n, last, employer, kind, diy)

    def upload_done(self, day):
        return self.upload / "_done" / iso_date(day)

    # network, reports, work search, logs
    def matches_csv(self, day):
        return self.network / f"matches_{iso_date(day)}.csv"

    def week_md(self, day):
        return self.reports / f"week_{iso_date(day)}.md"

    def worksearch_file(self, state, start, end, ext):
        return self.worksearch / f"{state}_{iso_date(start)}_{iso_date(end)}.{ext.lstrip('.')}"

    def runner_log(self, day):
        return self.logs / f"runner_{iso_date(day)}.log"

    def inside(self, path):
        """True when path resolves inside the root (events, requests, leads)."""
        try:
            Path(self.root, path).resolve().relative_to(self.root.resolve())
            return True
        except (ValueError, OSError):
            return False
