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
import json
import re
import sys
import tomllib
from datetime import date, timedelta
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
MAX_QUERIES = 4           # per source per day
MUSE_MAX_AGE = 14         # days (aggregators A2)
ADZUNA_PAGE = 50

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
            kind = "leads" if self.name in ("adzuna", "muse") else "postings"
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
    plan = [("usajobs", lambda: run_usajobs(fetcher, keys, profile, today)),
            ("adzuna", lambda: run_adzuna(fetcher, keys, profile, today, salt)),
            ("workable_search", lambda: run_workable(fetcher, profile, today)),
            ("muse", lambda: run_muse(fetcher, keys, profile, today, salt))]
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
