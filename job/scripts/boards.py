"""Watch-list board readers for the runner (spec 3.2, 3.3, 3.7, 3.14).

Usage:
    python boards.py fetch --board <ats>:<board>[/<site>][@<host>] [--extra k=v;k=v] [--root R]
                           [--offline DIR] [--out F.jsonl] [--list-only] [--max-pages N]
    python boards.py poll --root R [--offline DIR] [--out F.jsonl] [--list-only]

One reader per ATS. Each returns posting.Reading records (a jobfs.Posting plus
the AI-TEXT and HIDDEN flags) through netfetch only: policy, robots, one
request per second per host. Two-stage fetch: the list call gives list-level
records; only the ones a keep() hook accepts (the runner passes the list
gates) get a detail call, which fills the text and the authoritative date.

Board spec: workday:nvidia/NVIDIAExternalCareerSite@wd5 means ats workday,
board (tenant) nvidia, site NVIDIAExternalCareerSite, host nvidia.wd5.
myworkdayjobs.com. Watch-list rows give the same parts in the board, site,
host and extra columns (sources.md lists them per ATS).

Output lines, one per board:
    BOARD <ats>:<board> OK <n> postings pages=<p> stop=<why>
    BOARD <ats>:<board> EMPTY pages=<p>        (an empty list is "unknown", never gone)
    BOARD <ats>:<board> GONE <why>             (tenant gone: redirect off the ATS host, 404)
    BOARD <ats>:<board> ERROR <why> | DENY <reason> | SKIP <why>
stop is one of one-call, empty, short, total, seen-ids, page-cap, next-none.

poll reads config/watchlist.csv and state/watchlist_auto.csv, polls the
active rows, never makes a list call for R3 or lead-only rows, and updates
state/board_health.csv. Exit codes: 0 ok (fetch: OK or EMPTY), 1 bad input,
3 nothing usable (GONE or ERROR), 7 DENY.
Stdlib only.
"""
import argparse
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import netfetch  # noqa: E402
import posting  # noqa: E402
from netfetch import Denied, FetchError  # noqa: E402

MAX_PAGES = 10            # per board per run (ats A1 correction, 3.14)
WORKDAY_PAGE = 20         # CXS rejects a larger limit with HTTP 400 (ats A1)
WORKDAY_CAP = 2000        # a capped tenant reports exactly 2000 and wraps to page 0
ADP_PAGE = 20
ORACLE_PAGE = 200
DAYFORCE_PAGE = 25
LEVER_PAGE = 100
TEAMTAILOR_PAGE = 100
PHENOM_MAX = 200          # job URLs per Phenom board per day (3.2)
SITEMAP_CHILDREN = 5

# R3: these ATSs are lead-only. No list call ever, whatever the row says (3.3).
LEAD_ONLY_ATS = {"smartrecruiters", "icims", "ukg", "paycom", "clearcompany", "paycor", "neogov", "other"}
POLL_STATUSES = {"active", "dormant"}
HEALTH_COLUMNS = ["key", "status", "last_ok", "last_count", "last_error", "consecutive_errors"]
# A redirect to the vendor's own site means the tenant is gone (ats A28).
VENDOR_DOMAINS = {"bamboohr": "bamboohr.com", "jazzhr": "jazzhr.com", "jobvite": "jobvite.com",
                  "breezy": "breezy.hr", "workable": "workable.com", "pinpoint": "pinpointhq.com",
                  "teamtailor": "teamtailor.com", "recruitee": "recruitee.com"}


class Gone(FetchError):
    """The board says the tenant no longer exists (redirect, 404 or invalid page)."""


# ---------- board specs ----------

@dataclass
class BoardSpec:
    ats: str
    board: str
    site: str = ""
    host: str = ""
    extra: dict = field(default_factory=dict)
    employer: str = ""
    status: str = "active"
    careers_url: str = ""

    @property
    def label(self):
        return f"{self.ats}:{self.board}"

    @property
    def key(self):
        """The board_health.csv key: every identifier, so two Workday sites of
        one tenant stay apart."""
        return self.label + (f"/{self.site}" if self.site else "") + (f"@{self.host}" if self.host else "")


SPEC_RE = re.compile(r"^([a-z_]+):([^/@\s]+)(?:/([^@\s]+))?(?:@(\S+))?$")


def parse_extra(text):
    """'search=Cepheid;facet=123' -> {'search': 'Cepheid', 'facet': '123'}."""
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip().lower()] = v.strip()
    return out


def parse_spec(text, extra=""):
    """A BoardSpec from 'ats:board[/site][@host]'; ValueError on a bad spec."""
    m = SPEC_RE.match((text or "").strip())
    if not m:
        raise ValueError(f"bad board spec {text!r}; expected <ats>:<board>[/<site>][@<host>]")
    ats, board, site, host = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
    return BoardSpec(ats, board, site, expand_host(ats, board, host), parse_extra(extra))


def expand_host(ats, board, host):
    """Workday's @wd5 shorthand becomes <tenant>.wd5.myworkdayjobs.com."""
    host = (host or "").strip().lower()
    if ats == "workday" and re.fullmatch(r"wd\d+", host):
        return f"{board.lower()}.{host}.myworkdayjobs.com"
    return host


def spec_from_row(row):
    """A BoardSpec from a watch-list row (2.5 columns)."""
    ats = (row.get("ats") or "").strip().lower()
    board = (row.get("board") or "").strip()
    return BoardSpec(ats, board, (row.get("site") or "").strip(),
                     expand_host(ats, board, row.get("host") or ""), parse_extra(row.get("extra")),
                     (row.get("employer") or "").strip(), (row.get("status") or "active").strip().lower(),
                     (row.get("careers_url") or "").strip())


# ---------- one board's result ----------

@dataclass
class Item:
    """One listed job: its public URL, the posting dict (posting.new_posting
    shape) and how to read its detail."""
    url: str
    p: dict
    detail: object = None          # callable(ctx, item) or None
    data: dict = field(default_factory=dict)
    report: posting.Report = field(default_factory=posting.Report)


@dataclass
class BoardResult:
    spec: BoardSpec
    status: str = "OK"             # OK | EMPTY | GONE | ERROR | DENY | SKIP
    why: str = ""
    pages: int = 0
    stop: str = ""
    readings: list = field(default_factory=list)
    detail_errors: list = field(default_factory=list)

    def line(self):
        lab = f"BOARD {self.spec.label}"
        if self.status == "OK":
            return f"{lab} OK {len(self.readings)} postings pages={self.pages} stop={self.stop}"
        if self.status == "EMPTY":
            return f"{lab} EMPTY pages={self.pages}"
        return f"{lab} {self.status} {self.why}".rstrip()


class Ctx:
    """What a reader needs: the fetcher, the spec, search terms and limits."""

    def __init__(self, fetcher, spec, searches=(), places=(), max_pages=MAX_PAGES):
        self.f, self.spec = fetcher, spec
        self.searches = list(searches) or [""]
        if spec.extra.get("search"):
            self.searches = [spec.extra["search"]]
        self.places = [p for p in places if p]
        self.max_pages = max_pages
        self.pages = 0
        self.stop = ""
        self.seen = set()

    def get(self, url, accept=None, headers=None):
        self.pages += 1
        return self.f.get(url, accept=accept, headers=headers)

    def post(self, url, obj, headers=None):
        self.pages += 1
        return self.f.post_json(url, obj, headers=headers)

    def capped(self):
        if self.pages >= self.max_pages:
            self.stop = "page-cap"
            return True
        return False

    def fresh(self, ident):
        """True the first time an id is seen on this board (the seen-ID guard)."""
        ident = str(ident)
        if ident in self.seen:
            return False
        self.seen.add(ident)
        return True


# ---------- small helpers ----------

def body_json(r, what):
    r.check()
    try:
        return r.json()
    except ValueError:
        raise FetchError(f"{what}: response is not JSON")


def check_gone(r, ats, host):
    """Gone markers (ats A28): a 404/410, or a final URL off the tenant host."""
    final = netfetch.host_of(r.url)
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    if host and final and final != host:
        raise Gone(f"redirect {final}")
    if "invalid=1" in (r.url or ""):
        raise Gone(f"redirect {final}")


def denied_gone(e, ats):
    """A redirect to the vendor's marketing site is refused by policy (it is not
    an ATS host), which is itself the gone marker."""
    domain = VENDOR_DOMAINS.get(ats)
    host = (e.reason.split() + [""])[1]
    return bool(domain and e.reason.startswith(("host-not-allowed", "not-https"))
                and netfetch.on_domain(host, domain))


def iso_day(v):
    v = posting.short_date(v or "") if v not in (None, "") else ""
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or "") else ""


def set_posted(p, value, field_name):
    day = iso_day(value)
    p["posted"], p["posted_field"] = (day, field_name) if day else ("", "")


def html_text(src, item):
    return posting.html_to_text(src or "", item.report)


def place(*parts):
    return posting.join_nonempty([x for x in parts if isinstance(x, str)], ", ")


def new_item(spec, url, source, role, location="", req=""):
    p = posting.new_posting(source)
    p["company"], p["role"], p["location"], p["req"] = spec.employer, role or "", location or "", req or ""
    p["apply_url"] = url
    return Item(url, p)


def balanced_json(text, marker):
    """The JSON value that follows `marker =` in a page script, or None."""
    m = re.search(re.escape(marker) + r"\s*=\s*", text or "")
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, m.end())[0]
    except ValueError:
        return None


def parse_xml(body, what):
    """ElementTree over untrusted XML. A DOCTYPE or ENTITY is refused outright,
    so no entity expansion (billion laughs) or external entity can happen;
    job feeds and sitemaps never need one."""
    raw = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", raw[:4096], re.I) or b"<!ENTITY" in raw:
        raise FetchError(f"{what}: XML with a DOCTYPE or ENTITY is refused")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise FetchError(f"{what}: not XML")


def sitemap_entries(text):
    """(loc, lastmod) pairs of a urlset, or child sitemap locs of an index."""
    root = parse_xml(text, "sitemap")
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    kind = root.tag[len(ns):]
    rows = []
    for node in root:
        loc = node.findtext(f"{ns}loc") or ""
        mod = node.findtext(f"{ns}lastmod") or ""
        if loc.strip():
            rows.append((loc.strip(), mod.strip()))
    return kind, rows


def page_links(page, pattern, base):
    """(absolute url, anchor text) per unique link whose href matches pattern."""
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page or "", re.S | re.I):
        href = m.group(1).split("?")[0]
        if not re.search(pattern, href):
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, posting.one_line(re.sub(r"<[^>]+>", " ", m.group(2)))))
    return out


# ---------- readers: Greenhouse, Lever, Ashby ----------

def read_greenhouse(ctx):
    s = ctx.spec
    r = ctx.get(f"https://boards-api.greenhouse.io/v1/boards/{quote(s.board)}/jobs?content=true")
    if r.status == 404:
        raise Gone("HTTP 404")
    d = body_json(r, "greenhouse")
    if not isinstance(d, dict) or not isinstance(d.get("jobs"), list):
        raise FetchError("greenhouse: no jobs list")
    items = []
    for j in d["jobs"]:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        page = f"https://job-boards.greenhouse.io/{s.board}/jobs/{jid}"
        absolute = posting.web_url(j.get("absolute_url"))
        url = absolute if jobkey.job_key(absolute) == f"greenhouse:{jid}" else page
        it = new_item(s, url, "greenhouse-api", j.get("title"), (j.get("location") or {}).get("name", ""),
                      j.get("requisition_id"))
        it.p["company"] = s.employer or j.get("company_name") or s.board
        it.p["apply_url"] = absolute or page
        it.p["description"] = html_text(j.get("content"), it)
        # The list date is provisional (C22): first_published, then updated_at.
        if j.get("first_published"):
            set_posted(it.p, j["first_published"], "list.first_published")
        else:
            set_posted(it.p, j.get("updated_at"), "list.updated_at")
        it.data = {"id": jid}
        it.detail = detail_greenhouse
        items.append(it)
    ctx.stop = "one-call"
    return items


def detail_greenhouse(ctx, it):
    s = ctx.spec
    r = ctx.f.get(f"https://boards-api.greenhouse.io/v1/boards/{quote(s.board)}/jobs/{it.data['id']}?questions=true")
    try:
        d = r.json()
    except ValueError:
        d = {}
    why = posting.closed_marker("greenhouse", d)  # an explicit body marker, never a bare 404
    if why:
        return close(it, why)
    r.check()
    q = posting.parse_greenhouse(d, {"company": it.p["company"]}, it.report)
    merge(it, q, keep=("company", "apply_url"))


def read_lever(ctx, eu=False):
    s = ctx.spec
    api = "api.eu.lever.co" if eu or s.ats == "lever_eu" else "api.lever.co"
    items, skip = [], 0
    while not ctx.capped():
        r = ctx.get(f"https://{api}/v0/postings/{quote(s.board)}?mode=json&skip={skip}&limit={LEVER_PAGE}")
        if r.status == 404:
            raise Gone("HTTP 404")
        rows = body_json(r, "lever")
        if not isinstance(rows, list):
            raise FetchError("lever: not a postings list")
        new = [j for j in rows if j.get("id") and ctx.fresh(j["id"])]
        for j in new:
            url = posting.web_url(j.get("hostedUrl")) or f"https://jobs.lever.co/{s.board}/{j['id']}"
            it = Item(url, {})
            it.p = posting.parse_lever(j, {"company": s.employer or s.board}, it.report)
            items.append(it)
        if not rows:
            ctx.stop = "empty"
            break
        if not new:
            ctx.stop = "seen-ids"
            break
        if len(rows) < LEVER_PAGE:
            ctx.stop = "short"
            break
        skip += LEVER_PAGE
    return items


def read_ashby(ctx):
    s = ctx.spec
    r = ctx.get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(s.board)}?includeCompensation=true")
    if r.status == 404:
        raise Gone("HTTP 404")
    d = body_json(r, "ashby")
    if not isinstance(d, dict) or not isinstance(d.get("jobs"), list):
        raise FetchError("ashby: no jobs list")
    items = []
    for j in d["jobs"]:
        if j.get("isListed") is False or not j.get("id") or not ctx.fresh(j["id"]):
            continue
        url = posting.web_url(j.get("jobUrl")) or f"https://jobs.ashbyhq.com/{s.board}/{j['id']}"
        it = Item(url, {})
        it.p = posting.parse_ashby({"jobs": [j]}, {"company": s.employer or s.board, "id": str(j["id"])}, it.report)
        items.append(it)
    ctx.stop = "one-call"
    return items


# ---------- Workday (CXS and sitemap) ----------

def workday_base(s):
    host = s.host or f"{s.board.lower()}.wd1.myworkdayjobs.com"
    return host, f"https://{host}/wday/cxs/{s.board}/{s.site}"


def workday_url(s, host, path):
    if host.endswith("myworkdaysite.com"):
        return f"https://{host}/en-US/recruiting/{s.board}/{s.site}{path}"
    return f"https://{host}/en-US/{s.site}{path}"


def location_facets(d, places):
    """{facetParameter: [ids]} for location facet values naming a place. Only
    the parameter with the most matches is used: Workday ANDs parameters."""
    found = {}

    def walk(facets):
        for f in facets or []:
            param = f.get("facetParameter", "")
            for v in f.get("values") or []:
                if "values" in v and "id" not in v:
                    walk([v])
                    continue
                name = str(v.get("descriptor", "")).lower()
                if "location" in param.lower() and v.get("id") and any(
                        re.search(r"\b" + re.escape(pl.lower()) + r"\b", name) for pl in places):
                    found.setdefault(param, []).append(v["id"])
    walk(d.get("facets"))
    if not found:
        return {}
    best = max(found, key=lambda k: len(found[k]))
    return {best: found[best]}


def read_workday(ctx):
    s = ctx.spec
    if s.extra.get("mode") == "sitemap":
        return read_workday_sitemap(ctx)
    if not s.site:
        raise ValueError("workday needs the site from the real careers URL (board/site)")
    host, base = workday_base(s)
    items = []
    for search in ctx.searches:
        facets, offset, total = {}, 0, None
        while not ctx.capped():
            body = {"appliedFacets": facets, "limit": WORKDAY_PAGE, "offset": offset, "searchText": search or ""}
            r = ctx.post(base + "/jobs", body)
            if r.status in (404, 422):
                raise Gone(f"HTTP {r.status}") if r.status == 404 else FetchError("HTTP 422 (wrong site name?)")
            d = body_json(r, "workday")
            if offset == 0 and total is None:
                total = int(d.get("total") or 0)
                want = location_facets(d, ctx.places) if ctx.places and not facets and total > WORKDAY_PAGE else {}
                if want:
                    facets, total = want, None
                    continue  # restart the walk inside the matching locations
            rows = d.get("jobPostings") or []
            new = [j for j in rows if j.get("externalPath") and ctx.fresh(j["externalPath"])]
            for j in new:
                path = j["externalPath"]
                bullets = [b for b in j.get("bulletFields") or [] if isinstance(b, str)]
                it = new_item(s, workday_url(s, host, path), "workday-api", j.get("title"),
                              j.get("locationsText", ""), bullets[0] if bullets else "")
                it.p["company"] = s.employer or s.board
                it.data = {"path": path}  # postedOn is relative text: never a date (3.10)
                it.detail = detail_workday
                items.append(it)
            if not rows:
                ctx.stop = "empty"
                break
            if not new:
                ctx.stop = "seen-ids"  # a capped tenant wraps to page 0 (ats A1 correction)
                break
            offset += WORKDAY_PAGE
            if total and offset >= total:
                ctx.stop = "total"
                break
        if ctx.stop == "page-cap":
            break
    return items


def detail_workday(ctx, it):
    _, base = workday_base(ctx.spec)
    r = ctx.f.get(base + it.data["path"])
    d = body_json(r, "workday detail")
    if posting.closed_marker("workday", d):
        return close(it, "canApply is false")
    q = posting.parse_workday(d, {"company": it.p["company"]}, it.report)
    merge(it, q, keep=("company",))
    if not q.get("posted_field"):  # no startDate: keep a sitemap lastmod if there was one
        set_posted(it.p, it.data.get("lastmod"), "lastmod")


def read_workday_sitemap(ctx):
    """Fallback for huge tenants: /{site}/siteMap.xml lists job URLs with lastmod."""
    s = ctx.spec
    host, _ = workday_base(s)
    r = ctx.get(f"https://{host}/{s.site}/siteMap.xml", accept="application/xml,text/xml")
    r.check()
    _, rows = sitemap_entries(r.body)
    items = []
    for loc, mod in sorted(rows, key=lambda x: x[1], reverse=True)[:PHENOM_MAX]:
        m = re.search(r"(/job/.+)$", urlparse(loc).path)
        if not m or not ctx.fresh(m.group(1)):
            continue
        it = new_item(s, loc, "workday-api", "", "")
        it.p["company"] = s.employer or s.board
        it.data = {"path": m.group(1), "lastmod": mod}
        set_posted(it.p, mod, "lastmod")
        it.detail = detail_workday
        items.append(it)
    ctx.stop = "one-call"
    return items


# ---------- BambooHR, Breezy, Workable, Rippling, Pinpoint ----------

def read_bamboohr(ctx):
    s = ctx.spec
    host = f"{s.board}.bamboohr.com"
    r = ctx.get(f"https://{host}/careers/list", accept="application/json")
    check_gone(r, "bamboohr", host)  # a gone tenant lands on www.bamboohr.com (ats A28)
    d = body_json(r, "bamboohr")
    if not isinstance(d, dict) or not isinstance(d.get("result"), list):
        raise FetchError("bamboohr: no result list")
    items = []
    for j in d["result"]:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        loc = j.get("atsLocation") or {}
        if not any(loc.get(k) for k in ("city", "state", "province", "country")):
            loc = j.get("location") or {}  # atsLocation is often all nulls
        it = new_item(s, f"https://{host}/careers/{jid}", "bamboohr-api", j.get("jobOpeningName"),
                      place(loc.get("city"), loc.get("state") or loc.get("province"), loc.get("country")))
        if j.get("isRemote") or str(j.get("locationType")) == "1":
            it.p["remote"] = "remote"
        it.data = {"id": jid}
        it.detail = detail_bamboohr
        items.append(it)
    ctx.stop = "one-call"
    return items


def detail_bamboohr(ctx, it):
    s = ctx.spec
    r = ctx.f.get(f"https://{s.board}.bamboohr.com/careers/{it.data['id']}/detail", accept="application/json")
    q = posting.parse_bamboohr(body_json(r, "bamboohr detail"), {"company": it.p["company"]}, it.report)
    merge(it, q, keep=("company",))


def read_breezy(ctx):
    s = ctx.spec
    host = f"{s.board}.breezy.hr"
    r = ctx.get(f"https://{host}/json")
    check_gone(r, "breezy", host)
    rows = body_json(r, "breezy")
    if not isinstance(rows, list):
        raise FetchError("breezy: not a list")
    items = []
    for j in rows:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        loc = j.get("location") or {}
        state = loc.get("state") if isinstance(loc.get("state"), dict) else {}
        country = loc.get("country") if isinstance(loc.get("country"), dict) else {}
        url = posting.web_url(j.get("url")) or f"https://{host}/p/{j.get('friendly_id') or jid}"
        it = new_item(s, url, "breezy", j.get("name"),
                      place(loc.get("city"), state.get("id") or state.get("name"), country.get("id") or country.get("name")))
        if loc.get("is_remote"):
            it.p["remote"] = "remote"
        set_posted(it.p, j.get("published_date"), "published_date")
        it.data = {"list_date": True}
        it.detail = detail_page
        items.append(it)
    ctx.stop = "one-call"
    return items


def read_workable(ctx):
    s = ctx.spec
    r = ctx.get(f"https://apply.workable.com/api/v1/widget/accounts/{quote(s.board)}?details=true")
    if r.status == 404:
        raise Gone("HTTP 404")
    d = body_json(r, "workable")
    if not isinstance(d, dict) or not isinstance(d.get("jobs"), list):
        raise FetchError("workable: no jobs list")
    items = []
    for j in d["jobs"]:
        code = str(j.get("shortcode", ""))
        if not code or not ctx.fresh(code):
            continue
        # The widget's own url has no account in it; this form keys as workable:<account>:<code>.
        url = f"https://apply.workable.com/{s.board}/j/{code}/"
        it = new_item(s, url, "workable", j.get("title"), place(j.get("city"), j.get("state"), j.get("country")))
        it.p["company"] = s.employer or d.get("name") or s.board
        it.p["apply_url"] = posting.web_url(j.get("application_url")) or posting.web_url(j.get("url")) or url
        it.p["remote"] = "remote" if j.get("telecommuting") else ""
        it.p["description"] = html_text(j.get("description"), it)
        set_posted(it.p, j.get("published_on") or j.get("created_at"),
                   "published_on" if j.get("published_on") else "created_at")
        items.append(it)
    ctx.stop = "one-call"
    return items


def read_rippling(ctx):
    s = ctx.spec
    r = ctx.get(f"https://api.rippling.com/platform/api/ats/v1/board/{quote(s.board)}/jobs")
    if r.status == 404:
        raise Gone("HTTP 404")
    rows = body_json(r, "rippling")
    if not isinstance(rows, list):
        raise FetchError("rippling: not a list")
    items = []
    for j in rows:
        uid = str(j.get("uuid", ""))
        if not uid or not ctx.fresh(uid):
            continue
        url = posting.web_url(j.get("url")) or f"https://ats.rippling.com/{s.board}/jobs/{uid}"
        it = new_item(s, url, "rippling", j.get("name"), (j.get("workLocation") or {}).get("label", ""))
        it.data = {"id": uid}  # no dates on Rippling: first_seen after 2 polls (3.10)
        it.detail = detail_rippling
        items.append(it)
    ctx.stop = "one-call"
    return items


def detail_rippling(ctx, it):
    r = ctx.f.get(f"https://api.rippling.com/platform/api/ats/v1/board/{quote(ctx.spec.board)}/jobs/{it.data['id']}")
    d = body_json(r, "rippling detail")
    desc = d.get("description")
    if isinstance(desc, dict):
        desc = "\n".join(v for v in desc.values() if isinstance(v, str))
    it.p["description"] = html_text(desc, it)


def read_pinpoint(ctx):
    s = ctx.spec
    host = f"{s.board}.pinpointhq.com"
    r = ctx.get(f"https://{host}/postings.json")
    check_gone(r, "pinpoint", host)
    d = body_json(r, "pinpoint")
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise FetchError("pinpoint: no data list")
    items = []
    for j in rows:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        loc = j.get("location") or {}
        url = posting.web_url(j.get("url")) or f"https://{host}/en/postings/{jid}"
        it = new_item(s, url, "pinpoint", j.get("title"), place(loc.get("city"), loc.get("province")),
                      (j.get("job") or {}).get("requisition_id"))
        it.p["remote"] = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(
            str(j.get("workplace_type") or ""), "")
        parts = [j.get("description"), j.get("key_responsibilities"), j.get("skills_knowledge_expertise")]
        it.p["description"] = html_text("\n".join(x for x in parts if isinstance(x, str)), it)
        if j.get("compensation_visible") and (j.get("compensation_minimum") or j.get("compensation_maximum")):
            it.p["salary"] = posting.salary(j.get("compensation_minimum"), j.get("compensation_maximum"),
                                            j.get("compensation_frequency"), j.get("compensation_currency"))
        items.append(it)  # no posted date in the list (3.2): first_seen after 2 polls
    ctx.stop = "one-call"
    return items


# ---------- Teamtailor, Personio, Recruitee ----------

def read_teamtailor(ctx):
    s = ctx.spec
    host = s.host or f"{s.board}.teamtailor.com"
    items, offset = [], 0
    while not ctx.capped():
        url = f"https://{host}/jobs.json" + (f"?offset={offset}&per_page={TEAMTAILOR_PAGE}" if offset else "")
        r = ctx.get(url, accept="application/feed+json,application/json")
        if r.status == 404:
            raise Gone("HTTP 404")
        d = body_json(r, "teamtailor")
        rows = d.get("items") if isinstance(d, dict) else None
        if not isinstance(rows, list):
            raise FetchError("teamtailor: not a JSON Feed")
        new = [j for j in rows if j.get("url") and ctx.fresh(j.get("id") or j["url"])]
        for j in new:
            it = Item(j["url"], {})
            jp = j.get("_jobposting") if isinstance(j.get("_jobposting"), dict) else {}
            it.p = posting.parse_jsonld(jp, j["url"], it.report) if jp else posting.new_posting("teamtailor")
            it.p["source"] = "teamtailor"
            it.p["company"] = s.employer or it.p.get("company") or s.board
            it.p["role"] = it.p.get("role") or j.get("title", "")
            if not it.p.get("description"):
                it.p["description"] = html_text(j.get("content_html"), it)
            if not it.p.get("posted_field"):
                set_posted(it.p, j.get("date_published"), "date_published")
            it.p["apply_url"] = j["url"]
            items.append(it)
        if not rows:
            ctx.stop = "empty"
            break
        if not new:
            ctx.stop = "seen-ids"
            break
        if len(rows) < TEAMTAILOR_PAGE:
            ctx.stop = "short"
            break
        offset += len(rows)
    return items


def read_personio(ctx):
    s = ctx.spec
    host = s.host or f"{s.board}.jobs.personio.de"
    r = ctx.get(f"https://{host}/xml?language=en", accept="application/xml,text/xml")
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    r.check()
    root = parse_xml(r.body, "personio")
    items = []
    for pos in root.iter("position"):
        jid = (pos.findtext("id") or "").strip()
        if not jid or not ctx.fresh(jid):
            continue
        offices = [pos.findtext("office") or ""] + [o.text or "" for o in pos.iter("office")][1:]
        url = f"https://{host}/job/{jid}?language=en"
        it = new_item(s, url, "personio", (pos.findtext("name") or "").strip(),
                      posting.join_nonempty(offices, " | "))
        blocks = [f"<h3>{d.findtext('name') or ''}</h3>{d.findtext('value') or ''}"
                  for d in pos.iter("jobDescription")]
        it.p["description"] = html_text("\n".join(blocks), it)
        set_posted(it.p, pos.findtext("createdAt"), "createdAt")
        items.append(it)
    ctx.stop = "one-call"
    return items


def read_recruitee(ctx):
    """Enabled in poll only after the selftest from the user's PC passes (ats A25)."""
    s = ctx.spec
    r = ctx.get(f"https://{s.board}.recruitee.com/api/offers/")
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    d = body_json(r, "recruitee")
    rows = d.get("offers") if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise FetchError("recruitee: no offers list")
    items = []
    for j in rows:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        url = posting.web_url(j.get("careers_url")) or f"https://{s.board}.recruitee.com/o/{j.get('slug', jid)}"
        it = new_item(s, url, "recruitee", j.get("title"),
                      j.get("location") or place(j.get("city"), j.get("state_code"), j.get("country_code")))
        it.p["remote"] = "remote" if j.get("remote") else ""
        it.p["apply_url"] = posting.web_url(j.get("careers_apply_url")) or url
        it.p["description"] = html_text((j.get("description") or "") + (j.get("requirements") or ""), it)
        set_posted(it.p, j.get("published_at"), "published_at")
        items.append(it)
    ctx.stop = "one-call"
    return items


# ---------- ADP, Oracle, Paylocity, Dayforce ----------

ADP_BASE = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"


def adp_query(s):
    cc = s.extra.get("ccid") or s.site
    return f"cid={quote(s.board)}" + (f"&ccId={quote(cc)}" if cc else "") + "&lang=en_US&locale=en_US"


def read_adp(ctx):
    """Stop on meta.totalNumber or an empty page, never on a short page: a page
    can hold 19 of 20 with more to come (ats A11 correction)."""
    s = ctx.spec
    items, skip, total = [], 0, None
    while not ctx.capped():
        r = ctx.get(f"{ADP_BASE}?{adp_query(s)}&$top={ADP_PAGE}&$skip={skip}")
        d = body_json(r, "adp")
        rows = d.get("jobRequisitions") if isinstance(d, dict) else None
        if not isinstance(rows, list):
            raise FetchError("adp: no jobRequisitions")
        if total is None:
            total = int(((d.get("meta") or {}).get("totalNumber")) or 0)
        new = [j for j in rows if j.get("itemID") and ctx.fresh(j["itemID"])]
        for j in new:
            cc = s.extra.get("ccid") or s.site
            url = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
                   f"cid={s.board}" + (f"&ccId={cc}" if cc else "") + f"&jobId={j['itemID']}&lang=en_US")
            locs = [posting.adp_location(x) for x in j.get("requisitionLocations") or []]
            it = new_item(s, url, "adp-api", j.get("requisitionTitle"), posting.join_nonempty(locs, " | "),
                          j.get("clientRequisitionID"))
            set_posted(it.p, j.get("postDate"), "postDate")
            it.data = {"id": j["itemID"]}
            it.detail = detail_adp
            items.append(it)
        if not rows:
            ctx.stop = "empty"
            break
        if not new:
            ctx.stop = "seen-ids"
            break
        skip += ADP_PAGE
        if total and len(ctx.seen) >= total:
            ctx.stop = "total"
            break
    return items


def detail_adp(ctx, it):
    r = ctx.f.get(f"{ADP_BASE}/{quote(str(it.data['id']))}?{adp_query(ctx.spec)}")
    q = posting.parse_adp(body_json(r, "adp detail"), {"company": it.p["company"], "id": str(it.data["id"])},
                          it.report)
    merge(it, q, keep=("company", "posted", "posted_field", "role", "location"))


def oracle_host(s):
    return s.host or f"{s.board}.fa.us2.oraclecloud.com"


def read_oracle(ctx):
    s = ctx.spec
    host = oracle_host(s)
    if not s.site:
        raise ValueError("oracle needs the siteNumber (board/site)")
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    items = []
    for search in ctx.searches:
        offset, total = 0, None
        while not ctx.capped():
            finder = f"findReqs;siteNumber={s.site},limit={ORACLE_PAGE},offset={offset},sortBy=POSTING_DATES_DESC"
            if search:
                finder += f",keyword=%22{quote(search)}%22"
            if s.extra.get("facet"):
                finder += f",selectedLocationsFacet={quote(s.extra['facet'])}"
            r = ctx.get(f"{api}?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}")
            d = body_json(r, "oracle")
            block = (d.get("items") or [{}])[0] if isinstance(d, dict) else {}
            rows = block.get("requisitionList") or []
            if total is None:
                total = int(block.get("TotalJobsCount") or 0)
            new = [j for j in rows if j.get("Id") and ctx.fresh(j["Id"])]
            for j in new:
                url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{s.site}/job/{j['Id']}"
                others = [x.get("Name", "") for x in j.get("secondaryLocations") or [] if isinstance(x, dict)]
                it = new_item(s, url, "oracle-api", j.get("Title"),
                              posting.join_nonempty([j.get("PrimaryLocation", "")] + others, " | "), j.get("Id"))
                set_posted(it.p, j.get("PostedDate"), "PostedDate")
                it.data = {"id": j["Id"]}
                it.detail = detail_oracle
                items.append(it)
            if not rows:
                ctx.stop = "empty"
                break
            if not new:
                ctx.stop = "seen-ids"
                break
            offset += len(rows)
            if total and offset >= total:
                ctx.stop = "total"
                break
        if ctx.stop == "page-cap":
            break
    return items


def detail_oracle(ctx, it):
    s = ctx.spec
    r = ctx.f.get(f"https://{oracle_host(s)}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                  f"?expand=all&onlyData=true&finder=ById;Id=%22{it.data['id']}%22,siteNumber={s.site}")
    d = body_json(r, "oracle detail")
    if posting.closed_marker("oracle", d):
        return close(it, "no requisition")
    q = posting.parse_oracle(d, {"company": it.p["company"]}, it.report)
    merge(it, q, keep=("company", "posted", "posted_field"))  # PostedDate from the list (3.2)


def read_paylocity(ctx):
    s = ctx.spec
    board = f"https://recruiting.paylocity.com/recruiting/jobs/All/{s.board}" + (f"/{s.site}" if s.site else "")
    r = ctx.get(board, accept="text/html")
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    r.check()
    data = balanced_json(r.text(), "window.pageData")
    if not isinstance(data, dict) or not isinstance(data.get("Jobs"), list):
        raise FetchError("paylocity: no window.pageData.Jobs")
    items = []
    for j in data["Jobs"]:
        jid = str(j.get("JobId", ""))
        if not jid or j.get("IsInternal") or not ctx.fresh(jid):
            continue
        loc = j.get("JobLocation") or {}
        url = f"https://recruiting.paylocity.com/recruiting/jobs/Details/{jid}/{s.board}"
        it = new_item(s, url, "paylocity", j.get("JobTitle"),
                      place(loc.get("City"), loc.get("State")) or j.get("LocationName", ""))
        if j.get("IsRemote"):
            it.p["remote"] = "remote"
        set_posted(it.p, j.get("PublishedDate"), "PublishedDate")
        it.data = {"list_date": True}
        it.detail = detail_page  # one at a time; netfetch honors Retry-After (ats A14)
        items.append(it)
    ctx.stop = "one-call"
    return items


def read_dayforce(ctx):
    """GET the board (cookies), GET /api/auth/csrf, then POST the geo search
    with X-CSRF-TOKEN, 25 per page (ats A13 correction)."""
    s = ctx.spec
    host = s.host or "jobs.dayforcehcm.com"
    page = f"https://{host}/en-US/{s.board}/{s.site}"
    r = ctx.get(page, accept="text/html")
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    tok = body_json(ctx.get(f"https://{host}/api/auth/csrf", headers={"Referer": page}), "dayforce csrf")
    token = tok.get("csrfToken") if isinstance(tok, dict) else None
    if not token:
        raise FetchError("dayforce: no csrfToken")
    items, start, total = [], 0, None
    hdrs = {"X-CSRF-TOKEN": token, "Referer": page}
    while not ctx.capped():
        body = {"clientNamespace": s.board, "jobBoardCode": s.site, "cultureCode": "en-US",
                "distanceUnit": 1, "paginationStart": start}
        d = body_json(ctx.post(f"https://{host}/api/geo/{s.board}/jobposting/search", body, hdrs), "dayforce")
        if not isinstance(d, dict):
            raise FetchError("dayforce: not a search result")
        rows = d.get("jobPostings") or []
        if total is None:
            total = int(d.get("maxCount") or 0)
        new = [j for j in rows if j.get("jobPostingId") and ctx.fresh(j["jobPostingId"])]
        for j in new:
            locs = j.get("postingLocations") or []
            loc = locs[0] if locs else {}
            url = f"https://{host}/en-US/{s.board}/{s.site}/jobs/{j['jobPostingId']}"
            it = new_item(s, url, "dayforce", j.get("jobTitle"),
                          place(loc.get("cityName"), loc.get("stateCode"), loc.get("isoCountryCode"))
                          or loc.get("formattedAddress", ""), str(j.get("jobReqId") or ""))
            if j.get("hasVirtualLocation"):
                it.p["remote"] = "remote"
            it.p["description"] = html_text(j.get("jobDescription"), it)
            set_posted(it.p, j.get("postingStartTimestampUTC"), "postingStartTimestampUTC")
            items.append(it)
        if not rows:
            ctx.stop = "empty"
            break
        if not new:
            ctx.stop = "seen-ids"
            break
        start += len(rows)
        if total and start >= total:
            ctx.stop = "total"
            break
    return items


# ---------- HTML lists with JSON-LD details: Jobvite, JazzHR, Phenom ----------

def read_jobvite(ctx):
    s = ctx.spec
    r = ctx.get(f"https://jobs.jobvite.com/{quote(s.board)}/jobs", accept="text/html")
    check_gone(r, "jobvite", "jobs.jobvite.com")
    r.check()
    items = []
    for url, title in page_links(r.text(), rf"^/{re.escape(s.board)}/job/[A-Za-z0-9]+$", "https://jobs.jobvite.com"):
        if ctx.fresh(url):
            it = new_item(s, url, "jobvite", title)
            it.detail = detail_page
            items.append(it)
    ctx.stop = "one-call"
    return items


def read_jazzhr(ctx):
    s = ctx.spec
    host = f"{s.board}.applytojob.com"
    r = ctx.get(f"https://{host}/apply/jobs", accept="text/html")
    check_gone(r, "jazzhr", host)
    r.check()
    if "Inactive Career Page" in r.text()[:5000]:
        raise Gone("inactive career page")
    items = []
    for url, title in page_links(r.text(), r"/apply/jobs/details/[A-Za-z0-9]+$", f"https://{host}"):
        if ctx.fresh(url):
            it = new_item(s, url, "jazzhr", title)
            it.detail = detail_page
            items.append(it)
    ctx.stop = "one-call"
    return items


def read_phenom(ctx):
    """sitemap.xml (or its index) gives job URLs with lastmod; at most 200 a
    board a day, newest first; each job page carries JSON-LD (ats A23)."""
    s = ctx.spec
    host = s.host or s.board
    r = ctx.get(f"https://{host}/sitemap.xml", accept="application/xml,text/xml")
    r.check()
    kind, rows = sitemap_entries(r.body)
    if kind == "sitemapindex":
        children = [loc for loc, _ in rows if netfetch.host_of(loc) == host]
        jobish = [c for c in children if re.search(r"job|vacanc|posting", c, re.I)] or children
        rows = []
        for child in jobish[:SITEMAP_CHILDREN]:
            if ctx.capped():
                break
            c = ctx.get(child, accept="application/xml,text/xml")
            c.check()
            rows += sitemap_entries(c.body)[1]
    jobs = [(loc, mod) for loc, mod in rows if "/job/" in urlparse(loc).path and netfetch.host_of(loc) == host]
    items = []
    for loc, mod in sorted(jobs, key=lambda x: x[1], reverse=True)[:PHENOM_MAX]:
        if ctx.fresh(loc):
            it = new_item(s, loc, "phenom", "")
            set_posted(it.p, mod, "lastmod")  # provisional; the page's datePosted wins
            it.detail = detail_page
            items.append(it)
    ctx.stop = ctx.stop or "one-call"
    return items


def detail_page(ctx, it):
    """A job page read for its JSON-LD JobPosting (else its visible text)."""
    r = ctx.f.get(it.url, accept="text/html")
    r.check()
    q = posting.parse_html(r.text(), it.url, it.report)
    keep = ("company",)
    if it.data.get("list_date") or not q.get("posted_field"):  # Breezy, Paylocity: the list date (3.2)
        keep += ("posted", "posted_field")
    merge(it, q, keep=keep)
    it.p["role"] = it.p["role"] or q.get("role", "")


# ---------- Comeet, Manatal, Hireology ----------

def read_comeet(ctx):
    s = ctx.spec
    r = ctx.get(f"https://www.comeet.com/jobs/{quote(s.board)}/{quote(s.site)}", accept="text/html")
    if r.status in (404, 410):
        raise Gone(f"HTTP {r.status}")
    r.check()
    rows = balanced_json(r.text(), "COMPANY_POSITIONS_DATA")
    if not isinstance(rows, list):
        raise FetchError("comeet: no COMPANY_POSITIONS_DATA")
    items = []
    for j in rows:
        uid = str(j.get("uid", ""))
        if not uid or not ctx.fresh(uid):
            continue
        loc = j.get("location") or {}
        url = posting.web_url(j.get("url_comeet_hosted_page")) or f"https://www.comeet.com/jobs/{s.board}/{s.site}/x/{uid}"
        it = new_item(s, url, "comeet", j.get("name"), place(loc.get("city"), loc.get("state"), loc.get("country")))
        if loc.get("is_remote"):
            it.p["remote"] = "remote"
        details = ((j.get("custom_fields") or {}).get("details") or [])
        it.p["description"] = html_text("\n".join(f"<h3>{d.get('name', '')}</h3>{d.get('value') or ''}"
                                                  for d in details if isinstance(d, dict)), it)
        items.append(it)  # no posted date: first_seen
    ctx.stop = "one-call"
    return items


def read_manatal(ctx):
    s = ctx.spec
    url = f"https://www.careers-page.com/api/v1.0/c/{quote(s.board)}/jobs/"
    items = []
    while url and not ctx.capped():
        r = ctx.get(url)
        if r.status == 404:
            raise Gone("HTTP 404")
        d = body_json(r, "manatal")
        rows = d.get("results") if isinstance(d, dict) else None
        if not isinstance(rows, list):
            raise FetchError("manatal: no results list")
        new = [j for j in rows if j.get("id") and ctx.fresh(j["id"])]
        for j in new:
            page = f"https://www.careers-page.com/{s.board}/job/{j.get('hash') or j['id']}"
            it = new_item(s, page, "manatal", j.get("position_name"),
                          j.get("location_display") or place(j.get("city"), j.get("state"), j.get("country")))
            it.p["description"] = html_text(j.get("description"), it)
            items.append(it)
        nxt = d.get("next")
        url = nxt if isinstance(nxt, str) and netfetch.host_of(nxt) == "www.careers-page.com" and new else None
        ctx.stop = "next-none" if not nxt else ("seen-ids" if not new else ctx.stop)
    return items


def read_hireology(ctx):
    s = ctx.spec
    r = ctx.get(f"https://api.hireology.com/v2/public/careers/{quote(s.board)}?page_size=500")
    if r.status == 404:
        raise Gone("HTTP 404")
    d = body_json(r, "hireology")
    rows = d.get("data") if isinstance(d, dict) else None
    if not isinstance(rows, list):
        raise FetchError("hireology: no data list")
    items = []
    for j in rows:
        jid = str(j.get("id", ""))
        if not jid or not ctx.fresh(jid):
            continue
        locs = j.get("locations") or []
        loc = locs[0] if locs else {}
        url = posting.web_url(j.get("career_site_url")) or f"https://careers.hireology.com/{s.board}/{jid}/description"
        it = new_item(s, url, "hireology", j.get("name"), place(loc.get("city"), loc.get("state")))
        it.p["remote"] = "remote" if j.get("remote") else ""
        it.p["description"] = html_text(j.get("job_description"), it)
        set_posted(it.p, j.get("created_at"), "created_at")
        items.append(it)
    ctx.stop = "one-call"
    return items


READERS = {
    "greenhouse": read_greenhouse, "lever": read_lever, "lever_eu": read_lever, "ashby": read_ashby,
    "workday": read_workday, "bamboohr": read_bamboohr, "breezy": read_breezy, "workable": read_workable,
    "rippling": read_rippling, "pinpoint": read_pinpoint, "teamtailor": read_teamtailor,
    "personio": read_personio, "recruitee": read_recruitee, "adp": read_adp, "oracle": read_oracle,
    "paylocity": read_paylocity, "dayforce": read_dayforce, "jobvite": read_jobvite, "jazzhr": read_jazzhr,
    "phenom": read_phenom, "comeet": read_comeet, "manatal": read_manatal, "hireology": read_hireology,
}
RECORD_ATS = {"lever_eu": "lever"}


# ---------- records ----------

def merge(it, q, keep=()):
    """Detail values over list values, except the keys named in keep (and
    blanks never overwrite a list value)."""
    for k, v in q.items():
        if k in keep or v in ("", None, []):
            continue
        it.p[k] = v


def close(it, why):
    it.data["closed"] = why


US_STATE_NAMES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
                  "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
                  "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
                  "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
                  "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
                  "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
                  "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
                  "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
                  "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
                  "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
                  "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC"}
US_DASHED = re.compile(r"^\s*(?:united states(?: of america)?|usa|us)\s*-\s*([a-z .]+?)\s*-\s*(.+?)\s*$", re.I)
US_CODED = re.compile(r"^\s*US\s*-\s*([A-Z]{2})\s*-\s*(.+?)\s*$")


def fix_place(rec):
    """Workday and Oracle write 'United States - California - Foster City' or
    'US-CA-Menlo Park'; split_place does not read those, so fix city/state."""
    first = (rec.location or "").split("|")[0]
    m = US_DASHED.match(first)
    if m and m.group(1).lower() in US_STATE_NAMES:
        rec.city, rec.state, rec.country = m.group(2), US_STATE_NAMES[m.group(1).lower()], "US"
    elif US_CODED.match(first):
        m = US_CODED.match(first)
        rec.city, rec.state, rec.country = m.group(2), m.group(1), "US"
    else:
        parts = [x.strip() for x in first.split(",")]
        if len(parts) >= 2 and parts[1].lower() in US_STATE_NAMES:  # 'San Carlos, California, United States'
            rec.city, rec.state, rec.country = parts[0], US_STATE_NAMES[parts[1].lower()], "US"
        elif rec.country.lower() in ("united states", "united states of america", "usa"):
            rec.country = "US"
    if rec.country == "US":
        rec.us_eligible = True


def to_reading(it, spec, first_seen):
    """posting.Reading for one item (list-level or after its detail)."""
    p = it.p
    if not p.get("company"):
        p["company"] = spec.employer or spec.board
    posting.clean_fields(p, it.report)
    ats = RECORD_ATS.get(spec.ats, spec.ats)
    rec = posting.to_record(p, it.url, lane="watch", first_seen=first_seen, ats=ats)
    rec.board = spec.board
    rec.apply_url = p.get("apply_url") or it.url
    fix_place(rec)
    if it.data.get("closed"):
        rec.closed = True
    return posting.Reading(rec, "", p.get("source", ""), posting.ai_lines(p, it.report),
                           list(it.report.hidden), it.report.stripped)


# ---------- one board ----------

def read_board(spec, fetcher, *, searches=(), places=(), keep=None, details=True, max_pages=MAX_PAGES,
               first_seen=None):
    """List one board, then read details for the records keep() accepts.

    keep(posting) -> bool sees list-level jobfs.Posting records (the runner
    passes its list gates); None keeps everything. Never raises for network
    trouble: the result's status says what happened.
    """
    res = BoardResult(spec)
    first_seen = first_seen or jobfs.today().isoformat()
    reader = READERS.get(spec.ats)
    if spec.ats in LEAD_ONLY_ATS or reader is None:
        res.status, res.why = "SKIP", "lead-only" if spec.ats in LEAD_ONLY_ATS else f"no reader for {spec.ats}"
        return res
    ctx = Ctx(fetcher, spec, searches, places, max_pages)
    try:
        items = reader(ctx)
    except Gone as e:
        res.status, res.why = "GONE", str(e)
    except Denied as e:
        res.status, res.why = ("GONE", f"redirect {e.reason.split()[-1]}") if denied_gone(e, spec.ats) else ("DENY", e.reason)
    except (FetchError, ValueError) as e:
        res.status, res.why = "ERROR", str(e)[:200]
    res.pages, res.stop = ctx.pages, ctx.stop
    if res.status != "OK":
        return res
    if not items:
        res.status = "EMPTY"
        return res
    for it in items:
        if details and it.detail and (keep is None or keep(to_reading(clone(it), spec, first_seen).posting)):
            try:
                it.detail(ctx, it)
            except Denied as e:
                res.detail_errors.append(f"{it.url}: denied {e.reason}")
            except (FetchError, ValueError, KeyError, TypeError, AttributeError) as e:
                res.detail_errors.append(f"{it.url}: {str(e)[:120]}")
        res.readings.append(to_reading(it, spec, first_seen))
    return res


def clone(it):
    """A copy for the keep() preview, so cleaning does not touch the real item."""
    return Item(it.url, dict(it.p), None, dict(it.data), posting.Report())


# ---------- watch list, health and poll ----------

def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except OSError:
        return []


def watchlist_rows(root):
    """config/watchlist.csv then state/watchlist_auto.csv, one row per board key."""
    folder, out, seen = jobfs.Folder(root), [], set()
    for path in (folder.watchlist_csv, folder.watchlist_auto_csv):
        for row in read_csv(path):
            spec = spec_from_row(row)
            if spec.ats and spec.board and spec.key not in seen:
                seen.add(spec.key)
                out.append(spec)
    return out


def load_health(root):
    return {r["key"]: r for r in read_csv(jobfs.Folder(root).board_health_csv) if r.get("key")}


def save_health(root, health):
    path = jobfs.Folder(root).board_health_csv
    lines = [",".join(HEALTH_COLUMNS)]
    for key in sorted(health):
        row = health[key]
        lines.append(",".join(csv_cell(row.get(c, "")) for c in HEALTH_COLUMNS))
    jobfs.atomic_write_text(path, "\n".join(lines) + "\n")


def csv_cell(v):
    v = str(v if v is not None else "").replace("\r", " ").replace("\n", " ")
    return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',"') else v


def update_health(health, res, today):
    """3.7: 3 consecutive errors pause a board; a gone marker twice makes it
    gone; an empty list is unknown and never counts toward gone."""
    key = res.spec.key
    row = health.get(key) or {"key": key, "status": "active", "last_ok": "", "last_count": "",
                              "last_error": "", "consecutive_errors": "0"}
    errors = int(row.get("consecutive_errors") or 0)
    if res.status in ("OK", "EMPTY"):
        # dormant (no family match for 60 days) is set by the gates' owner; a good read keeps it
        row.update(status="dormant" if row.get("status") == "dormant" else "active", last_ok=today, last_count=str(len(res.readings)), last_error="",
                   consecutive_errors="0")
    elif res.status == "GONE":
        row["status"] = "gone" if row.get("last_error", "").startswith("gone") else row.get("status") or "active"
        row.update(last_error=f"gone: {res.why}", consecutive_errors=str(errors + 1))
    elif res.status in ("ERROR", "DENY"):
        errors += 1
        row.update(last_error=f"{res.status.lower()}: {res.why}", consecutive_errors=str(errors))
        if res.why.startswith("robots "):
            row["status"] = "robots_blocked"
        elif errors >= 3:
            row["status"] = "paused"
    health[key] = row
    return row


def recruitee_enabled(root):
    """Recruitee reads only after the runner selftest passed it from the user's PC
    (status.json selftest.recruitee = PASS; ats A25)."""
    try:
        data = jobfs.read_json_verified(jobfs.Folder(root).status_json, retries=0)
    except jobfs.NotReady:
        return False
    return str((data.get("selftest") or {}).get("recruitee", "")).upper() == "PASS"


def search_terms(terms):
    """Plain search phrases from title terms. Gate syntax (stem*, a+b, a|b;
    references/discovery.md) means nothing to a server's search box, so the
    parts are split out and the stars dropped: 'trainee+cls|clinical lab*'
    gives 'trainee', 'cls', 'clinical lab'."""
    out = []
    for term in terms or []:
        for part in re.split(r"[+|]", str(term)):
            phrase = " ".join(w.strip("*") for w in part.split() if w.strip("*"))
            if phrase:
                out.append(phrase)
    return list(dict.fromkeys(out))


def family_searches(profile):
    """One search text per active family for servers that filter (Workday
    searchText, Oracle keyword): its title_any words, else its core titles,
    at most 5 words. An empty list means one unfiltered walk."""
    out = []
    for fam in profile.get("family") or []:
        if fam.get("status", "active") != "active":
            continue
        words = search_terms(fam.get("title_any"))
        if not words:
            words = [w for t in fam.get("core_titles") or [] for w in str(t).split()]
        text = " ".join(dict.fromkeys(str(w).strip() for w in words if str(w).strip()))
        text = " ".join(list(dict.fromkeys(text.split()))[:5])
        if text and text not in out:
            out.append(text)
    return out


def poll_skip(spec, health, settings, today, root):
    """Why poll leaves this row alone today, or ''."""
    if spec.ats in LEAD_ONLY_ATS:
        return "lead-only"
    if spec.status not in POLL_STATUSES:
        return spec.status or "inactive"
    if spec.ats == "dayforce" and not settings["sources"].get("dayforce", True):
        return "sources.dayforce off"
    if spec.ats == "recruitee" and not recruitee_enabled(root):
        return "recruitee waits for the selftest"
    state = (health.get(spec.key) or {}).get("status", "active")
    if state in ("gone",):
        return state
    weekly = str(settings["schedule"].get("weekly_day", "monday")).lower()
    if (state in ("paused", "dormant", "robots_blocked") or spec.status == "dormant") \
            and today.strftime("%A").lower() != weekly:
        return f"{state if state != 'active' else spec.status} (polled on {weekly})"
    return ""


def poll(root, fetcher=None, offline=None, keep=None, details=True, max_pages=MAX_PAGES, emit=print):
    """Poll every active watch-list board. Returns the BoardResults and writes
    state/board_health.csv. The runner passes keep (the list gates)."""
    root = Path(root)
    settings = jobfs.settings(root)
    profile = jobfs.search_profile(root)
    today = jobfs.today(root)
    fetcher = fetcher or netfetch.Fetcher(root=root, offline=offline, cookies=True)
    searches = family_searches(profile)
    places = list((profile.get("home") or {}).get("places") or [])
    health = load_health(root)
    results = []
    for spec in watchlist_rows(root):
        why = poll_skip(spec, health, settings, today, root)
        if why:
            res = BoardResult(spec, "SKIP", why)
        else:
            res = read_board(spec, fetcher, searches=searches, places=places, keep=keep, details=details,
                             max_pages=max_pages, first_seen=today.isoformat())
            update_health(health, res, today.isoformat())
        emit(res.line())
        for err in res.detail_errors[:3]:
            emit(f"WARN detail {err}")
        results.append(res)
    save_health(root, health)
    return results


# ---------- CLI ----------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def write_jsonl(path, results):
    lines = []
    for res in results:
        for rd in res.readings:
            rec = rd.posting.to_dict()
            rec["ai_text"], rec["hidden"] = rd.ai_text, rd.hidden
            lines.append(json.dumps(rec, ensure_ascii=True, separators=(",", ":")))
    jobfs.atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def cmd_fetch(args):
    try:
        spec = parse_spec(args.board, args.extra or "")
    except ValueError as e:
        print(f"ERROR {e}")
        return 1
    if spec.ats not in READERS and spec.ats not in LEAD_ONLY_ATS:
        print(f"ERROR unknown ats {spec.ats}; one of {', '.join(sorted(READERS))}")
        return 1
    root = Path(args.root) if args.root else None
    extra = tuple(h for h in (spec.host,) if h)
    f = netfetch.Fetcher(root=root, offline=args.offline, cookies=True, extra_hosts=extra)
    profile = jobfs.search_profile(root) if root else {}
    try:
        res = read_board(spec, f, searches=family_searches(profile) if root else (),
                         places=((profile.get("home") or {}).get("places") or []) if root else (),
                         details=not args.list_only, max_pages=args.max_pages,
                         first_seen=jobfs.today(root).isoformat())
    except ValueError as e:
        print(f"ERROR {e}")
        return 1
    print(res.line())
    for err in res.detail_errors[:5]:
        print(f"WARN detail {err}")
    if args.out:
        write_jsonl(args.out, [res])
        print(f"WROTE {args.out}")
    return {"OK": 0, "EMPTY": 0, "DENY": netfetch.EXIT_DENY, "SKIP": 0}.get(res.status, 3)


def cmd_poll(args):
    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR no folder {root}")
        return 1
    results = poll(root, offline=args.offline, details=not args.list_only, max_pages=args.max_pages)
    if args.out:
        write_jsonl(args.out, results)
        print(f"WROTE {args.out}")
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("POLL " + " ".join(f"{k.lower()}={v}" for k, v in sorted(counts.items())) if counts else "POLL boards=0")
    return 0


def main(argv=None):
    ap = Parser(description="Watch-list board readers (job_skill v1.2 runner).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="read one board")
    f.add_argument("--board", required=True, help="<ats>:<board>[/<site>][@<host>]")
    f.add_argument("--extra", help="k=v;k=v (search=, facet=, ccid=, mode=sitemap)")
    f.add_argument("--root", help="JobSearch folder: search terms, places, robots cache, watch-list hosts")
    f.add_argument("--offline", help="fixture folder with index.json instead of the network")
    f.add_argument("--out", help="write the postings as JSONL")
    f.add_argument("--list-only", action="store_true", help="no detail calls")
    f.add_argument("--max-pages", type=int, default=MAX_PAGES)
    p = sub.add_parser("poll", help="read every active watch-list board")
    p.add_argument("--root", required=True)
    p.add_argument("--offline")
    p.add_argument("--out")
    p.add_argument("--list-only", action="store_true")
    p.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = ap.parse_args(argv)
    return cmd_fetch(args) if args.cmd == "fetch" else cmd_poll(args)


if __name__ == "__main__":
    sys.exit(main())
