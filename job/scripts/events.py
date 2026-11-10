"""Event files: what Claude hands the runner (spec 2.5, 2.7; C2).

Every event is one create-only file runner/in/events/<YYYYMMDDTHHMMSSZ>_<8hex>_<type>.json:
    {"v":1, "id":"<stamp>_<8hex>", "type":T, "at":"<local ISO>", "session":"...",
     "job_key":"...", "n":7, "date":"YYYY-MM-DD", "payload":{...}}

Usage:
    python events.py emit --root R --type T (--json J | --json-file F) [--job-key K]
        [--n N] [--date YYYY-MM-DD] [--session S]
    python events.py add --root R --folder <job folder> --via pasted|add [--family F]
        [--date YYYY-MM-DD] [--session S]
    python events.py overlay --root R [--date YYYY-MM-DD]

emit prints EVENT <id>. J is the payload object, or an object holding
"payload" plus any of job_key, n, date, session. emit writes every type but
`added`, which only add writes. --json-file F names a file under
<root>/claude/ (like --args-file: at most 64 KB, read once, then removed).

A file in runner/in/events that cannot be read as JSON (over 64 KB, nested
deeper than 64, not UTF-8, or not JSON after 3 tries) never stops a reader:
the readers skip it, and the runner's tracker.py apply-events moves it to
runner/in/bad/ (with <name>.reason.txt) and prints one line
    ERROR bad event file <name>: <why>; moved to runner/in/bad/

add puts one job folder from a link into today's list (spec 5.11,
addendum_user): an `added` event holding the whole queue item, numbered
after the highest number given today. today_view, tracker.py check and the
overlay read it at once; the next tick writes it into
queue/<date>/addendum_user.json (the runner stays the only writer of queue
files). --via pasted is a /job <link> folder (Tier A, the single-job flow);
--via add is a /job add <link> folder (Tier B when resume_B.pdf is there).
posting.md's URL must be a plain web address (netfetch's canonical host) and
no job board. An added event whose apply_url is refused, whose upload name
does not carry its own number, or that reuses a number or job key given
today is never used.
    ADDED #<n> <job_key> (addendum_user)
    IN QUEUE <date> #<n>          (already in today's list; nothing written)
    NO LIST <date> (mode M): the tracker row is the record
    NO LIST <date> (no list built yet): the tracker row is the record

overlay prints the day's merged view of pending and applied events:
    OVERLAY <date>
    ITEM #<n> <job_key> <state> [reason=<code>] [questions=<ids>] [confirmation=<how>]
    ITEMS none
    ADDED #<n> <job_key> <via>
    TRACKER PENDING <count>
    TRACKER REJECTED <count>
    USAGE after #<n> session <s>% weekly <w>% minutes <m>
    ANSWER USED <ANS-id> x<count>

The library (validate, emit, read_file, pending_files, recent, move_done,
reject, note_failure, overlay, user_items) is what tracker.py apply-events,
today_view, jobqueue and caps use. Stdlib only (add also reads the queue
through jobqueue; an added event's link is checked with netfetch).

Exit codes: 0 ok, 1 bad input (ERROR ...).
"""
import argparse
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import argfile  # noqa: E402
import jobfs  # noqa: E402

MAX_BYTES = 64 * 1024
MAX_TRIES = 3            # a file that will not parse is retried on 3 runs, then rejected
YOUNG_SECONDS = 5        # a file this new may still be arriving over the bridge
RECENT_DAYS = 7          # how far back the overlay reads events_done

QUEUE_TYPES = ("started", "handed_over", "applied", "skipped", "later", "parked", "unparked", "blocked")
# "added" is a job from a link joining today's list (addendum_user, spec 5.11).
TYPES = ("tracker",) + QUEUE_TYPES + ("answer_used", "usage", "added")
# What `emit` may write: an added event comes only from `events.py add`, which
# numbers it and names its upload file itself (SEC-14).
EMIT_TYPES = tuple(t for t in TYPES if t != "added")
VIAS = ("pasted", "add")
ADDED_TEXT = ("company", "role", "location", "ats", "host", "apply_url", "folder", "family", "tier", "variant",
              "account", "attach", "upload", "resume_status", "answers_status")
# tracker.py write subcommands an event may carry (2.5 plus init and archive).
TRACKER_COMMANDS = ("init", "add", "update", "account", "log-email", "inbox-done", "referral-add",
                    "employer-set", "activity-add", "archive")
# Failure taxonomy (5.10), plus "user" for the user's own skip or later.
REASONS = ("closed", "login_required", "account_needed", "account_locked", "captcha_failed",
           "email_code_timeout", "sso_required", "spam_rejected", "site_error", "upload_failed",
           "form_error", "host_mismatch", "unknown_required", "knockout_at_form",
           "stored_resume_mismatch", "unsafe_verification", "page_injection", "browser_unavailable",
           "usage_limit", "deferred_cap", "prep_failed", "user")
CONFIRMATIONS = ("page", "email", "none")
PATH_KEYS = ("folder", "path", "file", "proof")
BANNED_ARGS = ("--root", "--transport", "-h", "--help")

ID_RE = re.compile(r"^(\d{8}T\d{6}Z)_([0-9a-f]{8})$")
NAME_RE = re.compile(r"^(\d{8}T\d{6}Z_[0-9a-f]{8})_([a-z_]+)\.json$")
PID_RE = re.compile(r"^P-[0-9a-f]{8}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ANSWER_RE = re.compile(r"^ANS-\d{1,6}$")


class EventError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- ids ----------

def new_id(root):
    """<stamp>_<8hex>, ordered: the hex starts with the microsecond and is above
    every same-second event already waiting, so name order is creation order
    even when two events land in one second (or the clock is pinned)."""
    folder = jobfs.Folder(root)
    st = jobfs.stamp()
    base = (jobfs.utcnow().microsecond << 12) | secrets.randbits(12)
    floor = 0
    for p in folder.events.glob(f"{st}_*.json") if folder.events.exists() else ():
        m = NAME_RE.match(p.name)
        if m:
            floor = max(floor, int(m.group(1)[-8:], 16) + 1)
    return f"{st}_{min(max(base, floor), 0xFFFFFFFF):08x}"


def file_name(ev):
    return f"{ev['id']}_{ev['type']}.json"


# ---------- validation ----------

def _inside(root, value):
    return jobfs.Folder(root).inside(value)


def _paths(obj, key=""):
    """(key, text) for every path-like value in a payload, at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _paths(v, str(k).lower())
    elif isinstance(obj, list):
        for v in obj:
            yield from _paths(v, key)
    elif isinstance(obj, str) and key in PATH_KEYS:
        yield key, obj


def tracker_folders(argv):
    """Folder values a tracker command line sets (--folder X, --folder=X, folder=X)."""
    out = []
    for i, tok in enumerate(argv):
        low = tok.lower()
        if low == "--folder" and i + 1 < len(argv):
            out.append(argv[i + 1])
        elif low.startswith("--folder="):
            out.append(tok.split("=", 1)[1])
        elif re.match(r"^folder\s*=", low):
            out.append(tok.split("=", 1)[1])
    return [f for f in out if f.strip()]


def check_tracker_payload(p, root):
    argv = p.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(t, str) for t in argv):
        return "payload.argv must be a non-empty list of strings"
    if len(argv) > 80:
        return "payload.argv has too many items"
    if argv[0] not in TRACKER_COMMANDS:
        return f"tracker command '{argv[0]}' is not a write command"
    for tok in argv[1:]:
        if tok.split("=", 1)[0].lower() in BANNED_ARGS:
            return f"payload.argv may not hold {tok.split('=', 1)[0]}"
    if "pid" in p and not (isinstance(p["pid"], str) and PID_RE.match(p["pid"])):
        return "payload.pid must be P-<8 hex>"
    for f in tracker_folders(argv):
        if not _inside(root, f):
            return f"folder outside the root: {f}"
    return None


def check_queue_payload(p):
    reason = p.get("reason", "")
    if reason and reason not in REASONS:
        return f"unknown reason '{reason}'"
    q = p.get("question_ids", [])
    if not isinstance(q, list) or not all(isinstance(x, str) and len(x) <= 40 for x in q):
        return "payload.question_ids must be a list of short strings"
    conf = p.get("confirmation", "none")
    if conf not in CONFIRMATIONS:
        return f"unknown confirmation '{conf}'"
    return None


def check_usage_payload(p):
    for k in ("session_pct", "weekly_pct", "after_n", "minutes"):
        v = p.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"payload.{k} must be a number"
    return None


def check_added_payload(ev, p, root):
    """An added event: a numbered job folder under applications/, plain fields."""
    if not isinstance(ev.get("n"), int) or isinstance(ev["n"], bool) or not 0 < ev["n"] < 1000:
        return "an added event needs n (a number from 1 to 999)"
    if not ev.get("job_key"):
        return "an added event needs job_key"
    if p.get("via") not in VIAS:
        return f"payload.via must be one of {', '.join(VIAS)}"
    it = p.get("item")
    if not isinstance(it, dict):
        return "payload.item must be an object"
    for k in ADDED_TEXT:
        if not isinstance(it.get(k, ""), str) or len(it.get(k, "")) > 2000:
            return f"payload.item.{k} must be text"
    for k in ("needs", "sensitive"):
        v = it.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and len(x) <= 80 for x in v):
            return f"payload.item.{k} must be a list of short strings"
    if it.get("posted") not in (None, "") and posted_value(it["posted"]) != it["posted"]:
        return "payload.item.posted must be a YYYY-MM-DD date or \"undated\""
    folder = str(it.get("folder", "")).replace("\\", "/")
    if not folder.startswith("applications/") or not _inside(root, folder):
        return "payload.item.folder must be a job folder under applications/"
    url = it.get("apply_url") or ""
    if url:
        if not re.match(r"^https?://", url, re.I):
            return "payload.item.apply_url must be an http(s) URL"
        why = url_refusal(url)
        if why:
            return f"payload.item.apply_url refused: {why}"
        import netfetch
        if it.get("host") and it["host"] != netfetch.host_of(url):
            return "payload.item.host must be the apply_url's host"
    upload = it.get("upload") or ""
    if upload and not upload_fits(upload, ev["n"]):
        return f"payload.item.upload must be upload/{ev['n']:02d}_<Last>_Resume_<Employer>.pdf (this event's n)"
    return None


def posted_value(value):
    """A posting's posted value as stored or printed (shared decision R2): a
    real YYYY-MM-DD date, else "undated"."""
    s = str(value or "").strip()[:10]
    if DAY_RE.match(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            pass
    return "undated"


UPLOAD_RE = re.compile(r"^upload/(\d{2,3})[cw]?_[A-Za-z0-9]{1,30}_(?:Resume|Cover)_[A-Za-z0-9]{1,12}\.(?:pdf|docx)$")


def upload_fits(upload, n):
    """True when an added item's upload name carries its own number, so it
    never names (and a stage replaces) another item's file (SEC-14)."""
    m = UPLOAD_RE.match(str(upload).replace("\\", "/"))
    return bool(m) and int(m.group(1)) == int(n)


def url_refusal(url):
    """Why a link may not join today's list (shared decision 1), else None:
    a host that is not a plain web address (bad-url), a job board, or a
    private or local address: netfetch.web_refusal, the one link refusal (R1)."""
    import netfetch  # stdlib and jobfs only
    return netfetch.web_refusal(url)


def validate(ev, root, name=None):
    """None when the event is well formed, else the reason it is not."""
    if not isinstance(ev, dict):
        return "not a JSON object"
    if ev.get("v") != jobfs.EVENT_SCHEMA:
        return f"schema version {ev.get('v')!r} (want {jobfs.EVENT_SCHEMA})"
    if not isinstance(ev.get("id"), str) or not ID_RE.match(ev["id"]):
        return "id must be <YYYYMMDDTHHMMSSZ>_<8hex>"
    if ev.get("type") not in TYPES:
        return f"unknown type {ev.get('type')!r}"
    if name is not None and name != file_name(ev):
        return f"file name {name} does not match id and type"
    try:
        datetime.fromisoformat(str(ev.get("at")))
    except ValueError:
        return "at must be an ISO timestamp"
    if ev.get("date") not in (None, "") and not DAY_RE.match(str(ev["date"])):
        return "date must be YYYY-MM-DD"
    if ev.get("n") is not None and (isinstance(ev["n"], bool) or not isinstance(ev["n"], int)):
        return "n must be a number"
    for k in ("job_key", "session"):
        if not isinstance(ev.get(k, ""), str) or len(ev.get(k, "")) > 300:
            return f"{k} must be text"
    p = ev.get("payload")
    if not isinstance(p, dict):
        return "payload must be an object"
    for key, value in _paths(p):
        if not value.lower().startswith("gmail:") and not _inside(root, value):
            return f"{key} path outside the root: {value}"
    if ev["type"] == "tracker":
        return check_tracker_payload(p, root)
    if ev["type"] in QUEUE_TYPES:
        return check_queue_payload(p)
    if ev["type"] == "answer_used" and not ANSWER_RE.match(str(p.get("answer_id", ""))):
        return "payload.answer_id must be ANS-<number>"
    if ev["type"] == "usage":
        return check_usage_payload(p)
    if ev["type"] == "added":
        return check_added_payload(ev, p, root)
    return None


# ---------- writing ----------

def make(root, ev_type, payload, job_key="", n=None, day=None, session=None):
    return {"v": jobfs.EVENT_SCHEMA, "id": new_id(root), "type": ev_type, "at": jobfs.now_iso(root),
            "session": session if session is not None else os.environ.get("JOB_SESSION", ""),
            "job_key": job_key or "", "n": n,
            "date": jobfs.iso_date(day) if day else jobfs.today(root).isoformat(),
            "payload": payload}


def emit(root, ev_type, payload, job_key="", n=None, day=None, session=None):
    """Write one event file, create-only. Returns (event, path); EventError if invalid."""
    folder = jobfs.Folder(root)
    for _ in range(5):
        ev = make(root, ev_type, payload, job_key, n, day, session)
        reason = validate(ev, root, file_name(ev))
        if reason:
            raise EventError(reason)
        body = json.dumps(ev, ensure_ascii=True, indent=1) + "\n"
        if len(body.encode("ascii")) > MAX_BYTES:
            raise EventError("event over 64 KB")
        path = folder.event(ev["id"], ev_type)
        try:
            jobfs.create_only(path, body)
            return ev, path
        except FileExistsError:
            continue
    raise EventError("could not find a free event name")


# ---------- reading ----------

def read_file(path):
    """(event, error, permanent), through the safe loader (shared decision R3).
    A file over 64 KB or nested deeper than 64 is permanent; one that does not
    parse yet is not: it may still be arriving, so it is retried."""
    try:
        ev = jobfs.read_json(path, MAX_BYTES)
    except OSError as e:
        return None, f"unreadable: {e}", False
    except jobfs.BadJson as e:
        if e.kind == "size":
            return None, "event over 64 KB", True
        return None, (f"not JSON: {e}" if e.kind != "json" else str(e)), e.permanent
    return ev, None, False


def bad_dir(root):
    """runner/in/bad/: event and request files that cannot be read as JSON."""
    return jobfs.Folder(root).runner_in / "bad"


def set_aside(root, path, reason):
    """Move a file that cannot be read as JSON to runner/in/bad/ with
    <name>.reason.txt beside it (never deleted). Returns the destination."""
    dest = bad_dir(root)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / Path(path).name
    os.replace(path, target)
    line = re.sub(r"[\r\n]+", " ", str(reason))[:500]
    jobfs.atomic_write_text(dest / (Path(path).name + ".reason.txt"), f"{line}\n")
    clear_failure(root, [Path(path).name])
    return target


def is_young(path, now=None):
    try:
        return (now or time.time()) - Path(path).stat().st_mtime < YOUNG_SECONDS
    except OSError:
        return False


def pending_files(root):
    """Event files waiting in runner/in/events, in name order."""
    folder = jobfs.Folder(root).events
    if not folder.exists():
        return []
    return sorted((p for p in folder.glob("*.json") if not p.name.startswith(".")), key=lambda p: p.name)


def done_dirs(root, today=None, days=RECENT_DAYS):
    base = jobfs.Folder(root).runner_out / "events_done"
    if not base.exists():
        return []
    today = today or jobfs.today(root)
    first = (today - timedelta(days=days)).isoformat()
    return sorted(d for d in base.iterdir() if d.is_dir() and DAY_RE.match(d.name) and d.name >= first)


def recent(root, today=None, days=RECENT_DAYS):
    """Valid events, pending and recently applied, once each, in name order."""
    paths = list(pending_files(root))
    for d in done_dirs(root, today, days):
        paths += [p for p in d.glob("*.json") if not p.name.startswith(".")]
    seen, out = set(), []
    for p in sorted(paths, key=lambda p: p.name):
        ev, err, _ = read_file(p)
        if err or validate(ev, root, p.name) or ev["id"] in seen:
            continue
        seen.add(ev["id"])
        ev["_pending"] = p.parent == jobfs.Folder(root).events
        out.append(ev)
    return out


# ---------- moving (runner side) ----------

def _retries_path(root):
    return jobfs.Folder(root).state / "event_retries.json"


def _read_retries(root):
    return jobfs.read_json_or(_retries_path(root), jobfs.MAX_FILE, {})


def note_failure(root, name):
    """Count one failed read of an event file; returns the count so far."""
    counts = _read_retries(root)
    counts[name] = int(counts.get(name, 0)) + 1
    jobfs.atomic_write_text(_retries_path(root), json.dumps(counts, indent=1, sort_keys=True))
    return counts[name]


def clear_failure(root, names):
    counts = _read_retries(root)
    if any(n in counts for n in names):
        for n in names:
            counts.pop(n, None)
        jobfs.atomic_write_text(_retries_path(root), json.dumps(counts, indent=1, sort_keys=True))


def move_done(root, path, day):
    dest = jobfs.Folder(root).events_done(day)
    dest.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest / Path(path).name)


def reject(root, path, reason):
    """Move a file to runner/out/events_rejected/ with <name>.reason.txt beside it."""
    dest = jobfs.Folder(root).events_rejected
    dest.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest / Path(path).name)
    line = re.sub(r"[\r\n]+", " ", str(reason))[:500]
    jobfs.atomic_write_text(dest / (Path(path).name + ".reason.txt"), f"{line}\n")
    clear_failure(root, [Path(path).name])


def rejected_list(root, days=14):
    """[{file, reason}] for event files rejected in the last `days` days: those
    in runner/out/events_rejected/ and the unreadable ones set aside in
    runner/in/bad/ (request files there are not events and are left out)."""
    cutoff = time.time() - days * 86400
    out = []
    for dest in (jobfs.Folder(root).events_rejected, bad_dir(root)):
        if not dest.exists():
            continue
        for p in sorted(dest.glob("*.reason.txt")):
            name = p.name[: -len(".reason.txt")]
            m = NAME_RE.match(name)
            if dest != jobfs.Folder(root).events_rejected and not (m and m.group(2) in TYPES):
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    continue
                reason = p.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            out.append({"file": name, "reason": reason})
    return out


# ---------- the day's overlay ----------

def fold(evs, day):
    """Per item for one date: the last state event and its details."""
    items = {}
    for ev in evs:
        if ev["type"] not in QUEUE_TYPES or (ev.get("date") or "") != day:
            continue
        key = (ev.get("n"), ev.get("job_key", ""))
        it = items.setdefault(key, {"n": ev.get("n"), "job_key": ev.get("job_key", ""), "history": []})
        p = ev["payload"]
        it.update(state=ev["type"], reason=p.get("reason", ""), question_ids=p.get("question_ids", []),
                  confirmation=p.get("confirmation", ""), at=ev["at"])
        it["history"].append(ev["type"])
    return items


def runner_given(root, day):
    """(job keys, numbers) the runner's queue files for the day gave:
    queue.json and its addenda, but not addendum_user.json (the added jobs
    themselves) or a *.prev.json copy."""
    qd = jobfs.Folder(root).queue_dir(day)
    keys, numbers = set(), set()
    if not qd.exists():
        return keys, numbers
    for p in [qd / "queue.json"] + sorted(qd.glob("addendum_*.json")):
        if p.name == "addendum_user.json" or p.name.endswith(".prev.json") or not p.exists():
            continue
        data = jobfs.read_json_or(p, jobfs.MAX_STATE, {})
        for it in (data.get("items") or []) if isinstance(data, dict) else []:
            if isinstance(it, dict):
                keys.add(it.get("job_key"))
                numbers.add(it.get("n"))
    return keys, numbers


def user_items(root, day=None, evs=None):
    """The day's jobs from links (added events, pending and applied) as queue
    items in number order: the event's item plus n, job_key and added_by.
    The first event for a job key wins, and so does the first for a number;
    a number or job key the runner's own queue files gave today is never
    taken: an event reusing one is ignored (SEC-14), so no two items of the
    day share a number or an upload name."""
    day = jobfs.iso_date(day) if day else jobfs.today(root).isoformat()
    evs = recent(root) if evs is None else evs
    keys, numbers = runner_given(root, day)
    out = []
    for ev in evs:
        if ev["type"] != "added" or (ev.get("date") or "") != day or ev["job_key"] in keys \
                or ev.get("n") in numbers:
            continue
        keys.add(ev["job_key"])
        numbers.add(ev.get("n"))
        it = dict(ev["payload"]["item"])
        it.update(n=ev["n"], job_key=ev["job_key"], added_by=ev["payload"]["via"])
        it.setdefault("needs", [])
        it.setdefault("sensitive", [])
        out.append(it)
    return sorted(out, key=lambda it: it["n"])


def overlay(root, day=None):
    """The merged view for one date: queue items' states, jobs added from links,
    pending tracker events, rejected files, usage readings and answers used."""
    today = jobfs.today(root)
    day = jobfs.iso_date(day) if day else today.isoformat()
    evs = recent(root, today)
    usage = [dict(ev["payload"], at=ev["at"]) for ev in evs if ev["type"] == "usage" and ev.get("date") == day]
    used = {}
    for ev in evs:
        if ev["type"] == "answer_used" and ev.get("date") == day:
            aid = ev["payload"]["answer_id"]
            used[aid] = used.get(aid, 0) + 1
    return {"date": day, "items": fold(evs, day), "added": user_items(root, day, evs),
            "tracker_pending": [ev["id"] for ev in evs if ev["type"] == "tracker" and ev["_pending"]],
            "rejected": rejected_list(root), "usage": usage, "answers_used": used}


KEY_REFUSED = "(key refused: it holds characters a command line must not carry)"


def shown_key(key):
    """A job key as printed for Claude to put inline in a command (shared
    decision R2): the key when it holds only A-Z a-z 0-9 . _ : / - (at most
    200), else one plain refusal in its place."""
    import jobkey
    if not key:
        return "-"
    return key if jobkey.inline_ok(key) and len(str(key)) <= jobkey.KEY_MAX else KEY_REFUSED


def item_line(it):
    parts = [f"ITEM #{it['n'] if it['n'] is not None else '-'}", shown_key(it["job_key"]), it["state"]]
    if it.get("reason"):
        parts.append(f"reason={it['reason']}")
    if it.get("question_ids"):
        parts.append("questions=" + ",".join(it["question_ids"]))
    if it.get("confirmation") and it["state"] == "applied":
        parts.append(f"confirmation={it['confirmation']}")
    return " ".join(parts)


def print_overlay(view):
    print(f"OVERLAY {view['date']}")
    items = sorted(view["items"].values(), key=lambda it: (it["n"] is None, it["n"] or 0, it["job_key"]))
    for it in items:
        print(item_line(it))
    if not items:
        print("ITEMS none")
    for it in view.get("added") or []:
        print(f"ADDED #{it['n']} {shown_key(it['job_key'])} {it['added_by']}")
    print(f"TRACKER PENDING {len(view['tracker_pending'])}")
    print(f"TRACKER REJECTED {len(view['rejected'])}")
    for u in view["usage"]:
        print(f"USAGE after #{u.get('after_n', '-')} session {u.get('session_pct', '-')}% "
              f"weekly {u.get('weekly_pct', '-')}% minutes {u.get('minutes', '-')}")
    for aid in sorted(view["answers_used"]):
        print(f"ANSWER USED {aid} x{view['answers_used'][aid]}")


# ---------- a job from a link joins today's list ----------

def job_folder_rel(root, folder):
    """applications/<name> for a job folder given as a path (absolute or from the root)."""
    root = Path(root)
    path = Path(folder) if Path(folder).is_absolute() else root / folder
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        raise EventError(f"--folder {folder} is outside the JobSearch folder")
    if len(rel.parts) != 2 or rel.parts[0] != "applications" or rel.parts[1].startswith("_"):
        raise EventError(f"--folder {folder} is not a job folder under applications/")
    if not (root / rel / "posting.md").exists():
        raise EventError(f"--folder {folder} has no posting.md (save the posting first)")
    return rel.as_posix()


def add(root, folder, via, family="", day=None, session=None):
    """Number one job folder from a link into today's list. Returns the line to
    print and the event written (None when nothing was written)."""
    import jobkey
    import jobqueue  # the queue files and the item shape; read only from here
    import netfetch
    root = Path(root)
    day = jobfs.iso_date(day) if day else jobfs.today(root).isoformat()
    rel = job_folder_rel(root, folder)
    head = jobqueue.posting_head(root / rel)
    # R2: an earlier version's (or a hand-edited) Job key line in today's safe form
    key = jobkey.safe_key(head.get("Job key")) or jobkey.job_key(head.get("URL", ""))
    if not key:
        raise EventError("posting.md has no Job key or URL line")
    why = url_refusal(head["URL"]) if head.get("URL") else None
    if why and why.startswith("job-board"):
        raise EventError(f"posting.md's URL is a job board ({why.partition(' ')[2] or why}): "
                         "it goes on the do-yourself list, never in today's list")
    if why:
        raise EventError(f"posting.md's URL is refused ({why}): its address is not a plain web address; ask "
                         "the user for the employer's own link")
    if str(jobfs.settings(root)["runner"].get("mode", "L")).upper() == "M":
        return f"NO LIST {day} (mode M): the tracker row is the record", None
    if not jobfs.Folder(root).queue_json(day).exists():
        return f"NO LIST {day} (no list built yet): the tracker row is the record", None
    items = jobqueue.day_items(root, day)
    same = next((it for it in items if jobkey.same_key(it.get("job_key"), key)), None)  # R2: an earlier key too
    if same is not None:
        return f"IN QUEUE {day} #{same.get('n')}", None
    n = max([int(it.get("n") or 0) for it in items] + [0]) + 1
    it = jobqueue.user_item(root, n, key, rel, via, family)
    payload = {"via": via, "item": {k: v for k, v in it.items() if k in ADDED_TEXT + ("needs", "sensitive", "posted")}}
    if "posted" in payload["item"]:  # R2: a YYYY-MM-DD date or "undated", wherever it is stored or printed
        payload["item"]["posted"] = posted_value(payload["item"]["posted"])
    ev, _ = emit(root, "added", payload, job_key=key, n=n, day=day, session=session)
    return f"ADDED #{n} {shown_key(key)} (addendum_user)", ev


# ---------- CLI ----------

def load_json_arg(a):
    """The --json object, or the --json-file one: a file under <root>/claude/,
    at most 64 KB, read once and then removed (argfile.load, like --args-file)."""
    if a.json_file:
        try:
            obj = argfile.load(a.json_file, a.root)
        except argfile.ArgfileError as e:
            raise EventError(f"--json-file {e}")
        return obj
    if len(a.json.encode("utf-8", "replace")) > MAX_BYTES:
        raise EventError("--json is over 64 KB")
    try:
        obj = jobfs.loads_json(a.json)
    except jobfs.BadJson as e:
        raise EventError(f"--json is not JSON: {e}")
    if not isinstance(obj, dict):
        raise EventError("--json must be a JSON object")
    return obj


def cmd_emit(a):
    obj = load_json_arg(a)
    if "payload" in obj:
        env, payload = obj, obj["payload"]
    else:
        env, payload = {}, obj
    day = a.date or env.get("date")
    if day and not DAY_RE.match(str(day)):
        raise EventError(f"date must be YYYY-MM-DD, got '{day}'")
    n = a.n if a.n is not None else env.get("n")
    ev, _ = emit(a.root, a.type, payload, job_key=a.job_key or env.get("job_key", ""), n=n, day=day,
                 session=a.session if a.session is not None else env.get("session"))
    print(f"EVENT {ev['id']}")
    return 0


def cmd_overlay(a):
    if a.date and not DAY_RE.match(a.date):
        raise EventError(f"--date must be YYYY-MM-DD, got '{a.date}'")
    print_overlay(overlay(a.root, a.date))
    return 0


def cmd_add(a):
    if a.date and not DAY_RE.match(a.date):
        raise EventError(f"--date must be YYYY-MM-DD, got '{a.date}'")
    if a.family and not re.fullmatch(r"[a-z0-9_]{1,40}", a.family):
        raise EventError(f"--family must be a family id (a-z, 0-9, _), got '{a.family}'")
    line, _ = add(a.root, a.folder, a.via, a.family or "", a.date, a.session)
    print(line)
    return 0


def build_parser():
    ap = Parser(prog="events.py", description="Event files for the runner (spec 2.5).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("emit", help="Write one create-only event file; prints EVENT <id>.", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--type", required=True, choices=EMIT_TYPES)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="the payload, or {payload, job_key, n, date, session}")
    src.add_argument("--json-file", help="the same JSON from a file under <root>/claude/ (read once, then removed)")
    p.add_argument("--job-key", default="")
    p.add_argument("--n", type=int)
    p.add_argument("--date")
    p.add_argument("--session")
    p.set_defaults(func=cmd_emit)
    p = sub.add_parser("add", help="Put one job folder from a link into today's list (addendum_user).",
                       allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--folder", required=True, help="the job folder (applications/<date> <Company> - <Role>)")
    p.add_argument("--via", required=True, choices=VIAS, help="pasted (/job <link>) or add (/job add <link>)")
    p.add_argument("--family", default="", help="the role family its title matches, if any")
    p.add_argument("--date")
    p.add_argument("--session")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("overlay", help="Print the merged view of the day's events.", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--date")
    p.set_defaults(func=cmd_overlay)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = argfile.parse(build_parser(), argv)  # outside text arrives in a JSON file, never inline
    try:
        return args.func(args)
    except (EventError, jobfs.ConfigError, OSError) as e:
        print(f"ERROR {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
