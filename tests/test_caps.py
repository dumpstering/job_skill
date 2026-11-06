"""Check job/scripts/caps.py: the pure caps_verdict (spec 3.9) and `caps.py check`
on a fixture folder built with tracker.py.

Usage: python tests/test_caps.py
Needs openpyxl (to build the fixture). Prints ok <name> per test and PASS last.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-20T18:00:00Z"
import caps  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import tracker  # noqa: E402

D = date(2026, 11, 20)
RULES = {"per_company_30d": 3, "per_family_per_company_30d": 1, "reject_reapply_days": 90,
         "ashby_one_open": True, "per_company_per_day": 2, "parents": {},
         "employer": [{"name": "Google", "match": ["google"], "per_30d": 3, "same_job_reapply_days": 90}]}
GLOBEX = ("Globex is looking for a data analyst to join the reporting group. You will build weekly dashboards "
          "for the finance team, clean source extracts, document every metric definition, and answer ad hoc "
          "questions from product managers. The ideal candidate writes clear SQL, explains tradeoffs plainly, "
          "and enjoys working with people who do not read code. Experience with a warehouse such as Snowflake "
          "or BigQuery is helpful but not required for this role. ") * 2


def E(company, day, status="Applied", family="", job_key="", host="", req_ids=(), simhash="", rejected_on=None):
    return {"company_norm": jobkey.norm_company(company), "family": family, "job_key": job_key, "host": host,
            "ats": "", "status": status, "date": day, "rejected_on": rejected_on, "req_ids": set(req_ids),
            "simhash": simhash}


def verdict(index, company, family="", job_key="", req_ids=(), host="", today=D, rules=RULES, simhash=None):
    return caps.caps_verdict(index, jobkey.norm_company(company), family, job_key, set(req_ids), host, today,
                             rules, simhash=simhash)


def line(*a, **k):
    return caps.verdict_line(verdict(*a, **k))


def test_company_and_family():
    first = date(2026, 11, 1)
    idx = [E("Acme Bio", first, family="a"), E("Acme Bio", first + timedelta(5), family="b"),
           E("Acme Bio, Inc.", first + timedelta(9), family="c")]
    assert verdict(idx[:2], "Acme Bio", "d") == ("ALLOW", None)
    v = verdict(idx, "Acme Bio", "d", job_key="greenhouse:4")
    assert v == ("DEFER", "company 3/3", first + timedelta(30)), v
    assert line(idx, "Acme Bio", "d") == "CAP DEFER company 3/3 until 2026-12-01"
    # the window rolls: on the day the first leaves, one more is allowed
    assert verdict(idx, "Acme Bio", "d", today=first + timedelta(30)) == ("ALLOW", None)
    assert line([E("Initech", date(2026, 11, 10), family="qc_micro")], "Initech", "qc_micro") == \
        "CAP DEFER family qc_micro 1/1 until 2026-12-10"
    assert verdict([E("Initech", date(2026, 11, 10), family="qc_micro")], "Initech", "other") == ("ALLOW", None)
    # the job being checked does not count against itself
    keyed = [dict(e, job_key=f"greenhouse:{i}") for i, e in enumerate(idx)]
    assert verdict(keyed, "Acme Bio", "d", job_key="greenhouse:2", today=first + timedelta(20)) == ("ALLOW", None)


def test_google():
    days = [date(2026, 11, 2), date(2026, 11, 10), date(2026, 11, 15)]
    idx = [E("Google LLC", d, family="qc_micro", job_key=f"greenhouse:{i}") for i, d in enumerate(days)]
    assert line(idx, "Google", "other") == "CAP DEFER google 3/3 until 2026-12-02"
    assert verdict(idx[:2], "Google", "other") == ("ALLOW", None)
    applied = date(2026, 6, 1)
    one = [E("Google", applied, job_key="workday:google:R-1")]
    assert line(one, "Google", job_key="workday:google:R-1", today=applied + timedelta(89)) == \
        f"CAP DEFER google same-job until {(applied + timedelta(90)).isoformat()}"
    assert verdict(one, "Google", job_key="workday:google:R-1", today=applied + timedelta(91)) == ("ALLOW", None)
    assert verdict(one, "Google", job_key="workday:google:R-2", today=applied + timedelta(10)) == ("ALLOW", None)


def test_rejected_repost():
    rejected = date(2026, 8, 1)
    sh_old = jobkey.simhash64(GLOBEX)
    sh_new = jobkey.simhash64(GLOBEX.replace("Globex is looking", "Our client is looking"))
    assert jobkey.hamming(sh_old, sh_new) <= 3
    idx = [E("Globex", date(2026, 7, 1), status="Rejected", job_key="lever:old", simhash=sh_old,
             req_ids={"R-555"}, rejected_on=rejected)]
    v = verdict(idx, "Staffing Partners", job_key="url:agency/1", today=rejected + timedelta(89), simhash=sh_new)
    assert v == ("DEFER", "rejected-block", rejected + timedelta(90)), v
    assert verdict(idx, "Staffing Partners", job_key="url:agency/1", today=rejected + timedelta(91),
                   simhash=sh_new) == ("ALLOW", None)
    # the same requisition at the same company, and the same job key, are blocked too
    assert verdict(idx, "Globex", req_ids={"R-555"}, today=rejected + timedelta(30))[1] == "rejected-block"
    assert verdict(idx, "Globex", job_key="lever:old", today=rejected + timedelta(30))[1] == "rejected-block"
    assert verdict(idx, "Initech", req_ids={"R-555"}, today=rejected + timedelta(30)) == ("ALLOW", None)
    unrelated = jobkey.simhash64("Pastry chef wanted for a busy bakery; early mornings, croissants. " * 4)
    assert verdict(idx, "Bakery", today=rejected + timedelta(30), simhash=unrelated) == ("ALLOW", None)


def test_ashby_and_day():
    idx = [E("Ashworth Bio", D - timedelta(3), status="Filled - awaiting submit", job_key="ashby:aaaa",
             host="jobs.ashbyhq.com")]
    assert line(idx, "Ashworth Bio", job_key="ashby:bbbb", host="jobs.ashbyhq.com") == "CAP DEFER ashby-open ashworth bio"
    assert verdict(idx, "Ashworth Bio", job_key="greenhouse:9", host="boards.greenhouse.io") == ("ALLOW", None)
    closed = [dict(idx[0], status="Rejected")]
    assert verdict(closed, "Ashworth Bio", job_key="ashby:bbbb", host="jobs.ashbyhq.com") == ("ALLOW", None)
    off = dict(RULES, ashby_one_open=False)
    assert verdict(idx, "Ashworth Bio", job_key="ashby:bbbb", host="jobs.ashbyhq.com", rules=off) == ("ALLOW", None)
    today2 = [E("Umbrella", D, status="Queued", job_key="q:1"), E("Umbrella", D, job_key="q:2")]
    assert line(today2, "Umbrella", job_key="q:3") == "CAP DEFER company-day 2/2 until 2026-11-21"


def test_parent():
    rules = dict(RULES, parents={"genentech": "roche", "roche diagnostics": "roche", "roche": "roche"})
    idx = [E("Genentech", date(2026, 11, 3)), E("Genentech", date(2026, 11, 6)),
           E("Roche Diagnostics", date(2026, 11, 9))]
    assert line(idx, "Genentech", rules=rules) == "CAP DEFER company 3/3 until 2026-12-03"
    assert line(idx, "Roche", rules=rules) == "CAP DEFER company 3/3 until 2026-12-03"
    assert verdict(idx, "Genentech") == ("ALLOW", None), "without the parent link they are separate"


def tr(root, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = tracker.main([args[0], "--root", str(root)] + [str(a) for a in args[1:]])
    assert code == 0, (args, out.getvalue())
    return out.getvalue()


def build_fixture():
    root = Path(tempfile.mkdtemp(prefix="job_caps_test_"))
    for d, i in (("2026-11-02", 1), ("2026-11-10", 2), ("2026-11-15", 3)):
        tr(root, "add", "--company", "Google", "--role", f"Scientist {i}", "--status", "Applied", "--applied", d,
           "--family", "qc_micro", "--url", f"https://boards.greenhouse.io/google/jobs/{i}")
    for d, fam in (("2026-11-05", "a"), ("2026-11-08", "b"), ("2026-11-12", "c")):
        tr(root, "add", "--company", "Acme Bio, Inc.", "--role", fam, "--status", "Applied", "--applied", d,
           "--family", fam)
    tr(root, "add", "--company", "Initech", "--role", "QC", "--status", "Applied", "--applied", "2026-11-10",
       "--family", "qc_micro")
    tr(root, "add", "--company", "Ashworth Bio", "--role", "R", "--status", "Filled - awaiting submit",
       "--url", "https://jobs.ashbyhq.com/ashbyco/1b2c3d4e-0000-4000-8000-000000000001")
    for co, d in (("Genentech", "2026-11-03"), ("Genentech", "2026-11-06"), ("Roche Diagnostics", "2026-11-09")):
        tr(root, "add", "--company", co, "--role", "R", "--status", "Applied", "--applied", d)
    folder = root / "applications" / "2026-07-01 Globex - Analyst"
    folder.mkdir(parents=True)
    (folder / "posting.md").write_text(GLOBEX, encoding="utf-8")
    tr(root, "add", "--company", "Globex", "--role", "Analyst", "--status", "Applied", "--applied", "2026-07-01",
       "--folder", "applications/2026-07-01 Globex - Analyst")
    tr(root, "update", "12", "status=Rejected", "last_update=2026-08-01")
    tr(root, "add", "--company", "Umbrella", "--role", "R", "--status", "Filled - awaiting submit")
    tr(root, "add", "--company", "Started Co", "--role", "R")  # Started: not an application yet
    (root / "config").mkdir()
    (root / "config" / "watchlist.csv").write_text(
        "employer,employer_norm,parent,ats\nGenentech,genentech,Roche,greenhouse\n"
        "Roche Diagnostics,roche diagnostics,Roche,workday\n", encoding="utf-8")
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "M"\n', encoding="utf-8")
    jobfs.atomic_write_json(root / "queue" / "2026-11-20" / "queue.json", {"v": 1, "date": "2026-11-20", "items": [
        {"n": 12, "job_key": "greenhouse:777", "company": "Umbrella", "family": "x"}]})
    agency = root / "applications" / "2026-10-29 Staffing - Analyst"
    agency.mkdir(parents=True)
    (agency / "posting.md").write_text(GLOBEX.replace("Globex is looking", "Our client is looking"), encoding="utf-8")
    return root


def cli(root, *args):
    r = subprocess.run([sys.executable, str(SCRIPTS / "caps.py"), "check", "--root", str(root)] + list(args),
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def test_cli_fixture():
    root = build_fixture()
    want = [
        (["--company", "Google", "--family", "qc_micro", "--date", "2026-11-20"],
         "CAP DEFER google 3/3 until 2026-12-02"),
        (["--company", "Acme Bio", "--family", "d", "--date", "2026-11-20"], "CAP DEFER company 3/3 until 2026-12-05"),
        (["--company", "Initech", "--family", "qc_micro", "--date", "2026-11-20"],
         "CAP DEFER family qc_micro 1/1 until 2026-12-10"),
        (["--company", "Initech", "--family", "other", "--date", "2026-11-20"], "CAP ok"),
        (["--company", "Ashworth Bio", "--url", "https://jobs.ashbyhq.com/ashbyco/2b2c3d4e-0000-4000-8000-000000000002",
          "--date", "2026-11-20"], "CAP DEFER ashby-open ashworth bio"),
        (["--company", "Genentech", "--date", "2026-11-20"], "CAP DEFER company 3/3 until 2026-12-03"),
        (["--company", "Staffing Partners", "--posting",
          str(root / "applications" / "2026-10-29 Staffing - Analyst" / "posting.md"), "--date", "2026-10-29"],
         "CAP DEFER rejected-block until 2026-10-30"),
        (["--company", "Staffing Partners", "--posting",
          str(root / "applications" / "2026-10-29 Staffing - Analyst" / "posting.md"), "--date", "2026-10-31"],
         "CAP ok"),
        (["--company", "Umbrella", "--date", "2026-11-20", "--job-key", "lever:new"],
         "CAP DEFER company-day 2/2 until 2026-11-21"),
        (["--company", "Started Co", "--date", "2026-11-20"], "CAP ok"),
    ]
    for args, expect in want:
        got = cli(root, *args)
        assert got == expect, (args, got, expect)
    # the same verdicts from the runner's snapshot (mode L reads; no xlsx access)
    tr(root, "snapshot")
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n', encoding="utf-8")
    os.rename(root / "applications.xlsx", root / "hidden.xlsx")
    for args, expect in want[:6]:
        assert cli(root, *args) == expect, args
    os.rename(root / "hidden.xlsx", root / "applications.xlsx")
    # tracker.py check on the same fixture: the job is in today's queue
    r = subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "check", "--root", str(root), "--company",
                        "Umbrella", "--title", "R2", "--url", "https://boards.greenhouse.io/umbrella/jobs/777"],
                       capture_output=True, text=True, encoding="utf-8")
    assert "IN QUEUE 2026-11-20 #12" in r.stdout.splitlines(), r.stdout
    r = subprocess.run([sys.executable, str(SCRIPTS / "caps.py"), "check", "--root", str(root), "--company", "X",
                        "--date", "11/20/2026"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1 and r.stdout.startswith("ERROR --date"), r.stdout


def main():
    for fn in (test_company_and_family, test_google, test_rejected_repost, test_ashby_and_day, test_parent,
               test_cli_fixture):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
