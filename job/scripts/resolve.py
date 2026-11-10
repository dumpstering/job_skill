"""Lead resolver for the runner (spec 3.6, 3.3, 3.7): turns alert and aggregator
leads (company, title, location) into postings on the employer's own board.

Usage:
    python resolve.py run --root R [--offline DIR] [--date YYYY-MM-DD]
    python resolve.py probe-url --root R --url U [--company C] [--title T] [--location L]
                                [--offline DIR] [--date YYYY-MM-DD]

run reads every runner/in/leads/*.jsonl line it has not finished
(state/leads_state.json), collapses repeats on company + title + city, and per
lead group:
1. Known board (config/watchlist.csv, state/watchlist_auto.csv,
   state/company_ats.json): matches today's fetched list, or reads that board
   once. A match is a title token-set similarity of at least 0.85 and a
   compatible location (same city, a [home] place, or remote-US). Every match
   is its own posting.
2. Unknown company: probes slug candidates (name; no spaces; hyphenated; a
   trailing industry word dropped) on Greenhouse, Lever, Ashby, Workable,
   BambooHR, Breezy, JazzHR, Pinpoint, Personio, Teamtailor, Rippling,
   Recruitee (after its selftest) and Jobvite, in that order. A hit needs the
   board on the ATS host, the expected body and at least 1 job; an empty board
   is unknown, never a miss. Misses (404, gone redirects) are cached for 30
   days in state/probe_cache.csv. A hit joins state/watchlist_auto.csv.
3. Still unknown, or a lead-only ATS (R3): listed in runner/out/needs_search.json
   for one web search ("<company> <title> careers") by Claude, whose job page
   comes back as a probe_url request.
4. Unresolved on 2 different days, and (for a lead step 3 lists) listed in
   needs_search.json on 2 different days, so at least one job-morning had it
   to search: LinkedIn leads go to the do-yourself list (queue/<date>/diy.md,
   company, title and location only, no link); other leads are discarded with
   reason unresolved (queue/<date>/discards.csv). A lead still pending 30
   days after it was seen goes the same way, never dropped silently.

probe-url refuses job-board URLs unopened (DENY job-board <domain>, exit 7),
reads a posting URL on an ATS host directly (an R3 host only where robots
allows that exact path, else posting_status browser_read), and otherwise
fetches the careers page once and runs the ATS detect patterns over its HTML
and its first allowed iframe. A Phenom career site page with no ATS link (a
landing page: Sutter, Lilly) leads to one more same-host page, a job page or
the site's own job search, whose apply links name the Workday tenant
(PHENOM <url>). A Workday site comes from the real URL.

Output lines:
    RESOLVED <lead_id> <how> <job_key>          (how: known, probe <ats>:<slug>, url)
    PROBE <ats>:<slug> HIT <n> | MISS <why> | UNKNOWN <why> | CACHED miss
    UNRESOLVED <lead_id> <company> day=<n> <why>   (why ends "expired" past 30 days)
    DIY <lead_id> | DISCARD <lead_id> unresolved
    DETECT <ats>:<board>[/<site>][@<host>] | DETECT none <host> | BROWSER_READ <url> | PHENOM <url>
    RESOLVE leads=<n> groups=<n> resolved=<n> unresolved=<n> needs_search=<n> diy=<n> discarded=<n>
Stdlib only. Exit codes: 0 ok, 1 bad input, 7 DENY.
"""
import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boards  # noqa: E402
import gates  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import leads as leadslib  # noqa: E402
import netfetch  # noqa: E402
import posting  # noqa: E402
from netfetch import Denied, FetchError  # noqa: E402

MATCH_SIM = 0.85
MISS_DAYS = 30
GIVE_UP_DAYS = 2
LEAD_MAX_AGE = 30
SEARCHES_MAX = 10
PROBE_ORDER = ["greenhouse", "lever", "ashby", "workable", "bamboohr", "breezy", "jazzhr", "pinpoint", "personio",
               "teamtailor", "rippling", "recruitee", "jobvite"]
PROBE_CACHE_COLUMNS = ["company_norm", "ats", "slug", "result", "date", "detail"]
WATCH_COLUMNS = ["employer", "employer_norm", "parent", "ats", "board", "host", "site", "extra", "careers_url",
                 "main_website", "segment", "city", "families", "priority", "status", "added_on", "added_by", "ref",
                 "notes", "first_resolved", "last_lead", "evidence"]
INDUSTRY_WORDS = {"bio", "biosciences", "bioscience", "biotherapeutics", "therapeutics", "pharmaceuticals",
                  "pharma", "labs", "laboratories", "sciences", "science", "technologies", "technology",
                  "diagnostics", "genomics", "health", "medical", "biologics", "biotech", "systems"}
LANE_OF = {"linkedin_alert": "alert:linkedin", "adzuna": "adzuna", "muse": "muse", "usajobs": "usajobs"}
HTML_SCAN_MAX = 512_000   # detect and the iframe scan read at most the first 512 KB of a page
DETECT_TOKENS_MAX = 400   # URL-like tokens around ATS host names, per page
TOKEN_BACK, TOKEN_AHEAD = 200, 600

# ATS detect patterns over a careers page's HTML and iframe URLs (ats sources
# "detect"). Groups give board, then site and host where the ATS has them.
DETECT = [
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?for=([A-Za-z0-9_-]+)",
                              re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
    ("lever_eu", re.compile(r"jobs\.eu\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("workday", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")),
    ("workday_site",
     re.compile(r"(?<![a-z0-9])(wd\d{1,4})\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([a-z0-9_-]+)/([A-Za-z0-9_-]+)")),
    ("bamboohr", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.bamboohr\.com", re.I)),
    ("breezy", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.breezy\.hr", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("jazzhr", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.applytojob\.com", re.I)),
    ("pinpoint", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.pinpointhq\.com", re.I)),
    ("personio", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.jobs\.personio\.(?:de|com)", re.I)),
    ("teamtailor", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.teamtailor\.com", re.I)),
    ("rippling", re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.recruitee\.com", re.I)),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([A-Za-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("icims", re.compile(r"careers-([a-z0-9-]{1,63})\.icims\.com", re.I)),
    ("ukg", re.compile(r"recruiting2?\.ultipro\.com/([A-Za-z0-9]+)/JobBoard/([0-9a-fA-F-]{36})")),
    ("oracle", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.fa\.([a-z0-9-]{1,63})\.oraclecloud\.com/hcmUI/CandidateExperience/[a-z]{2}"
                          r"/sites/([A-Za-z0-9_]+)")),
    ("paylocity",
     re.compile(r"recruiting\.paylocity\.com/recruiting/jobs/All/([0-9a-fA-F-]{36})(?:/([A-Za-z0-9_-]+))?")),
    ("dayforce", re.compile(r"jobs\.dayforcehcm\.com/(?:[a-z]{2}-[A-Z]{2}/)?([a-z0-9]+)/([A-Za-z0-9_]+)")),
    ("adp", re.compile(r"workforcenow\.adp\.com/[^\"'\s<>]{0,400}?cid=([0-9a-fA-F-]{36})")),
    ("paycom", re.compile(r"(?<![a-z0-9-])([a-z0-9-]{1,63})\.paycomonline\.net", re.I)),
]
NOT_BOARDS = {"www", "api", "app", "apply", "jobs", "careers", "help", "support", "embed", "static", "cdn", "assets",
              "v1", "js", "css", "images", "img"}
IFRAME = re.compile(r"<iframe\b[^<>]{0,500}?\bsrc\s*=\s*[\"']([^\"'<>]{1,2000})[\"']", re.I)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- small helpers ----------

def lane_of(source):
    if source in LANE_OF:
        return LANE_OF[source]
    return "alert:" + source[:-6] if source.endswith("_alert") else source


def city_of(location):
    return " ".join(re.findall(r"[a-z0-9]+", (location or "").split(",")[0].lower()))


def group_key(lead):
    return (jobkey.norm_company(lead["company"]), jobkey.norm_title(lead["title"]), city_of(lead.get("location")))


def slug_candidates(company):
    """name; no spaces; hyphenated; a trailing industry word dropped (deduped)."""
    raw = "".join(re.findall(r"[a-z0-9]+", (company or "").lower()))
    norm = jobkey.norm_company(company).split()
    out = [raw, "".join(norm), "-".join(norm)]
    if len(norm) > 1 and norm[-1] in INDUSTRY_WORDS:
        out += ["".join(norm[:-1]), "-".join(norm[:-1])]
    return [s for s in dict.fromkeys(out) if s]


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def write_csv(path, columns, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([gates.neutral(r.get(c, "")) for c in columns])
    jobfs.atomic_write_text(path, buf.getvalue())


def read_json(path, default):
    """The safe loader (R3): missing is default; a bad file is default with one ERROR line."""
    return jobfs.read_json_or(path, jobfs.MAX_STATE, default)


def spec_label(spec):
    return spec.label + (f"/{spec.site}" if spec.site else "") + (f"@{spec.host}" if spec.host else "")


# ---------- location compatibility ----------

def location_ok(lead_loc, p, places):
    """Same city, a [home] place, or remote-US (3.6)."""
    if not (lead_loc or "").strip():
        return True
    if p.remote == "remote" and p.us_eligible is not False:
        return True
    text = gates.words(" ".join([p.location or "", p.city or ""]))
    lead_city = city_of(lead_loc)
    if lead_city and (" " + lead_city + " ") in f" {text} ":
        return True
    return any(gates.term_hits(pl, text) for pl in places)


def matches(lead, readings, places):
    out = []
    for r in readings:
        p = r.posting
        if p.closed or not p.title:
            continue
        if gates.dice(lead["title"], p.title) >= MATCH_SIM and location_ok(lead.get("location"), p, places):
            out.append(r)
    return out


# ---------- detection ----------

# The ATS host names DETECT looks for. detect() finds these literals in one
# pass, cuts the URL-like token around each (bounded on both sides at a quote,
# space or angle bracket) and runs the DETECT patterns on those tokens only,
# so a hostile page costs linear time whatever it holds.
ATS_HOSTS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|myworkdaysite\.com|bamboohr\.com"
                       r"|breezy\.hr|workable\.com|applytojob\.com|pinpointhq\.com|jobs\.personio\.(?:de|com)"
                       r"|teamtailor\.com|rippling\.com|recruitee\.com|jobvite\.com|smartrecruiters\.com|icims\.com"
                       r"|ultipro\.com|oraclecloud\.com|paylocity\.com|dayforcehcm\.com|workforcenow\.adp\.com"
                       r"|paycomonline\.net", re.I)
TOKEN_END = re.compile(r"[\s\"'<>()\[\]{}`]")


def url_tokens(text):
    """(start, token) for each URL-like token holding an ATS host name, in page
    order, from the first HTML_SCAN_MAX characters; at most DETECT_TOKENS_MAX."""
    text = (text or "")[:HTML_SCAN_MAX]
    out, last_end = [], -1
    for m in ATS_HOSTS.finditer(text):
        if m.start() < last_end:
            continue  # inside the token already taken
        lo = max(0, m.start() - TOKEN_BACK, last_end)
        back = text[lo:m.start()]
        cut = max((back.rfind(c) for c in " \t\r\n\"'<>()[]{}`"), default=-1)
        start = lo + cut + 1
        ahead = TOKEN_END.search(text, m.end(), min(len(text), m.end() + TOKEN_AHEAD))
        end = ahead.start() if ahead else min(len(text), m.end() + TOKEN_AHEAD)
        out.append((start, text[start:end]))
        last_end = end
        if len(out) >= DETECT_TOKENS_MAX:
            break
    return out


def detect(text):
    """[{ats, board, site, host}] found in HTML or a URL, first occurrence order.
    Scans only the URL-like tokens around ATS host names (url_tokens), with
    bounded patterns; JSON's escaped slashes (https:\\/\\/...) read as '/'."""
    found, seen = [], set()
    for start, token in url_tokens((text or "")[:HTML_SCAN_MAX].replace("\\/", "/")):
        for ats, rx in DETECT:
            for m in rx.finditer(token):
                spec = spec_of_match(ats, m)
                if spec and (spec["ats"], spec["board"].lower()) not in seen:
                    seen.add((spec["ats"], spec["board"].lower()))
                    found.append(dict(spec, at=start + m.start()))
    return [{k: v for k, v in s.items() if k != "at"} for s in sorted(found, key=lambda s: s["at"])]


def spec_of_match(ats, m):
    g = m.groups()
    board = g[0] or ""
    if ats == "workday":
        tenant, wd, site = g
        return {"ats": "workday", "board": tenant, "site": site, "host": f"{tenant}.{wd}.myworkdayjobs.com"} \
            if tenant.lower() not in NOT_BOARDS else None
    if ats == "workday_site":
        wd, tenant, site = g
        return {"ats": "workday", "board": tenant, "site": site, "host": f"{wd}.myworkdaysite.com"}
    if ats == "oracle":
        prefix, dc, site = g
        return {"ats": "oracle", "board": prefix, "site": site, "host": f"{prefix}.fa.{dc}.oraclecloud.com"}
    if ats in ("ukg", "paylocity", "dayforce"):
        return {"ats": ats, "board": board, "site": g[1] or "", "host": ""}
    if ats == "ashby" and "%" in board:
        # A board name with a space is linked as %20; read_ashby quotes the name
        # itself, so a quoted one would be quoted twice and 404.
        board = unquote(board)
        if not re.fullmatch(r"[A-Za-z0-9 _.-]+", board):
            return None
    if board.lower() in NOT_BOARDS:
        return None
    return {"ats": ats, "board": board, "site": "", "host": ""}


# ---------- the resolver ----------

@dataclass
class Outcome:
    readings: list = field(default_factory=list)       # new postings (posting.Reading) for the gates
    resolved: dict = field(default_factory=dict)       # lead_id -> [job keys]
    needs_search: list = field(default_factory=list)
    diy: list = field(default_factory=list)
    discarded: list = field(default_factory=list)
    lines: list = field(default_factory=list)


class Resolver:
    def __init__(self, root, today=None, fetcher=None, offline=None, emit=print):
        self.root = Path(root)
        self.folder = jobfs.Folder(root)
        self.today = today or jobfs.today(root)
        self.fetcher = fetcher or netfetch.Fetcher(root=root, offline=offline, cookies=True)
        self.emit = emit
        self.places = [str(p) for p in (jobfs.search_profile(root).get("home") or {}).get("places") or []]
        self.state = read_json(self.folder.state / "leads_state.json", {})
        if not isinstance(self.state.get("leads"), dict):
            self.state["leads"] = {}
        self.state["leads"] = {k: v for k, v in self.state["leads"].items()
                               if isinstance(v, dict) and isinstance(v.get("status"), str)
                               and isinstance(v.get("company"), str) and isinstance(v.get("title"), str)
                               and isinstance(v.get("tries"), list) and isinstance(v.get("job_keys"), list)}
        self.company_ats = read_json(self.folder.company_ats_json, {})
        self.company_ats.pop("_rev", None)
        self.company_ats.pop("_sha256", None)
        self.cache = [r for r in read_csv(self.folder.probe_cache_csv)
                      if all(isinstance(r.get(c), str) for c in PROBE_CACHE_COLUMNS)]
        self.auto = read_csv(self.folder.watchlist_auto_csv)
        self.recruitee = boards.recruitee_enabled(root)

    # ----- leads -----
    def load_leads(self, extra=()):
        """New lead lines (runner/in/leads and extra, e.g. Adzuna) joined to the
        unfinished ones in state."""
        rows = []
        if self.folder.leads.exists():
            for path in sorted(self.folder.leads.glob("*.jsonl")):
                try:
                    rows += jobfs.read_jsonl(path)
                except Exception as e:  # noqa: BLE001  a locked or odd lead file is skipped this run, named
                    self.emit(f"ERROR leads file {path.name[:80]}: {type(e).__name__}")
        rows += list(extra)
        leads = self.state["leads"]
        for ld in rows:
            if not isinstance(ld, dict) or not isinstance(ld.get("lead_id"), str) or not ld["lead_id"] \
                    or ld["lead_id"] in leads:
                continue
            fields, why = leadslib.check_card(ld)
            if why or "linkedin.com" in json.dumps(ld).lower():
                continue  # leads.py validated it; anything else is dropped, fail closed
            leads[ld["lead_id"]] = dict(fields, lead_id=ld["lead_id"], source=str(ld.get("source", "")),
                                        seen=str(ld.get("seen", "")), status="pending", tries=[], job_keys=[])
        return [ld for ld in leads.values() if ld["status"] == "pending"]

    def expired(self, lead):
        cutoff = (self.today - timedelta(days=LEAD_MAX_AGE)).isoformat()
        return (lead.get("seen") or "9") < cutoff

    # ----- known boards -----
    def known_specs(self, company_norm):
        specs = [s for s in boards.watchlist_rows(self.root)
                 if jobkey.norm_company(s.employer or s.board) == company_norm]
        c = self.company_ats.get(company_norm)
        if c and not any(s.ats == c["ats"] and s.board.lower() == c["board"].lower() for s in specs):
            specs.append(boards.BoardSpec(c["ats"], c["board"], c.get("site", ""), c.get("host", ""), {},
                                          c.get("company", ""), c.get("status", "active")))
        return specs

    def board_readings(self, spec, today_readings, title):
        """Today's fetched list for this board, else one read of it now."""
        mine = [r for r in today_readings if r.posting.ats == boards.RECORD_ATS.get(spec.ats, spec.ats)
                and (r.posting.board or "").lower() == spec.board.lower()]
        if mine:
            return mine
        res = boards.read_board(spec, self.fetcher, searches=[title], places=self.places, max_pages=2,
                                keep=lambda p: gates.dice(title, p.title) >= MATCH_SIM, details=True,
                                first_seen=self.today.isoformat())
        return res.readings if res.status == "OK" else []

    # ----- probes -----
    def cached(self, company_norm, ats, slug):
        for r in reversed(self.cache):
            if r["company_norm"] == company_norm and r["ats"] == ats and r["slug"] == slug:
                d = gates.to_date(r.get("date"))
                if r["result"] == "miss" and d and (self.today - d).days < MISS_DAYS:
                    return "miss"
                if r["result"] in ("unknown", "error") and d == self.today:
                    return r["result"]
                return None
        return None

    def note(self, company_norm, ats, slug, result, detail=""):
        self.cache.append({"company_norm": company_norm, "ats": ats, "slug": slug, "result": result,
                           "date": self.today.isoformat(), "detail": detail[:120]})

    def probe(self, company, company_norm, title):
        """(spec, readings) for the first board that answers, else (None, [])."""
        for ats in PROBE_ORDER:
            if ats == "recruitee" and not self.recruitee:
                continue
            for slug in slug_candidates(company):
                tag = f"{ats}:{slug}"
                hit = self.cached(company_norm, ats, slug)
                if hit:
                    self.emit(f"PROBE {tag} CACHED {hit}")
                    continue
                spec = boards.BoardSpec(ats, slug, employer=company)
                res = boards.read_board(spec, self.fetcher, max_pages=1, details=True, searches=[title],
                                        keep=lambda p: gates.dice(title, p.title) >= MATCH_SIM,
                                        first_seen=self.today.isoformat())
                if res.status == "OK" and res.readings:
                    self.note(company_norm, ats, slug, "hit", f"{len(res.readings)} jobs")
                    self.emit(f"PROBE {tag} HIT {len(res.readings)}")
                    return spec, res.readings
                if res.status == "GONE":
                    self.note(company_norm, ats, slug, "miss", res.why)
                    self.emit(f"PROBE {tag} MISS {res.why}")
                else:
                    why = "empty board" if res.status == "EMPTY" else f"{res.status.lower()} {res.why}".strip()
                    self.note(company_norm, ats, slug, "unknown" if res.status == "EMPTY" else "error", why)
                    self.emit(f"PROBE {tag} UNKNOWN {why}")
        return None, []

    def remember(self, company, company_norm, spec, how, evidence, lead_id=""):
        """company_ats.json plus a state/watchlist_auto.csv row (3.7 growth)."""
        lead_only = spec["ats"] in boards.LEAD_ONLY_ATS
        self.company_ats[company_norm] = {"company": company, "ats": spec["ats"], "board": spec["board"],
                                          "site": spec.get("site", ""), "host": spec.get("host", ""), "how": how,
                                          "on": self.today.isoformat(),
                                          "status": "lead_only" if lead_only else "active"}
        for r in self.auto:
            if r.get("ats") == spec["ats"] and (r.get("board") or "").lower() == spec["board"].lower():
                r["last_lead"] = self.today.isoformat()
                return
        self.auto.append({"employer": company, "employer_norm": company_norm, "ats": spec["ats"],
                          "board": spec["board"], "site": spec.get("site", ""), "host": spec.get("host", ""),
                          "status": "lead_only" if lead_only else "active", "added_on": self.today.isoformat(),
                          "added_by": "lead", "ref": lead_id, "first_resolved": self.today.isoformat(),
                          "last_lead": self.today.isoformat(), "evidence": evidence[:200]})

    # ----- one run -----
    def run(self, readings=(), extra_leads=()):
        out = Outcome()
        pending = self.load_leads(extra_leads)
        groups = {}
        for ld in pending:
            groups.setdefault(group_key(ld), []).append(ld)
        needs, offered = {}, {}
        for (cnorm, _, _), group in sorted(groups.items()):
            lead = group[0]
            if all(self.expired(g) for g in group):
                self.emit(f"UNRESOLVED {lead['lead_id']} {lead['company']} day={len(lead['tries'])} expired")
                self.give_up(group, out)
                continue
            try:
                found, why = self.resolve_group(lead, cnorm, list(readings), out)
            except Exception as e:  # noqa: BLE001  one lead's odd board or record never stops the run
                self.emit(f"ERROR lead {lead['lead_id']} {lead['company'][:60]}: {type(e).__name__}")
                continue  # not a try: the lead stays pending, and expires after LEAD_MAX_AGE days at worst
            ids = [g["lead_id"] for g in group]
            if found:
                for r in found:
                    p = r.posting
                    p.lead_ids = sorted(set(p.lead_ids or []) | set(ids))
                    for g in group:
                        g["job_keys"] = sorted(set(g["job_keys"]) | {p.job_key})
                        out.resolved.setdefault(g["lead_id"], []).append(p.job_key)
                    self.emit(f"RESOLVED {lead['lead_id']} {why} {p.job_key}")
                for g in group:
                    g.update(status="resolved", done_on=self.today.isoformat())
                continue
            for g in group:
                if self.today.isoformat() not in g["tries"]:
                    g["tries"].append(self.today.isoformat())
            days = len(group[0]["tries"])
            self.emit(f"UNRESOLVED {lead['lead_id']} {lead['company']} day={days} {why}")
            searchable = why in ("unknown", "lead_only")
            # A lead for the web search waits until needs_search.json listed it on
            # GIVE_UP_DAYS days: the first listing is often written after that
            # morning's job-morning read the file (a 06:45 tick).
            if days >= GIVE_UP_DAYS and (not searchable or len(lead.get("offered") or []) >= GIVE_UP_DAYS):
                self.give_up(group, out)
            elif searchable:
                e = needs.setdefault(cnorm, {"company": lead["company"], "company_norm": cnorm, "why": why,
                                             "titles": [], "leads": 0})
                e["titles"] = sorted(set(e["titles"]) | {lead["title"]})
                e["leads"] += len(group)
                offered.setdefault(cnorm, []).extend(group)
        out.needs_search = sorted(needs.values(), key=lambda e: (-e["leads"], e["company_norm"]))[:SEARCHES_MAX]
        for e in out.needs_search:
            for g in offered.get(e["company_norm"], []):
                g["offered"] = sorted(set(g.get("offered") or []) | {self.today.isoformat()})
        self.save(out)
        self.emit(f"RESOLVE leads={len(pending)} groups={len(groups)} resolved={len(out.resolved)} "
                  f"unresolved={sum(1 for g in groups.values() if g[0]['status'] == 'pending')} "
                  f"needs_search={len(out.needs_search)} diy={len(out.diy)} discarded={len(out.discarded)}")
        return out

    def resolve_group(self, lead, cnorm, today_readings, out):
        """(readings, how) or ([], why-not)."""
        lane = lane_of(lead.get("source", ""))
        specs = self.known_specs(cnorm)
        if specs:
            usable = [s for s in specs if s.ats not in boards.LEAD_ONLY_ATS and s.status not in ("gone",)]
            if not usable:
                return [], "lead_only"
            found = []
            for spec in usable:
                rs = self.board_readings(spec, today_readings, lead["title"])
                new = [r for r in matches(lead, rs, self.places) if r not in found]
                for r in new:
                    if r not in today_readings:
                        r.posting.lane = lane
                        out.readings.append(r)
                found += new
            return (found, "known") if found else ([], "no matching title on the known board")
        spec, rs = self.probe(lead["company"], cnorm, lead["title"])
        if not spec:
            return [], "unknown"
        self.remember(lead["company"], cnorm, {"ats": spec.ats, "board": spec.board}, "probe",
                      f"probe {spec.ats}:{spec.board}", lead["lead_id"])
        found = matches(lead, rs, self.places)
        for r in found:
            r.posting.lane = lane
            out.readings.append(r)
        return (found, f"probe {spec.ats}:{spec.board}") if found else ([], "no matching title on the probed board")

    def give_up(self, group, out):
        lead = group[0]
        linkedin = lead.get("source") == "linkedin_alert"
        for g in group:
            g.update(status="diy" if linkedin else "discarded", done_on=self.today.isoformat())
        if linkedin:
            out.diy.append({k: lead[k] for k in ("lead_id", "company", "title", "location")})
            self.emit(f"DIY {lead['lead_id']}")
        else:
            out.discarded.append(lead["lead_id"])
            gates.write_discards(self.root, self.today,
                                 [[self.today.isoformat(), lane_of(lead.get("source", "")), lead["company"],
                                   lead["title"], lead.get("location", ""), "", "", "resolve", "unresolved", ""]])
            self.emit(f"DISCARD {lead['lead_id']} unresolved")

    # ----- files -----
    def save(self, out):
        f = self.folder
        jobfs.atomic_write_text(f.state / "leads_state.json", json.dumps(self.state, indent=1, sort_keys=True) + "\n")
        jobfs.atomic_write_json(f.company_ats_json, dict(self.company_ats))
        keep = [r for r in self.cache if r.get("result") != "miss" or
                (gates.to_date(r.get("date")) and (self.today - gates.to_date(r["date"])).days < MISS_DAYS)]
        write_csv(f.probe_cache_csv, PROBE_CACHE_COLUMNS, keep[-5000:])
        if self.auto:
            write_csv(f.watchlist_auto_csv, WATCH_COLUMNS, self.auto)
        jobfs.atomic_write_json(f.needs_search_json, {"v": 1, "date": self.today.isoformat(),
                                                      "companies": out.needs_search})
        if out.diy:
            write_diy(self.root, self.today, out.diy)


# ---------- do-yourself list ----------

def diy_leads(root, today=None):
    """(listed, set_aside): the unresolved LinkedIn leads kept for the
    do-yourself list, in the one order every view of the list uses (L4-02:
    today_view diy, the runner's staging and E numbers, queue/<date>/diy.md).
    Each lead goes through the title and place gates a read posting would
    (gates.title_place_gates: G3, G4, the G7 title knockouts), judged with
    today's profile. Those that pass are listed: the higher-priority family
    first, then a core title before an adjacent one, then the newest give-up.
    Those that fail are set aside (never listed; their state stays diy, so a
    profile change can list them)."""
    today = today or jobfs.today(root)
    state = read_json(jobfs.Folder(root).state / "leads_state.json", {}).get("leads", {})
    items = [v for v in state.values() if isinstance(v, dict) and v.get("status") == "diy"]
    items.sort(key=lambda v: (str(v.get("done_on", "")), str(v.get("lead_id", ""))), reverse=True)
    try:
        rules = gates.Rules(root, today)
    except Exception:  # noqa: BLE001  no readable profile: the leads are listed as they are (D2)
        return items, []
    if not rules.families:  # no family to judge a title by (a profile being set up): listed as they are
        return items, []
    prio = {f.get("id"): int(f.get("priority", 2) or 2) for f in rules.families}
    listed, aside = [], []
    for v in items:
        company, title, loc = str(v.get("company", "")), str(v.get("title", "")), str(v.get("location", ""))
        p = jobfs.Posting(company=company, company_norm=jobkey.norm_company(company), title=title,
                          title_norm=jobkey.norm_title(title), location=loc, lane=lane_of(str(v.get("source", ""))))
        p.city, p.state, p.country = posting.split_place(loc)
        verdict = gates.title_place_gates(p, rules)
        if verdict.ok:
            listed.append((prio.get(verdict.info.get("family"), 2), -float(verdict.info.get("title_match") or 0), v))
        else:
            aside.append(dict(v, set_aside=f"{verdict.gate} {verdict.reason}"))
    listed.sort(key=lambda t: t[:2])  # stable: the newest give-up first within a rank
    return [v for _, _, v in listed], aside


def diy_items(root, today=None):
    """The do-yourself leads in list order (diy_leads, set-aside ones left out)."""
    return diy_leads(root, today)[0]


def write_diy(root, today, new_items):
    """queue/<date>/diy.md: the do-yourself list as the session shows it
    (today_view.diy_all: the same items, order and E numbers), at most
    easy_apply_list_max entries, then one line for the rest and one for the
    leads set aside, no links (R2). new_items (today's give-ups) are already
    in the lead state."""
    import today_view  # its list is the one the user works from
    limit = int(jobfs.settings(root)["daily"].get("easy_apply_list_max", 5))
    rows, aside = today_view.diy_all(root, today)
    lines = ["# Do it yourself", "",
             "Apply on the job site in your own browser, then say `log e1` (and so on) when done.", ""]
    for i, (company, title, loc) in enumerate(rows[:limit], 1):
        lines.append(f"- E{i} | {company} | {title} | {loc}")
    more = len(rows) - min(limit, len(rows))
    if more or aside:
        lines.append("")
    if more:
        lines.append(f"And {more} more waiting: say `diy all` to see them.")
    if aside:
        lines.append(f"{len(aside)} more {'leads were' if len(aside) != 1 else 'lead was'} set aside: the gates "
                     "would not take the title or place.")
    jobfs.atomic_write_text(jobfs.Folder(root).diy_md(today), "\n".join(lines) + "\n")


# ---------- probe_url (a request from Claude) ----------

def browser_read_reading(url, company, title, location, lane, today):
    rec = jobfs.Posting(job_key=jobkey.job_key(url), ats=(posting.classify(url) or {}).get("source", "other"),
                        company=company, company_norm=jobkey.norm_company(company), title=title,
                        title_norm=jobkey.norm_title(title), location=location, url=url, apply_url=url,
                        lane=lane, first_seen=today.isoformat())
    rec.city, rec.state, rec.country = posting.split_place(location)
    return posting.Reading(rec, "", "browser_read")


def probe_url(root, url, company="", title="", location="", fetcher=None, offline=None, today=None, emit=print):
    """Read or detect from one URL. Returns {"ok", "deny", "detect", "readings"}."""
    root = Path(root)
    today = today or jobfs.today(root)
    result = {"ok": False, "deny": "", "detect": [], "readings": []}
    deny = netfetch.hard_deny(url)
    if deny:
        result["deny"] = deny
        emit(f"DENY {deny}")
        return result
    fetcher = fetcher or netfetch.Fetcher(root=root, offline=offline, cookies=True)
    host = netfetch.host_of(url)
    why = fetcher.policy.reason(url)
    if why and why.startswith("host-not-allowed"):
        fetcher.policy.allow_host(host)  # the exact host of a validated request (8.2)
        why = fetcher.policy.reason(url)
    if why:
        result["deny"] = why
        emit(f"DENY {why}")
        return result
    res = Resolver(root, today, fetcher)
    cnorm = jobkey.norm_company(company)
    lane = "pasted" if not company else "search"
    on_url = detect(url)
    key = jobkey.job_key(url)
    if on_url and not key.startswith("url:"):
        try:
            reading = posting.read_posting(url, fetcher=fetcher, root=root, lane=lane, first_seen=today.isoformat())
            result["readings"].append(reading)
            emit(f"READ {reading.posting.job_key}")
        except Denied as e:
            if not e.reason.startswith(("robots", "crawl-delay")):
                result["deny"] = e.reason
                emit(f"DENY {e.reason}")
                return result
            result["readings"].append(browser_read_reading(url, company, title, location, lane, today))
            emit(f"BROWSER_READ {url}")
        except FetchError as e:
            emit(f"WARN {str(e)[:160]}")
    found = on_url
    if not found:
        found = page_detect(fetcher, url, emit)
    for spec in found[:1]:
        emit(f"DETECT {spec['ats']}:{spec['board']}" + (f"/{spec['site']}" if spec.get("site") else "")
             + (f"@{spec['host']}" if spec.get("host") else ""))
        if cnorm:
            res.remember(company, cnorm, spec, "probe_url", f"{host}")
    if not found:
        emit(f"DETECT none {host}")
    result["detect"] = found[:1]
    result["ok"] = bool(found or result["readings"])
    res.save(Outcome(needs_search=read_json(res.folder.needs_search_json, {}).get("companies", [])))
    return result


PHENOM_MARK = re.compile(r"cdn\.phenompeople\.com|phenompeople\.com/CareerConnectResources", re.I)
PHENOM_JOB = re.compile(r"href\s*=\s*[\"']([^\"'<>\s]{1,400}/job/[^\"'<>\s]{1,300})[\"']", re.I)
PHENOM_SEARCH = re.compile(r"\"target\"\s*:\s*\"(https://[^\"<>\s]{1,300}/search-results\?keywords=)"
                           r"\{search_term_string\}\"", re.I)
PHENOM_BASE = re.compile(r"\"baseUrl\"\s*:\s*\"(https://[^\"<>\s]{1,300}/)\"", re.I)


def phenom_next(html, page_url):
    """The one same-host page worth reading after a Phenom career site page
    with no ATS link (L4-01): Phenom fronts a Workday (or other) tenant, and
    its job pages and its job search page link that tenant (each job's apply
    URL), while its landing page does not. A job link on the page, else the
    page's own search (its JSON-LD SearchAction, keywords empty: every job),
    else '<baseUrl>search-results'. None for a page that is not Phenom's."""
    if not PHENOM_MARK.search(html or ""):
        return None
    host = netfetch.host_of(page_url)
    cands = [urljoin(page_url, m.group(1)) for m in PHENOM_JOB.finditer(html)][:5]
    cands += [m.group(1) for m in PHENOM_SEARCH.finditer(html)][:2]
    cands += [m.group(1) + "search-results" for m in PHENOM_BASE.finditer(html)][:10]
    for c in cands:
        try:
            if c.startswith("https://") and netfetch.host_of(c) == host and c.split("#")[0] != page_url:
                return c
        except (Denied, ValueError):
            continue
    return None


def page_detect(fetcher, url, emit):
    """Fetch a careers page once; detect over its HTML; on a Phenom page with
    no ATS link, detect over one more same-host page (phenom_next); then the
    first iframe."""
    try:
        r = fetcher.get(url, accept="text/html")
        r.check()
    except Denied as e:
        emit(f"DENY {e.reason}")
        return []
    except FetchError as e:
        emit(f"WARN {str(e)[:160]}")
        return []
    html = r.text()[:HTML_SCAN_MAX]
    found = detect(html)
    if found:
        return found
    nxt = phenom_next(html, r.url or url)
    if nxt:
        emit(f"PHENOM {netfetch.shown(nxt, 160)}")
        try:
            nr = fetcher.get(nxt, accept="text/html")
            nr.check()
            found = detect(nr.text()[:HTML_SCAN_MAX])
        except Denied as e:  # the site's own page, not the link Claude sent: a warning, never a DENY line
            emit(f"WARN the site's job search was not read ({e.reason})")
        except FetchError as e:
            emit(f"WARN {str(e)[:160]}")
        if found:
            return found
    for src in IFRAME.findall(html)[:3]:
        frame = urljoin(r.url or url, src)
        if netfetch.hard_deny(frame) or fetcher.policy.reason(frame):
            continue
        found = detect(frame)
        if found:
            return found
        try:
            fr = fetcher.get(frame, accept="text/html")
            return detect(fr.text()[:HTML_SCAN_MAX])
        except FetchError:
            continue
    return []


# ---------- CLI ----------

def parse_day(s, root):
    if not s:
        return jobfs.today(root)
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"ERROR --date must be YYYY-MM-DD, got '{s}'")


def cmd_run(a):
    today = parse_day(a.date, a.root)
    Resolver(a.root, today, offline=a.offline).run()
    return 0


def cmd_probe_url(a):
    today = parse_day(a.date, a.root)
    r = probe_url(a.root, a.url, a.company or "", a.title or "", a.location or "", offline=a.offline, today=today)
    if r["deny"]:
        return 7
    return 0


def build_parser():
    ap = Parser(prog="resolve.py", description="Lead resolver (spec 3.6).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("run", help="Resolve pending leads.", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--offline")
    p.add_argument("--date")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("probe-url", help="Read or detect from one URL (a probe_url request).", allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--company")
    p.add_argument("--title")
    p.add_argument("--location")
    p.add_argument("--offline")
    p.add_argument("--date")
    p.set_defaults(func=cmd_probe_url)
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
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code)
            return 1
        raise
    except (jobfs.ConfigError, OSError, ValueError) as e:
        print(f"ERROR {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
