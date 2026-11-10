"""Check job/scripts/worksearch.py: the CA two-week export, the per-week state
checks, the employer gaps, and that no export is ever rewritten (spec 6.6).

Usage: python tests/test_worksearch.py
Needs openpyxl. Works on copies of tests/fixtures/week (the runner's snapshot plus
one pending by-hand event, mode L). Prints ok <name> per test and PASS last.
"""
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
FIX = HERE / "fixtures" / "week"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-16T17:00:00Z"
import worksearch  # noqa: E402

SPEC_COLUMNS = ("week_start, week_end, activity_date, activity_type, contact_type, employer_name, "
                "employer_location, job_title, job_req_id, method, job_site, posting_url, employer_main_website, "
                "employer_street, employer_city, employer_state, employer_zip, employer_phone, employer_email, "
                "employer_fax, contact_person, type_of_work_sought, result, proof_ref, counts_toward_minimum, "
                "reported_to_state_on").split(", ")


def run(*args, code=0):
    r = subprocess.run([sys.executable, str(SCRIPTS / "worksearch.py")] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8", cwd=tempfile.gettempdir())
    assert r.returncode == code, f"{args} exited {r.returncode}:\n{r.stdout}{r.stderr}"
    return r.stdout.splitlines()


def copy_fixture():
    root = Path(tempfile.mkdtemp(prefix="job_worksearch_test_")) / "week"
    shutil.copytree(FIX, root)
    return root


def snapshot_root(apps):
    """A folder in mode L whose snapshot holds only these application rows."""
    root = Path(tempfile.mkdtemp(prefix="job_worksearch_test_"))
    (root / "config").mkdir()
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n', encoding="ascii")
    snap = {"v": 1, "applications": apps, "archived": [], "accounts": [], "email_log_ids": [], "referrals": [],
            "employers": [], "activities": [], "settings": {}, "refs": {}, "applied_ids": []}
    (root / "runner" / "out").mkdir(parents=True)
    (root / "runner" / "out" / "tracker.json").write_text(json.dumps(snap), encoding="ascii")
    return root


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_export_ca():
    root = copy_fixture()
    out = run("export", "--root", root, "--state", "CA", "--from", "2026-11-01", "--to", "2026-11-14")
    base = root / "worksearch" / "CA_2026-11-01_2026-11-14"
    for ext in ("csv", "xlsx", "md"):
        assert f"WROTE worksearch/CA_2026-11-01_2026-11-14.{ext}" in out, out
        assert base.with_suffix("." + ext).exists()
    assert "FLAG fill SSN/ID yourself" in out
    with open(base.with_suffix(".csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == SPEC_COLUMNS == worksearch.COLUMNS, rows[0]
    recs = [dict(zip(rows[0], r)) for r in rows[1:]]
    applied = [r for r in recs if r["activity_type"] == "Applied"]
    assert len(applied) == 12, len(applied)
    weeks = {(r["week_start"], r["week_end"]) for r in applied}
    assert weeks == {("2026-11-01", "2026-11-07"), ("2026-11-08", "2026-11-14")}, weeks
    assert sum(1 for r in applied if r["week_start"] == "2026-11-01") == 6
    assert "WEEK 2026-11-01 to 2026-11-07 | 6 rows | 1 day" in out, out
    # by-hand rows: one in the snapshot, one still a pending event
    names = {r["employer_name"] for r in applied}
    assert {"Hooli Health", "Cyberdyne Labs"} <= names, names
    assert "ROWS 15 | applications 12 | referrals 2 | activities 1 | by hand 2" in out, out
    acme = next(r for r in applied if r["job_title"] == "QC Microbiologist II")
    assert acme["posting_url"] == "https://boards.greenhouse.io/example/jobs/4001001"
    assert acme["employer_main_website"] == "https://acmebio.example.com"
    assert acme["contact_type"] == "New" and acme["employer_phone"] == "(555) 555-0100"
    assert acme["type_of_work_sought"] == "QC / Microbiology" and acme["proof_ref"] == "gmail:msg-0001"
    again = next(r for r in applied if r["job_title"] == "QC Microbiologist III")
    assert again["contact_type"] == "Follow-up", again
    ref = [r for r in recs if r["activity_type"] == "Referral contact"]
    assert sorted(r["contact_person"] for r in ref) == ["Jane Doe", "Pat Kim"], ref  # the Drafted ask is not one
    assert not any("Massive Dynamic" in r["employer_name"] or "Old Corp" in r["employer_name"] for r in recs)
    wb = openpyxl.load_workbook(base.with_suffix(".xlsx"))
    assert [c.value for c in wb["Work search"][1]] == SPEC_COLUMNS
    claim = {r[0].value: (r[1].value, r[2].value) for r in wb["Claimant"].iter_rows(min_row=2)}
    assert claim["SSN or claimant ID"][0] is None and "FLAG" in claim["SSN or claimant ID"][1], claim
    assert claim["Claimant name"][0] is None and claim["Signature"][0] is None
    md = base.with_suffix(".md").read_text(encoding="utf-8")
    assert "FLAG fill SSN/ID yourself" in md.splitlines()[2], md
    assert "## Week 2026-11-01 to 2026-11-07" in md and "## Week 2026-11-08 to 2026-11-14" in md
    assert "SSN" not in "".join(r for row in rows[1:] for r in row)
    # the repo fixture itself is never written
    assert not (FIX / "worksearch").exists()


def test_never_rewrites():
    root = copy_fixture()
    run("export", "--root", root, "--state", "CA", "--from", "2026-11-01", "--to", "2026-11-14")
    folder = root / "worksearch"
    before = {p.name: sha(p) for p in folder.iterdir()}
    out = run("export", "--root", root, "--state", "CA", "--from", "2026-11-01", "--to", "2026-11-14")
    assert "KEPT worksearch/CA_2026-11-01_2026-11-14.csv (earlier exports are never rewritten)" in out, out
    assert "WROTE worksearch/CA_2026-11-01_2026-11-14_v2.csv" in out, out
    after = {p.name: sha(p) for p in folder.iterdir()}
    assert all(after[k] == v for k, v in before.items()), "an earlier export changed"
    assert len(after) == 6
    for args in (("check", "--state", "GA", "--week", "2026-11-01"), ("employers",), ("states",)):
        run(*args[:1], *(["--root", root] if args[0] != "states" else []), *args[1:])
    assert {p.name: sha(p) for p in folder.iterdir()} == after, "a later command touched an export"
    # default period: CA's two weeks ending yesterday (2026-11-15 is a Sunday, so ending Saturday 11-14)
    out = run("export", "--root", root)
    assert "WROTE worksearch/CA_2026-11-01_2026-11-14_v3.csv" in out, out


def test_checks():
    root = copy_fixture()
    out = run("check", "--root", root, "--state", "GA", "--week", "2026-11-01")
    assert out[0] == "WEEK GA 2026-11-01 to 2026-11-07 | contacts 6 | days 1 | new employers 6", out
    assert "WARN GA needs contacts on 3 or more days" in out, out
    out = run("check", "--root", root, "--state", "AZ", "--week", "2026-11-11")
    assert out[0].startswith("WEEK AZ 2026-11-08 to 2026-11-14 | contacts 9 | days 3"), out
    assert "WARN AZ needs contacts on 4 or more days" in out, out
    out = run("check", "--root", root, "--state", "CA", "--week", "2026-11-08")
    assert out[-1] == "CHECK ok", out
    out = run("check", "--root", root, "--state", "WY", "--week", "2026-11-08")
    assert any(ln.startswith("NOTE WY has no preset") for ln in out), out
    out = run("check", "--root", root, "--state", "GA", "--week", "11/08/2026", code=1)
    assert out[0].startswith("ERROR --week"), out


def app(i, company, applied, key="", location="Springfield, IL", role="QC Analyst"):
    return {"ID": i, "Date": applied, "Company": company, "Role": role, "Location": location, "Status": "Applied",
            "Applied": applied, "Last update": applied, "Job key": key, "Source": "Runner"}


def test_fl_and_pa():
    apps = [app(1, "Acme Bio", "2026-10-20", "k1"), app(2, "Acme Bio", "2026-10-27", "k2"),
            app(3, "Acme Bio", "2026-11-03", "k3"), app(4, "Acme Bio", "2026-11-04", "k4", location="Far City, IL"),
            app(5, "Globex Labs", "2026-11-03", "k5"), app(6, "Globex Labs", "2026-11-05", "k5")]
    root = snapshot_root(apps)
    out = run("check", "--root", root, "--state", "FL", "--week", "2026-11-03")
    assert "WARN FL Acme Bio at the same location 3 weeks in a row does not count" in out, out
    assert sum(1 for ln in out if ln.startswith("WARN FL Acme Bio")) == 1, out
    out = run("check", "--root", root, "--state", "PA", "--week", "2026-11-03")
    assert "WARN PA repeat application to the same job does not count: Globex Labs - QC Analyst" in out, out
    out = run("check", "--root", root, "--state", "NY", "--week", "2026-11-03")
    assert out[0].startswith("WEEK NY 2026-11-02 to 2026-11-08"), out  # NY weeks end on Sunday


def test_formula_guard_and_employers():
    root = snapshot_root([app(1, '=HYPERLINK("x")', "2026-11-03", "k1"), app(2, "Acme Bio", "2026-11-04", "k2")])
    run("export", "--root", root, "--state", "TX", "--from", "2026-11-01", "--to", "2026-11-07")
    with open(root / "worksearch" / "TX_2026-11-01_2026-11-07.csv", encoding="utf-8", newline="") as f:
        names = [r["employer_name"] for r in csv.DictReader(f)]
    assert "'=HYPERLINK(\"x\")" in names, names
    wb = openpyxl.load_workbook(root / "worksearch" / "TX_2026-11-01_2026-11-07.xlsx")
    cell = [c for c in wb["Work search"]["F"] if c.value and "HYPERLINK" in c.value][0]
    assert cell.data_type == "s", cell.data_type
    out = run("employers", "--root", root)
    assert "MISSING Acme Bio | street, city, state, zip, phone, main website" in out, out
    assert out[-1].startswith("NEXT "), out


def test_presets():
    data = worksearch.load_presets()
    states = data["states"]
    assert {"TX", "FL", "NY", "GA", "NC", "OH", "PA", "AZ", "IL", "CA"} <= set(states)
    for code, body in list(states.items()) + [("generic", data["generic"])]:
        for k, v in body.items():
            if k in ("name", "note"):
                continue
            assert isinstance(v, dict) and {"value", "ref", "confirmed"} <= set(v), (code, k, v)
    assert worksearch.preset("NC")["website"] == "main" and worksearch.preset("CA")["period_weeks"] == 2
    assert worksearch.preset("GA")["min_days"] == 3 and worksearch.preset("AZ")["min_days"] == 4
    assert not worksearch.preset("ZZ")["listed"]
    assert Path(worksearch.PRESETS).read_bytes().decode("ascii")


def test_old_keys_and_employer_site():
    """R2: a tracker row keeping an earlier version's job key (with '?' and
    '=') and a row with this version's key for the same link are one job (PA
    repeat rule). SV-09: the record's employer website is never a job board,
    social or reference page (the row's Employer site from posting.py)."""
    import jobkey
    link = "https://careers.example-bio.com/Jobs/QC?jobId=77"
    old = jobkey.raw_key(link)
    assert old != jobkey.job_key(link) and "?" in old, old
    a1, a2 = app(1, "Example Bio", "2026-11-03", old), app(2, "Example Bio", "2026-11-05", jobkey.job_key(link))
    acts = [worksearch.app_activity(r, {}, {}) for r in (a1, a2)]
    assert worksearch.repeat_jobs(acts) == {1}, [a["_key"] for a in acts]
    for site, want in (("https://www.linkedin.com/company/example-bio", ""), ("https://twitter.com/examplebio", ""),
                       ("https://www.example-bio.com", "https://www.example-bio.com"), ("http://10.0.0.7/", "")):
        rec = worksearch.app_activity(dict(a1, **{"Employer site": site}), {}, {})
        assert rec["employer_main_website"] == want, (site, rec["employer_main_website"])
    # R1: the posting link a log row shows is one netfetch.web_refusal passes; a job board's is never echoed
    for url, want in (("https://www.linkedin.com/jobs/view/1", ""), ("https://user@careers.example-bio.com/j/1", ""),
                      ("javascript:alert(1)", ""), (link, link)):
        rec = worksearch.app_activity(dict(a1, URL=url), {}, {})
        assert rec["posting_url"] == want, (url, rec["posting_url"])


def main():
    for fn in (test_export_ca, test_never_rewrites, test_checks, test_fl_and_pa, test_formula_guard_and_employers,
               test_presets, test_old_keys_and_employer_site):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
