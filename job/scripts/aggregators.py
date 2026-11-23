"""Aggregator lanes for the runner (spec 3.4, 3.14, 8.4).

Usage:
    python aggregators.py run --root R [--offline DIR] [--keys PATH]
                              [--out-postings F.jsonl] [--out-leads F.jsonl]

Sources, each behind config/settings.toml [sources]:
- usajobs (direct postings; on once keyed): GET data.usajobs.gov/api/search
  with the three headers Host, User-Agent (the user's key email) and
  Authorization-Key; DatePosted=3, ResultsPerPage=500.
- adzuna (leads only; on once keyed): what_or per family, where, distance,
  max_days_old=3, sort_by=date. Results become lead lines (company, title,
  location, seen) with no URL fields; redirect_url is never requested.
- workable_search (off): jobs.workable.com/api/v1/jobs, one query per family.
- muse (off): leads only; items older than 14 days are dropped.
- science_careers, nature_careers (off): Madgex RSS, one query per family; a
  feed item holds only a snippet, so leads only; kept when it names a home
  place or state, or says remote and remote counts.
- himalayas, remoteok, jobicy, wwr (off): remote-job feeds, direct postings
  that carry the source's credit (posting.SOURCE_CREDITS); they run only when
  remote counts ([home] remote = "us"). wwr stays off until its terms are read.
- hn_hiring (off): leads only, from the latest "Ask HN: Who is hiring?"
  thread, read at most once a day (state/hn_hiring.json).

Keys live only in %LOCALAPPDATA%\\JobRunner\\keys.toml (--keys overrides, for
tests) and are never printed:
    [usajobs]  email = "...", key = "..."
    [adzuna]   app_id = "...", app_key = "..."
    [muse]     api_key = "..."   (optional)

Output lines: SOURCE <name> OK <n> postings|leads requests=<k>, SOURCE <name>
OFF, SOURCE <name> NOKEY, SOURCE <name> ERROR <why>, SOURCE <name> DENY <reason>.
Exit codes: 0 ok, 1 bad input. Stdlib only.
"""
import argparse
import hashlib
import html
import json
import re
import sys
import tomllib
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import netfetch  # noqa: E402
import boards  # noqa: E402
import posting  # noqa: E402
from netfetch import Denied, FetchError  # noqa: E402

USAJOBS_URL = "https://data.usajobs.gov/api/search"
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/us/search/1"
WORKABLE_URL = "https://jobs.workable.com/api/v1/jobs"
MUSE_URL = "https://www.themuse.com/api/public/jobs"
MADGEX_URLS = {"science_careers": "https://jobs.sciencecareers.org/jobsrss/",
               "nature_careers": "https://www.nature.com/naturecareers/jobsrss/"}
HIMALAYAS_URL = "https://himalayas.app/jobs/api/search"
REMOTEOK_URL = "https://remoteok.com/api"
JOBICY_URL = "https://jobicy.com/api/v2/remote-jobs"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/"
WWR_URLS = ("https://weworkremotely.com/categories/remote-product-jobs.rss",
            "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss")
MAX_QUERIES = 4           # per source per day
MUSE_MAX_AGE = 14         # days (aggregators A2)
MADGEX_MAX_AGE = 30       # days: a Madgex posting runs about a month
HN_MAX_AGE = 14           # days: a comment older than this is dropped
HN_STORY_AGE = 35         # days: a cached thread id still stands in when the search fails
HN_MAX_TITLES = 3         # leads from one comment
HIMALAYAS_PAGES = 2       # per query
JOBICY_COUNT = 50
ADZUNA_PAGE = 50
LEAD_LANES = ("adzuna", "muse", "science_careers", "nature_careers", "hn_hiring")

# Lead validation, as for alert cards (2.5): fail closed.
LEAD_LIMITS = {"company": 80, "title": 120, "location": 80}
URLISH = re.compile(r"https?:|www\.|@|\b[a-z0-9-]+\.(?:com|net|org|io|co|gov|edu|ly|me|jobs)\b", re.I)
INSTRUCTION = re.compile(r"\b(ignore|you are|assistant|system)\b", re.I)


class Lane:
    """One source's outcome."""

    def __init__(self, name):
        self.name, self.status, self.why = name, "OK", ""
        self.postings, self.leads, self.requests, self.skipped = [], [], 0, 0

    def line(self):
        if self.status == "OK":
            kind = "leads" if self.name in LEAD_LANES else "postings"
            n = len(self.leads) if kind == "leads" else len(self.postings)
            odd = f" skipped={self.skipped}" if self.skipped else ""
            return f"SOURCE {self.name} OK {n} {kind} requests={self.requests}{odd}"
        return f"SOURCE {self.name} {self.status} {self.why}".rstrip()


# ---------- keys and queries ----------

def keys_path():
    return jobfs.runner_home() / "keys.toml"


def load_keys(path=None):
    """keys.toml as a dict ({} when missing or unreadable). Never printed."""
    try:
        return tomllib.loads(Path(path or keys_path()).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def active_families(profile):
    return [f for f in profile.get("family") or [] if f.get("status", "active") == "active"]


def family_words(fam, limit=6):
    """A family's search words: title_any (gate syntax removed), else the
    words of its core titles."""
    words = boards.search_terms(fam.get("title_any"))
    if not words:
        words = [w for t in fam.get("core_titles") or [] for w in str(t).split()]
    return list(dict.fromkeys(str(w).strip() for w in words if str(w).strip()))[:limit]


def home_city(profile):
    """The city part of [home] label ('Springfield, IL' -> 'Springfield')."""
    return str((profile.get("home") or {}).get("label") or "").split(",")[0].strip()


def radius(profile):
    return int((profile.get("home") or {}).get("radius_miles") or 40)


# ---------- leads ----------

def clean_field(value, limit):
    """A lead field, or None when it must drop the card (2.5 validation)."""
    v = posting.one_line(value)
    if not v or len(v) > limit or URLISH.search(v) or INSTRUCTION.search(v) or "linkedin.com" in v.lower():
        return None
    return v


def make_lead(source, company, title, location, seen, salt=""):
    """A lead line (spec 2.5) with no URL fields, or None when a field fails
    validation. salt is the local salt the lead store uses (WP4's leads.py)."""
    c, t = clean_field(company, LEAD_LIMITS["company"]), clean_field(title, LEAD_LIMITS["title"])
    loc = clean_field(location, LEAD_LIMITS["location"]) if location else ""
    if not c or not t or loc is None:
        return None
    city = loc.split(",")[0].strip()
    lead_id = hashlib.sha256(f"{salt}|{source}|{c}|{t}|{city}".encode("utf-8")).hexdigest()[:16]
    return {"v": 1, "lead_id": lead_id, "source": source, "company": c, "title": t, "location": loc,
            "seen": seen}


# ---------- USAJOBS ----------

def usajobs_queries(profile):
    """One search per usajobs-mode family (else per active family): Keyword is
    its first core title, around [home] label within radius_miles."""
    fams = [f for f in active_families(profile) if f.get("mode") == "usajobs"] or active_families(profile)
    out = []
    for fam in fams:
        kw = (fam.get("core_titles") or [fam.get("label") or ""])[0]
        params = {"Keyword": kw} if kw else {}
        label = str((profile.get("home") or {}).get("label") or "")
        if label:
            params.update(LocationName=label, Radius=radius(profile))
        params.update(DatePosted=3, ResultsPerPage=500)
        if params not in out:
            out.append(params)
    return out[:MAX_QUERIES] or [{"DatePosted": 3, "ResultsPerPage": 500}]


def usajobs_url(params):
    return f"{USAJOBS_URL}?{urlencode(params)}"


def run_usajobs(fetcher, keys, profile, first_seen):
    lane = Lane("usajobs")
    k = keys.get("usajobs") or {}
    if not k.get("email") or not k.get("key"):
        lane.status, lane.why = "NOKEY", "add the email and key at the installer prompt"
        return lane
    headers = {"Host": "data.usajobs.gov", "User-Agent": str(k["email"]), "Authorization-Key": str(k["key"]),
               "Accept": "application/json"}
    seen = set()
    for params in usajobs_queries(profile):
        lane.requests += 1
        r = fetcher.request("GET", usajobs_url(params), None, headers).check()
        d = r.json()
        result = d.get("SearchResult") if isinstance(d, dict) else None
        items = result.get("SearchResultItems") if isinstance(result, dict) else None
        for reading in each(lane, items, lambda item: usajobs_reading(item, first_seen)):
            if reading.posting.job_key not in seen:
                seen.add(reading.posting.job_key)
                lane.postings.append(reading)
    return lane


def each(lane, rows, fn):
    """fn(row) for each object in rows, skipping None results. A row that is
    not an object, or one fn fails on, is skipped and counted (lane.skipped):
    one odd item never costs the lane."""
    out = []
    if isinstance(rows, list):
        lane.skipped += sum(1 for row in rows if not isinstance(row, dict))
    for row in boards.dicts(rows):
        try:
            got = fn(row)
        except Exception:  # noqa: BLE001  one odd item is skipped and counted
            lane.skipped += 1
            continue
        if got is not None:
            out.append(got)
    return out


def usajobs_reading(item, first_seen):
    """posting.Reading for one search item (direct posting, Tier A later)."""
    report = posting.Report()
    try:
        p = posting.parse_usajobs_item(item, report)
    except FetchError:
        return None
    m = item.get("MatchedObjectDescriptor", item)
    m = m if isinstance(m, dict) else item
    url = posting.web_url(m.get("PositionURI")) or boards.web_link(p.get("apply_url"))
    if not url or not boards.web_link(url):
        return None
    posting.clean_fields(p, report)
    rec = posting.to_record(p, url, lane="usajobs", first_seen=first_seen, ats="usajobs")
    return posting.Reading(rec, "", p["source"], posting.ai_lines(p, report), list(report.hidden), report.stripped)


# ---------- Adzuna (leads only) ----------

def adzuna_queries(profile, keys):
    """One query per active family (what_or), around the home city."""
    k = keys.get("adzuna") or {}
    out = []
    for fam in active_families(profile)[:MAX_QUERIES]:
        words = family_words(fam)
        if not words:
            continue
        params = {"app_id": k.get("app_id", ""), "app_key": k.get("app_key", ""), "what_or": " ".join(words)}
        if home_city(profile):
            params["where"] = home_city(profile)
        params.update(distance=radius(profile), max_days_old=3, sort_by="date", results_per_page=ADZUNA_PAGE)
        out.append(params)
    return out


def run_adzuna(fetcher, keys, profile, today, salt=""):
    """Leads only. redirect_url is never requested: its destination is
    untested and may be a job board (aggregators source Adzuna correction)."""
    lane = Lane("adzuna")
    k = keys.get("adzuna") or {}
    if not k.get("app_id") or not k.get("app_key"):
        lane.status, lane.why = "NOKEY", "add app_id and app_key at the installer prompt"
        return lane
    seen = set()
    for params in adzuna_queries(profile, keys):
        lane.requests += 1
        r = fetcher.get(f"{ADZUNA_URL}?{urlencode(params)}", accept="application/json").check()
        d = r.json()
        for lead in each(lane, d.get("results") if isinstance(d, dict) else None,
                         lambda job: make_lead("adzuna", obj(job.get("company")).get("display_name"),
                                               job.get("title"), obj(job.get("location")).get("display_name"),
                                               today, salt)):
            if lead["lead_id"] not in seen:
                seen.add(lead["lead_id"])
                lane.leads.append(lead)
    return lane


def obj(v):
    """v when it is a dict, else {} (a field a feed sent as a string or a list)."""
    return v if isinstance(v, dict) else {}


# ---------- Workable cross-search (off) and The Muse (off) ----------

def run_workable(fetcher, profile, first_seen):
    lane = Lane("workable_search")
    seen = set()
    for fam in active_families(profile)[:MAX_QUERIES]:
        words = family_words(fam, 3)
        if not words:
            continue
        params = {"query": " ".join(words)}
        if (profile.get("home") or {}).get("label"):
            params["location"] = profile["home"]["label"]
        lane.requests += 1
        r = fetcher.get(f"{WORKABLE_URL}?{urlencode(params)}", accept="application/json").check()
        d = r.json()
        for reading in each(lane, d.get("jobs") if isinstance(d, dict) else None,
                            lambda j: workable_reading(j, first_seen)):
            if reading.posting.job_key not in seen:
                seen.add(reading.posting.job_key)
                lane.postings.append(reading)
    return lane


def workable_reading(j, first_seen):
    url = posting.web_url(j.get("url"))
    if not url or not j.get("title"):
        return None
    report = posting.Report()
    p = posting.new_posting("workable-search")
    loc = j.get("location") or {}
    p["company"] = (j.get("company") or {}).get("title", "")
    p["role"] = j.get("title", "")
    p["location"] = posting.join_nonempty([loc.get("city"), loc.get("subregion"), loc.get("countryName")], ", ")
    parts = [j.get("description"), j.get("requirementsSection"), j.get("benefitsSection")]
    p["description"] = posting.html_to_text("\n".join(x for x in parts if isinstance(x, str)), report)
    day = posting.short_date(j.get("created") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        p["posted"], p["posted_field"] = day, "created"
    p["apply_url"] = url
    posting.clean_fields(p, report)
    rec = posting.to_record(p, url, lane="workable_search", first_seen=first_seen, ats="workable")
    return posting.Reading(rec, "", p["source"], posting.ai_lines(p, report), list(report.hidden), report.stripped)


def run_muse(fetcher, keys, profile, today, salt=""):
    """Leads only; stale items (over 14 days) are dropped (aggregators A2)."""
    lane = Lane("muse")
    params = {"page": 0, "descending": "true"}
    if (profile.get("home") or {}).get("label"):
        params["location"] = profile["home"]["label"]
    if (keys.get("muse") or {}).get("api_key"):
        params["api_key"] = keys["muse"]["api_key"]
    lane.requests += 1
    r = fetcher.get(f"{MUSE_URL}?{urlencode(params)}", accept="application/json").check()
    cutoff = (date.fromisoformat(today) - timedelta(days=MUSE_MAX_AGE)).isoformat()

    def one(j):
        day = posting.short_date(j.get("publication_date") or "")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "") or day < cutoff:
            return None
        locs = [x.get("name", "") for x in boards.dicts(j.get("locations"))]
        return make_lead("muse", obj(j.get("company")).get("name"), j.get("name"), locs[0] if locs else "",
                         today, salt)
    d = r.json()
    lane.leads += each(lane, d.get("results") if isinstance(d, dict) else None, one)
    return lane


# ---------- home and remote filters (the new lanes) ----------

REMOTE = re.compile(r"\bremote\b(?!\s+sensing)", re.I)
WORLDWIDE = re.compile(r"\b(?:worldwide|anywhere|global|north america|the americas)\b", re.I)


def core_queries(profile):
    """One search text per active family (at most 4): its first core title, else its label."""
    out = []
    for fam in active_families(profile)[:MAX_QUERIES]:
        q = posting.one_line((fam.get("core_titles") or [fam.get("label") or ""])[0])
        if q and q not in out:
            out.append(q)
    return out


def title_hit(profile, title):
    """True when title matches an active family's title terms (the gates' own G4 test)."""
    import gates  # same folder; gates imports boards only inside a function
    return any(gates.family_score(fam, title)[0] for fam in active_families(profile))


def place_in_home(part, area):
    """One place of a location text: a [home] place with no other state or country beside it ('Dublin,
    Ireland' and 'Newark, NJ' are out for a California home), else a [home] state itself ('Fresno,
    California'). The gates judge the posting again when it is read."""
    places, states, _ = area
    st, foreign, us = posting.region_of(part)
    if (foreign and not us and not st) or (st and states and not st & states):
        return False
    low = part.lower()
    if any(re.search(r"\b" + re.escape(str(pl).lower()) + r"\b", low) for pl in places):
        return True
    return bool(st and states and st <= states)


def home_hit(location, area, extra=""):
    """'place' when location names a home place or state, 'remote' when it or extra says remote and
    remote counts, else ''. With no [home] places or states set there is nothing to judge by: 'place'."""
    places, states, remote = area
    if not places and not states:
        return "place"
    if any(place_in_home(part, area) for part in posting.place_parts(location)):
        return "place"
    return "remote" if remote and REMOTE.search(f"{location} {extra}") else ""


def remote_us_ok(text):
    """True when a remote job's region text lets a US worker apply: blank, worldwide or anywhere, North
    America, or the US or a US state named. Europe only, a foreign country or LATAM is False."""
    t = posting.one_line(text)
    if not t or WORLDWIDE.search(t):
        return True
    return posting.us_eligible(t, "", "remote") is True


def day_of(v):
    """YYYY-MM-DD of an ISO date, an RFC 2822 date or epoch seconds, else ''."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            return datetime.fromtimestamp(v, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    v = posting.one_line(v)
    if re.match(r"\d{4}-\d{2}-\d{2}", v):
        return v[:10]
    try:
        return parsedate_to_datetime(v).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def rss_items(root):
    """The <item> children of an RSS feed as {tag: text} dicts."""
    return [{c.tag: c.text or "" for c in item} for item in root.iter("item")]


def lane_reading(p, url, lane, first_seen, report):
    """posting.Reading for a lane's posting dict; the lane name is the record's lane (its credit is
    posting.SOURCE_CREDITS)."""
    posting.clean_fields(p, report)
    rec = posting.to_record(p, url, lane=lane, first_seen=first_seen)
    return posting.Reading(rec, "", p["source"], posting.ai_lines(p, report), list(report.hidden), report.stripped)


def fill_salary(p, lo, hi, period, currency):
    """p's salary and pay text; a zero or missing amount is no pay stated."""
    p["salary"] = posting.salary(lo or None, hi or None, period, currency)
    if p["salary"]:
        p["pay"] = posting.fmt_pay(currency, p["salary"]["min"], p["salary"]["max"], period)


def posted(p, v, field_name):
    day = day_of(v)
    if day:
        p["posted"], p["posted_field"] = day, field_name


# ---------- Science Careers and Nature Careers (Madgex RSS, leads only) ----------

SENTENCE = re.compile(r"[!?:;]|\b(?:is|are|was|located|seeking|the)\b", re.I)
PLACE_HEAD = re.compile(r"([A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){0,2}),\s*([A-Z][A-Za-z]+(?: [A-Z][a-z]+)?)\b")


def madgex_place(desc):
    """The place a Madgex item ends with: its last line ('Rockville, Maryland', 'Boston, Massachusetts
    (US)'), cut to 'City, State' when a sentence runs on, else ''."""
    lines = [ln.strip() for ln in str(desc or "").splitlines() if ln.strip()]
    last = re.sub(r"\s*\((?:US|USA)\)\s*$", "", lines[-1]) if lines else ""
    if last and len(last) <= 60 and not SENTENCE.search(last):
        return last
    m = PLACE_HEAD.match(last)
    if m and (m.group(2) in posting.US_STATES or m.group(2).lower() in posting.US_STATE_NAMES):
        return f"{m.group(1)}, {m.group(2)}"
    return ""


def madgex_lead(name, item, area, today, salt):
    """A lead from one feed item ('Employer: Role', the snippet, its date), or None: no ': ' in the
    title, older than 30 days, not at home, or a field the lead validation refuses."""
    company, sep, role = posting.one_line(item.get("title")).partition(": ")
    day = day_of(item.get("pubDate"))
    if not sep or (day and day < (date.fromisoformat(today) - timedelta(days=MADGEX_MAX_AGE)).isoformat()):
        return None
    loc = madgex_place(posting.html_to_text(item.get("description") or "", posting.Report()))
    kind = home_hit(loc, area, role)
    if not kind:
        return None
    return make_lead(name, company, role, loc if kind == "place" else "Remote", today, salt)


def run_madgex(fetcher, name, profile, area, today, salt=""):
    """Leads only: an item is a snippet (employer, role, place, date), so it is a lead the resolver
    reads later. The feed ignores its location parameter, so only items at home or remote are kept."""
    lane = Lane(name)
    seen = set()
    for kw in core_queries(profile):
        lane.requests += 1
        r = fetcher.get(f"{MADGEX_URLS[name]}?{urlencode({'keywords': kw, 'countrycode': 'US'})}",
                        accept="application/rss+xml,application/xml,text/xml").check()
        for lead in each(lane, rss_items(boards.parse_xml(r.body, name)),
                         lambda item: madgex_lead(name, item, area, today, salt)):
            if lead["lead_id"] not in seen:
                seen.add(lead["lead_id"])
                lane.leads.append(lead)
    return lane


# ---------- Himalayas (remote postings; credit "Himalayas", apply through applicationLink) ----------

def run_himalayas(fetcher, profile, area, first_seen):
    lane = Lane("himalayas")
    if not area[2]:  # remote jobs only: nothing here is at home when remote does not count
        return lane
    seen = set()
    for q in core_queries(profile):
        cursor = ""
        for _ in range(HIMALAYAS_PAGES):
            params = {"q": q, "country": "US"}
            if cursor:
                params["cursor"] = cursor
            lane.requests += 1
            r = fetcher.get(f"{HIMALAYAS_URL}?{urlencode(params)}", accept="application/json").check()
            d = r.json()
            for reading in each(lane, d.get("jobs") if isinstance(d, dict) else None,
                                lambda j: himalayas_reading(j, first_seen)):
                if reading.posting.job_key not in seen:
                    seen.add(reading.posting.job_key)
                    lane.postings.append(reading)
            cursor = str(d.get("nextCursor") or "") if isinstance(d, dict) else ""
            if not cursor:
                break
    return lane


def himalayas_reading(j, first_seen):
    """One search job as a posting. The terms: a visible 'Himalayas' credit (SOURCE_CREDITS) and every
    apply action through applicationLink, so that link is the url and the apply url."""
    url = posting.web_url(j.get("applicationLink"))
    if not url or not j.get("title") or not j.get("companyName"):
        return None
    restrict = j.get("locationRestrictions")
    where = [posting.one_line(x) for x in restrict if isinstance(x, str)] if isinstance(restrict, list) else []
    if where and not any(remote_us_ok(x) for x in where):
        return None
    report = posting.Report()
    p = posting.new_posting("himalayas")
    p["company"], p["role"], p["remote"] = j["companyName"], j["title"], "remote"
    p["location"] = f"Remote ({', '.join(where)})" if where else "Remote"
    p["description"] = posting.html_to_text(j.get("description") or j.get("excerpt") or "", report)
    posted(p, j.get("pubDate"), "pubDate")
    p["closes"] = day_of(j.get("expiryDate"))
    fill_salary(p, j.get("minSalary"), j.get("maxSalary"), j.get("salaryPeriod"), j.get("currency"))
    p["apply_url"] = url
    return lane_reading(p, url, "himalayas", first_seen, report)


# ---------- Remote OK (remote postings; credit "Remote OK", link back to the job's page) ----------

def run_remoteok(fetcher, profile, area, first_seen):
    """One call; element 0 is the legal notice. No query parameter, so families filter the titles here."""
    lane = Lane("remoteok")
    if not area[2]:
        return lane
    lane.requests += 1
    d = fetcher.get(REMOTEOK_URL, accept="application/json").check().json()
    lane.postings += each(lane, d if isinstance(d, list) else None, lambda j: remoteok_reading(j, profile, first_seen))
    return lane


def remoteok_reading(j, profile, first_seen):
    """One job as a posting. The terms: link back to the job's Remote OK page (url, kept as the source
    link) and name Remote OK as the source (SOURCE_CREDITS)."""
    url = posting.web_url(j.get("url"))
    if "legal" in j or not url or not j.get("position") or not j.get("company") \
            or not title_hit(profile, str(j["position"])):
        return None
    loc = posting.one_line(j.get("location"))
    if not remote_us_ok(loc):
        return None
    report = posting.Report()
    p = posting.new_posting("remoteok")
    p["company"], p["role"], p["remote"] = j["company"], j["position"], "remote"
    p["location"] = f"Remote ({loc})" if loc else "Remote"
    p["description"] = posting.html_to_text(j.get("description") or "", report)
    posted(p, j.get("date"), "date")
    fill_salary(p, j.get("salary_min"), j.get("salary_max"), "year", "USD")  # a yearly USD range; 0 is none
    apply = posting.web_url(j.get("apply_url"))
    p["apply_url"] = apply if apply and not netfetch.web_refusal(apply) else url  # never a job board's link
    return lane_reading(p, url, "remoteok", first_seen, report)


# ---------- Jobicy (remote postings; credit "Jobicy", one pass a run) ----------

def run_jobicy(fetcher, profile, area, first_seen):
    """One pass: a request per active family's first word (at most 4), never repeated or paged. The terms
    allow one request a second and one automated pass an hour; a run is one pass, and the throttle spaces
    the requests."""
    lane = Lane("jobicy")
    if not area[2]:
        return lane
    tags = list(dict.fromkeys(w for fam in active_families(profile)[:MAX_QUERIES] for w in family_words(fam, 1)))
    seen = set()
    for tag in tags:
        lane.requests += 1
        params = {"count": JOBICY_COUNT, "geo": "usa", "tag": tag}
        d = fetcher.get(f"{JOBICY_URL}?{urlencode(params)}", accept="application/json").check().json()
        for reading in each(lane, d.get("jobs") if isinstance(d, dict) else None,
                            lambda j: jobicy_reading(j, first_seen)):
            if reading.posting.job_key not in seen:
                seen.add(reading.posting.job_key)
                lane.postings.append(reading)
    return lane


def jobicy_reading(j, first_seen):
    """One job as a posting. The terms: credit Jobicy (SOURCE_CREDITS) and send every apply action to the
    job's own url in the feed."""
    url = posting.web_url(j.get("url"))
    geo = posting.one_line(j.get("jobGeo"))
    if not url or not j.get("jobTitle") or not j.get("companyName") or not remote_us_ok(geo):
        return None
    report = posting.Report()
    p = posting.new_posting("jobicy")
    p["company"], p["role"], p["remote"] = j["companyName"], j["jobTitle"], "remote"
    p["location"] = f"Remote ({geo})" if geo else "Remote"
    p["description"] = posting.html_to_text(j.get("jobDescription") or j.get("jobExcerpt") or "", report)
    posted(p, j.get("pubDate"), "pubDate")
    fill_salary(p, j.get("salaryMin"), j.get("salaryMax"), j.get("salaryPeriod"), j.get("salaryCurrency"))
    p["apply_url"] = url
    return lane_reading(p, url, "jobicy", first_seen, report)


# ---------- We Work Remotely (off until its terms are read) ----------

def run_wwr(fetcher, profile, area, first_seen):
    lane = Lane("wwr")
    if not area[2]:
        return lane
    seen = set()
    for url in WWR_URLS:
        lane.requests += 1
        r = fetcher.get(url, accept="application/rss+xml,application/xml,text/xml").check()
        for reading in each(lane, rss_items(boards.parse_xml(r.body, "wwr")),
                            lambda it: wwr_reading(it, profile, area, first_seen)):
            if reading.posting.job_key not in seen:
                seen.add(reading.posting.job_key)
                lane.postings.append(reading)
    return lane


def wwr_reading(item, profile, area, first_seen):
    """One feed item ('Company: Role', a region, an HTML description) as a posting."""
    url = posting.web_url(item.get("link"))
    company, sep, role = posting.one_line(item.get("title")).partition(": ")
    region = posting.one_line(f"{item.get('region', '')} {item.get('country', '')}")
    state = posting.region_of(posting.one_line(item.get("state")))[0]
    if not url or not sep or not title_hit(profile, role) or not remote_us_ok(region) \
            or (state and area[1] and not state & area[1]):
        return None
    report = posting.Report()
    p = posting.new_posting("wwr")
    p["company"], p["role"], p["remote"] = company, role, "remote"
    p["location"] = f"Remote ({region})" if region else "Remote"
    p["description"] = posting.html_to_text(item.get("description") or "", report)
    posted(p, item.get("pubDate"), "pubDate")
    p["closes"] = day_of(item.get("expires_at"))
    p["apply_url"] = url
    return lane_reading(p, url, "wwr", first_seen, report)


# ---------- Hacker News "Who is hiring?" (leads only) ----------

HN_TITLE = re.compile(r"^\s*Ask HN:\s*Who is hiring\?", re.I)
HN_REMOTE_US = re.compile(r"\b(?:us|usa|u\.s\.|united states|worldwide|anywhere|global|any time ?zone|north america)\b",
                          re.I)
HN_PIECES = re.compile(r"\s*(?:[,;]|\s/\s|\s+or\s+)\s*", re.I)
LINK_BITS = re.compile(r"\(?\s*(?:https?://|www\.)[^\s|)]*\s*\)?", re.I)


def hn_head(text):
    """A comment's first line as plain text: up to the first paragraph break, tags and links out."""
    first = re.split(r"<p>|<br\s*/?>|\n", str(text or ""), maxsplit=1, flags=re.I)[0]
    return posting.one_line(LINK_BITS.sub(" ", html.unescape(re.sub(r"<[^>]*>", "", first))))


def hn_leads(text, profile, area, today, salt=""):
    """Leads from one top-level comment. Its first line is pipe-separated (company | role(s) | place |
    remote flag | ...); a lead needs a role matching an active family's title terms and either a home
    place or, when remote counts, REMOTE with the US or anywhere named. Messy lines drop."""
    fields = [f for f in (x.strip() for x in hn_head(text).split("|")) if f]
    if len(fields) < 3:
        return []
    company, titles, used = re.sub(r"\s*\([^)]*\)\s*$", "", fields[0]), [], set()
    for f in fields[1:]:
        pieces = [x.strip() for x in HN_PIECES.split(f) if x.strip()]
        hits = [x for x in pieces if title_hit(profile, x)] or ([f] if title_hit(profile, f) else [])
        if hits:
            titles += hits
            used.add(f)
    others = [f for f in fields[1:] if f not in used]
    where = next((part for f in others for part in posting.place_parts(f) if place_in_home(part, area)), "")
    remote = area[2] and any(REMOTE.search(f) for f in others) and HN_REMOTE_US.search(" | ".join(fields))
    if not titles or not (where or remote or (not area[0] and not area[1])):
        return []
    out = []
    for title in list(dict.fromkeys(titles))[:HN_MAX_TITLES]:
        lead = make_lead("hn_hiring", company, title, where or "Remote", today, salt)
        if lead:
            out.append(lead)
    return out


def hn_story(fetcher):
    """{id, title, created} of the newest 'Ask HN: Who is hiring?' story, or None."""
    d = fetcher.get(HN_SEARCH_URL, accept="application/json").check().json()
    for h in boards.dicts(d.get("hits") if isinstance(d, dict) else None):
        sid = str(h.get("objectID") or "")
        if HN_TITLE.match(str(h.get("title") or "")) and re.fullmatch(r"\d{1,12}", sid):
            return {"id": sid, "title": posting.one_line(h["title"]), "created": day_of(h.get("created_at"))}
    return None


def run_hn(fetcher, root, profile, area, today, salt=""):
    """Leads only. The thread is read once a day: state/hn_hiring.json holds the story id and the date
    of the last read (a same-day rerun makes no request). A failed search falls back to the cached id
    while that thread is under 35 days old."""
    lane = Lane("hn_hiring")
    path = jobfs.Folder(root).hn_hiring_json
    try:
        state = jobfs.read_json_verified(path, retries=0)
    except jobfs.NotReady:
        state = {}
    if state.get("read") == today:
        return lane
    lane.requests += 1
    story, err = None, None
    try:
        story = hn_story(fetcher)
    except Denied:
        raise
    except FetchError as e:
        err = e
    if story is None:
        sid, made = str(state.get("story_id") or ""), str(state.get("created") or "")
        cutoff = (date.fromisoformat(today) - timedelta(days=HN_STORY_AGE)).isoformat()
        if re.fullmatch(r"\d{1,12}", sid) and made >= cutoff:
            story = {"id": sid, "title": str(state.get("title") or ""), "created": made}
        elif err:
            raise err
        else:
            lane.status, lane.why = "ERROR", "no Who is hiring thread in the search"
            return lane
    lane.requests += 1
    d = fetcher.get(HN_ITEM_URL + story["id"], accept="application/json").check().json()
    cutoff = (date.fromisoformat(today) - timedelta(days=HN_MAX_AGE)).isoformat()

    def one(c):
        day = day_of(c.get("created_at"))
        return None if day and day < cutoff else hn_leads(c.get("text"), profile, area, today, salt)
    seen = set()
    for batch in each(lane, d.get("children") if isinstance(d, dict) else None, one):
        for lead in batch:
            if lead["lead_id"] not in seen:
                seen.add(lead["lead_id"])
                lane.leads.append(lead)
    try:
        jobfs.atomic_write_json(path, {"v": 1, "story_id": story["id"], "title": story["title"],
                                       "created": story["created"], "read": today})
    except OSError:
        pass  # no state file: the thread is read again on the next run
    return lane


# ---------- run ----------

def guarded(lane_fn, name):
    """Run one lane; network trouble becomes an ERROR or DENY line."""
    try:
        return lane_fn()
    except Denied as e:
        lane = Lane(name)
        lane.status, lane.why = "DENY", e.reason
    except FetchError as e:  # network trouble: the reason says it
        lane = Lane(name)
        lane.status, lane.why = "ERROR", str(e)[:160]
    except Exception as e:  # noqa: BLE001  an odd answer (JSON nested thousands deep) stops this lane only
        lane = Lane(name)
        lane.status, lane.why = "ERROR", f"unreadable answer ({type(e).__name__})"
    return lane


def run(root, fetcher=None, offline=None, keys=None, keys_file=None, salt="", emit=print):
    """Every enabled aggregator lane. Returns the Lanes (postings are
    posting.Reading records; leads are lead dicts)."""
    root = Path(root)
    settings, profile = jobfs.settings(root), jobfs.search_profile(root)
    today = jobfs.today(root).isoformat()
    keys = keys if keys is not None else load_keys(keys_file)
    fetcher = fetcher or netfetch.Fetcher(root=root, offline=offline)
    src = settings["sources"]
    areas = []

    def area():  # (places, home states, remote counts): read once, and only when a lane that needs it is on
        if not areas:
            areas.append(boards.home_area(root, profile))
        return areas[0]
    plan = [("usajobs", lambda: run_usajobs(fetcher, keys, profile, today)),
            ("adzuna", lambda: run_adzuna(fetcher, keys, profile, today, salt)),
            ("workable_search", lambda: run_workable(fetcher, profile, today)),
            ("muse", lambda: run_muse(fetcher, keys, profile, today, salt)),
            ("science_careers", lambda: run_madgex(fetcher, "science_careers", profile, area(), today, salt)),
            ("nature_careers", lambda: run_madgex(fetcher, "nature_careers", profile, area(), today, salt)),
            ("himalayas", lambda: run_himalayas(fetcher, profile, area(), today)),
            ("remoteok", lambda: run_remoteok(fetcher, profile, area(), today)),
            ("jobicy", lambda: run_jobicy(fetcher, profile, area(), today)),
            ("hn_hiring", lambda: run_hn(fetcher, root, profile, area(), today, salt)),
            ("wwr", lambda: run_wwr(fetcher, profile, area(), today))]
    lanes = []
    for name, fn in plan:
        if not src.get(name, False):
            lane = Lane(name)
            lane.status = "OFF"
        else:
            lane = guarded(fn, name)
        emit(lane.line())
        lanes.append(lane)
    return lanes


# ---------- CLI ----------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def main(argv=None):
    ap = Parser(description="Aggregator lanes (job_skill v1.2 runner).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run every enabled aggregator lane", allow_abbrev=False)
    r.add_argument("--root", required=True)
    r.add_argument("--offline", help="fixture folder with index.json instead of the network")
    r.add_argument("--keys", help="keys.toml path (default %%LOCALAPPDATA%%\\JobRunner\\keys.toml)")
    r.add_argument("--out-postings")
    r.add_argument("--out-leads")
    args = ap.parse_args(argv)
    if not Path(args.root).is_dir():
        print(f"ERROR no folder {args.root}")
        return 1
    lanes = run(args.root, offline=args.offline, keys_file=args.keys)
    if args.out_postings:
        rows = []
        for lane in lanes:
            for rd in lane.postings:
                rec = rd.posting.to_dict()
                rec["ai_text"], rec["hidden"] = rd.ai_text, rd.hidden
                rows.append(json.dumps(rec, ensure_ascii=True, separators=(",", ":")))
        jobfs.atomic_write_text(args.out_postings, "\n".join(rows) + ("\n" if rows else ""))
        print(f"WROTE {args.out_postings}")
    if args.out_leads:
        rows = [json.dumps(ld, ensure_ascii=True, separators=(",", ":")) for lane in lanes for ld in lane.leads]
        jobfs.atomic_write_text(args.out_leads, "\n".join(rows) + ("\n" if rows else ""))
        print(f"WROTE {args.out_leads}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
