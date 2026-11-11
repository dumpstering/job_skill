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
    G1 host       a posting or apply URL that is not http(s), names a host netfetch
                  refuses (bad-url) or is on a job board: discard; R3 hosts go
                  lead-only; robots. Every host is netfetch.host_of's canonical form.
    G2 seen       already pooled, queued, deferred, applied or closed: skipped, no log
    G3 location   [home] places, remote with a US restriction, or an unknown
                  location (judged again on the detail). A place of an enabled
                  [[home.region]] (somewhere the user would move to) passes
                  marked relocate: queued after the rest, under that region's
                  own pay_floor. A second_ring place is
                  out until the widen step is on (reason location:second_ring when
                  title and age pass). An office label ('MBC Lab') of a watch-listed
                  employer is judged by that row's city.
    G4 title      a family's core or adjacent title (token-set >= 0.8) or a title_any
                  term; no title_none term and no [gates] exclude_title_words word
    G5 age        over 30 days and not reposted: stale
    G6 closed     the reader's closed marker
    G7 knockout   a [[knockout]] title or required pattern, unless profile.md
                  "Licenses held" lists it; a preferred pattern only flags
    G8 knockout   sponsorship (and citizenship) against profile.md
    G9 title      no require_any word in the requirements, or more avoid_any words
                  (another field's training: chemistry, formulation, biophysics,
                  LC-MS, machine learning) than require_any words, at least 2
    G9 terms      a family's min_terms (a user's setting for a broad family):
                  the posting names fewer of the user's terms (techniques.md
                  and the family's terms, counted as fit counts them) than
                  that: discard, reason title
    G9 seniority  judged against the user (R6): their years and title levels
                  from the achievement library's role headings, or [experience]
                  in settings.toml. Clearly beyond (a principal-level title two
                  steps over the user's, unless the text asks only a modest bar
                  the user meets; or BEYOND_YEARS more years than the user has):
                  discard, reason seniority. Asking more (a higher title, more
                  years, or DOMAIN_YEARS of a field the library never names):
                  kept as borderline, which the queue lists after the rest.
                  Industry-mode families only.
    G9 work type  a temporary job when [gates] employment_types leaves out
                  "temporary" (the title or the text says temp or temporary,
                  and not also temp-to-hire, contract-to-hire or full-time):
                  discard, reason employment:temporary (the title alone is
                  judged at G4). Shift work (night, swing, 12-hour, weekend)
                  is kept and marked low_rank when [gates] shift_work is
                  "last": the queue lists it after everything else.
    G9 pay        [gates] pay_floor (a yearly amount; 0 is off): a posting
                  whose stated pay tops out below it is discarded, reason
                  pay. Hourly rates count at 2,080 hours a year. A posting
                  that states no pay is kept. A job in a relocation region
                  ([[home.region]]) is judged by that region's pay_floor.
    G10 location  remote but not open to the US
    G11 AI text   text addressed to an AI: parked as needs-you, never queued
    G12 apply     no apply URL, or one G1 would refuse: discard; chat apply:
                  do-yourself; an http link from a board is opened as https
G13 (caps) belongs to the queue builder (jobqueue.py), which defers, never discards.
A posting that fails to gate is skipped with one ERROR line (D2). A posting
still without a title after its detail read (a closed Phenom job's list
record) is never pooled: one SKIP line, nothing recorded, read again next run.

fit (the queue's order): 0.5 x overlap + 0.2 x title match + 0.15 x level +
0.075, where overlap counts the user's terms the posting names (each
techniques.md technique, hands-on 1 and familiar 0.5, and each family term,
1 when the achievement library names it and 0.5 when not, then half of that
for a technique's general form alone: 'PCR' for 'real-time PCR'), FIT_TERMS
of them being 1.0. The queue lists the day's picks by fit, best first.

Title terms (title_any, title_none, require_any, exclude_title_words) match whole
words of the normalized title (jobkey.norm_title), so "capa" never hits
"Capacity". "stem*" also matches longer words (microbiolog* -> microbiology),
"a+b" needs every part, and "a|b" inside a part is either. A family's
title_allow phrases are set aside before its title_none terms are tried
("associate director" keeps an Associate Director title in a family whose
title_none has "director"). An exclude word
written in capitals of three letters or fewer ("IT", "HR", "VP") matches the
title's own capitals only, never right after a number ('08-HR' is a shift).
Of the families that match, the closest core or adjacent title wins (an
exact one first), then file order, so specific families go before general
ones; for academic, institute and government employers a cv-mode family
goes first. G3: a remote list naming only states outside home is regional
('Remote Michigan | Remote United States' is out).

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
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import netfetch  # noqa: E402
import posting  # noqa: E402

STALE_DAYS = 30
REQ_SECTION_MIN = 200   # requirements sections shorter than this ('PhD and 2 years') say too little for G9
REQ_SECTION_MAX = 3000  # a requirements section with no heading after it stops here (before the company blurb)
REQ_TEXT_MAX = 200_000  # the requirement scans read at most this much of a posting (bounded work on any text)
REQ_SECTIONS_MAX = 20   # and at most this many requirements headings
AVOID_MIN = 2           # G9: this many avoid_any words (and more than require_any words) mark another field's job
FIT_TERMS = 6           # fit: this many of the user's terms named by a posting is a full overlap
FIT_TEXT_MAX = 60_000   # fit reads at most this much of a posting
# fit: a technique's last word that is a method on its own (general_forms)
GENERAL_METHODS = {"pcr", "sequencing", "microscopy", "cytometry", "chromatography", "electrophoresis",
                   "spectrometry", "cloning", "immunoassay", "immunoassays", "blot", "blotting",
                   "transfection", "mutagenesis", "crispr"}
WEB_SCHEMES = ("http", "https")
SITE_WORDS = re.compile(r"\b(lab|labs|laboratory|laboratories|campus|building|bldg|floor|suite|hq|headquarters)\b",
                        re.I)
STREET = re.compile(r"^\s*\d{1,6}[a-z]?\s+[A-Za-z]", re.I)  # '300 Pasteur Dr - Floor 01'
DORMANT_DAYS = 60       # no family match on a board for this long: dormant, polled weekly (3.7)
SEEN_DAYS = 180
TITLE_SIM = 0.8
DUP_TITLE_SIM = 0.85      # the v1.1 SIMILAR ROLE threshold
REPOST_BITS = 3
BANDS = [(3, "0-3", 40), (7, "4-7", 25), (14, "8-14", 15), (30, "15-30", 5)]
UNDATED = ("undated", 5)       # an undated posting the board has not shown to be new
FILTER_REASONS = ("location", "title", "stale", "knockout", "seniority", "pay", "employment")
HOURS_PER_YEAR = 2080     # an hourly rate as a yearly amount (the pay floor)
PAY_TEXT_MAX = 200_000    # the pay scan reads at most this much of a posting
PAY_HITS_MAX = 60         # and at most this many dollar amounts
SHIFT_TEXT_MAX = 60_000   # the shift and temporary scans read at most this much
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
PREFERRED_HEADING = re.compile(r"(?i)\b(preferred|desired|desirable|nice to have|bonus|a plus)\b")
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
    """A global exclude word in a title. A word in capitals ("HR", "IT") is
    matched in the title's own capitals, and never right after a number with
    an optional '-' or space: '08-HR', '8 HR' and '(08HR)' are shift lengths."""
    if caps_word(word):
        return re.search(rf"(?<![A-Za-z0-9])(?<!\d-)(?<!\d ){re.escape(word)}(?![A-Za-z0-9])", raw or "") is not None
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


def posted_value(v):
    """A posted value as it may be stored or printed (R2): 'YYYY-MM-DD' when
    it holds a date, None when blank, else 'undated'."""
    d = to_date(v)
    if d:
        return d.isoformat()
    return "undated" if str(v or "").strip() else None


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
    """company_norm -> {segment, priority, families, city} from the watch lists."""
    f = jobfs.Folder(root)
    out = {}
    for path in (f.watchlist_csv, f.watchlist_auto_csv):
        for r in read_csv(path):
            n = jobkey.norm_company(r.get("employer_norm") or r.get("employer") or "")
            if n and n not in out:
                out[n] = {"segment": (r.get("segment") or "").strip().lower(), "priority": r.get("priority") or "",
                          "families": [x.strip() for x in (r.get("families") or "").split(";") if x.strip()],
                          "city": (r.get("city") or "").strip()}
            elif n and not out[n].get("city") and (r.get("city") or "").strip():
                out[n]["city"] = r["city"].strip()
    return out


def widen_steps(root):
    """Widen steps switched on by the queue builder (state/queue_state.json)."""
    try:
        data = jobfs.read_json(jobfs.Folder(root).queue_state_json, jobfs.MAX_STATE)  # R3: the safe loader
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


def amount(v):
    """A yearly pay amount from a setting: a number from 0 to 10 million, else 0 (off)."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 10_000_000 else 0.0


def regions(home):
    """The enabled [[home.region]] tables of [home]: places the user would
    move to for the right job (off unless enabled = true). Each is {label,
    places, states, pay_floor, unstated_pay}: pay_floor is that region's own
    yearly floor (0: the [gates] floor), and unstated_pay says what happens
    to a job there that states no pay ("drop", the default: moving needs a
    stated range that reaches the floor; or "keep")."""
    out = []
    raw = (home or {}).get("region") or []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or r.get("enabled") is not True:
            continue
        places = [str(p) for p in r.get("places") or [] if str(p).strip()]
        states = home_states(r)
        label = str(r.get("label") or "").strip() or (places[0] if places else "/".join(sorted(states)))
        if label and (places or states):
            out.append({"label": label[:60], "places": places, "states": states,
                        "pay_floor": amount(r.get("pay_floor")),
                        "unstated_pay": "keep" if str(r.get("unstated_pay", "drop")).lower() == "keep" else "drop"})
    return out


def search_area(home, widen=()):
    """(places, states) the board readers walk: [home] places (with the
    second ring once widened) and states, plus every enabled region's."""
    home = home or {}
    places = [str(p) for p in home.get("places") or [] if str(p).strip()]
    label = str(home.get("label") or "").split(",")[0].strip()
    if label:
        places.append(label)
    if "second_ring" in widen:
        places += [str(p) for p in home.get("second_ring") or [] if str(p).strip()]
    states = set(home_states(home))
    for r in regions(home):
        places += [p for p in r["places"] if p not in places]
        states |= r["states"]
    return places, frozenset(states)


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
        ring = [p for p in self.home.get("second_ring") or [] if str(p).strip()]
        if "second_ring" in self.widen:
            self.places += ring
            ring = []
        self.second_ring = ring  # set aside (with their own discard reason) until the widen step is on
        self.home_states = home_states(self.home)
        self.regions = regions(self.home)  # enabled [[home.region]] tables: places the user would move to
        self.pay_floor = amount(gates.get("pay_floor"))  # yearly; 0 is off
        kinds = gates.get("employment_types")
        self.temp_ok = not isinstance(kinds, list) or any(str(k).strip().lower() in ("temporary", "temp")
                                                          for k in kinds)
        self.shift_last = str(gates.get("shift_work", "any")).strip().lower() == "last"
        self.remote_ok = str(self.home.get("remote", "us")).lower() == "us" or "remote_us" in self.widen
        self.families = [f for f in self.profile.get("family") or [] if self.family_on(f)]
        self.knockouts = [k for k in self.profile.get("knockout") or [] if k.get("id")]
        self.employers = employers(root)
        try:  # R6: seniority is judged against the user
            self.experience = experience(root, today)
        except Exception:  # noqa: BLE001  a library or settings the reader cannot use: no seniority judged (D2)
            self.experience = None
        self.hash = self.make_hash()

    def family_on(self, fam):
        status = str(fam.get("status", "active")).lower()
        if status == "paused" or (status == "proposed" and "adjacent_families" not in self.widen):
            return False
        if status not in ("active", "proposed"):
            return False
        return all(norm_license(x) in self.me["held"] for x in fam.get("needs_license") or [])

    def make_hash(self):
        me = self.experience
        body = {"profile": dict(self.profile), "held": sorted(self.me["held"]),
                "sponsorship": self.me["sponsorship"], "widen": self.widen}
        if me is not None:  # a seniority discard is judged again when the user's whole years or titles change
            body["experience"] = [int(me.years), int(me.industry_years), me.level, me.doctorate]
        if any(f.get("min_terms") for f in self.families):  # and a min_terms discard when techniques.md changes
            try:
                body["techniques"] = hashlib.sha256(jobfs.Folder(self.root).techniques.read_bytes()).hexdigest()[:12]
            except OSError:
                body["techniques"] = ""
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
        key = jobkey.safe_key(rec["job_key"])  # R2: a key an earlier version stored, in today's form
        rec = rec if key == rec["job_key"] else dict(rec, job_key=key)
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


def host_text(url):
    """The canonical host of url for display (discards.csv, trail lines); ''
    for a URL netfetch refuses. Never used for an allow or deny decision."""
    try:
        return netfetch.host_of(url) if url else ""
    except (netfetch.Denied, ValueError):
        return ""


def url_refusal(url):
    """Why a posting or apply URL may never be pooled or handed to the user,
    else None. 'not-web' for a scheme other than http or https (a file:// or
    smb:// link makes Windows connect to that host when the browser opens
    it); otherwise netfetch.web_refusal decides (R1): the job-board domain,
    'bad-url' (a lookalike host, userinfo, a backslash, whitespace, an odd
    port, an IP literal, a single-label host) or 'non-public' (localhost,
    .local, .internal and the like)."""
    try:
        scheme = urlparse(str(url)).scheme.lower()
    except ValueError:
        return "bad-url"
    if scheme not in WEB_SCHEMES:
        return "not-web"
    # R1: the one link refusal; G1 and G12 name its kind (bad-url, non-public) or the job-board domain
    kind, _, rest = (netfetch.web_refusal(url) or "").partition(" ")
    return (rest or "job-board") if kind == "job-board" else (kind or None)


def refusal_kind(reason):
    """A netfetch refusal reason as a G1 discard reason (host:<it>): the
    job-board domain or 'bad-url'; None for any other refusal."""
    kind, _, rest = str(reason or "").partition(" ")
    if kind == "job-board":
        return rest or "job-board"
    return "bad-url" if kind == "bad-url" else None


def g1_host(p, v, fetcher):
    for url in (p.url, p.apply_url):
        why = url_refusal(url) if url else None
        if why:  # never fetched, never handed to the user
            return v.stop("G1", "discard", f"host:{why}")
    host = host_text(p.url)
    if p.ats in LEAD_ONLY_ATS or r3_host(host) or netfetch.hard_deny(p.url):
        v.info["lead_only"] = True
        v.gate_line("G1", True, f"lead-only {host}")
        return None
    if fetcher is not None and fetcher.robots is not None and host and not fetcher.policy.reason(p.url):
        # A robots.txt read refused by policy (it redirects to a job board, a
        # bad-url or a non-public host) is never 'allowed' (SV-07): a job
        # board or bad-url is a discard, anything else the unreachable skip.
        try:
            allowed, _, why = fetcher.robots.check(p.url)
        except netfetch.Denied as d:
            kind = refusal_kind(d.reason)
            if kind:
                return v.stop("G1", "discard", f"host:{kind}", f"robots.txt of {host}")
            allowed, why = False, f"robots-unreachable {host}"
        except netfetch.FetchError:
            allowed, why = False, f"robots-unreachable {host}"
        if not allowed and refusal_kind(why):
            return v.stop("G1", "discard", f"host:{refusal_kind(why)}")
        if not allowed and why and why.startswith(("robots-unreachable", "crawl-delay")):
            return v.stop("G1", "skip", why.split()[0], host)  # the host is skipped this run only
        if not allowed:
            return v.stop("G1", "discard", f"robots:{host}")
    v.gate_line("G1", True, host or "-")
    return None


def place_verdict(part, rules, company_norm=""):
    """home_place_verdict, and for a place that is out of the home area,
    ('region', label) when it is in an enabled [[home.region]] (a place the
    user would move to): one of that region's places with no state outside
    the region beside it, or anywhere in its states when it lists no places."""
    kind, what = home_place_verdict(part, rules, company_norm)
    if kind == "out" and getattr(rules, "regions", None):
        states, foreign, us = posting.region_of(part)
        if not (foreign and not us and not states) and not re.search(r"\bremote\b", part, re.I):
            pw = words(part)
            for r in rules.regions:
                if states and r["states"] and not states & r["states"]:
                    continue
                if any(term_hits(pl, pw) for pl in r["places"]) or (not r["places"] and states & r["states"]):
                    return "region", r["label"]
    return kind, what


def home_place_verdict(part, rules, company_norm=""):
    """One place of a location field: ('in', place), ('remote', ''),
    ('unknown', why) or ('out', why). A place name counts only when no other
    state or country is named with it ('Dublin, Ireland', 'Newark, NJ',
    'Dublin OH' and 'Richmond - VA - US' are out for a California home: the
    state comes from posting.region_of, which reads 'City ST' too). A
    second_ring place before the widen step is out with its own reason. An
    office or lab label ('MBC Lab', '300 Pasteur Dr - Floor 01', the
    employer's own name) of a watch-listed employer is judged by that row's
    city, and is unknown when the row names none."""
    states, foreign, us = posting.region_of(part)
    if foreign and not us and not states:
        return "out", "outside the US"
    if states and rules.home_states and not states & rules.home_states:
        return "out", f"state {'/'.join(sorted(states))}"
    if re.search(r"\bremote\b", part, re.I):
        return "remote", ""
    pw = words(part)
    hit = next((pl for pl in rules.places if term_hits(pl, pw)), None)
    if hit:
        return "in", hit
    ring = next((pl for pl in getattr(rules, "second_ring", []) if term_hits(pl, pw)), None)
    if ring:
        return "out", f"second ring {ring}"
    if posting.MULTI_LOCATION.match(part):
        return "unknown", "several locations"
    known = all(t.lower() in posting.US_NAMES or t.lower() in posting.US_STATE_NAMES or t in posting.US_STATES
                for t in posting.place_tokens(part))
    if known:  # 'US', 'United States', 'CA, United States': no city given
        return "unknown", "no city"
    row = rules.employers.get(company_norm) if company_norm else None
    if row is not None and not foreign and site_label(part, company_norm):
        city = row.get("city") or ""
        if not city:
            return "unknown", "office label"
        kind, what = home_place_verdict(city, rules)
        if kind == "in":
            return "in", f"{what} (the watch list's city for {quote(part, 40)})"
        return "out", f"office label; the watch list's city {quote(city, 40)}"
    return "out", "not a home place"


def remote_states(parts, rules):
    """The states a remote location list limits the job to, when it names at
    least one and none of them is a home state (sorted codes), else []. Only
    the remote places count ('Remote Michigan', 'Remote - TX'); an office
    city in the same list does not. A bare 'Remote United States' beside them
    is one more regional entry, not a nationwide job."""
    if not rules.home_states:
        return []
    named = set()
    for part in parts:
        if re.search(r"\bremote\b", part, re.I):
            named |= posting.region_of(part)[0]
    return sorted(named) if named and not named & rules.home_states else []


def site_label(part, company_norm):
    """True when a location names an office, lab or address rather than a
    place: a site word ('MBC Lab', 'Main Campus'), a street address, or the
    employer's own name ('California Academy of Sciences')."""
    if SITE_WORDS.search(part) or STREET.match(part):
        return True
    return bool(company_norm) and jobkey.norm_company(part) == company_norm


def g3_location(p, v, rules):
    """Pass when any listed place is a home place (or remote within the US);
    an empty location, 'N Locations' or a bare country or state is unknown,
    judged again on the detail record (detail_gates)."""
    loc = p.location or p.city or ""
    v.info["g3_loc"] = loc
    v.info.pop("region", None)
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
    verdicts = [place_verdict(part, rules, p.company_norm) for part in parts]
    hit = next((what for kind, what in verdicts if kind == "in"), None)
    if hit:
        v.gate_line("G3", True, f"{quote(loc)} place {quote(hit)}")
        return None
    region = next((what for kind, what in verdicts if kind == "region"), None)
    if region:  # a place the user would move to: kept, marked, queued after the rest (pool_item)
        v.info["region"] = region
        v.gate_line("G3", True, f"{quote(loc)} relocate {quote(region)}")
        return None
    # 'Remote, Florida, USA' names the state the remote worker must live in;
    # a board's own remote flag with an office city ('Boston, MA') does not.
    said_remote = any(re.search(r"\bremote\b", part, re.I) for part in parts)
    regional = remote_states(parts, rules)
    if regional:  # 'Remote Michigan | Remote Ohio | Remote United States': a regional field job (L3-06)
        return v.stop("G3", "discard", "location", quote(loc) + f" (remote, other states {'/'.join(regional)})")
    if rules.remote_ok and (any(kind == "remote" for kind, _ in verdicts)
                            or (p.remote == "remote" and not said_remote and p.us_eligible is not False)):
        v.gate_line("G3", True, f"remote {quote(loc)}")
        return None
    unknown = next((what for kind, what in verdicts if kind == "unknown"), None)
    if unknown:
        flags.append("location_unknown")
        v.gate_line("G3", True, f"location unknown ({unknown}) {quote(loc)}")
        return None
    ring = next((what for kind, what in verdicts if what.startswith("second ring ")), None)
    if ring:  # the caller turns this into location:second_ring when nothing else stops the posting
        v.info["second_ring"] = ring.split(" ", 2)[2]
    why = ring or next((what for kind, what in verdicts if kind == "out"), "")
    return v.stop("G3", "discard", "location", quote(loc) + (f" ({why})" if why else ""))


def family_match(fam, title):
    """(title_match, how) for one family, or (0, why-not)."""
    tm, how, _ = family_score(fam, title)
    return tm, how


def set_aside(title_words, allow):
    """title_words with each title_allow phrase taken out, so a title_none
    term no longer sees it: with title_allow ["associate director"] and
    title_none ["director"], 'associate director msat' is kept and 'director
    msat' (or 'associate director director msat') is not."""
    out = f" {title_words} "
    for phrase in allow or []:
        w = words(str(phrase))
        if w:
            out = out.replace(f" {w} ", " _ ")
    return out.strip()


def family_score(fam, title):
    """(title_match, how, raw) for one family: raw is the token-set score of
    the core or adjacent title that matched (an exact title is 1.0), 0 for a
    title_any match or none."""
    tw = words(title)
    none = first_hit(fam.get("title_none"), set_aside(tw, fam.get("title_allow")))
    if none:
        return 0, f"title_none {none}", 0
    best = max(((dice(title, c), c) for c in fam.get("core_titles") or []), default=(0, ""))
    if best[0] >= TITLE_SIM:
        return 1.0, f"core {quote(best[1])}", best[0]
    adj = max(((dice(title, c), c) for c in fam.get("adjacent_titles") or []), default=(0, ""))
    if adj[0] >= TITLE_SIM:
        return 0.6, f"adjacent {quote(adj[1])}", adj[0]
    hit = first_hit(fam.get("title_any"), tw)
    if hit:
        return 0.6, f"any {quote(hit)}", 0
    return 0, "no title match", 0


def pick_family(rules, p, lane=""):
    """(family, title_match, how): cv-mode families first for academic
    employers and usajobs-mode families only for USAJOBS postings; then the
    closest core or adjacent title (an exact one first: a bare 'Research
    Associate' is the research associate family's, not 'Clinical Research
    Associate's'); then file order."""
    usajobs = lane == "usajobs" or p.ats == "usajobs"
    seg = rules.segment(p.company_norm)
    allowed = (rules.employers.get(p.company_norm) or {}).get("families") or []
    hits = []
    for i, fam in enumerate(rules.families):
        mode = str(fam.get("mode", "industry")).lower()
        if (mode == "usajobs" and not usajobs) or (allowed and fam.get("id") not in allowed):
            continue
        tm, how, raw = family_score(fam, p.title)
        if tm:
            hits.append((fam, tm, how, mode, raw, i))
    if not hits:
        return None, 0, ""
    if usajobs:
        first = (lambda h: h[3] != "usajobs")
    elif seg in ACADEMIC:
        first = (lambda h: h[3] != "cv")
    else:
        first = (lambda h: h[3] == "cv")
    hits.sort(key=lambda h: (first(h), -h[4], h[5]))
    fam, tm, how = hits[0][:3]
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
    if not getattr(rules, "temp_ok", True) and temp_marked(p.title):
        return v.stop("G4", "discard", "employment:temporary", f"title {quote(p.title)}")
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


def title_place_gates(p, rules):
    """G3 (place), G4 (title family) and the G7 title knockouts on a record
    that has only a company, title and place: a do-yourself lead (L4-02),
    which the user would apply for unread. Returns the Verdict: ok when all
    three pass, with info family and title_match; else the gate that stopped it."""
    v = Verdict()
    _ = g3_location(p, v, rules) or g4_title(p, v, rules) or g7_knockouts(p, v, rules)
    return v


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
    whole text when the requirements sections are missing or very short.
    avoid_any names other fields' requirement words (synthetic or analytical
    chemistry, formulation, biophysics, LC-MS, machine learning, physics):
    when the requirements name at least AVOID_MIN of them and more of them
    than require_any words, the job's core training is another field's (D6)."""
    need = (fam or {}).get("require_any") or []
    avoid = (fam or {}).get("avoid_any") or []
    if not need and not avoid:
        v.gate_line("G9", True, "no require_any")
        return None
    section = requirement_text(p.text)
    body = words(section if len(section.strip()) >= REQ_SECTION_MIN else (p.text or "")[:REQ_TEXT_MAX])
    on = [t for t in need if term_hits(t, body)]
    if need and not on:
        return v.stop("G9", "discard", "title", "no require_any word in the requirements")
    off = [t for t in avoid if term_hits(t, body)]
    if len(off) >= AVOID_MIN and len(off) > len(on):
        return v.stop("G9", "discard", "title", "requirements name another field: " + ", ".join(off[:4]))
    v.gate_line("G9", True, quote(on[0]) if on else "no require_any")
    return None


def g9_terms(p, v, fam, techniques, library_text=""):
    """A family's min_terms (a user's own setting for a broad family; absent
    or 0 is off): the posting must name at least that many of the user's
    terms, counted as fit counts them (overlap: each techniques.md technique,
    hands-on 1 and familiar 0.5, each family term, a general form at half).
    A 'Scientist' posting that names none of the user's methods is another
    specialty's. Not judged until techniques.md lists something."""
    need = (fam or {}).get("min_terms")
    if not isinstance(need, (int, float)) or isinstance(need, bool) or need <= 0 or not techniques:
        return None
    n = overlap(p.text, fam, techniques, library_text)
    if n < need:
        return v.stop("G9", "discard", "title", f"names {n:g} of your terms; {fam.get('id')} asks {need:g}")
    v.gate_line("G9", True, f"names {n:g} of your terms")
    return None


# ---------- seniority against the user (R6) ----------
#
# Judged against the user, never by fixed words: the years and title levels
# come from the achievement library's role headings (or the [experience]
# override in settings.toml), so a senior user keeps senior roles. Only what
# is clearly beyond is dropped (a principal-level title two or more steps over
# the user's, or a stated bar of BEYOND_YEARS more years than the user has);
# the rest of what asks for more is 'borderline', queued after the others.

BEYOND_YEARS = 5   # a stated bar this many years over the user's own is clearly beyond
MODEST_BAR = 5     # a principal-level title whose text asks at most this many years (that the user has) is borderline
DOMAIN_YEARS = 4   # this many years of a field the user's library never names: borderline
GENERIC_FIELD = frozenset(
    "relevant related relevent industry industrial academic academia hands experience experienced work working "
    "professional post doc docs doctoral postdoctoral graduate postgraduate scientific science sciences biotech "
    "biotechnology pharmaceutical pharma company companies environment environments setting settings field fields "
    "similar equivalent combination total minimum least more prior previous practical overall strong proven "
    "progressive progressively responsible full time direct directly demonstrated successful year years laboratory "
    "lab labs research the and with for within including plus combined applicable both either other any area areas "
    "role roles position positions job jobs bench based independent independently leading leadership level "
    "life biopharmaceutical biopharma advanced managing manage management projects project end hands on "
    "relevant cross functional functional team teams".split())
LEVEL_WORDS = (("senior principal", 6), ("distinguished", 6), ("associate principal", 5), ("principal", 5),
               ("associate director", 5), ("senior staff", 5), ("staff", 4), ("senior", 3))
LEVEL_NAMES = {0: "entry", 1: "entry", 2: "scientist", 3: "senior", 4: "staff", 5: "principal", 6: "senior principal"}
ASSOCIATE_TRACK = re.compile(r"\b(research associate|associate scientist|research assistant|lab associate|"
                             r"laboratory associate|associate specialist|assistant|technician|technologist)\b")
STUDENT_ROLE = re.compile(r"\b(graduate|student|phd candidate|doctoral candidate|intern|internship|undergraduate|"
                          r"teaching assistant|trainee)\b", re.I)
ACADEMIC_ROLE = re.compile(r"\b(postdoc\w*|post doc\w*|postdoctoral|post doctoral|fellow|professor|lecturer|"
                           r"instructor)\b", re.I)
ACADEMIC_ORG = re.compile(r"\b(university|college|institute|school|academy|hospital|medical center)\b", re.I)
DOCTORATE_USER = re.compile(r"\bph\.? ?d\b|\bdoctora(?:l|te)\b|doctor of philosophy|\bthesis\b|\bdissertation\b|"
                            r"\bpostdoc|\bpost-?doctoral", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
SPAN_END = re.compile(r"(?i)\b(present|current|now|today|ongoing)\b")
YEAR_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
_N = r"(\d{1,2}|" + "|".join(YEAR_NUM) + r")"
YEARS_ASKED = re.compile(r"(?i)\b" + _N + r"(?:\s*\(\d{1,2}\))?\s*(?:\+|plus)?\s*(?:(?:-|to|\u2013|\u2014)\s*" + _N +
                         r"\s*\+?\s*)?(?:or more\s+|\+\s*)?(?:years?|yrs?)\b(?!\s+(?:of age|old))")
YEARS_CONTEXT = re.compile(r"(?i)\b(experience[ds]?|industry|industrial|post-?docs?|post-?doctoral|post-?graduate)\b")
UP_TO = re.compile(r"(?i)(?:up to|less than|fewer than|no more than|under|maximum of|at most)\s*$")
DOCTORATE = re.compile(r"(?i)\b(ph\.?\s?d|doctorate|doctoral|m\.?d|pharm\.?\s?d|dvm)\b")
OTHER_DEGREE = re.compile(r"(?i)\b(b\.?s|b\.?a|bachelor'?s?|m\.?s|master'?s?|associate'?s degree)\b")
INDUSTRY = re.compile(r"(?i)\b(industry|industrial|biotech\w*|pharma\w*|commercial)\b")
ACADEMIC_TOO = re.compile(r"(?i)\b(academi[ac]\w*|post-?doc\w*|post-?doctoral|or equivalent)\b")


def title_level(title):
    """The seniority a title asks for, the lowest among its alternatives
    ('Scientist / Senior Scientist' asks for none): 6 senior principal or
    distinguished, 5 principal or senior staff, 4 staff, 3 senior, else 0.
    An associate-track title (research associate, associate scientist,
    assistant, technician) is at most 2, below a PhD scientist, unless it
    names a principal level. Roman numerals and digits say nothing: a
    Genentech 'Scientist 3' is an entry PhD role."""
    cap = 2 if ASSOCIATE_TRACK.search(words(title)) else None
    alts = []
    for part in re.split(r"\s*/\s*|\s+or\s+", str(title or "")):
        w = words(part)
        if alts and re.match(r"(?:i{1,3}|iv|[1-4])\b", w):
            alts[-1] += " " + w  # 'Scientist I/II', 'Scientist 3/4': one alternative
        elif w:
            alts.append(w)
    levels = []
    for w in alts or [""]:
        padded = f" {w} "
        lvl = max((n for phrase, n in LEVEL_WORDS if f" {phrase} " in padded), default=0)
        levels.append(min(lvl, cap) if cap is not None and lvl < 5 else lvl)
    return min(levels)


@dataclass
class Experience:
    years: float           # after the degree: every role but student ones (postdocs count)
    industry_years: float  # of those, outside academia
    level: int             # the highest title level held (title_level; a plain title is 2)
    effective: int         # level, lifted at most one step by years
    doctorate: bool
    source: str            # "library" or "settings"
    vocab: frozenset = frozenset()  # words (and 6-letter stems) of the library and techniques.md

    def text(self):
        return (f"{self.years:g} years ({self.industry_years:g} in industry), titles up to "
                f"{LEVEL_NAMES.get(self.level, self.level)}, judged as {LEVEL_NAMES.get(self.effective)}")


def month_of(text, end=False):
    """(year, month) of 'Jun 2023', '2015' or 'Present' (today), else None."""
    m = re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", text)
    if m:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    m = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if m:
        return int(m.group(1)), 12 if end else 1
    return None


def role_span(dates, today):
    """(start, end) months since year 0 of a heading's dates, or None."""
    parts = re.split(r"\s+(?:-|to|\u2013|\u2014)\s+|\s*[\u2013\u2014]\s*", dates, maxsplit=1)
    if len(parts) != 2:
        parts = dates.rsplit("-", 1) if dates.count("-") == 1 else [dates, dates]
    start = month_of(parts[0])
    end = (today.year, today.month) if SPAN_END.search(parts[1]) else month_of(parts[1], end=True)
    if not start or not end:
        return None
    a, b = start[0] * 12 + start[1] - 1, end[0] * 12 + end[1]
    return (a, b) if b > a else None


def union_years(spans):
    total, last = 0, None
    for a, b in sorted(spans):
        if last is not None and a < last:
            a = last
        if b > a:
            total += b - a
        last = b if last is None else max(last, b)
    return round(total / 12, 1)


def experience(root, today):
    """The user's Experience from the achievement library's role headings
    ('### Title | Employer | Mon YYYY - Mon YYYY'), with [experience] years
    and industry_years in settings.toml taking over when set; None when
    neither gives any years (nothing is judged by seniority then)."""
    import library  # same folder
    folder = jobfs.Folder(root)
    try:
        text = folder.library.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        text = ""
    spans, industry, level, fence, found = [], [], 0, False, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        h = None if fence else library.HEADING_RE.match(line)
        role = library.role_of(h.group(1)) if h else None
        if not role:
            continue
        title, org, dates = role
        found = True
        if STUDENT_ROLE.search(title):
            level = max(level, 1)
            continue
        lvl = title_level(title) or (1 if ASSOCIATE_TRACK.search(words(title)) else 2)
        level = max(level, lvl)
        span = role_span(dates, today)
        if span:
            spans.append(span)
            if not (ACADEMIC_ROLE.search(title) or ACADEMIC_ORG.search(org)):
                industry.append(span)
    years, ind, source = union_years(spans), union_years(industry), "library"
    over = (jobfs.settings(root).get("experience") or {}) if (folder.config / "settings.toml").exists() else {}

    def number(key):  # a number from 0 to 60, else None ("" is blank: the library counts)
        v = over.get(key) if isinstance(over, dict) else None
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 60 else None
    set_years, set_ind = number("years"), number("industry_years")
    if set_years is not None:
        years, ind, source = set_years, min(ind, set_years), "settings"
    if set_ind is not None:
        ind, years, source = set_ind, max(years, set_ind), "settings"
    if not spans and source != "settings":
        return None
    level = level or 2
    weighted = ind + 0.5 * max(0.0, years - ind)
    by_years = 2 if weighted < 4 else 3 if weighted < 7 else 4 if weighted < 11 else 5
    try:
        text += "\n" + folder.techniques.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        pass
    vocab = set(words(text[:400_000]).split())
    vocab |= {w[:6] for w in vocab if len(w) >= 6}
    return Experience(years, ind, level, max(level, min(level + 1, by_years)),
                      bool(DOCTORATE_USER.search(text)), source, frozenset(vocab))


def requirement_lines(text):
    """The lines of the required (not preferred) requirements sections; the
    whole text's lines when there are none."""
    text = (text or "")[:REQ_TEXT_MAX]
    out = []
    for i, m in enumerate(REQ_HEADING.finditer(text)):
        if i >= REQ_SECTIONS_MAX:
            break
        if not PREFERRED_HEADING.search(m.group(0)):
            out += section_after(text, m, REQ_SECTION_MAX).splitlines()
    if sum(len(x) for x in out) < REQ_SECTION_MIN:
        out = text.splitlines()
    return [x for x in out if x.strip()]


def years_asked(text, doctorate=True):
    """[(years, industry, what)] for each required line that asks for years.
    Per line: the years after a doctorate ('PhD with 4+ years'); a line
    naming only other degrees is skipped for a doctorate holder (the PhD path
    is elsewhere); else the first number. 'up to 2 years' and '0-3 years'
    ask for none. A line must speak of experience (not 'a two year, fixed
    term position') and not be preferred. industry: the line asks for
    industry years and not academic ones too. what: the words after the
    number up to the end of that clause ('drug discovery experience')."""
    out = []
    for line in requirement_lines(text):
        if len(line) > 600:
            continue
        line = " ".join(line.split())  # one space between words: the patterns below stay linear (D3)
        if not YEARS_CONTEXT.search(line) or PREFERRED_WORDS.search(line):
            continue
        hits = list(YEARS_ASKED.finditer(line))
        if not hits:
            continue
        doc = DOCTORATE.search(line)
        if doc and doctorate:
            after = [m for m in hits if m.start() >= doc.end()]
            pick = after[:1] or hits[-1:]
            if after and OTHER_DEGREE.search(line, doc.end(), after[0].start()):
                continue  # 'Ph.D. or Master's plus 10 years': the years are the other degree's
        elif OTHER_DEGREE.search(line) and doctorate:
            continue
        elif OTHER_DEGREE.search(line):
            pick = sorted(hits, key=lambda m: years_low(line, m))[:1]
        else:
            pick = hits[:1]
        what = re.split(r"[.;:()\[\]]|,\s*(?:including|with|or|and)\b|\bincluding\b", line[pick[0].end():], maxsplit=1)[0]
        out.append((years_low(line, pick[0]), bool(INDUSTRY.search(line)) and not ACADEMIC_TOO.search(line),
                    what[:120]))
    return out


def years_bar(text, doctorate=True):
    """(years, industry) the requirements ask for (the highest line of
    years_asked), or (None, False)."""
    asked = years_asked(text, doctorate)
    if not asked:
        return None, False
    n, industry, _ = max(asked, key=lambda a: a[0])
    return n, industry


def domain_gap(asked, vocab):
    """(years, field) for the largest 'DOMAIN_YEARS or more years of <field>'
    whose field names nothing the user's library or techniques name ('5+
    years of industry drug discovery experience'), else None. A field of
    generic words only ('relevant industry experience') is no field."""
    best = None
    for n, _, what in asked:
        if n < DOMAIN_YEARS:
            continue
        content = [w for w in words(what).split() if w not in GENERIC_FIELD and not w.isdigit() and len(w) > 2]
        if content and not any(named(w, vocab) for w in content):
            if best is None or n > best[0]:
                best = (n, " ".join(content[:5]))
    return best


def named(word, vocab):
    """True when the user's vocabulary holds the word or a word sharing its
    first 6 letters ('microbiolog...')."""
    return word in vocab or (len(word) >= 6 and word[:6] in vocab)


def years_low(line, m):
    """The lowest number of years one YEARS_ASKED match asks for."""
    if UP_TO.search(line[max(0, m.start() - 16):m.start()]):
        return 0
    n = m.group(1).lower()
    return int(YEAR_NUM.get(n, n))


def seniority(title, text, fam, me):
    """('ok' | 'borderline' | 'beyond', why) for an industry-mode family's
    posting against the user's Experience (me; None judges nothing)."""
    if me is None or str((fam or {}).get("mode", "industry")).lower() != "industry":
        return "ok", ""
    lvl = title_level(title)
    asked = years_asked(text, me.doctorate) if text else []
    bar, industry = max(asked, key=lambda a: a[0])[:2] if asked else (None, False)
    have = me.industry_years if industry else me.years
    asks = f"asks {bar}+ years{' in industry' if industry else ''}, you have {have:g}" if bar is not None else ""
    if bar is not None and bar - have >= BEYOND_YEARS:
        return "beyond", asks
    name = LEVEL_NAMES.get(lvl, "")
    if lvl >= 5 and lvl - me.effective >= 2:
        if bar is not None and bar <= MODEST_BAR and bar <= have:
            return "borderline", f"{name} title, but {asks}"
        return "beyond", f"{name} level; your titles reach {LEVEL_NAMES.get(me.level)}" + (f"; {asks}" if asks else "")
    if lvl > me.effective:
        return "borderline", f"{name} level; you are judged {LEVEL_NAMES.get(me.effective)}"
    if bar is not None and bar > have:
        return "borderline", asks
    gap = domain_gap(asked, me.vocab)
    if gap:
        return "borderline", f"asks {gap[0]}+ years of {gap[1]}, which your library does not name"
    return "ok", ""


def g9_seniority(p, v, fam, rules, unread=False):
    """Clearly beyond the user: discarded (seniority, a filter reason judged
    again when the library or settings change). Borderline: kept, marked so
    the queue lists it after the rest. Unread postings (no text) are never
    discarded here."""
    verdict, why = seniority(p.title, "" if unread else p.text, fam, rules.experience)
    if verdict == "beyond" and not unread:
        return v.stop("G9", "discard", "seniority", why)
    if verdict != "ok":
        v.info["seniority"] = "borderline"
        v.gate_line("G9", True, f"seniority borderline: {why}")
    return None


# ---------- work type and pay (the user's own limits) ----------
#
# All three read [gates] settings of search_profile.toml, so they are the
# user's and never a preset's: employment_types (a temporary job is out when
# "temporary" is not listed), shift_work ("last": shift jobs are queued after
# everything else) and pay_floor (a yearly amount; 0 is off).

TEMP = re.compile(r"(?i)\b(?:temp|temporary)\b")
TEMP_KEEP = re.compile(r"(?i)\b(?:temp(?:orary)?[ -]to[ -](?:hire|perm\w*)|contract[ -]to[ -](?:hire|perm\w*)|"
                       r"full[ -]?time|permanent)\b")
TEMP_TEXT = re.compile(r"(?i)\b(?:this is an?|is an?|for an?|as an?)\s+(?:\d{1,2}[ -](?:month|week)s?\s+)?"
                       r"(?:temporary|temp)\s+(?:position|role|assignment|job|opportunity|appointment|contract)\b|"
                       r"\b(?:employment|job|position|worker|assignment|employee)\s+type\s*:?\s*(?:temporary|temp)\b")
SHIFT_TITLE = re.compile(r"(?i)\b(?:night|nights|swing|graveyard|overnight|evening|evenings|weekend|weekends|pm|"
                         r"2nd|second|3rd|third|rotating)\s*shifts?\b|\b(?:10|12)[ -]?h(?:ou)?rs?\b|"
                         r"\b(?:nights|evenings|weekends|graveyard|overnight|swing)\b")
SHIFT_TEXT = re.compile(r"(?i)\b(?:night|swing|graveyard|overnight|weekend|rotating|2nd|second|3rd|third|"
                        r"12[ -]?hour)[ -]shifts?\b")
SHIFT_MAYBE = re.compile(r"(?i)\b(?:may|might|occasional\w*|as needed|if needed|when needed|flexib\w+|willing\w*|"
                         r"ability to|able to|some|including|or)\b")


def temp_marked(title, text=""):
    """The words that mark a temporary job, or '': 'temp' or 'temporary' in
    the title, or a plain statement in the text ('This is a temporary
    position', 'Employment type: Temporary'). Never when the title or the
    text also says temp-to-hire, contract-to-hire, full-time or permanent
    ('QA Associate - Temporary and Full Time' is both)."""
    title, text = str(title or ""), str(text or "")[:SHIFT_TEXT_MAX]
    m = TEMP.search(title)
    if m:
        return "" if TEMP_KEEP.search(title) else m.group(0)
    m = TEMP_TEXT.search(text)
    if m and not TEMP_KEEP.search(title) and not re.search(r"(?i)\b(?:temp(?:orary)?|contract)[ -]to[ -](?:hire|perm)",
                                                          text):
        return " ".join(m.group(0).split())
    return ""


def shift_marked(title, text=""):
    """The words that mark shift work, or '': night, swing, graveyard,
    evening, weekend, second or third shift, or 10- or 12-hour days, in the
    title ('Night Shift', '- Evenings', '12-HR'), or stated as the job's own
    schedule in the text ('This is a swing shift position'). 'May require
    occasional weekend shifts' is not a shift job; a day shift is not marked."""
    m = SHIFT_TITLE.search(str(title or ""))
    if m:
        return " ".join(m.group(0).split())
    text = str(text or "")[:SHIFT_TEXT_MAX]
    for i, m in enumerate(SHIFT_TEXT.finditer(text)):
        if i >= 20:
            break
        if not SHIFT_MAYBE.search(text[max(0, m.start() - 80):m.start()]):
            return " ".join(m.group(0).split())
    return ""


def g9_work_type(p, v, rules, unread=False):
    """A temporary job the user does not take is discarded
    (employment:temporary, a filter reason); shift work is marked low_rank so
    the queue lists it after everything else. Unread postings are judged by
    their title only."""
    text = "" if unread else (p.text or "")
    if not getattr(rules, "temp_ok", True):
        why = temp_marked(p.title, text)
        if why:
            return v.stop("G9", "discard", "employment:temporary", quote(why))
    if getattr(rules, "shift_last", False):
        why = shift_marked(p.title, text)
        if why:
            low = v.info.setdefault("low_rank", [])
            if "shift" not in low:
                low.append("shift")
            v.gate_line("G9", True, f"shift work {quote(why)}: listed after the rest")
    return None


MONEY = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,7}(?:\.\d{1,2})?)\s?([kK]\b)?"
PAY_AMOUNT = re.compile(r"\$\s?" + MONEY + r"(?:\s*(?:-|\u2013|\u2014|to|and|through)\s*\$?\s?" + MONEY + r")?")
# A pay word right beside the amount (PAY_STRONG, within 90 characters before or 40 after) makes it pay; a
# weaker one further off (PAY_NEAR, within 220 before) only for a range ('... base salary range for the <long
# title> role in the United States is: $155,600 - $233,400').
PAY_STRONG = re.compile(r"(?i)\b(?:salary|salaries|compensation|wage|wages|hourly|pay range|pay rate|base pay|"
                        r"pay scale|annually|annual|per year|a year|per hour|an hour|ote)\b|/(?:yr|year|hr|hour)\b")
PAY_NEAR = re.compile(r"(?i)\b(?:salary|salaries|compensation|pay|paid|wage|wages|hourly|rate|base|earnings|"
                      r"annually|annual|per year|a year|per hour|an hour|ote)\b|/(?:yr|year|hr|hour)\b")
PAY_NOT = re.compile(r"(?i)\b(?:bonus|stipend|reimburs\w*|referral|sign[ -]?on|signing|relocation|tuition|equity|"
                     r"grant\w*|award\w*|funding|raised|revenue|budget|401|savings|fee|fees|cost|costs|valued|quota|"
                     r"allowance|insurance|coverage|contribution\w*|match\w*|discount\w*|credit\w*|"
                     r"scholarship\w*|loan\w*|deductible)\b[^.$]{0,30}$")
PAY_BIG = re.compile(r"(?i)^\s?(?:m\b|mm\b|b\b|bn\b|million|billion)")
PAY_MONTHLY = re.compile(r"(?i)^\s*(?:usd\s*)?(?:/|per\s+|a\s+)\s*(?:mo|month)\b|\b(?:monthly|per month)\b[^.$]{0,60}$")


def yearly(value, unit):
    """A stated amount as a yearly one (HOURS_PER_YEAR hours, 12 months),
    or None when it is no plausible pay: under $10 or over $500 an hour,
    under $15,000 or over $3 million a year."""
    if unit == "hour":
        return value * HOURS_PER_YEAR if 10 <= value <= 500 else None
    if unit == "month":
        value *= 12
    return value if 15_000 <= value <= 3_000_000 else None


def pay_in_text(text):
    """[(low, high)] yearly amounts for each pay range or single amount the
    text states: a dollar amount with a pay word near it ('salary range',
    'hourly rate', 'compensation', '/hr'), not a bonus, stipend, funding or
    revenue figure. An amount under 1,000 with no 'k' is an hourly rate."""
    text = " ".join(str(text or "")[:PAY_TEXT_MAX].split())
    out, last = [], None
    for i, m in enumerate(PAY_AMOUNT.finditer(text)):
        if i >= PAY_HITS_MAX:
            break
        before, after = text[max(0, m.start() - 220):m.start()], text[m.end():m.end() + 60]
        if PAY_BIG.match(after) or PAY_NOT.search(before):
            continue
        # a pay word near it, or another range right after one that had it ('... for a Scientist 3 and
        # $124,800 to $231,800 for a Scientist 4')
        if not (PAY_STRONG.search(before[-90:]) or PAY_STRONG.search(after[:40]) or
                (m.group(3) and PAY_NEAR.search(before)) or (last is not None and m.start() - last <= 300)):
            continue
        nums = []
        for raw, k in ((m.group(1), m.group(2)), (m.group(3), m.group(4))):
            if raw:
                nums.append((float(raw.replace(",", "")), bool(k)))
        k_any = any(k for _, k in nums)
        vals = [n * 1000 if (k or (k_any and n < 1000)) else n for n, k in nums]
        if max(vals) < 1000:  # '$45 - $50', with or without 'per hour'
            unit = "hour"
        elif max(vals) < 15_000 and (PAY_MONTHLY.search(after) or PAY_MONTHLY.search(before)):
            unit = "month"
        else:  # '$77,000 - $85,000 USD per hour or annually' is a yearly amount
            unit = "year"
        vals = [y for y in (yearly(x, unit) for x in vals) if y is not None]
        if vals:
            out.append((min(vals), max(vals)))
            last = m.end()
    return out


def stated_pay(p):
    """(low, high, basis) the posting's stated pay as yearly US dollars, or
    None when it states none: the reader's salary field (basis 'field'),
    else the ranges in its text (basis 'text'; the lowest low and the
    highest high of all of them, so a posting with a range per level or per
    place is judged by its best one)."""
    s = getattr(p, "salary", None)
    if isinstance(s, dict) and str(s.get("currency") or "USD").strip().upper() in ("USD", "US$", "$"):
        period = str(s.get("period") or "").lower()
        vals = [float(x) for x in (s.get("min"), s.get("max"))
                if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
        if vals:
            unit = "hour" if "hour" in period else "month" if "month" in period else \
                "year" if ("year" in period or "annual" in period or max(vals) >= 1000) else "hour"
            vals = [y for y in (yearly(x, unit) for x in vals) if y is not None]
            if vals:
                return min(vals), max(vals), "field"
    ranges = pay_in_text(getattr(p, "text", "") or "")
    if ranges:
        return min(lo for lo, _ in ranges), max(hi for _, hi in ranges), "text"
    return None


def dollars(n):
    return f"${n:,.0f}"


def g9_pay(p, v, rules, unread=False):
    """The pay floor: [gates] pay_floor, or for a job in a relocation region
    that region's own pay_floor when it has one. A stated range that tops
    out below the floor is discarded (pay, a filter reason: judged again
    when the floor changes). No stated pay: kept, except in a relocation
    region whose unstated_pay is "drop" (moving needs a stated range)."""
    region = next((r for r in getattr(rules, "regions", []) if r["label"] == v.info.get("region")), None)
    floor = (region or {}).get("pay_floor") or getattr(rules, "pay_floor", 0)
    if not floor:
        v.gate_line("G9", True, "no pay floor")
        return None
    pay = stated_pay(p)
    where = f" for {region['label']}" if region else ""
    if pay is None:
        if region and region["unstated_pay"] == "drop" and not unread:
            return v.stop("G9", "discard", "pay", f"no stated pay; the floor{where} is {dollars(floor)}")
        v.gate_line("G9", True, "no stated pay")
        return None
    v.info["pay"] = [round(pay[0]), round(pay[1])]
    if pay[1] < floor:
        return v.stop("G9", "discard", "pay", f"tops out at {dollars(pay[1])} a year ({pay[2]}); "
                                              f"the floor{where} is {dollars(floor)}")
    v.gate_line("G9", True, f"pay up to {dollars(pay[1])} ({pay[2]})")
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
    """The apply route, judged on the detail record (G1 saw the list record):
    a URL that is not http(s), names a host netfetch refuses or is a job
    board is discarded (D1, SEC-11). An http apply link from a board or
    aggregator becomes https before it can reach the OPEN line; a pasted
    link keeps the form the user gave."""
    url = p.apply_url or p.url
    if not url:
        return v.stop("G12", "discard", "apply_route", "no apply URL")
    why = url_refusal(url)
    if why in ("not-web", "bad-url", "non-public"):
        return v.stop("G12", "discard", "apply_route", f"{why} apply link")
    if why:
        return v.stop("G12", "discard", "apply_route", f"job board {why}")
    host = netfetch.host_of(url)
    note = ""
    if urlparse(url).scheme.lower() == "http" and p.lane != "pasted":
        p.apply_url, note = "https" + url[len("http"):], " (http link opened as https)"
    if any(host == h or host.endswith("." + h) for h in CHAT_APPLY_HOSTS):
        return v.stop("G12", "diy", "chat_apply", host)
    v.gate_line("G12", True, host + note)
    return None


# ---------- fit (3.12) ----------

def section_after(text, m, cap):
    """The text after heading match m up to the next heading (when that is
    more than 40 characters on), else at most cap characters. Searches from
    an offset, never on a copy of the rest, so many headings stay linear."""
    nxt = ANY_HEADING.search(text, m.end() + 1)
    end = nxt.start() if nxt and nxt.start() - m.end() > 40 else m.end() + cap
    return text[m.end():end]


def requirement_text(text):
    """Every requirements-like section (Requirements, Basic and Preferred
    Qualifications, What you bring, ...) joined; '' when there is none. Reads
    the first REQ_TEXT_MAX characters and at most REQ_SECTIONS_MAX headings."""
    text = (text or "")[:REQ_TEXT_MAX]
    out = []
    for i, m in enumerate(REQ_HEADING.finditer(text)):
        if i >= REQ_SECTIONS_MAX:
            break
        out.append(section_after(text, m, REQ_SECTION_MAX))
    return "\n".join(out)


def requirement_section(text):
    text = (text or "")[:REQ_TEXT_MAX]
    m = REQ_HEADING.search(text)
    if not m:
        return text
    return section_after(text, m, len(text))


def general_forms(t):
    """The general forms of a technique: the last word of its name or an
    alias when that word is a method on its own ('real-time PCR' -> 'PCR',
    '16S rRNA sequencing' -> 'sequencing', 'confocal microscopy' ->
    'microscopy'). 'culture', 'testing' and the like are not: a posting's
    'company culture' says nothing about anaerobic culture."""
    out = []
    for form in t.forms:
        parts = str(form).split()
        last = parts[-1] if len(parts) > 1 else ""
        if last.lower() in GENERAL_METHODS and last.lower() not in (x.lower() for x in out):
            out.append(last)
    return out


def overlap(text, fam, techniques, library_text=""):
    """How many of the user's terms a posting names (the TERMS overlap, D6):
    each techniques.md technique in any of its forms (hands-on 1, familiar
    0.5), each family term (1 when the achievement library names it too,
    else 0.5), and then a technique's general form when the posting names
    only that (half: a posting's 'PCR' for the user's 'real-time PCR'). A
    posting that names many of them comes first in the day's list. Ordering
    only: this never decides what a resume may claim."""
    import checks  # same folder (term_regex)
    text = (text or "")[:FIT_TEXT_MAX]
    n, counted = 0.0, set()
    found = set()
    for t in techniques:
        if checks.term_found(text, t.name, t.aliases):
            n += 1.0 if t.level == "hands-on" else 0.5
            counted |= {f.lower() for f in t.forms}
            found.add(id(t))
    lib = library_text or ""
    for term in (fam or {}).get("terms") or []:
        low = str(term).lower()
        if low in counted or not str(term).strip():
            continue
        if checks.term_found(text, term):
            n += 1.0 if checks.term_found(lib, term) else 0.5
            counted.add(low)
    for t in techniques:
        if id(t) in found:
            continue
        for g in general_forms(t):
            if g.lower() not in counted and checks.term_found(text, g):
                n += 0.5 if t.level == "hands-on" else 0.25
                counted.add(g.lower())
                break
    return n


def coverage(text, fam, techniques, library_text=""):
    """The overlap as a share: FIT_TERMS of the user's terms is 1.0."""
    return round(min(1.0, overlap(text, fam, techniques, library_text) / FIT_TERMS), 3)


def level_match(title, fam):
    ok = [w.lower() for w in (fam or {}).get("seniority_ok") or []]
    have = [w for w in words(title).split() if w in SENIORITY]
    if not ok or not have:
        return 1.0
    return 1.0 if any(w in ok for w in have) else 0.0


def fit_of(text, title, fam, title_match, techniques, library_text=""):
    cov = coverage(text, fam, techniques, library_text)
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

    def __init__(self, root, today=None, fetcher=None, widen=None, emit=None):
        self.root = Path(root)
        self.folder = jobfs.Folder(root)
        self.today = today or jobfs.today(root)
        self.fetcher = fetcher
        self.emit = emit or print
        self.rules = Rules(root, self.today, widen)
        self.seen = Seen(root, self.today, buffered=True)
        self.discard_rows = []
        self.polls = self._load_polls()
        self.boards_today = set()
        self.cache = {}
        self.counts = {"seen": 0, "passed": 0, "discarded": 0, "parked": 0, "diy": 0, "skipped": 0}
        self.pooled, self.needs_you, self.diy, self.errors = [], [], [], []
        self._techniques = None
        self._library = None

    # state files
    def _load_polls(self):
        """state/board_polls.json: {"polls": {board: [dates]}, "matched": {board: date}}."""
        data = jobfs.read_json_or(self.folder.state / "board_polls.json", jobfs.MAX_STATE, {})  # R3: the safe loader
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

    def library_text(self):
        if self._library is None:
            try:
                self._library = self.folder.library.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._library = ""
        return self._library

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
        elif v.gate == "G3" and v.info.get("second_ring"):
            self.ring_only(p, v)
        self.cache[p.job_key] = v
        if not v.ok:
            self.finish(p, v)
        return v

    def ring_only(self, p, v):
        """A G3 discard for a second_ring place becomes location:second_ring
        when the title (G4) and age (G5) would pass: set aside for its place
        alone, so the weekly summary can count it (D8)."""
        probe = Verdict(info={"flags": list(v.info.get("flags") or [])})
        seen_rec = self.seen.get(p.job_key)
        if g4_title(p, probe, self.rules) is None and g5_age(
                p, probe, seen_rec, self.polls_before(p, (seen_rec or {}).get("first_seen")), self.today) is None:
            v.reason = "location:second_ring"

    def keep(self, p):
        """boards.poll(keep=...): True when a detail call is worth making. A
        failure judging one posting skips it with one ERROR line (D2); the
        next run judges it again."""
        try:
            return self.list_gates(p).ok
        except Exception as e:  # noqa: BLE001  one posting never stops the pass
            self.error(getattr(p, "job_key", ""), e)
            return False

    def error(self, key, e):
        self.errors.append(f"{key}: {type(e).__name__}")
        self.counts["skipped"] += 1
        self.emit(f"ERROR gate {key or '(no job key)'}: {type(e).__name__}")

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
        if getattr(reading, "scan_incomplete", False):  # hidden.py ran out of budget and kept the text
            v.lines.append("WARN HIDDEN-SCAN incomplete")
            self.emit(f"WARN hidden-scan incomplete {p.job_key}: the page had more style rules than one read "
                      "checks; its text was kept")
        v.info["posting_status"] = "browser_read" if (status == "browser_read" or
                                                      (v.info.get("lead_only") and not p.text)) else "read"
        unread = v.info["posting_status"] == "browser_read"
        if not p.closed and not (p.title or "").strip():
            # L4-06: a list record with no title whose detail gave none (a closed Phenom job's page
            # answers 404) is never pooled, so it can never be queued as '<company> - Role'. Nothing
            # is recorded: the next run reads its detail again.
            v.stop("G4", "skip", "no_title", "the detail gave no title")
            self.emit(f"SKIP {p.job_key} no title: the detail read gave none; judged again next run")
            self.finish(p, v, reading)
            return v
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
                 or (None if unread else g9_words(p, v, fam))
                 or (None if unread or not (fam or {}).get("min_terms") else
                     g9_terms(p, v, fam, self.techniques(), self.library_text()))
                 or g9_seniority(p, v, fam, self.rules, unread)
                 or g9_work_type(p, v, self.rules, unread) or g9_pay(p, v, self.rules, unread)
                 or g10_remote(p, v) or g11_ai(v, ai_text)
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
                if v.info.get("second_ring"):
                    self.ring_only(p, v)
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
        fit, cov, lvl = fit_of(p.text, p.title, fam, v.info.get("title_match", 0.6), self.techniques(),
                               self.library_text())
        d = p.to_dict()
        d["posted"] = posted_value(p.posted) or ""  # R2: a YYYY-MM-DD date or "undated", never other text
        d.update(family=fam.get("id", ""), mode=str(fam.get("mode", "industry")),
                 family_priority=int(fam.get("priority", 2) or 2),
                 title_match=v.info.get("title_match", 0.6), fit=fit, coverage=cov, level_match=lvl,
                 flags=list(dict.fromkeys(v.info.get("flags") or [])), band=v.info.get("band", ""),
                 age_days=v.info.get("age_days"), date_basis=v.info.get("date_basis", ""),
                 repost_of=v.info.get("repost_of"), segment=self.rules.segment(p.company_norm),
                 posting_status=v.info.get("posting_status", "read"), markdown=md, ai_text=ai_text,
                 hidden=hidden, pooled_on=self.today.isoformat(), folder="",
                 seniority=v.info.get("seniority", ""))
        low = list(v.info.get("low_rank") or [])
        if v.info.get("region"):  # a place the user would move to: after the home jobs
            d["relocate"] = v.info["region"]
            low.append("relocate")
        if low:  # the queue lists these after everything else (jobqueue.low_rank)
            d["low_rank"] = low
        if v.info.get("pay"):  # the stated pay as yearly dollars [low, high]
            d["pay_stated"] = v.info["pay"]
        return d

    def save_trail(self, p, v):
        month = self.folder.state / "trails" / f"{self.today:%Y-%m}.jsonl"
        jobfs.append_jsonl(month, {"job_key": p.job_key, "date": self.today.isoformat(), "lines": v.lines})

    def write_discard(self, p, gate, reason, profile_hash):
        """Kept until save(): discards.csv may be open in Excel, and that must
        not stop the pass (write_discards)."""
        self.discard_rows.append([self.today.isoformat(), p.lane, p.company, p.title, p.location,
                                  host_text(p.url), p.job_key, gate, reason, profile_hash])

    def process(self, readings):
        """Gate detailed readings (Reading, Posting or saved dicts). Returns the
        pool items added this call. A reading that fails to gate is skipped
        with one ERROR line naming it (D2): the pass goes on, nothing is
        recorded for it, and the next run judges it again."""
        start = len(self.pooled)

        def lane(r):
            try:
                return as_reading(r)[0].lane != "watch"
            except Exception:  # noqa: BLE001  judged (and reported) below
                return True
        for r in sorted(readings, key=lane):  # board records win dedupe
            try:
                self.detail_gates(r)
            except Exception as e:  # noqa: BLE001  one posting never stops the pass
                key = (r.get("job_key") if isinstance(r, dict) else
                       getattr(getattr(r, "posting", r), "job_key", "")) or ""
                self.error(key, e)
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
        items = {it["job_key"]: it for it in pool.get("items", []) if str(it.get("title") or "").strip()}
        blank = sum(1 for it in pool.get("items", []) if not str(it.get("title") or "").strip())
        if blank:  # L4-06: pooled before the no-title rule; never queued as '<company> - Role'
            self.emit(f"SKIP {blank} pool item{'s' if blank != 1 else ''} with no title dropped from the pool")
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
    for it in [x for k in ("items", "needs_you") if isinstance(data[k], list) for x in data[k]]:
        # R2: keys an earlier version stored, in today's form
        if isinstance(it, dict) and isinstance(it.get("job_key"), str):
            it["job_key"] = jobkey.safe_key(it["job_key"])
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
    run.process(readings)  # one bad record is an ERROR line, never a stopped pass
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
    import argfile  # a job key with other characters arrives in a JSON file, never inline
    args = argfile.parse(build_parser(), argv)
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
