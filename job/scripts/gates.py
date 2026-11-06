"""Hard gates G1-G12 for the runner (spec 3.8, 3.10, 3.11, 3.13), cheapest first,
before any model call or build.

Usage:
    python gates.py run --root R --in postings.jsonl [--date YYYY-MM-DD] [--offline DIR]
    python gates.py explain --root R --job-key K

run gates saved postings (boards.py or aggregators.py --out lines: a Posting
record plus ai_text and hidden) into state/pool.json and prints
    GATES seen=<n> passed=<n> discarded=<n> parked=<n> diy=<n> skipped=<n>
explain prints the gate trail of one posting, from the newest run that judged it:
    EXPLAIN <job key> <company> | <title> | <date>
    GATE G1 ok ...
    GATE G7 knockout:ca_cls required "CLS license required"

The gates (list stage G1-G5 decide whether a detail call is made at all):
    G1 host       hard deny (job boards) discards; R3 hosts go lead-only; robots
    G2 seen       already pooled, queued, deferred, applied or closed: skipped, no log
    G3 location   [home] places (plus second_ring when widened), remote with a US
                  restriction, or an unknown location (judged again at G10)
    G4 title      a family's core or adjacent title (token-set >= 0.8) or a title_any
                  term; no title_none term and no [gates] exclude_title_words word
    G5 age        over 30 days and not reposted: stale
    G6 closed     the reader's closed marker
    G7 knockout   a [[knockout]] title or required pattern, unless profile.md
                  "Licenses held" lists it; a preferred pattern only flags
    G8 knockout   sponsorship (and citizenship) against profile.md
    G9 title      no require_any word in the description
    G10 location  remote but not open to the US
    G11 AI text   text addressed to an AI: parked as needs-you, never queued
    G12 apply     no apply URL or a job-board apply URL: discard; chat apply: do-yourself
G13 (caps) belongs to the queue builder (jobqueue.py), which defers, never discards.

Title terms (title_any, title_none, require_any, exclude_title_words) match whole
words of the normalized title (jobkey.norm_title), so "capa" never hits
"Capacity". "stem*" also matches longer words (microbiolog* -> microbiology),
"a+b" needs every part, and "a|b" inside a part is either. An exclude word
written in capitals of three letters or fewer ("IT", "HR", "VP") matches the
title's own capitals only. Families are tried in file order and the first that
matches wins, so specific families go before general ones; for academic,
institute and government employers a cv-mode family goes first.

Every discard writes one line to queue/<date>/discards.csv and a seen record
(state/seen/<YYYY-MM>.jsonl). Filter discards (location, title, stale,
knockout) carry profile_hash and are judged again after search_profile.toml,
the licenses held or the widen steps change. Stdlib only.

Exit codes: 0 ok, 1 bad input (ERROR ...).
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import netfetch  # noqa: E402
import posting  # noqa: E402

STALE_DAYS = 30
REQ_SECTION_MIN = 200   # requirements sections shorter than this ('PhD and 2 years') say too little for G9
REQ_SECTION_MAX = 3000  # a requirements section with no heading after it stops here (before the company blurb)
DORMANT_DAYS = 60       # no family match on a board for this long: dormant, polled weekly (3.7)
SEEN_DAYS = 180
TITLE_SIM = 0.8
DUP_TITLE_SIM = 0.85      # the v1.1 SIMILAR ROLE threshold
REPOST_BITS = 3
BANDS = [(3, "0-3", 40), (7, "4-7", 25), (14, "8-14", 15), (30, "15-30", 5)]
UNDATED = ("undated", 5)       # an undated posting the board has not shown to be new
FILTER_REASONS = ("location", "title", "stale", "knockout")
DISCARD_COLUMNS = ["date", "lane", "company", "role", "location", "host", "job_key", "gate", "reason",
                   "profile_hash"]
ACADEMIC = {"academic", "institute", "government"}
LEAD_ONLY_ATS = {"smartrecruiters", "icims", "ukg", "paycom", "clearcompany", "paycor", "neogov"}
LEAD_ONLY_HOSTS = ("smartrecruiters.com", "icims.com", "ultipro.com", "paycomonline.net",
                   "clearcompany.com", "recruitingbypaycor.com", "governmentjobs.com")
CHAT_APPLY_HOSTS = ("paradox.ai", "olivia.ai")
STOP = {"of", "and", "the", "in", "for", "to", "a", "an", "at", "with", "on"}
SENIORITY = {"intern", "junior", "associate", "senior", "staff", "principal", "lead", "manager", "i", "ii",
             "iii", "iv", "v", "1", "2", "3", "4"}
PREFERRED_WORDS = re.compile(r"\b(preferred|a plus|desir(?:ed|able)|nice to have|bonus|advantageous)\b", re.I)
REQ_HEADING = re.compile(r"^[ \t]*(?:#+[ \t]*)?(?:minimum |basic |required |preferred )?(?:requirements|qualifications|"
                         r"what you(?:'ll)? (?:bring|need)|who you are|you have|must have|skills|experience)\b.*$",
                         re.I | re.M)
ANY_HEADING = re.compile(r"^[ \t]*#+[ \t]+\S.*$|^[A-Z][A-Za-z ,'&/-]{2,60}:?[ \t]*$", re.M)
NO_SPONSOR = re.compile(r"(unable|not able|cannot|can ?not|will not|won't|does not|do not|no)\s+(to\s+)?"
                        r"(provide\s+|offer\s+)?(visa\s+)?sponsor(ship)?", re.I)
CITIZEN = re.compile(r"\b(U\.?S\.? citizen(ship)?|United States citizen(ship)?)\s+(is\s+)?(required|only)|"
                     r"must be an? (U\.?S\.?|United States) citizen", re.I)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- title terms ----------

def words(text):
    """Lowercase words with punctuation as spaces and v1.1 abbreviations expanded."""
    return jobkey.norm_title(text)


def tokens(text):
    return [w for w in words(text).split() if w not in STOP]


def dice(a, b):
    """Token-set similarity of two titles: 2|A&B| / (|A|+|B|)."""
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def alt_hits(alt, padded):
    """One alternative: a phrase (whole words) or a stem ending in *."""
    stem = alt.strip().endswith("*")
    w = words(alt.strip().rstrip("*"))
    if not w:
        return False
    return (" " + w) in padded if stem else (" " + w + " ") in padded


def term_hits(term, text_words):
    """term: parts joined by +, each part alternatives joined by |."""
    padded = f" {text_words} "
    parts = [p for p in str(term).split("+") if p.strip()]
    return bool(parts) and all(any(alt_hits(a, padded) for a in p.split("|") if a.strip()) for p in parts)


def caps_word(word):
    return bool(re.fullmatch(r"[A-Z]{2,3}", word or ""))


def exclude_hit(word, raw, title_words):
    if caps_word(word):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", raw or "") is not None
    return term_hits(word.lower(), title_words)


def first_hit(terms, text_words):
    return next((t for t in terms or [] if term_hits(t, text_words)), None)


# ---------- dates ----------

def to_date(v):
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def band_of(age):
    """(band, fresh points) for an age in days, or (None, 0) when stale."""
    for limit, band, pts in BANDS:
        if age <= limit:
            return band, pts
    return None, 0


# ---------- the profile side ----------

def norm_license(text):
    return "_".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def person(root):
    """profile.md fields the gates use: licenses held, sponsorship, authorized."""
    import answers  # same folder; WP5's profile reader
    values, _ = answers.load_profile(jobfs.Folder(root).profile)
    held = {norm_license(x) for x in re.split(r"[,;]| and ", values.get("licenses") or "") if norm_license(x)}
    held -= {"none", "n_a", "na", "no"}
    return {"held": held, "sponsorship": (values.get("sponsorship") or "").strip().lower(),
            "authorized": (values.get("authorized") or "").lower(), "last_name": values.get("last_name") or ""}


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except OSError:
        return []


def employers(root):
    """company_norm -> {segment, parent, priority, families} from the watch lists."""
    f = jobfs.Folder(root)
    out = {}
    for path in (f.watchlist_csv, f.watchlist_auto_csv):
        for r in read_csv(path):
            n = jobkey.norm_company(r.get("employer_norm") or r.get("employer") or "")
            if n and n not in out:
                out[n] = {"segment": (r.get("segment") or "").strip().lower(), "priority": r.get("priority") or "",
                          "families": [x.strip() for x in (r.get("families") or "").split(";") if x.strip()]}
    return out


def widen_steps(root):
    """Widen steps switched on by the queue builder (state/queue_state.json)."""
    try:
        data = json.loads(jobfs.Folder(root).queue_state_json.read_text(encoding="utf-8"))
        return sorted(set((data.get("widen") or {}).get("on") or []))
    except (OSError, ValueError, AttributeError):
        return []


def home_states(home):
    """The US state codes of [home] states (["CA"], or "California"). Empty:
    no state check, only the country one. Not taken from the label: a user
    may live in one state and search another region's places."""
    out = set()
    raw = home.get("states") or []
    for x in [raw] if isinstance(raw, str) else raw:
        states, _, _ = posting.region_of(str(x))
        out |= states
    return frozenset(out)


class Rules:
    """search_profile.toml, profile.md and the widen state, ready for the gates."""

    def __init__(self, root, today, widen=None):
        self.root, self.today = Path(root), today
        self.profile = jobfs.search_profile(root)
        self.home = self.profile.get("home") or {}
        self.widen = sorted(set(widen if widen is not None else widen_steps(root)))
        gates = self.profile.get("gates") or {}
        self.exclude = [str(w) for w in gates.get("exclude_title_words") or []]
        self.me = person(root)
        self.places = [p for p in self.home.get("places") or [] if str(p).strip()]
        label = str(self.home.get("label") or "").split(",")[0].strip()
        if label:
            self.places.append(label)
        if "second_ring" in self.widen:
            self.places += [p for p in self.home.get("second_ring") or [] if str(p).strip()]
        self.home_states = home_states(self.home)
        self.remote_ok = str(self.home.get("remote", "us")).lower() == "us" or "remote_us" in self.widen
        self.families = [f for f in self.profile.get("family") or [] if self.family_on(f)]
        self.knockouts = [k for k in self.profile.get("knockout") or [] if k.get("id")]
        self.employers = employers(root)
        self.hash = self.make_hash()

    def family_on(self, fam):
        status = str(fam.get("status", "active")).lower()
        if status == "paused" or (status == "proposed" and "adjacent_families" not in self.widen):
            return False
        if status not in ("active", "proposed"):
            return False
        return all(norm_license(x) in self.me["held"] for x in fam.get("needs_license") or [])

    def make_hash(self):
        body = {"profile": dict(self.profile), "held": sorted(self.me["held"]),
                "sponsorship": self.me["sponsorship"], "widen": self.widen}
        canon = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]

    def held(self, ko):
        return norm_license(ko.get("id")) in self.me["held"] or norm_license(ko.get("name")) in self.me["held"]

    def segment(self, company_norm):
        return (self.employers.get(company_norm) or {}).get("segment", "")


# ---------- the seen store (2.5, 3.11) ----------

def cnt_of(p):
    city = " ".join(re.findall(r"[a-z0-9]+", (p.city or (p.location or "").split(",")[0]).lower()))
    return f"{p.company_norm}|{p.title_norm}|{city}"


def url_norm(url):
    return jobkey.canonical_url(url)[0]


def bands16(h):
    v = int(h, 16)
    return [(i, (v >> (16 * i)) & 0xFFFF) for i in range(4)]


def blank_hash(h):
    return not h or not re.fullmatch(r"[0-9a-f]{16}", h) or h == "0" * 16


class Seen:
    """state/seen/<YYYY-MM>.jsonl: the last 180 days in memory, newest record per key.

    buffered: records are kept in memory until flush() (GateRun.save calls it
    after state/pool.json is written), so a pass that stops halfway leaves no
    'pooled' record for a posting the pool never got."""

    def __init__(self, root, today, buffered=False):
        self.folder, self.today = jobfs.Folder(root), today
        self.buffered, self.pending = buffered, []
        self.by_key, self.by_url, self.by_req, self.by_cnt, self.by_band = {}, {}, {}, {}, {}
        first = (today - timedelta(days=SEEN_DAYS)).strftime("%Y-%m")
        base = self.folder.state / "seen"
        for path in sorted(base.glob("*.jsonl")) if base.exists() else []:
            if path.stem >= first:
                for rec in jobfs.read_jsonl(path):
                    if rec.get("job_key"):
                        self._index(rec)

    def _index(self, rec):
        key = rec["job_key"]
        old = self.by_key.get(key)
        if old:
            rec = dict(rec, first_seen=min(old.get("first_seen") or rec.get("first_seen") or "",
                                           rec.get("first_seen") or old.get("first_seen") or ""))
        self.by_key[key] = rec
        if rec.get("url_norm"):
            self.by_url[rec["url_norm"]] = key
        company = (rec.get("cnt") or "").split("|")[0]
        for r in rec.get("req_ids") or []:
            self.by_req.setdefault(f"{company}|{r}", set()).add(key)
        if rec.get("cnt"):
            self.by_cnt.setdefault(rec["cnt"], set()).add(key)
        if not blank_hash(rec.get("simhash")):
            for b in bands16(rec["simhash"]):
                self.by_band.setdefault(b, set()).add(key)

    def get(self, key):
        return self.by_key.get(key)

    def exact(self, p):
        rec = self.by_key.get(p.job_key)
        if rec is None and p.url:
            k = self.by_url.get(url_norm(p.url))
            rec = self.by_key.get(k) if k else None
        return rec

    def related(self, p, text_only=False):
        """[(record, how)] for other keys: same req ID and company, same
        company+title+city (unless both carry requisition IDs and none is
        shared: one title open in two labs, as a university posts it), or
        SimHash within 3 bits."""
        found = {}
        if not text_only:
            mine = set(p.req_ids or [])
            for r in p.req_ids or []:
                for k in self.by_req.get(f"{p.company_norm}|{r}", ()):
                    found.setdefault(k, f"req {r}")
            title, city = cnt_of(p).split("|")[1:]
            if title and city:  # a list with no location (Jobvite) or no title (Phenom) cannot tell jobs apart
                for k in self.by_cnt.get(cnt_of(p), ()):
                    theirs = set((self.by_key.get(k) or {}).get("req_ids") or [])
                    if mine and theirs and not mine & theirs:
                        continue  # two openings: a reworded repost is still caught by SimHash below
                    found.setdefault(k, "company+title+city")
        if not blank_hash(p.simhash):
            for b in bands16(p.simhash):
                for k in self.by_band.get(b, ()):
                    rec = self.by_key.get(k)
                    # boilerplate text is shared across levels, so the titles must agree too
                    title = ((rec or {}).get("cnt") or "||").split("|")[1]
                    if k not in found and rec and jobkey.hamming(p.simhash, rec["simhash"]) <= REPOST_BITS \
                            and dice(p.title_norm or p.title, title) >= DUP_TITLE_SIM:
                        found[k] = "simhash"
        return [(self.by_key[k], how) for k, how in sorted(found.items()) if k != p.job_key and k in self.by_key]

    def record(self, p, outcome, profile_hash="", first_seen=None):
        old = self.by_key.get(p.job_key) or {}
        rec = {"job_key": p.job_key, "url_norm": url_norm(p.url), "cnt": cnt_of(p),
               "req_ids": sorted(p.req_ids or []), "simhash": p.simhash or "",
               "first_seen": old.get("first_seen") or first_seen or p.first_seen or self.today.isoformat(),
               "last_seen": self.today.isoformat(), "posted": p.posted, "lane": p.lane, "outcome": outcome,
               "profile_hash": profile_hash}
        if old and all(old.get(k) == rec[k] for k in ("outcome", "profile_hash", "posted")):
            return old
        if self.buffered:
            self.pending.append(rec)
        else:
            jobfs.append_jsonl(self.folder.seen(self.today), rec)
        self._index(rec)
        return rec

    def flush(self):
        """Write the buffered records (state/seen/<month>.jsonl)."""
        recs, self.pending = self.pending, []
        for rec in recs:
            jobfs.append_jsonl(self.folder.seen(self.today), rec)


# ---------- verdicts and the trail ----------

@dataclass
class Verdict:
    ok: bool = True
    action: str = "pass"          # pass | discard | skip | park | diy
    gate: str = ""
    reason: str = ""
    lines: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def gate_line(self, gid, ok, text):
        self.lines.append(f"GATE {gid} {'ok ' if ok else ''}{text}".rstrip())

    def stop(self, gid, action, reason, text=""):
        self.ok, self.action, self.gate, self.reason = False, action, gid, reason
        self.lines.append(f"GATE {gid} {reason}" + (f" {text}" if text else ""))
        return self


def quote(text, n=80):
    t = re.sub(r"\s+", " ", str(text or "")).strip().replace('"', "'")
    return f'"{t[:n]}"'


def is_filter(reason):
    return (reason or "").split(":")[0] in FILTER_REASONS


# ---------- single gates ----------

def r3_host(host):
    return any(host == h or host.endswith("." + h) for h in LEAD_ONLY_HOSTS)


def g1_host(p, v, fetcher):
    host = netfetch.host_of(p.url)
    for url in (p.url, p.apply_url):
        board = netfetch.job_board(netfetch.host_of(url))
        if board:
            return v.stop("G1", "discard", f"host:{board}")
        if not netfetch.host_of(url).isascii():  # a lookalike spelling: never fetched, never handed to the user
            return v.stop("G1", "discard", "host:not-ascii")
    if p.ats in LEAD_ONLY_ATS or r3_host(host) or netfetch.hard_deny(p.url):
        v.info["lead_only"] = True
        v.gate_line("G1", True, f"lead-only {host}")
        return None
    if fetcher is not None and fetcher.robots is not None and host and not fetcher.policy.reason(p.url):
        try:
            allowed, _, why = fetcher.robots.check(p.url)
        except netfetch.FetchError:
            allowed, why = True, None
        if not allowed and why and why.startswith("robots-unreachable"):
            return v.stop("G1", "skip", "robots-unreachable", host)
        if not allowed:
            return v.stop("G1", "discard", f"robots:{host}")
    v.gate_line("G1", True, host or "-")
    return None


def place_verdict(part, rules):
    """One place of a location field: ('in', place), ('remote', ''),
    ('unknown', why) or ('out', why). A place name counts only when no other
    state or country is named with it ('Dublin, Ireland', 'Newark, NJ' and
    'Richmond - VA - US' are out for a California home)."""
    states, foreign, us = posting.region_of(part)
    if foreign and not us and not states:
        return "out", "outside the US"
    if states and rules.home_states and not states & rules.home_states:
        return "out", f"state {'/'.join(sorted(states))}"
    if re.search(r"\bremote\b", part, re.I):
        return "remote", ""
    hit = next((pl for pl in rules.places if term_hits(pl, words(part))), None)
    if hit:
        return "in", hit
    if posting.MULTI_LOCATION.match(part):
        return "unknown", "several locations"
    known = all(t.lower() in posting.US_NAMES or t.lower() in posting.US_STATE_NAMES or t in posting.US_STATES
                for t in posting.place_tokens(part))
    if known:  # 'US', 'United States', 'CA, United States': no city given
        return "unknown", "no city"
    return "out", "not a home place"


def g3_location(p, v, rules):
    """Pass when any listed place is a home place (or remote within the US);
    an empty location, 'N Locations' or a bare country or state is unknown,
    judged again on the detail record (detail_gates)."""
    loc = p.location or p.city or ""
    v.info["g3_loc"] = loc
    flags = v.info.setdefault("flags", [])
    if "location_unknown" in flags:
        flags.remove("location_unknown")
    if not loc.strip():
        if p.remote == "remote" and rules.remote_ok and p.us_eligible is not False:
            v.gate_line("G3", True, "remote")
            return None
        flags.append("location_unknown")
        v.gate_line("G3", True, "location unknown")
        return None
    if not rules.places:
        v.gate_line("G3", True, f"no places set {quote(loc)}")
        return None
    parts = posting.place_parts(loc)
    verdicts = [place_verdict(part, rules) for part in parts]
    hit = next((what for kind, what in verdicts if kind == "in"), None)
    if hit:
        v.gate_line("G3", True, f"{quote(loc)} place {quote(hit)}")
        return None
    # 'Remote, Florida, USA' names the state the remote worker must live in;
    # a board's own remote flag with an office city ('Boston, MA') does not.
    said_remote = any(re.search(r"\bremote\b", part, re.I) for part in parts)
    if rules.remote_ok and (any(kind == "remote" for kind, _ in verdicts)
                            or (p.remote == "remote" and not said_remote and p.us_eligible is not False)):
        v.gate_line("G3", True, f"remote {quote(loc)}")
        return None
    unknown = next((what for kind, what in verdicts if kind == "unknown"), None)
    if unknown:
        flags.append("location_unknown")
        v.gate_line("G3", True, f"location unknown ({unknown}) {quote(loc)}")
        return None
    why = next((what for kind, what in verdicts if kind == "out"), "")
    return v.stop("G3", "discard", "location", quote(loc) + (f" ({why})" if why else ""))


def family_match(fam, title):
    """(title_match, how) for one family, or (0, why-not)."""
    tw = words(title)
    none = first_hit(fam.get("title_none"), tw)
    if none:
        return 0, f"title_none {none}"
    best = max(((dice(title, c), c) for c in fam.get("core_titles") or []), default=(0, ""))
    if best[0] >= TITLE_SIM:
        return 1.0, f"core {quote(best[1])}"
    adj = max(((dice(title, c), c) for c in fam.get("adjacent_titles") or []), default=(0, ""))
    if adj[0] >= TITLE_SIM:
        return 0.6, f"adjacent {quote(adj[1])}"
    hit = first_hit(fam.get("title_any"), tw)
    if hit:
        return 0.6, f"any {quote(hit)}"
    return 0, "no title match"


def pick_family(rules, p, lane=""):
    """(family, title_match, how) by file order; cv-mode families first for
    academic employers; usajobs-mode families only for USAJOBS postings."""
    usajobs = lane == "usajobs" or p.ats == "usajobs"
    seg = rules.segment(p.company_norm)
    allowed = (rules.employers.get(p.company_norm) or {}).get("families") or []
    hits = []
    for fam in rules.families:
        mode = str(fam.get("mode", "industry")).lower()
        if (mode == "usajobs" and not usajobs) or (allowed and fam.get("id") not in allowed):
            continue
        tm, how = family_match(fam, p.title)
        if tm:
            hits.append((fam, tm, how, mode))
    if not hits:
        return None, 0, ""
    if usajobs:
        hits.sort(key=lambda h: h[3] != "usajobs")
    elif seg in ACADEMIC:
        hits.sort(key=lambda h: h[3] != "cv")
    else:
        hits.sort(key=lambda h: h[3] == "cv")
    fam, tm, how, _ = hits[0]
    return fam, tm, how


def g4_title(p, v, rules):
    v.info["g4_title"] = p.title or ""
    if not (p.title or "").strip():  # Phenom and Workday sitemap lists carry no title
        v.info.setdefault("flags", []).append("title_unknown")
        v.gate_line("G4", True, "title unknown")
        return None
    flags = v.info.get("flags") or []
    if "title_unknown" in flags:
        flags.remove("title_unknown")
    tw = words(p.title)
    for word in rules.exclude:
        if exclude_hit(word, p.title, tw):
            return v.stop("G4", "discard", "title", f"exclude {quote(word)}")
    fam, tm, how = pick_family(rules, p, p.lane)
    if not fam:
        return v.stop("G4", "discard", "title", quote(p.title))
    v.info.update(family=fam.get("id", ""), title_match=tm)
    v.gate_line("G4", True, f"family={fam.get('id')} {how}")
    return None


def freshness(p, seen_rec, polls_before, today):
    """(age_days or None, band, points, basis): the source date, else first_seen
    once the board was polled on 2 earlier runs (3.10)."""
    posted = to_date(p.posted)
    if posted:
        age = max(0, (today - posted).days)
        band, pts = band_of(age)
        return age, band, pts, "posted"
    first = to_date((seen_rec or {}).get("first_seen") or p.first_seen) or today
    if polls_before >= 2:
        age = max(0, (today - first).days)
        band, pts = band_of(age)
        return age, band, pts, "first_seen"
    return None, UNDATED[0], UNDATED[1], "undated"


def g5_age(p, v, seen_rec, polls_before, today):
    age, band, pts, basis = freshness(p, seen_rec, polls_before, today)
    v.info["g5_posted"] = p.posted or ""
    if band is None:
        return v.stop("G5", "discard", "stale", f"age={age} ({basis})")
    v.info.update(age_days=age, band=band, fresh=pts, date_basis=basis)
    v.gate_line("G5", True, f"band={band}" + (f" age={age}" if age is not None else "") + f" ({basis})")
    return None


def sentences_around(text, start, end):
    a = max(text.rfind(c, 0, start) for c in ".;\n") + 1
    b = min([i for i in (text.find(c, end) for c in ".;\n") if i >= 0] or [len(text)])
    return text[a:b]


def g7_knockouts(p, v, rules):
    flags = v.info.setdefault("flags", [])
    for ko in rules.knockouts:
        if rules.held(ko):
            continue
        kid = ko["id"]
        for pat in ko.get("title_patterns") or []:
            m = re.search(pat, p.title or "")
            if m:
                return v.stop("G7", "discard", f"knockout:{kid}", f"title {quote(m.group(0))}")
        for pat in ko.get("required_patterns") or []:
            for m in re.finditer(pat, p.text or ""):
                if not PREFERRED_WORDS.search(sentences_around(p.text, m.start(), m.end())):
                    return v.stop("G7", "discard", f"knockout:{kid}", f"required {quote(m.group(0))}")
                flags.append(f"license_preferred:{kid}")
                break
        for pat in ko.get("preferred_patterns") or []:
            if re.search(pat, p.text or "") and f"license_preferred:{kid}" not in flags:
                flags.append(f"license_preferred:{kid}")
    v.info["flags"] = list(dict.fromkeys(flags))
    v.gate_line("G7", True, " ".join(f for f in v.info["flags"] if f.startswith("license_")) or "none")
    return None


def g8_other(p, v, rules):
    needs = rules.me["sponsorship"].startswith("y")
    m = NO_SPONSOR.search(p.text or "")
    if needs and m:
        return v.stop("G8", "discard", "knockout:sponsorship", quote(m.group(0)))
    m = CITIZEN.search(p.text or "")
    if m:
        if needs or (rules.me["authorized"] and not re.search(r"\b(us|usa|united states)\b", rules.me["authorized"])):
            return v.stop("G8", "discard", "knockout:citizenship", quote(m.group(0)))
        v.info.setdefault("flags", []).append("citizenship_required")
    v.gate_line("G8", True, "none")
    return None


def g9_words(p, v, fam):
    """A require_any word in the requirements sections (company
    boilerplate names biology everywhere, so the whole text says little); the
    whole text when the requirements sections are missing or very short."""
    need = (fam or {}).get("require_any") or []
    if not need:
        v.gate_line("G9", True, "no require_any")
        return None
    section = requirement_text(p.text)
    body = section if len(section.strip()) >= REQ_SECTION_MIN else (p.text or "")
    hit = first_hit(need, words(body))
    if not hit:
        return v.stop("G9", "discard", "title", "no require_any word in the requirements")
    v.gate_line("G9", True, quote(hit))
    return None


def g10_remote(p, v):
    if (p.remote == "remote") and p.us_eligible is False:
        return v.stop("G10", "discard", "location", f"remote, not US {quote(p.location)}")
    v.gate_line("G10", True, p.remote or "unknown")
    return None


def g11_ai(v, ai_text):
    if ai_text:
        v.info["needs_you"] = str(ai_text[0])[:200]
        return v.stop("G11", "park", "ai_text", quote(ai_text[0]))
    v.gate_line("G11", True, "none")
    return None


def g12_route(p, v):
    url = p.apply_url or p.url
    host = netfetch.host_of(url)
    if not url or not host:
        return v.stop("G12", "discard", "apply_route", "no apply URL")
    if netfetch.job_board(host):
        return v.stop("G12", "discard", "apply_route", f"job board {host}")
    if any(host == h or host.endswith("." + h) for h in CHAT_APPLY_HOSTS):
        return v.stop("G12", "diy", "chat_apply", host)
    v.gate_line("G12", True, host)
    return None


# ---------- fit (3.12) ----------

def requirement_text(text):
    """Every requirements-like section (Requirements, Basic and Preferred
    Qualifications, What you bring, ...) joined; '' when there is none."""
    out = []
    for m in REQ_HEADING.finditer(text or ""):
        rest = text[m.end():]
        nxt = ANY_HEADING.search(rest, 1)
        out.append(rest[:nxt.start()] if nxt and nxt.start() > 40 else rest[:REQ_SECTION_MAX])
    return "\n".join(out)


def requirement_section(text):
    m = REQ_HEADING.search(text or "")
    if not m:
        return text or ""
    rest = text[m.end():]
    nxt = ANY_HEADING.search(rest, 1)
    return rest[:nxt.start()] if nxt and nxt.start() > 40 else rest


def coverage(text, fam, techniques):
    """Share of the requirement-section terms (family terms plus techniques)
    that techniques.md marks hands-on."""
    import checks  # same folder (term_regex)
    section = requirement_section(text)
    found, handson = set(), set()
    for t in techniques:
        if checks.term_found(section, t.name, t.aliases):
            found.add(t.name.lower())
            if t.level == "hands-on":
                handson.add(t.name.lower())
    for term in (fam or {}).get("terms") or []:
        if term.lower() not in found and checks.term_found(section, term):
            found.add(term.lower())
            tech = next((t for t in techniques if term.lower() in (f.lower() for f in t.forms)), None)
            if tech and tech.level == "hands-on":
                handson.add(term.lower())
    return round(len(handson) / len(found), 3) if found else 0.0


def level_match(title, fam):
    ok = [w.lower() for w in (fam or {}).get("seniority_ok") or []]
    have = [w for w in words(title).split() if w in SENIORITY]
    if not ok or not have:
        return 1.0
    return 1.0 if any(w in ok for w in have) else 0.0


def fit_of(text, title, fam, title_match, techniques):
    cov = coverage(text, fam, techniques)
    lvl = level_match(title, fam)
    return round(0.5 * cov + 0.2 * title_match + 0.15 * lvl + 0.15 * 0.5, 3), cov, lvl


# ---------- one run ----------

def as_reading(obj):
    """(Posting, ai_text, hidden, markdown, posting_status) from a Reading, a
    Posting or a saved dict."""
    if isinstance(obj, jobfs.Posting):
        return obj, [], [], "", "read"
    if isinstance(obj, dict):
        p = jobfs.Posting.from_dict(obj)
        return p, list(obj.get("ai_text") or []), list(obj.get("hidden") or []), obj.get("markdown") or "", \
            obj.get("posting_status") or "read"
    status = "browser_read" if getattr(obj, "source", "") == "browser_read" else "read"
    return obj.posting, list(obj.ai_text or []), list(obj.hidden or []), obj.markdown or "", status


class GateRun:
    """One runner pass: list gates (also as boards.poll keep=), detail gates,
    discards.csv, the seen store, trails and state/pool.json."""

    def __init__(self, root, today=None, fetcher=None, widen=None):
        self.root = Path(root)
        self.folder = jobfs.Folder(root)
        self.today = today or jobfs.today(root)
        self.fetcher = fetcher
        self.rules = Rules(root, self.today, widen)
        self.seen = Seen(root, self.today, buffered=True)
        self.discard_rows = []
        self.polls = self._load_polls()
        self.boards_today = set()
        self.cache = {}
        self.counts = {"seen": 0, "passed": 0, "discarded": 0, "parked": 0, "diy": 0, "skipped": 0}
        self.pooled, self.needs_you, self.diy = [], [], []
        self._techniques = None

    # state files
    def _load_polls(self):
        """state/board_polls.json: {"polls": {board: [dates]}, "matched": {board: date}}."""
        try:
            data = json.loads((self.folder.state / "board_polls.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.matched = dict(data.get("matched") or {})
        return dict(data.get("polls") or {})

    def polls_before(self, p, first_seen):
        key = f"{p.ats}:{p.board}" if p.board else ""
        days = [d for d in self.polls.get(key, []) if d < (first_seen or self.today.isoformat())]
        return len(days)

    def note_match(self, p, rec=None):
        """A board showed a posting that fits a family (G4 passed now or before)."""
        if p.board and not (rec and rec.get("outcome") == "discarded:title"):
            self.matched[f"{p.ats}:{p.board}"] = self.today.isoformat()

    def techniques(self):
        if self._techniques is None:
            import library
            self._techniques = library.load_techniques(self.folder.techniques)
        return self._techniques

    # the stages
    def list_gates(self, p):
        """G1-G5 on a list-level record. Cached per job key for this run."""
        if p.job_key in self.cache:
            return self.cache[p.job_key]
        if p.board:
            self.boards_today.add(f"{p.ats}:{p.board}")
        pre = self.g2_seen(p, Verdict())
        if pre:  # seen before (even a G1 discard): skipped with no log noise
            self.note_match(p, self.seen.exact(p))
            self.cache[p.job_key] = pre
            self.finish(p, pre)
            return pre
        v = Verdict()
        v.lines.append(f"EXPLAIN {p.job_key} {p.company} | {p.title} | {self.today.isoformat()}")
        if not (g1_host(p, v, self.fetcher) or self.g2_seen(p, v) or g3_location(p, v, self.rules)
                or g4_title(p, v, self.rules)):
            self.note_match(p)
            seen_rec = self.seen.get(p.job_key)
            g5_age(p, v, seen_rec, self.polls_before(p, (seen_rec or {}).get("first_seen")), self.today)
        self.cache[p.job_key] = v
        if not v.ok:
            self.finish(p, v)
        return v

    def keep(self, p):
        """boards.poll(keep=...): True when a detail call is worth making."""
        return self.list_gates(p).ok

    def g2_seen(self, p, v):
        rec = self.seen.exact(p)
        if rec:
            out = rec.get("outcome", "")
            if out.startswith("discarded:"):
                reason = out.split(":", 1)[1]
                if is_filter(reason) and rec.get("profile_hash") != self.rules.hash:
                    v.gate_line("G2", True, f"judged again ({reason}; profile changed)")
                    return None
                newer = to_date(p.posted) and to_date(rec.get("posted")) and to_date(p.posted) > to_date(rec["posted"])
                if not newer:
                    return v.stop("G2", "skip", "seen", out)
                v.info["repost_of"] = rec["job_key"]
            elif out:
                return v.stop("G2", "skip", "seen", out)
        mine = to_date(p.posted) or self.today
        for other, how in self.seen.related(p):
            if self.stale_filter(other):
                continue  # that one is judged again too; it does not hide this one
            theirs = to_date(other.get("posted")) or to_date(other.get("first_seen")) or self.today
            if mine <= theirs:
                return v.stop("G2", "skip", "seen", f"same job as {other['job_key']} ({how})")
            v.info.setdefault("repost_of", other["job_key"])
            v.info.setdefault("repost_how", how)
        if v.info.get("repost_of"):
            v.gate_line("G2", True, f"repost_of={v.info['repost_of']} ({v.info.get('repost_how', 'redated')})")
        else:
            v.gate_line("G2", True, "new")
        return None

    def stale_filter(self, rec):
        out = rec.get("outcome", "")
        return out.startswith("discarded:") and is_filter(out.split(":", 1)[1]) \
            and rec.get("profile_hash") != self.rules.hash

    def detail_gates(self, reading):
        """G1-G5 (cached) then G6-G12 on the detailed record."""
        p, ai_text, _, _, status = as_reading(reading)
        v = self.list_gates(p)
        if not v.ok or v.info.get("detail_done"):
            return v
        v.info["detail_done"] = True
        v.info["posting_status"] = "browser_read" if (status == "browser_read" or
                                                      (v.info.get("lead_only") and not p.text)) else "read"
        unread = v.info["posting_status"] == "browser_read"
        if not p.closed and self.rejudge(p, v):
            self.finish(p, v, reading)
            return v
        if p.closed:
            v.stop("G6", "discard", "closed")
        else:
            v.gate_line("G6", True, "open")
        if v.ok and (unread or not self.recheck_simhash(p, v)):
            fam = next((f for f in self.rules.families if f.get("id") == v.info.get("family")), None)
            _ = (g7_knockouts(p, v, self.rules) or g8_other(p, v, self.rules)
                 or (None if unread else g9_words(p, v, fam)) or g10_remote(p, v) or g11_ai(v, ai_text)
                 or g12_route(p, v))
        self.finish(p, v, reading)
        return v

    def rejudge(self, p, v):
        """G3, G4 and G5 again on the detailed record when the list record
        lacked what they judge: a list with no location or 'N Locations', no
        title, or no date (or a provisional one) is judged by its detail.
        True when one of them stopped the posting."""
        flags = v.info.get("flags") or []
        loc = p.location or p.city or ""
        if loc != v.info.get("g3_loc", loc) or ("location_unknown" in flags and loc):
            v.lines.append("GATE G3 judged again on the detail")
            if g3_location(p, v, self.rules):
                return True
        if "title_unknown" in flags or (p.title or "") != v.info.get("g4_title", p.title or ""):
            v.lines.append("GATE G4 judged again on the detail")
            if g4_title(p, v, self.rules):
                return True
            self.note_match(p)
        if (p.posted or "") != v.info.get("g5_posted", p.posted or "") and p.posted:
            v.lines.append("GATE G5 judged again on the detail")
            seen_rec = self.seen.get(p.job_key)
            if g5_age(p, v, seen_rec, self.polls_before(p, (seen_rec or {}).get("first_seen")), self.today):
                return True
        return False

    def recheck_simhash(self, p, v):
        """G2 again with the description's SimHash (list records may lack text)."""
        if v.info.get("repost_of"):
            return False
        for other, how in self.seen.related(p, text_only=True):
            if self.stale_filter(other):
                continue
            theirs = to_date(other.get("posted")) or to_date(other.get("first_seen")) or self.today
            if (to_date(p.posted) or self.today) <= theirs:
                v.stop("G2", "skip", "seen", f"same job as {other['job_key']} (simhash)")
                return True
            v.info["repost_of"] = other["job_key"]
        return False

    # outcomes
    def finish(self, p, v, reading=None):
        if (v.action == "pass" and reading is None) or v.info.get("finished"):
            return  # a list-stage pass: the detail stage decides
        v.info["finished"] = True
        if v.action == "skip":
            self.counts["skipped"] += 1
            return
        self.counts["seen"] += 1
        self.save_trail(p, v)
        if v.action == "discard":
            self.counts["discarded"] += 1
            ph = self.rules.hash if is_filter(v.reason) else ""
            self.write_discard(p, v.gate, v.reason, ph)
            self.seen.record(p, f"discarded:{v.reason}", ph)
            return
        self.counts["passed" if v.action == "pass" else ("parked" if v.action == "park" else "diy")] += 1
        item = self.pool_item(p, v, reading)
        if v.action == "park":
            item["parked"] = "ai_text"
            self.needs_you.append({"job_key": p.job_key, "company": p.company, "role": p.title,
                                   "quote": v.info.get("needs_you", "")})
        elif v.action == "diy":
            item["diy"] = v.reason
            self.diy.append(item)
        self.pooled.append(item)
        self.seen.record(p, "pooled")

    def pool_item(self, p, v, reading):
        _, ai_text, hidden, md, _ = as_reading(reading)
        fam = next((f for f in self.rules.families if f.get("id") == v.info.get("family")), {}) or {}
        fit, cov, lvl = fit_of(p.text, p.title, fam, v.info.get("title_match", 0.6), self.techniques())
        d = p.to_dict()
        d.update(family=fam.get("id", ""), mode=str(fam.get("mode", "industry")),
                 family_priority=int(fam.get("priority", 2) or 2),
                 title_match=v.info.get("title_match", 0.6), fit=fit, coverage=cov, level_match=lvl,
                 flags=list(dict.fromkeys(v.info.get("flags") or [])), band=v.info.get("band", ""),
                 age_days=v.info.get("age_days"), date_basis=v.info.get("date_basis", ""),
                 repost_of=v.info.get("repost_of"), segment=self.rules.segment(p.company_norm),
                 posting_status=v.info.get("posting_status", "read"), markdown=md, ai_text=ai_text,
                 hidden=hidden, pooled_on=self.today.isoformat(), folder="")
        return d

    def save_trail(self, p, v):
        month = self.folder.state / "trails" / f"{self.today:%Y-%m}.jsonl"
        jobfs.append_jsonl(month, {"job_key": p.job_key, "date": self.today.isoformat(), "lines": v.lines})

    def write_discard(self, p, gate, reason, profile_hash):
        """Kept until save(): discards.csv may be open in Excel, and that must
        not stop the pass (write_discards)."""
        self.discard_rows.append([self.today.isoformat(), p.lane, p.company, p.title, p.location,
                                  netfetch.host_of(p.url), p.job_key, gate, reason, profile_hash])

    def process(self, readings):
        """Gate detailed readings (Reading, Posting or saved dicts). Returns the
        pool items added this call."""
        start = len(self.pooled)
        order = sorted(readings, key=lambda r: as_reading(r)[0].lane != "watch")  # board records win dedupe
        for r in order:
            self.detail_gates(r)
        return self.pooled[start:]

    def save(self):
        """Write state/pool.json (merged), board_polls.json; returns the counts."""
        for key in self.boards_today:
            days = sorted(set(self.polls.get(key, [])) | {self.today.isoformat()})[-10:]
            self.polls[key] = days
        jobfs.atomic_write_text(self.folder.state / "board_polls.json",
                                json.dumps({"polls": self.polls, "matched": self.matched}, indent=1,
                                           sort_keys=True) + "\n")
        self.mark_dormant()
        pool = load_pool(self.root)
        items = {it["job_key"]: it for it in pool.get("items", [])}
        for it in self.pooled:
            items.setdefault(it["job_key"], it)
        needs = {n["job_key"]: n for n in pool.get("needs_you", [])}
        for n in self.needs_you:
            needs.setdefault(n["job_key"], n)
        save_pool(self.root, list(items.values()), counts=self.counts, needs_you=list(needs.values()),
                  date_=self.today)
        # Only now that pool.json holds the pooled postings do their seen
        # records say so (G2 skips a seen posting on every later run).
        self.seen.flush()
        rows, self.discard_rows = self.discard_rows, []
        write_discards(self.root, self.today, rows)
        return dict(self.counts)


    def mark_dormant(self):
        """board_health.csv: a board polled today with no family match for 60
        days becomes dormant; a dormant board with a match today is active again."""
        import boards  # same folder (WP3)
        health = boards.load_health(self.root)
        changed = False
        for key in sorted(self.boards_today):
            last = to_date(self.matched.get(key) or min(self.polls.get(key) or [self.today.isoformat()]))
            dormant = last is not None and (self.today - last).days >= DORMANT_DAYS
            for hkey, row in health.items():
                if hkey != key and not hkey.startswith((key + "/", key + "@")):
                    continue
                if dormant and row.get("status") == "active":
                    row["status"], changed = "dormant", True
                elif not dormant and row.get("status") == "dormant":
                    row["status"], changed = "active", True
        if changed:
            boards.save_health(self.root, health)


# ---------- files ----------

def neutral(v):
    """CSV text safe to open in Excel: leading = + - @ neutralized."""
    s = re.sub(r"[\r\n]+", " ", str(v if v is not None else ""))
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def append_csv(path, columns, row):
    append_csv_rows(path, columns, [row])


def append_csv_rows(path, columns, rows):
    """One append of several rows (a header first for a new file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if not path.exists():
        w.writerow(columns)
    for row in rows:
        w.writerow([neutral(x) for x in row])
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def write_discards(root, day, rows, attempts=4, pause=0.25):
    """Append rows to queue/<day>/discards.csv. A file another program holds
    (Excel keeps an open CSV locked) is tried a few times; rows that still
    cannot be written wait in state/discards_pending.jsonl and go out on the
    next call. Nothing is dropped and the caller's pass goes on. Returns the
    number of rows still waiting."""
    import time
    folder = jobfs.Folder(root)
    pending = folder.state / "discards_pending.jsonl"
    waiting = [r for r in jobfs.read_jsonl(pending) if isinstance(r, dict) and isinstance(r.get("row"), list)] \
        if pending.exists() else []
    batches = {}
    for r in waiting:
        batches.setdefault(str(r.get("day") or jobfs.iso_date(day)), []).append(r["row"])
    if rows:
        batches.setdefault(jobfs.iso_date(day), []).extend(rows)
    left = []
    for d, batch in sorted(batches.items()):
        path = folder.discards_csv(datetime.strptime(d, "%Y-%m-%d").date())
        for attempt in range(attempts):
            try:
                append_csv_rows(path, DISCARD_COLUMNS, batch)
                break
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(pause)
        else:
            left += [{"day": d, "row": [str(x) if x is not None else "" for x in row]} for row in batch]
    if left:
        jobfs.atomic_write_text(pending, "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in left))
    elif waiting:
        pending.unlink(missing_ok=True)
    return len(left)


def load_pool(root, path=None):
    path = Path(path) if path else jobfs.Folder(root).pool_json
    if not path.exists():
        return {"v": 1, "items": [], "needs_you": [], "counts": {}}
    try:
        data = jobfs.read_json_verified(path, retries=0)
    except jobfs.NotReady:
        return {"v": 1, "items": [], "needs_you": [], "counts": {}}
    data.setdefault("items", [])
    data.setdefault("needs_you", [])
    return data


def save_pool(root, items, counts=None, needs_you=None, date_=None):
    old = load_pool(root)
    data = {"v": 1, "date": jobfs.iso_date(date_ or jobfs.today(root)),
            "counts": counts if counts is not None else old.get("counts", {}),
            "needs_you": needs_you if needs_you is not None else old.get("needs_you", []),
            "items": sorted(items, key=lambda it: it.get("job_key", ""))}
    return jobfs.atomic_write_json(jobfs.Folder(root).pool_json, data)


def needs_you(root):
    """Parked AI-text postings for the card (each once)."""
    return load_pool(root).get("needs_you", [])


# ---------- explain ----------

def trail(root, job_key):
    """The newest recorded trail lines for a job key, or None."""
    base = jobfs.Folder(root).state / "trails"
    found = None
    for path in sorted(base.glob("*.jsonl")) if base.exists() else []:
        for rec in jobfs.read_jsonl(path):
            if rec.get("job_key") == job_key:
                found = rec
    return found


def cmd_explain(a):
    rec = trail(a.root, a.job_key)
    if rec is None:
        seen = Seen(a.root, jobfs.today(a.root)).get(a.job_key)
        if seen:
            print(f"EXPLAIN {a.job_key} seen {seen.get('outcome')} (no trail kept)")
            return 0
        print(f"NOT FOUND {a.job_key}")
        return 1
    for line in rec.get("lines") or []:
        print(line)
    return 0


def cmd_run(a):
    today = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else jobfs.today(a.root)
    readings = jobfs.read_jsonl(a.input)
    fetcher = netfetch.Fetcher(root=a.root, offline=a.offline) if a.offline else None
    run = GateRun(a.root, today, fetcher=fetcher)
    for d in readings:
        run.detail_gates(d)
    counts = run.save()
    print("GATES " + " ".join(f"{k}={counts[k]}" for k in ("seen", "passed", "discarded", "parked", "diy", "skipped")))
    return 0


def build_parser():
    ap = Parser(prog="gates.py", description="Hard gates G1-G12 (spec 3.8).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("explain", help="The gate trail of one posting.", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--job-key", required=True)
    p.set_defaults(func=cmd_explain)
    p = sub.add_parser("run", help="Gate saved postings (JSONL) into state/pool.json.", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--in", dest="input", required=True)
    p.add_argument("--date")
    p.add_argument("--offline", help="netfetch fixture folder for robots.txt")
    p.set_defaults(func=cmd_run)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if not Path(args.root).is_dir():
        print(f"ERROR --root {args.root} is not a folder")
        return 1
    try:
        return args.func(args)
    except (jobfs.ConfigError, OSError, ValueError) as e:
        print(f"ERROR {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
