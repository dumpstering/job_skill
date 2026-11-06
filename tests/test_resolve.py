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

        # day 2: cached misses make no request; unresolved leads give up
        f2 = fetcher(root)
        lines = []
        out2 = resolve.Resolver(root, D2, f2, emit=lines.append).run()
        made = probes(f2)
        assert [u for u in made if "betabio" in u] == ["https://betabio.jobs.personio.de/xml?language=en"], made
        assert sum(1 for ln in lines if " CACHED miss" in ln) == 11
        diy = {d["company"] for d in out2.diy}
        assert diy == {"Betabio", "Zeta Pharma"}, out2.diy
        assert out2.discarded == [adzuna_lead(root)["lead_id"]], out2.discarded
        text = (root / "queue" / D2.isoformat() / "diy.md").read_text(encoding="utf-8")
        assert "- E1 |" in text and "Betabio" in text and "http" not in text, text
        rows = list(csv.DictReader(open(root / "queue" / D2.isoformat() / "discards.csv", encoding="utf-8")))
        assert [(r["company"], r["gate"], r["reason"], r["lane"]) for r in rows] == \
            [("Globex Bio", "resolve", "unresolved", "adzuna")], rows
        # a third run has nothing pending
        lines = []
        resolve.Resolver(root, date(2026, 11, 14), fetcher(root), emit=lines.append).run()
        assert lines[-1].startswith("RESOLVE leads=0"), lines


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


def test_helpers():
    assert resolve.slug_candidates("Acme Biosciences, Inc.") == ["acmebiosciencesinc", "acmebiosciences",
                                                                  "acme-biosciences", "acme"]
    assert resolve.slug_candidates("Synthego") == ["synthego"]
    assert resolve.lane_of("linkedin_alert") == "alert:linkedin" and resolve.lane_of("biospace_alert") == "alert:biospace"
    found = resolve.detect('<a href="https://wd1.myworkdaysite.com/en-US/recruiting/scu/scu">jobs</a> '
                           '<script src="https://boards.greenhouse.io/embed/job_board/js?for=gamma"></script>')
    assert [(d["ats"], d["board"], d["site"], d["host"]) for d in found] == \
        [("workday", "scu", "scu", "wd1.myworkdaysite.com"), ("greenhouse", "gamma", "", "")], found


def main():
    for fn in (test_two_runs, test_probe_url, test_helpers):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
