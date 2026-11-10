"""Check job/scripts/resolve.py: lead collapse, known-board matches, slug probes
(order, misses, unknown, the 30-day miss cache), needs_search.json, probe_url
(job-board refusal, careers-page detection, robots-blocked R3 postings), and the
2-day give-up (LinkedIn to the do-yourself list, others to discards).

Usage: python tests/test_resolve.py
Offline only (netfetch fixtures in tests/fixtures/resolve). Prints ok <name> per test and PASS last.
"""
import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
FIX = HERE / "fixtures" / "resolve"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-12T15:00:00Z"
import aggregators  # noqa: E402
import boards  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import leads  # noqa: E402
import netfetch  # noqa: E402
import resolve  # noqa: E402

D1, D2 = date(2026, 11, 12), date(2026, 11, 13)
WATCH = ("employer,employer_norm,parent,ats,board,host,site,extra,careers_url,main_website,segment,city,families,"
         "priority,status,added_on,added_by,ref,notes\n"
         "Acme Biosciences,acme biosciences,,greenhouse,acmebio,,,,,,biotech,,,1,active,2026-11-01,seed,,\n"
         "Globex Bio,globex bio,,lever,globexbio,,,,,,biotech,,,2,active,2026-11-01,seed,,\n"
         "Zeta Pharma,zeta pharma,,smartrecruiters,ZetaPharma,,,,,,pharma,,,2,lead_only,2026-11-01,seed,,R3 host\n")
CARDS = {"messages": [{"id": "<digest-1@mail.example.com>", "cards": [
    {"company": "Acme Biosciences, Inc.", "title": "Scientist II, Microbiology", "location": "South San Francisco, CA"},
    {"company": "Globex Bio", "title": "Research Associate II", "location": "Fremont, CA"},
    {"company": "Betabio", "title": "QC Microbiologist", "location": "South San Francisco, CA"},
    {"company": "Zeta Pharma", "title": "QC Microbiologist", "location": "Fremont, CA"}]},
    {"id": "<digest-2@mail.example.com>", "cards": [
        {"company": "Acme Biosciences", "title": "Scientist II, Microbiology", "location": "South San Francisco, CA"}]}]}
BETA_ORDER = [
    "https://boards-api.greenhouse.io/v1/boards/betabio/jobs?content=true",
    "https://api.lever.co/v0/postings/betabio?mode=json&skip=0&limit=100",
    "https://api.ashbyhq.com/posting-api/job-board/betabio?includeCompensation=true",
    "https://apply.workable.com/api/v1/widget/accounts/betabio?details=true",
    "https://betabio.bamboohr.com/careers/list",
    "https://betabio.breezy.hr/json",
    "https://betabio.applytojob.com/apply/jobs",
    "https://betabio.pinpointhq.com/postings.json",
    "https://betabio.jobs.personio.de/xml?language=en",
    "https://betabio.teamtailor.com/jobs.json",
    "https://api.rippling.com/platform/api/ats/v1/board/betabio/jobs",
    "https://jobs.jobvite.com/betabio/jobs",
]


def make_root(tmp):
    root = Path(tmp)
    (root / "config").mkdir(parents=True)
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "M"\n', encoding="utf-8")
    (root / "config" / "search_profile.toml").write_text(
        'version = 1\n[home]\nplaces = ["South San Francisco", "Fremont", "Newark"]\n', encoding="utf-8")
    (root / "config" / "watchlist.csv").write_text(WATCH, encoding="utf-8")
    cards = root / "scratch.json"
    cards.write_text(json.dumps(CARDS), encoding="utf-8")
    out = root / "runner" / "in" / "leads" / "2026-11-12_0700_linkedin.jsonl"
    with contextlib.redirect_stdout(io.StringIO()):
        code = leads.main(["validate", "--source", "linkedin_alert", "--in", str(cards), "--out", str(out),
                           "--root", str(root), "--seen", D1.isoformat()])
    assert code == 0
    cards.unlink()
    return root


def fetcher(root):
    return netfetch.Fetcher(root=root, offline=FIX, cookies=True)


def probes(f):
    return [u for m, u in f.offline.requests if not u.endswith("/robots.txt")]


def lead_ids(root):
    path = root / "runner" / "in" / "leads" / "2026-11-12_0700_linkedin.jsonl"
    return {(r["company"], r["title"]): r["lead_id"] for r in jobfs.read_jsonl(path)}


def adzuna_lead(root):
    return aggregators.make_lead("adzuna", "Globex Bio", "Chief Fun Officer", "Fremont, CA", D1.isoformat(),
                                 leads.salt(root))


def test_two_runs():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(tmp)
        ids = lead_ids(root)
        f = fetcher(root)
        # today's poll already read the Acme board (list level)
        poll = boards.read_board(boards.parse_spec("greenhouse:acmebio"), f, details=False)
        poll.spec.employer = "Acme Biosciences"
        for r in poll.readings:
            r.posting.company = "Acme Biosciences"
        before = len(probes(f))
        lines = []
        out = resolve.Resolver(root, D1, f, emit=lines.append).run(readings=poll.readings, extra_leads=[adzuna_lead(root)])
        made = probes(f)[before:]
        # the Acme lead (two cards from two digests collapse) resolves to the board posting, with no new request
        acme = ids[("Acme Biosciences, Inc.", "Scientist II, Microbiology")]
        assert out.resolved[acme] == ["greenhouse:900101"], out.resolved
        assert out.resolved[ids[("Acme Biosciences", "Scientist II, Microbiology")]] == ["greenhouse:900101"]
        assert not any("acmebio" in u for u in made), made
        assert poll.readings[0].posting.lead_ids and acme in poll.readings[0].posting.lead_ids
        # Globex: two matching titles (same city, and a home place) are two postings
        globex = ids[("Globex Bio", "Research Associate II")]
        assert sorted(out.resolved[globex]) == ["lever:00000000-0000-4000-8000-00000000a001",
                                                "lever:00000000-0000-4000-8000-00000000a002"], out.resolved
        new = {r.posting.job_key: r.posting for r in out.readings}
        assert len(new) == 2 and all(p.lane == "alert:linkedin" and globex in p.lead_ids for p in new.values())
        # Betabio: probes in the stated order (Recruitee waits for its selftest)
        beta = [u for u in made if "betabio" in u]
        assert beta == BETA_ORDER, beta
        status = {ln.split()[1]: ln.split()[2] for ln in lines if ln.startswith("PROBE ")}
        assert status["bamboohr:betabio"] == "MISS" and status["jazzhr:betabio"] == "MISS", status
        assert status["jobvite:betabio"] == "MISS" and status["personio:betabio"] == "UNKNOWN", status
        cache = list(csv.DictReader(open(root / "state" / "probe_cache.csv", encoding="utf-8")))
        assert {r["ats"]: r["result"] for r in cache}["personio"] == "unknown"
        assert sum(1 for r in cache if r["result"] == "miss") == 11, cache
        needs = json.loads((root / "runner" / "out" / "needs_search.json").read_text(encoding="utf-8"))
        assert {c["company_norm"]: c["why"] for c in needs["companies"]} == {"betabio": "unknown",
                                                                             "zeta pharma": "lead_only"}, needs
        assert any(ln.startswith("RESOLVE leads=6 groups=5 resolved=3") for ln in lines), lines

        # day 2: cached misses make no request; the lead with no search to wait
        # for gives up, while the two web-search leads stay listed one more day:
        # day 1's listing came from a tick after job-morning had read the file.
        f2 = fetcher(root)
        lines = []
        out2 = resolve.Resolver(root, D2, f2, emit=lines.append).run()
        made = probes(f2)
        assert [u for u in made if "betabio" in u] == ["https://betabio.jobs.personio.de/xml?language=en"], made
        assert sum(1 for ln in lines if " CACHED miss" in ln) == 11
        assert out2.diy == [], out2.diy
        assert out2.discarded == [adzuna_lead(root)["lead_id"]], out2.discarded
        needs = json.loads((root / "runner" / "out" / "needs_search.json").read_text(encoding="utf-8"))
        assert needs["date"] == D2.isoformat() and {c["company_norm"] for c in needs["companies"]} == \
            {"betabio", "zeta pharma"}, needs
        rows = list(csv.DictReader(open(root / "queue" / D2.isoformat() / "discards.csv", encoding="utf-8")))
        assert [(r["company"], r["gate"], r["reason"], r["lane"]) for r in rows] == \
            [("Globex Bio", "resolve", "unresolved", "adzuna")], rows
        # day 3: listed on two days and still unknown: LinkedIn leads go to the do-yourself list
        d3 = date(2026, 11, 14)
        out3 = resolve.Resolver(root, d3, fetcher(root), emit=lambda s: None).run()
        assert {d["company"] for d in out3.diy} == {"Betabio", "Zeta Pharma"}, out3.diy
        text = (root / "queue" / d3.isoformat() / "diy.md").read_text(encoding="utf-8")
        assert "- E1 |" in text and "Betabio" in text and "http" not in text, text
        # a fourth run has nothing pending
        lines = []
        resolve.Resolver(root, date(2026, 11, 15), fetcher(root), emit=lines.append).run()
        assert lines[-1].startswith("RESOLVE leads=0"), lines


def test_expired_lead_not_dropped():
    """A lead still pending 30 days after it was seen gives up like any other
    (do-yourself or a discards.csv line), never vanishing silently."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(tmp)
        old = aggregators.make_lead("adzuna", "Omega Bio", "QC Microbiologist", "Fremont, CA", "2026-09-01",
                                    leads.salt(root))
        lines = []
        out = resolve.Resolver(root, D1, fetcher(root), emit=lines.append).run(extra_leads=[old])
        assert out.discarded == [old["lead_id"]], (out.discarded, lines)
        assert any(ln.startswith(f"UNRESOLVED {old['lead_id']} Omega Bio") and ln.endswith("expired")
                   for ln in lines), lines
        rows = list(csv.DictReader(open(root / "queue" / D1.isoformat() / "discards.csv", encoding="utf-8")))
        assert [(r["company"], r["reason"]) for r in rows] == [("Omega Bio", "unresolved")], rows


def test_probe_url():
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(tmp)
        for url, deny in (("https://www.linkedin.com/jobs/view/4401234567", "job-board linkedin.com"),
                          ("https://www.indeed.com/viewjob?jk=abc", "job-board indeed.com"),
                          ("https://uk.indeed.com/viewjob?jk=abc", "job-board indeed.com")):
            f, lines = fetcher(root), []
            r = resolve.probe_url(root, url, "Acme", fetcher=f, today=D1, emit=lines.append)
            assert r["deny"] == deny and f.offline.requests == [] and lines == [f"DENY {deny}"], (url, r, lines)
        f, lines = fetcher(root), []
        r = resolve.probe_url(root, "https://careers.deltabio.example/jobs", "Delta Bio", fetcher=f, today=D1,
                              emit=lines.append)
        assert "DETECT greenhouse:deltabio" in lines and r["ok"], lines
        f, lines = fetcher(root), []
        resolve.probe_url(root, "https://www.epsilonbio.example/careers", "Epsilon Bio", fetcher=f, today=D1,
                          emit=lines.append)
        assert "DETECT workday:epsilonbio/EpsilonCareers@epsilonbio.wd5.myworkdayjobs.com" in lines, lines
        known = json.loads((root / "state" / "company_ats.json").read_text(encoding="utf-8"))
        assert known["delta bio"]["board"] == "deltabio" and known["epsilon bio"]["site"] == "EpsilonCareers"
        auto = list(csv.DictReader(open(root / "state" / "watchlist_auto.csv", encoding="utf-8")))
        assert {(r["ats"], r["board"], r["added_by"]) for r in auto} == {("greenhouse", "deltabio", "lead"),
                                                                         ("workday", "epsilonbio", "lead")}, auto
        # R3: a SmartRecruiters page whose robots.txt disallows it is read in the browser
        f, lines = fetcher(root), []
        url = "https://jobs.smartrecruiters.com/ZetaPharma/744000-qc-microbiologist"
        r = resolve.probe_url(root, url, "Zeta Pharma", "QC Microbiologist", "Fremont, CA", fetcher=f, today=D1,
                              emit=lines.append)
        assert f"BROWSER_READ {url}" in lines and r["readings"][0].source == "browser_read", lines
        p = r["readings"][0].posting
        assert p.job_key == "smartrecruiters:zetapharma:744000" and p.company == "Zeta Pharma" and p.city == "Fremont"
        assert [u for _, u in f.offline.requests] == ["https://jobs.smartrecruiters.com/robots.txt"], f.offline.requests
        # the CLI form
        cmd = [sys.executable, str(SCRIPTS / "resolve.py"), "probe-url", "--root", str(root), "--url",
               "https://www.linkedin.com/jobs/view/1", "--offline", str(FIX), "--date", D1.isoformat()]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 7 and res.stdout.strip() == "DENY job-board linkedin.com", res.stdout


PHENOM_FIX = HERE / "fixtures" / "resolve_phenom"


def test_phenom_landing_page():
    """L4-01: Sutter Health and Eli Lilly run Phenom career sites over Workday
    tenants. Their landing pages (the '<company> careers' search result) name
    no ATS, so probe_url said DETECT none and every lead went to the
    do-yourself list. A Phenom page with no ATS link now leads to one more
    same-host page, the site's own job search (from its JSON-LD SearchAction),
    whose apply links name the tenant, and the company is remembered. A job
    page names it at once (no second request). Fixtures: trimmed public pages."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(tmp)
        for company, url, search, want, board, site, host in (
                ("Sutter Health", "https://jobs.sutterhealth.org/us/en",
                 "https://jobs.sutterhealth.org/us/en/search-results?keywords=",
                 "DETECT workday:sutterhealth/SH@wd1.myworkdaysite.com", "sutterhealth", "SH", "wd1.myworkdaysite.com"),
                ("Eli Lilly and Company", "https://careers.lilly.com/us/en",
                 "https://careers.lilly.com/us/en/search-results?keywords=",
                 "DETECT workday:lilly/LLY@lilly.wd115.myworkdayjobs.com", "lilly", "LLY",
                 "lilly.wd115.myworkdayjobs.com")):
            f, lines = netfetch.Fetcher(root=root, offline=PHENOM_FIX, cookies=True), []
            r = resolve.probe_url(root, url, company, "Lab Associate II", "Livermore, CA", fetcher=f, today=D1,
                                  emit=lines.append)
            assert lines == [f"PHENOM {search}", want], lines
            assert r["ok"] and probes(f) == [url, search], probes(f)
            known = json.loads((root / "state" / "company_ats.json").read_text(encoding="utf-8"))
            c = known[jobkey.norm_company(company)]
            assert (c["ats"], c["board"], c["site"], c["host"], c["how"]) == ("workday", board, site, host,
                                                                              "probe_url"), c
            specs = resolve.Resolver(root, D1, f, emit=lambda s: None).known_specs(jobkey.norm_company(company))
            assert [(s.ats, s.board, s.site, s.host) for s in specs] == [("workday", board, site, host)], specs
        job = "https://jobs.sutterhealth.org/us/en/job/R-144891/Lab-Associate-III-Microbiology-Short-Hour"
        f, lines = netfetch.Fetcher(root=root, offline=PHENOM_FIX, cookies=True), []
        resolve.probe_url(root, job, "Sutter Health", fetcher=f, today=D1, emit=lines.append)
        assert lines == ["DETECT workday:sutterhealth/SH@wd1.myworkdaysite.com"] and probes(f) == [job], lines
    # only Phenom pages are followed, and only to their own host
    home = (PHENOM_FIX / "sutter_home.html").read_text(encoding="ascii")
    assert resolve.phenom_next(home.replace("cdn.phenompeople.com", "cdn.example.com"),
                               "https://jobs.sutterhealth.org/us/en") is None
    moved = home.replace("https://jobs.sutterhealth.org/us/en/", "https://jobs.other.example/us/en/")
    assert resolve.phenom_next(moved, "https://jobs.sutterhealth.org/us/en") is None, "another host is never read"
    assert resolve.phenom_next('<link href="https://cdn.phenompeople.com/x.css"><a href="/us/en/job/R-1/QC">QC</a>',
                               "https://jobs.example-bio.com/us/en") == "https://jobs.example-bio.com/us/en/job/R-1/QC"
    assert resolve.phenom_next("<p>Careers</p>", "https://www.example-bio.com/careers") is None


def test_helpers():
    assert resolve.slug_candidates("Acme Biosciences, Inc.") == ["acmebiosciencesinc", "acmebiosciences",
                                                                  "acme-biosciences", "acme"]
    assert resolve.slug_candidates("Synthego") == ["synthego"]
    assert resolve.lane_of("linkedin_alert") == "alert:linkedin" and resolve.lane_of("biospace_alert") == "alert:biospace"
    found = resolve.detect('<a href="https://wd1.myworkdaysite.com/en-US/recruiting/scu/scu">jobs</a> '
                           '<script src="https://boards.greenhouse.io/embed/job_board/js?for=gamma"></script>')
    assert [(d["ats"], d["board"], d["site"], d["host"]) for d in found] == \
        [("workday", "scu", "scu", "wd1.myworkdaysite.com"), ("greenhouse", "gamma", "", "")], found
    # An Ashby board name with a space is linked as %20: the spec holds the name
    # itself (read_ashby quotes it once), and an encoded slash is not a board.
    found = resolve.detect('<a href="https://jobs.ashbyhq.com/Nautilus%20Biotechnology">Jobs</a> '
                           '<a href="https://jobs.ashbyhq.com/evil%2F..%2Fx">x</a>')
    assert [(d["ats"], d["board"]) for d in found] == [("ashby", "Nautilus Biotechnology")], found


def test_detect_linear():
    """A careers page holding a long run of letters (or many unclosed iframe
    tags) is scanned in linear time: the old detect patterns tried every
    start position to the end (6 s for 8,000 letters; hours for 2 MB)."""
    import time
    for page in ("a" * 400000, "<iframe " * 50000, "workforcenow.adp.com/" * 20000, "a." * 200000):
        t = time.perf_counter()
        resolve.detect(page)
        assert time.perf_counter() - t < 3.0, (page[:20], time.perf_counter() - t)
    found = resolve.detect('<iframe title="jobs" src="https://acme.wd5.myworkdayjobs.com/en-US/Careers"></iframe>')
    assert [(d["ats"], d["board"], d["site"]) for d in found] == [("workday", "acme", "Careers")], found


def test_detect_bounded():
    """D3: detect reads only the URL-like tokens around ATS host names, from
    the first 512 KB, with bounded patterns. Each 1 MB hostile page (long
    letter runs, thousands of ATS names with no board, unclosed tags, dotted
    runs) is scanned in under 2 seconds; real links are still found, in page
    order, including JSON's escaped slashes (a percent-encoded link inside a
    query string is not a board link)."""
    import time
    mb = 1_000_000
    pages = {"letters": "a" * mb, "dotted": "a." * (mb // 2), "lever names": "lever.co" * (mb // 8),
             "workday names": ("x" * 60 + ".wd1.myworkdayjobs.com/") * (mb // 84),
             "unclosed iframes": "<iframe " * (mb // 8), "adp no cid": "workforcenow.adp.com/" * (mb // 21),
             "dash runs": ("-" * 5000 + ".bamboohr.com ") * (mb // 5014)}
    for name, page in pages.items():
        t = time.perf_counter()
        resolve.detect(page)
        took = time.perf_counter() - t
        assert took < 2.0, (name, took)
    page = ('<script>var cfg = {"url":"https:\\/\\/acme.wd5.myworkdayjobs.com\\/en-US\\/AcmeCareers"};</script>'
            '<a href="https://go.example/r?u=https%3A%2F%2Fjobs.lever.co%2Fglobex">x</a>'
            '<p>' + "filler " * 20 + '</p><a href="https://boards.greenhouse.io/initech">jobs</a>')
    found = resolve.detect(page)
    assert [(d["ats"], d["board"]) for d in found] == [("workday", "acme"), ("greenhouse", "initech")], found
    assert [(d["ats"], d["board"]) for d in resolve.detect("https://jobs.lever.co/globex/123")] == [("lever", "globex")]
    # past the first 512 KB nothing is read
    late = " " * (resolve.HTML_SCAN_MAX + 10) + '<a href="https://boards.greenhouse.io/late">x</a>'
    assert resolve.detect(late) == []


def test_one_lead_fails_soft():
    """D2: an exception while resolving one lead is one ERROR line naming the
    lead and the error type; the other leads are still resolved, the failing
    lead stays pending (no try counted), and a lead file nested thousands
    deep or a broken leads_state.json never stops the run."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_root(tmp)
        (root / "runner" / "in" / "leads" / "2026-11-12_0800_odd.jsonl").write_text(
            "[" * 50000 + "]" * 50000 + "\n" + '{"lead_id": ["x"], "company": "A", "title": "B"}\n', encoding="ascii")
        (root / "state").mkdir(exist_ok=True)
        (root / "state" / "leads_state.json").write_text('{"leads": {"zz": "not a lead"}}', encoding="ascii")
        res = resolve.Resolver(root, D1, fetcher(root), emit=lambda s: None)
        real, lines = res.resolve_group, []
        res.emit = lines.append

        def flaky(lead, cnorm, readings, out):
            if cnorm == "globex bio":
                raise KeyError("odd")
            return real(lead, cnorm, readings, out)
        res.resolve_group = flaky
        out = res.run()
        err = [ln for ln in lines if ln.startswith("ERROR lead ")]
        assert len(err) == 1 and err[0].endswith("Globex Bio: KeyError"), lines
        assert any(ln.startswith("RESOLVED ") for ln in lines) and lines[-1].startswith("RESOLVE leads="), lines
        state = json.loads((root / "state" / "leads_state.json").read_text(encoding="utf-8"))["leads"]
        globex = [v for v in state.values() if v["company"] == "Globex Bio"]
        assert globex and all(v["status"] == "pending" and v["tries"] == [] for v in globex), globex
        assert "zz" not in state and out.resolved, state.keys()


def main():
    for fn in (test_two_runs, test_expired_lead_not_dropped, test_probe_url, test_phenom_landing_page, test_helpers,
               test_detect_linear, test_detect_bounded, test_one_lead_fails_soft):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
