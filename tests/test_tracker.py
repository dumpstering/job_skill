"""Exercise tracker.py end to end on a temp JobSearch folder.

Usage: python tests/test_tracker.py
Needs openpyxl. Exits non-zero on the first failure; prints ok <name> per test
and PASS at the end.

The v1.1 cases are kept as they were, except where spec 2.6 changes behavior on
purpose (each marked "v1.2:"): the status list gains Blocked - site, the
workbook gains columns after the v1.1 ones plus three sheets, and dates come
from jobfs.today() in the configured time zone (pinned here so a run that
crosses midnight cannot flake).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "job" / "scripts" / "tracker.py"
sys.path.insert(0, str(SCRIPT.parent))
import jobfs  # noqa: E402
import tracker  # noqa: E402

# v1.2: tracker dates are jobfs.today() (America/Los_Angeles); pin the clock.
os.environ["JOB_SKILL_NOW"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
TODAY = jobfs.today()
V11_STATUSES = ["Started", "Not pursued", "Resume ready", "Filled - awaiting submit",
                "Answers ready - fill by hand", "Blocked - login", "Blocked - CAPTCHA", "Applied",
                "No response", "Assessment", "Screen", "Interview", "Offer", "Rejected", "Withdrawn",
                "Closed"]


def iso(days=0):
    return (TODAY + timedelta(days=days)).isoformat()


def run(root, *args, code=0, env=None):
    cmd = [sys.executable, str(SCRIPT), args[0]]
    if args[0] != "statuses":
        cmd += ["--root", str(root)]
    cmd += [str(a) for a in args[1:]]
    full_env = dict(os.environ, **(env or {}))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=tempfile.gettempdir(),
                       env=full_env)
    assert r.returncode == code, f"{' '.join(map(str, args))} exited {r.returncode} (want {code}):\n{r.stdout}{r.stderr}"
    return r.stdout


def new_root():
    return Path(tempfile.mkdtemp(prefix="job_tracker_test_"))


def posting(root, folder, body):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "posting.md").write_text(body, encoding="utf-8")
    return d / "posting.md"


def sheet_rows(path, sheet="Applications"):
    ws = openpyxl.load_workbook(path)[sheet]
    head = [c.value for c in ws[1]]
    out = []
    for vals in ws.iter_rows(min_row=2, values_only=True):
        if any(v not in (None, "") for v in vals):
            out.append(dict(zip(head, vals)))
    return out


def lines(out):
    return [ln for ln in out.splitlines() if ln.strip()]


ACME_TEXT = ("Acme Corp is hiring a Senior IT Manager. Requisition ID: R-123456. You will lead the "
             "service desk and endpoint team across three offices and own the budget for tooling.")
GLOBEX_TEXT = (
    "Globex is looking for a data analyst to join the reporting group. You will build weekly dashboards "
    "for the finance team, clean source extracts, document every metric definition, and answer ad hoc "
    "questions from product managers. The ideal candidate writes clear SQL, explains tradeoffs plainly, "
    "and enjoys working with people who do not read code. Experience with a warehouse such as Snowflake "
    "or BigQuery is helpful but not required for this role.")


def test_cli_basics():
    out = run("unused", "statuses")
    # v1.2: the v1.1 statuses plus Blocked - site (rank 2, after Blocked - CAPTCHA)
    assert lines(out) == V11_STATUSES[:7] + ["Blocked - site"] + V11_STATUSES[7:], out
    for cmd in ("init", "check", "add", "update", "show", "due", "account", "inbox-plan", "seen",
                "log-email", "inbox-done", "stats", "statuses", "referral-add", "employer-set",
                "activity-add", "archive", "apply-events", "snapshot", "upgrade"):
        r = subprocess.run([sys.executable, str(SCRIPT), cmd, "--help"], capture_output=True, text=True)
        assert r.returncode == 0 and "usage" in r.stdout.lower(), f"{cmd} --help failed: {r.stderr}"
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    bad = run(new_root(), "add", "--company", "X", code=1)  # --role missing
    assert bad.startswith("ERROR"), bad


def test_main_flow():
    root = new_root()
    path = root / "applications.xlsx"
    assert run(root, "show").strip() == "NO TRACKER" and not path.exists()
    assert run(root, "due").strip() == "NO TRACKER"
    out = run(root, "check", "--company", "Acme", "--title", "Manager", "--url", "https://x.com/j/1")
    assert lines(out)[0] == "JOB KEY url:x.com/j/1" and "NO MATCH" in out, out

    assert run(root, "init").startswith("CREATED")
    assert run(root, "init").startswith("EXISTS")
    out = run(root, "check", "--company", "Acme", "--title", "Manager")
    assert lines(out)[0].startswith("JOB KEY") and lines(out)[1] == "NO MATCH", out

    posting(root, "acme-senior-it-manager", ACME_TEXT)
    posting(root, "globex-data-analyst", GLOBEX_TEXT)
    assert run(root, "add", "--company", "Acme Corp", "--role", "Senior IT Manager", "--fit", "strong fit",
               "--url", "https://boards.greenhouse.io/acme/jobs/1111111", "--folder",
               "acme-senior-it-manager").strip() == "ADDED 1"
    # the first write of the day backs up the file as it was before the write
    backup = root / "backups" / f"applications_{iso()}.xlsx"
    assert backup.exists(), "daily backup missing"
    assert sheet_rows(backup) == [], "backup should hold the pre-add (empty) workbook"
    assert run(root, "add", "--company", "Globex", "--role", "Data Analyst", "--url",
               "https://jobs.lever.co/globex/abcd-1234", "--folder", "globex-data-analyst").strip() == "ADDED 2"
    assert run(root, "add", "--company", "Initech", "--role", "Support Engineer", "--url",
               "https://example.com/jobs/12", "--folder", "initech-support-engineer",
               "--notes", "met at fair").strip() == "ADDED 3"
    assert sheet_rows(backup) == [], "backup must not be rewritten on a later write the same day"

    # ---- check ----
    out = run(root, "check", "--company", "Acme Corp", "--title", "Senior IT Manager", "--url",
              "https://job-boards.greenhouse.io/acme/jobs/1111111?gh_src=abc")
    ln = lines(out)
    assert ln[0] == "JOB KEY greenhouse:1111111", out
    assert ln[1].startswith("SAME POSTING  ID 1 | ") and "acme-senior-it-manager" in ln[1], out
    assert "Senior IT Manager | Started" in ln[1], out

    newp = posting(root, "acme-new", "Lead our helpdesk. Req ID: R-123456. Totally rewritten blurb here.")
    out = run(root, "check", "--company", "ACME, Inc.", "--title", "Marketing Director", "--url",
              "https://acme.example/careers/other", "--posting", newp)
    assert lines(out)[1].startswith("LIKELY REPOST ID 1") and "same requisition R-123456" in out, out

    newp = posting(root, "globex-new", GLOBEX_TEXT.replace("weekly dashboards", "monthly dashboards"))
    out = run(root, "check", "--company", "Globex Corporation", "--title", "Quantum Chef", "--url",
              "https://globex.example/careers/77", "--posting", newp)
    assert lines(out)[1].startswith("LIKELY REPOST ID 2") and "posting text" in out, out

    out = run(root, "check", "--company", "Initech", "--title", "Support Engineer II")
    assert lines(out)[1].startswith("SIMILAR ROLE  ID 3") and "title" in out, out
    out = run(root, "check", "--company", "Initech", "--title", "Chief Pastry Officer")
    assert lines(out)[1].startswith("SAME COMPANY  ID 3") and "other role, 0 days ago" in out, out
    out = run(root, "check", "--company", "Umbrella", "--title", "Doctor")
    assert lines(out) == [lines(out)[0], "NO MATCH"], out
    # the new posting's own folder is skipped
    out = run(root, "check", "--company", "Acme Corp", "--title", "Senior IT Manager",
              "--url", "https://acme.example/else", "--posting", root / "acme-senior-it-manager" / "posting.md")
    assert "NO MATCH" in out, out
    # SAME COMPANY lapses after 90 days
    run(root, "update", 3, f"date={iso(-120)}")
    out = run(root, "check", "--company", "Initech", "--title", "Chief Pastry Officer")
    assert "NO MATCH" in out, out
    run(root, "update", 3, f"date={iso()}")

    # ---- update: follow-up, backward moves, notes ----
    out = run(root, "update", 1, "status=Applied", f"applied={iso(-10)}")
    assert out.startswith("UPDATED 1 ") and f"Follow-up:  -> {iso(-3)}" in out and "Status: Started -> Applied" in out, out
    out = run(root, "show", "--id", 1)
    row = lines(out)[1].split(" | ")
    assert lines(out)[0].startswith("ID | Date | Company") and row[5] == "Applied" and row[6] == iso(-10) \
        and row[7] == iso(-3), out
    # due: FOLLOW-UP
    out = run(root, "due")
    assert f"FOLLOW-UP ID 1 | Acme Corp | Senior IT Manager | applied {iso(-10)} | due {iso(-3)}" in out, out
    # update to Interview clears the follow-up
    out = run(root, "update", 1, "status=interview")
    assert "Status: Applied -> Interview" in out and f"Follow-up: {iso(-3)} ->" in out, out
    assert lines(run(root, "show", "--id", 1))[1].split(" | ")[7] == "", "follow-up should be cleared"
    assert "FOLLOW-UP" not in run(root, "due")
    # backward move refused (exit 5), then forced
    out = run(root, "update", 1, "status=Applied", code=5)
    assert out.strip() == "REFUSED 1 backward move Interview -> Applied (use --force to correct a mistake)", out
    assert sheet_rows(path)[0]["Status"] == "Interview"
    assert run(root, "update", 1, "status=Applied", "--force").startswith("UPDATED 1 ")
    assert sheet_rows(path)[0]["Status"] == "Applied"
    # same-rank and forward moves are fine
    assert run(root, "update", 1, "status=No response").startswith("UPDATED")
    # field name forms, clear, bad input
    run(root, "update", 1, "follow_up=" + iso(5), "next-step=Call recruiter", "resume sent=v2")
    r = sheet_rows(path)[0]
    assert r["Follow-up"].date() == TODAY + timedelta(days=5) and r["Next step"] == "Call recruiter" \
        and r["Resume sent"] == "v2", r
    run(root, "update", 1, "contact=")
    assert run(root, "update", 1, "status=Bogus", code=1).startswith("ERROR")
    assert run(root, "update", 1, "applied=11/05/2026", code=1).startswith("ERROR")
    assert run(root, "update", 1, "fit=Perfect", code=1).startswith("ERROR")
    assert run(root, "update", 1, "nonsense=1", code=1).startswith("ERROR")
    assert run(root, "update", 99, "notes=x", code=1).startswith("ERROR no row with ID 99")
    # notes+=
    run(root, "update", 2, "notes+=Called the recruiter")
    out = run(root, "update", 2, "notes+=Sent a thank-you")
    assert "Notes: +" in out, out
    notes = sheet_rows(path)[1]["Notes"]
    assert notes == f"{iso()}: Called the recruiter\n{iso()}: Sent a thank-you", repr(notes)

    # ---- due: UNSUBMITTED, STALE, UPCOMING ----
    run(root, "update", 2, "status=Filled - awaiting submit", f"last_update={iso(-5)}")
    run(root, "update", 3, f"last_update={iso(-20)}", f"next_step=Phone call {iso(3)} at 10am")
    out = run(root, "due")
    assert f"UNSUBMITTED ID 2 | Globex | Data Analyst | Filled - awaiting submit | last update {iso(-5)}" in out, out
    assert f"STALE ID 3 | Initech | Support Engineer | Started | last update {iso(-20)}" in out, out
    assert f"UPCOMING ID 3 | Initech | Support Engineer | Phone call {iso(3)} at 10am" in out, out
    run(root, "update", 3, f"last_update={iso(-1)}", f"next_step=Call {iso(9)}")
    assert run(root, "due").strip().startswith("UNSUBMITTED"), "nothing else should be due"
    return root


def test_workbook_shape(root):
    wb = openpyxl.load_workbook(root / "applications.xlsx")
    # v1.2: Referrals, Employers and Activities join, before Summary
    assert wb.sheetnames == ["Applications", "Accounts", "Email log", "Settings", "Referrals", "Employers",
                             "Activities", "Summary"], wb.sheetnames
    ws = wb["Applications"]
    heads = [c.value for c in ws[1]]
    v11 = ["ID", "Date", "Company", "Role", "Location", "Fit", "Status", "Applied", "Follow-up",
           "Last update", "Next step", "Source", "Job key", "Resume sent", "Contact", "Notes", "Folder", "URL"]
    # v1.2: the new columns come after the v1.1 ones
    assert heads == v11 + ["Tier", "Family", "Variant", "ATS", "Found via", "Posted", "Req ID", "Queue date",
                           "Account", "Referral", "Referral URL", "Confirmed", "Proof", "Block reason",
                           "Employer site", "Work search"], heads
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:AH"), (ws.freeze_panes, ws.auto_filter.ref)
    assert all(c.font.bold for c in ws[1]), "header must be bold"
    col = {h: i + 1 for i, h in enumerate(heads)}
    dvs = {str(dv.sqref): dv for dv in ws.data_validations.dataValidation}
    status_dv = next(dv for k, dv in dvs.items() if k.startswith("G2"))
    fit_dv = next(dv for k, dv in dvs.items() if k.startswith("F2"))
    assert status_dv.type == "list" and "Filled - awaiting submit" in status_dv.formula1 \
        and "Blocked - CAPTCHA" in status_dv.formula1 and "Blocked - site" in status_dv.formula1 \
        and len(status_dv.formula1) <= 257, status_dv.formula1
    assert str(status_dv.sqref) == "G2:G20000", status_dv.sqref  # v1.2: MAX_ROWS 20000
    assert fit_dv.formula1 == '"Strong Fit,Good Fit,Stretch,Long Shot"', fit_dv.formula1
    url_cell = ws.cell(2, col["URL"])
    assert url_cell.hyperlink is not None and url_cell.hyperlink.target == \
        "https://boards.greenhouse.io/acme/jobs/1111111", "URL must be a hyperlink"
    assert ws.cell(2, col["Date"]).number_format == "yyyy-mm-dd"
    assert ws.cell(2, col["Applied"]).number_format == "yyyy-mm-dd"
    assert ws.cell(4, col["Notes"]).alignment.wrap_text, "Notes must wrap"
    sm = wb["Summary"]
    assert [c.value for c in sm[1]] == ["Status", "Count"]
    assert sm["A2"].value == "Started" and sm["B2"].value == "=COUNTIF(Applications!$G:$G,A2)", sm["B2"].value
    assert sm["A9"].value == "Blocked - site"
    assert sm["A19"].value == "Total" and sm["B19"].value == "=SUM(B2:B18)", sm["B19"].value
    acc = [c.value for c in wb["Accounts"][1]]
    assert acc == ["Site", "Login email", "Created", "Notes", "Employer", "ATS", "Last used"] \
        and not any("pass" in str(h).lower() for h in acc)
    assert [c.value for c in wb["Email log"][1]] == ["Checked", "Message ID", "Email date", "From",
                                                     "Subject", "Job ID", "Classified as", "Quote", "Change",
                                                     "Kind"]
    assert [c.value for c in wb["Settings"][1]] == ["Key", "Value"] and wb["Settings"]["A2"].value == "Last inbox check"
    assert [c.value for c in wb["Referrals"][1]] == tracker.REF_COLS
    assert [c.value for c in wb["Employers"][1]] == tracker.EMP_COLS
    assert [c.value for c in wb["Activities"][1]] == tracker.ACT_COLS


def test_validation_fallback():
    wb = tracker.build_workbook()
    long_list = [f"A long status label number {i} with padding" for i in range(20)]
    f = tracker.list_validation(wb, wb["Applications"], "H2:H10", long_list, 6, "Test")
    assert f == "Settings!$F$2:$F$21", f
    assert wb["Settings"]["F2"].value == long_list[0] and wb["Settings"]["F21"].value == long_list[-1]


def test_accounts(root):
    out = run(root, "account", "--site", "linkedin.com", "--email", "me@example.com", "--notes", "Used Google sign-in")
    assert out.strip() == "ACCOUNT ADDED linkedin.com", out
    out = run(root, "account", "--site", "linked")
    assert out.strip() == f"ACCOUNT linkedin.com | me@example.com | {iso()} | Used Google sign-in", out
    out = run(root, "account", "--site", "LinkedIn.com", "--email", "new@example.com")
    assert out.strip() == "ACCOUNT UPDATED linkedin.com", out
    out = run(root, "account", "--site", "linkedin.com")
    assert "new@example.com" in out and "Used Google sign-in" in out and f"| {iso()} |" in out, out
    assert run(root, "account", "--site", "nope.com").strip() == "NONE"
    for extra in (["--notes", "my password is hunter2"], ["--email", "pwd.guy@example.com"],
                  ["--notes", "Passwd: abc"]):
        out = run(root, "account", "--site", "indeed.com", "--email", "x@example.com", *extra, code=1)
        assert out.strip() == "ERROR passwords are never stored", out
    assert run(root, "account", "--site", "indeed.com").strip() == "NONE"
    rows = sheet_rows(root / "applications.xlsx", "Accounts")
    assert len(rows) == 1 and rows[0]["Login email"] == "new@example.com", rows
    # v1.2: Employer, ATS, Last used; REUSE names the email to use on a new tenant
    out = run(root, "account", "--site", "https://acme.wd5.myworkdayjobs.com/en-US/careers", "--email",
              "new@example.com", "--employer", "Acme Corp", "--ats", "workday", "--used")
    assert out.strip() == "ACCOUNT ADDED acme.wd5.myworkdayjobs.com", out
    out = run(root, "account", "--site", "acme.wd5")
    assert out.strip() == (f"ACCOUNT acme.wd5.myworkdayjobs.com | new@example.com | {iso()} |  | "
                           f"Acme Corp | workday | {iso()}"), out
    assert lines(run(root, "account", "--site", "globex.wd1.myworkdayjobs.com", "--reuse")) == \
        ["NONE", "REUSE new@example.com"]


def test_email_log(root):
    assert lines(run(root, "seen", "m1", "m2")) == ["NEW m1", "NEW m2"]
    out = run(root, "log-email", "--message-id", "m1", "--email-date", "2026-11-01", "--from", "hr@acme.example",
              "--subject", "Thanks for applying", "--class", "Acknowledgement", "--job-id", 1,
              "--quote", "We received your application", "--change", "none")
    assert out.strip() == "LOGGED m1", out
    assert run(root, "log-email", "--message-id", "m1", "--email-date", "2026-11-01", "--from", "x",
               "--subject", "y", "--class", "z").strip() == "DUPLICATE m1"
    assert lines(run(root, "seen", "m1", "m2")) == ["SEEN m1", "NEW m2"]
    rows = sheet_rows(root / "applications.xlsx", "Email log")
    assert len(rows) == 1 and rows[0]["Message ID"] == "m1" and rows[0]["Job ID"] == 1 \
        and rows[0]["Classified as"] == "Acknowledgement" and rows[0]["Checked"].date() == TODAY \
        and rows[0]["Email date"].date() == date(2026, 11, 1) and rows[0]["Kind"] == "reply", rows
    # inbox-plan on the main root: earliest non-terminal Applied date, ACTIVE rows, LOGGED count
    out = lines(run(root, "inbox-plan"))
    assert out[0] == f"SINCE {iso(-10)}", out
    assert out[1] == ("ACTIVE ID 1 | Acme Corp | Senior IT Manager | No response | "
                      f"applied {iso(-10)} | greenhouse:1111111 | boards.greenhouse.io"), out
    assert out[2].startswith("ACTIVE ID 2 | Globex | Data Analyst | Filled - awaiting submit | applied - |"), out
    assert out[-1] == "LOGGED 1 emails" and len(out) == 4, out
    # v1.2: alert emails are logged with Kind alert
    run(root, "log-email", "--message-id", "a1", "--email-date", "2026-11-01", "--from", "alerts@example.com",
        "--subject", "New jobs", "--class", "Alert", "--kind", "alert")
    assert sheet_rows(root / "applications.xlsx", "Email log")[1]["Kind"] == "alert"
    assert run(root, "log-email", "--message-id", "a2", "--email-date", "x", "--from", "x", "--subject", "y",
               "--class", "z", "--kind", "spam", code=1).startswith("ERROR kind")


def test_inbox_branches():
    root = new_root()
    run(root, "init")
    assert lines(run(root, "inbox-plan"))[0] == f"SINCE {iso(-30)}"
    # a terminal row's Applied date is ignored
    run(root, "add", "--company", "OldCo", "--role", "Analyst", "--status", "Rejected", "--applied", iso(-50))
    assert lines(run(root, "inbox-plan"))[0] == f"SINCE {iso(-30)}"
    assert lines(run(root, "show", "--id", 1))[1].split(" | ")[7] == "", "no follow-up past Applied"
    run(root, "add", "--company", "NewCo", "--role", "Analyst", "--status", "Applied", "--applied", iso(-10))
    out = lines(run(root, "inbox-plan"))
    assert out[0] == f"SINCE {iso(-10)}" and len(out) == 3 and out[1].startswith("ACTIVE ID 2 | NewCo"), out
    assert lines(run(root, "show", "--id", 2))[1].split(" | ")[7] == iso(-3), "applied + 7 days"
    assert run(root, "inbox-done").strip() == f"INBOX CHECKED {iso()}"
    assert lines(run(root, "inbox-plan"))[0] == f"SINCE {iso(-2)}"
    path = root / "applications.xlsx"
    wb = openpyxl.load_workbook(path)
    wb["Settings"]["B2"].value = "2026-10-20"  # typed as text in Excel
    wb.save(path)
    assert lines(run(root, "inbox-plan"))[0] == "SINCE 2026-10-18"


def test_stats():
    root = new_root()
    data = [("Strong Fit", "2026-10-03", "Interview"), ("Strong Fit", "2026-10-20", "Rejected"),
            ("Good Fit", "2026-09-15", "Applied"), ("Stretch", None, "Started"), ("Good Fit", "2026-09-02", "Offer")]
    for i, (fit, applied, status) in enumerate(data, 1):
        args = ["add", "--company", f"Co{i}", "--role", "Role", "--fit", fit, "--status", status]
        if applied:
            args += ["--applied", applied]
        run(root, *args)
    out = run(root, "stats")
    for want in ("STATUS Started: 1", "STATUS Applied: 1", "STATUS Interview: 1", "STATUS Offer: 1",
                 "STATUS Rejected: 1", "TOTAL 5", "APPLIED 4", "RESPONSE RATE 3 of 4 (75%)",
                 "INTERVIEW RATE 2 of 4 (50%)",
                 "FIT Strong Fit | applied 2 | response rate 2 of 2 (100%) | interview rate 1 of 2 (50%)",
                 "FIT Good Fit | applied 2 | response rate 1 of 2 (50%) | interview rate 1 of 2 (50%)",
                 "FIT Stretch | applied 0 | response rate 0 of 0 (n/a) | interview rate 0 of 0 (n/a)",
                 "MONTH 2026-09: 2", "MONTH 2026-10: 2"):
        assert want in out.splitlines(), f"stats missing {want!r}:\n{out}"
    # show filters
    assert len(lines(run(root, "show", "--active"))) == 1 + 4  # header + all but Rejected
    assert len(lines(run(root, "show", "--status", "Offer", "interview"))) == 3
    assert run(root, "show", "--company", "co3").count("Co3") == 1
    assert run(root, "show", "--company", "nobody").strip() == "NONE"


def test_user_edited_workbook():
    root = new_root()
    path = root / "applications.xlsx"
    for name, url in (("Alpha", "https://alpha.example/j/1"), ("Beta", "https://beta.example/j/2"),
                      ("Gamma", "https://gamma.example/j/3")):
        run(root, "add", "--company", name + " Inc", "--role", name + " Role", "--url", url)
    wb = openpyxl.load_workbook(path)
    ws = wb["Applications"]
    ws.insert_cols(3)  # a stray column in the middle
    ws.cell(1, 3, "Stray")
    data = [[c.value for c in ws[r]] for r in range(2, 5)]
    for i, vals in enumerate(reversed(data)):  # hand-sorted: Gamma, Beta, Alpha
        for j, v in enumerate(vals, 1):
            ws.cell(2 + i, j, v)
    for r in range(2, 5):
        ws.cell(r, 3, f"note-{ws.cell(r, 4).value}")
    ws.cell(3, 2).value = iso()  # Beta's Date retyped as text
    ws.cell(3, 2).number_format = "General"
    wb.save(path)

    out = lines(run(root, "show"))
    assert [ln.split(" | ")[2] for ln in out[1:]] == ["Gamma Inc", "Beta Inc", "Alpha Inc"], out
    assert out[2].split(" | ")[1] == iso(), out  # text date accepted
    assert lines(run(root, "show", "--company", "beta"))[1].split(" | ")[0] == "2"
    out = run(root, "update", 2, "status=Applied", f"applied={iso(-8)}")
    assert "Status: Started -> Applied" in out and f"Follow-up:  -> {iso(-1)}" in out, out
    assert "FOLLOW-UP ID 2 | Beta Inc" in run(root, "due")
    assert run(root, "add", "--company", "Delta", "--role", "Dev").strip() == "ADDED 4"
    rows = sheet_rows(path)
    by = {r["Company"]: r for r in rows}
    assert len(rows) == 4 and by["Beta Inc"]["Status"] == "Applied" and by["Beta Inc"]["Stray"] == "note-Beta Inc" \
        and by["Delta"]["ID"] == 4 and by["Delta"]["Stray"] is None and by["Gamma Inc"]["Stray"] == "note-Gamma Inc", rows
    assert [r["Company"] for r in rows] == ["Gamma Inc", "Beta Inc", "Alpha Inc", "Delta"], "row order changed"
    assert openpyxl.load_workbook(path)["Applications"].cell(1, 3).value == "Stray"


def test_text_is_never_a_formula():
    root = new_root()
    path = root / "applications.xlsx"
    evil = '=HYPERLINK("http://x","y")'
    run(root, "add", "--company", "=1+1", "--role", "@SUM(A1)", "--notes", "-2+3",
        "--url", "file:///C:/x", "--location", "\t=cmd", "--family", "=f", "--req-id", "+r",
        "--referral=-ref", "--referral-url", "=x", "--proof", "@p")
    run(root, "add", "--company", "Site Co", "--role", "Dev", "--url", "javascript:alert(1)")
    run(root, "add", "--company", "Web Co", "--role", "Dev", "--url", "https://web.example/j/1")
    run(root, "log-email", "--message-id", "=m9", "--email-date", "2026-11-01", "--from", "+cmd",
        "--subject", evil, "--class", "=Other", "--quote", "@x", "--change", "-1")
    run(root, "update", 2, "notes=" + evil, "contact=\r=1")
    run(root, "account", "--site", "=evil.com", "--email", "a@example.com", "--notes", "+x", "--employer", "=e")
    run(root, "referral-add", "--contact", "=Jordan", "--company", "@Acme", "--notes", "=1")
    run(root, "employer-set", "--employer", "=Acme", "--street", "+1 Main", "--notes", "@n")
    run(root, "activity-add", "--type", "Workshop", "--description", "=desc", "--result=-r")
    wb = openpyxl.load_workbook(path)

    def cells(ws):
        heads = [c.value for c in ws[1]]
        return {h: ws.cell(2, i + 1) for i, h in enumerate(heads)}
    app = cells(wb["Applications"])
    for name, want in (("Company", "=1+1"), ("Role", "@SUM(A1)"), ("Notes", "-2+3"), ("Location", "=cmd"),
                       ("URL", "file:///C:/x"), ("Family", "=f"), ("Req ID", "+r"), ("Referral", "-ref"),
                       ("Referral URL", "=x"), ("Proof", "@p")):
        assert app[name].value == want and app[name].data_type == "s", (name, app[name].value, app[name].data_type)
    assert app["URL"].hyperlink is None, "file: URL must not become a hyperlink"
    assert app["Referral URL"].hyperlink is None
    ws = wb["Applications"]
    url3 = [c for c in ws[4] if c.value == "https://web.example/j/1"][0]
    assert url3.hyperlink is not None and url3.hyperlink.target == "https://web.example/j/1"
    assert [c for c in ws[3] if c.value == "javascript:alert(1)"][0].hyperlink is None
    assert [c for c in ws[3] if c.value == evil][0].data_type == "s"
    assert [c for c in ws[3] if c.value == "=1"][0].data_type == "s"  # leading CR stripped
    log = cells(wb["Email log"])
    for name, want in (("Message ID", "=m9"), ("From", "+cmd"), ("Subject", evil), ("Classified as", "=Other"),
                       ("Quote", "@x"), ("Change", "-1")):
        assert log[name].value == want and log[name].data_type == "s", (name, log[name].value, log[name].data_type)
    acc = cells(wb["Accounts"])
    assert acc["Site"].value == "=evil.com" and acc["Site"].data_type == "s" and acc["Notes"].data_type == "s" \
        and acc["Employer"].data_type == "s"
    for sheet, names in (("Referrals", ("Contact", "Company", "Notes")), ("Employers", ("Employer", "Street", "Notes")),
                         ("Activities", ("Description", "Result"))):
        c = cells(wb[sheet])
        for n in names:
            assert c[n].data_type == "s" and str(c[n].value)[0] in "=@+-", (sheet, n, c[n].value)
    # the script's own Summary formulas stay formulas
    assert wb["Summary"]["B2"].data_type == "f"
    for sheet in wb.worksheets:
        if sheet.title != "Summary":
            for row in sheet.iter_rows():
                assert not [c for c in row if c.data_type == "f"], f"formula cell on {sheet.title}"
    assert lines(run(root, "seen", "=m9")) == ["SEEN =m9"]


def test_locked_file(root):
    if os.name != "nt":
        print("SKIP lock test (needs Windows share semantics)")
        return
    before = len(sheet_rows(root / "applications.xlsx"))
    with open(root / "applications.xlsx", "rb"):
        out = run(root, "add", "--company", "Locked Co", "--role", "X", code=4)
    assert out.strip() == "LOCKED applications.xlsx is open in another program (Excel?). Close it and run again.", out
    assert not [p for p in root.iterdir() if p.name.startswith(".applications_")], "temp file left behind"
    assert len(sheet_rows(root / "applications.xlsx")) == before
    assert run(root, "add", "--company", "Free Co", "--role", "X").startswith("ADDED")


# ---------- v1.2 ----------

def v11_workbook(path):
    """A workbook exactly as v1.1 built it: 18 columns, 4 sheets plus Summary."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Applications"
    ws.append(tracker.V11_APP_COLS)
    ws.append([1, date(2026, 9, 1), "Acme Corp", "QC Analyst", "Springfield", "Good Fit", "Applied",
               date(2026, 9, 2), date(2026, 9, 9), date(2026, 9, 2), "Call 2026-09-20", "Skill",
               "greenhouse:1234567", "resume.pdf", "Pat", "line one\nline two", "applications/acme",
               "https://boards.greenhouse.io/acme/jobs/1234567"])
    ws.append([2, date(2026, 9, 5), "Globex", "Lab Tech", "", "Stretch", "Rejected", date(2026, 9, 6),
               None, date(2026, 10, 1), None, "By hand", "url:globex.example/j/2", None, None, None, None,
               "https://globex.example/j/2"])
    ws["R2"].hyperlink = "https://boards.greenhouse.io/acme/jobs/1234567"
    ws.cell(1, 19, "My column")  # a user column after the v1.1 ones
    ws.cell(2, 19, "mine")
    from openpyxl.worksheet.datavalidation import DataValidation
    dv = DataValidation(type="list", formula1='"' + ",".join(V11_STATUSES) + '"', allow_blank=True)
    dv.add("G2:G2000")
    ws.add_data_validation(dv)
    acc = wb.create_sheet("Accounts")
    acc.append(["Site", "Login email", "Created", "Notes"])
    acc.append(["acme.wd5.myworkdayjobs.com", "jqa@example.com", date(2026, 9, 1), "made it"])
    log = wb.create_sheet("Email log")
    log.append(["Checked", "Message ID", "Email date", "From", "Subject", "Job ID", "Classified as", "Quote",
                "Change"])
    log.append([date(2026, 9, 3), "msg-1", date(2026, 9, 3), "hr@acme.example", "Thanks", 1, "Acknowledgement",
                "q", "none"])
    st = wb.create_sheet("Settings")
    st.append(["Key", "Value"])
    st.append(["Last inbox check", date(2026, 10, 1)])
    sm = wb.create_sheet("Summary")
    sm.append(["Status", "Count"])
    for i, s in enumerate(V11_STATUSES, 2):
        sm.append([s, f"=COUNTIF(Applications!$G:$G,A{i})"])
    sm.append(["Total", "=SUM(B2:B17)"])
    wb.save(path)


def values(path, sheet):
    ws = openpyxl.load_workbook(path)[sheet]
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_v11_upgrade():
    root = new_root()
    path = root / "applications.xlsx"
    v11_workbook(path)
    before = {s: values(path, s) for s in ("Applications", "Accounts", "Email log", "Settings")}
    assert run(root, "show", "--id", 1).count("Acme Corp") == 1, "a v1.1 workbook reads without an upgrade"
    assert run(root, "upgrade").startswith("UPGRADED ")
    backup = root / "backups" / f"applications_{iso()}_before_upgrade.xlsx"
    assert backup.exists(), "upgrade backup missing"
    assert {s: values(backup, s) for s in before} == before, "backup must be the v1.1 file"
    after = openpyxl.load_workbook(path)
    assert after.sheetnames == ["Applications", "Accounts", "Email log", "Settings", "Referrals", "Employers",
                                "Activities", "Summary"], after.sheetnames
    for s, old in before.items():
        new = values(path, s)
        width = len(old[0])
        assert [r[:width] for r in new[:len(old)]] == old, f"{s}: values or column order changed"
    heads = [c.value for c in after["Applications"][1]]
    assert heads == tracker.V11_APP_COLS + ["My column"] + tracker.APP_COLS[18:], heads
    assert [c.value for c in after["Accounts"][1]] == tracker.ACC_COLS
    assert [c.value for c in after["Email log"][1]] == tracker.LOG_COLS
    assert after["Applications"]["R2"].hyperlink.target == "https://boards.greenhouse.io/acme/jobs/1234567"
    dvs = [dv for dv in after["Applications"].data_validations.dataValidation if str(dv.sqref).startswith("G2")]
    assert len(dvs) == 1 and "Blocked - site" in dvs[0].formula1 and str(dvs[0].sqref) == "G2:G20000"
    assert [after["Summary"].cell(r, 1).value for r in range(2, 19)] == tracker.STATUSES
    assert run(root, "upgrade").startswith("CURRENT ")
    # and the first ordinary write works on it
    assert run(root, "add", "--company", "Initech", "--role", "Dev", "--tier", "B").strip() == "ADDED 3"
    assert run(root, "update", 2, "status=Blocked - site", "--force").startswith("UPDATED 2 ")


def test_upgrade_on_first_write():
    root = new_root()
    v11_workbook(root / "applications.xlsx")
    assert run(root, "update", 1, "tier=A").startswith("UPDATED 1 Tier:  -> A")
    assert (root / "backups" / f"applications_{iso()}_before_upgrade.xlsx").exists()
    assert sheet_rows(root / "applications.xlsx")[0]["Tier"] == "A"


def test_followup_tiers():
    root = new_root()
    out = [run(root, "add", "--company", f"Co{i}", "--role", "R", "--status", "Applied", "--applied", iso(-1),
               *extra).strip() for i, extra in enumerate(
        (["--tier", "B"], ["--tier", "A"], ["--tier", "B", "--referral", "Pat Q."], []), 1)]
    assert out == ["ADDED 1", "ADDED 2", "ADDED 3", "ADDED 4"], out
    fu = [r["Follow-up"] for r in sheet_rows(root / "applications.xlsx")]
    assert fu[0] is None and fu[1].date() == TODAY + timedelta(days=6) and fu[2] is not None and fu[3] is not None, fu
    # update to Applied on a Tier B row sets no follow-up either
    run(root, "add", "--company", "Co5", "--role", "R", "--tier", "b", "--status", "Filled - awaiting submit")
    out = run(root, "update", 5, "status=Applied")
    assert "Follow-up" not in out and "Applied:  -> " + iso() in out, out
    assert run(root, "add", "--company", "Co6", "--role", "R", "--tier", "C", code=1).startswith("ERROR tier 'C'")
    assert run(root, "add", "--company", "X", "--role", "R", "--folder", "../outside", code=1) \
        .startswith("ERROR folder must be inside")
    assert run(root, "update", 1, "folder=C:/Windows", code=1).startswith("ERROR folder must be inside")


def test_new_sheets():
    root = new_root()
    out = run(root, "referral-add", "--contact", "Jordan Q. Avery", "--company", "Acme Bio", "--job-key",
              "greenhouse:1", "--channel", "linkedin message", "--referral-url", "https://example.com/r?gh_src=x")
    assert out.strip() == "REFERRAL ADDED Jordan Q. Avery | Acme Bio | Drafted", out
    out = run(root, "referral-add", "--contact", "Jordan Q. Avery", "--company", "Acme Bio, Inc.", "--job-key",
              "greenhouse:1", "--status", "sent", "--date", "2026-11-02")
    assert out.strip() == "REFERRAL UPDATED Jordan Q. Avery | Acme Bio, Inc. | Status: Drafted -> Sent; " \
                          "Nudge on:  -> 2026-11-07", out
    ref = sheet_rows(root / "applications.xlsx", "Referrals")
    assert len(ref) == 1 and ref[0]["ID"] == 1 and ref[0]["Channel"] == "LinkedIn message" \
        and ref[0]["Referral URL"] == "https://example.com/r?gh_src=x", ref
    for bad in ("jqa@example.com", "https://linkedin.example/in/x", "Pat 555-1234"):
        assert run(root, "referral-add", "--contact", bad, "--company", "Acme", code=1).strip() == \
            "ERROR --contact holds a name only"
    out = run(root, "employer-set", "--employer", "Acme Bio, Inc.", "--city", "Springfield", "--state", "IL",
              "--main-website", "https://acme.example", "--confirmed-on", "2026-11-01")
    assert out.strip() == "EMPLOYER ADDED Acme Bio, Inc.", out
    out = run(root, "employer-set", "--employer", "acme bio", "--zip", "62701", "--city", "Springfield")
    assert out.strip() == "EMPLOYER UPDATED acme bio: ZIP:  -> 62701", out
    emp = sheet_rows(root / "applications.xlsx", "Employers")
    assert len(emp) == 1 and emp[0]["Normalized"] == "acme bio" and emp[0]["ZIP"] == "62701" \
        and emp[0]["Confirmed on"].date() == date(2026, 11, 1), emp
    out = run(root, "activity-add", "--type", "job fair", "--description", "County job fair", "--date", "2026-11-03")
    assert out.strip() == "ACTIVITY ADDED 2026-11-03 | Job fair | County job fair", out
    assert run(root, "activity-add", "--type", "Party", "--description", "x", code=1).startswith("ERROR type")


def test_events_transport():
    root = new_root()
    out = run(root, "add", "--company", "Acme", "--role", "QC Micro", "--status", "Started", "--transport", "events")
    pid = out.strip().split()[1]
    assert out.strip().startswith("ADDED P-") and len(pid) == 10 and out.strip().endswith(" (pending)"), out
    assert all(c in "0123456789abcdef" for c in pid[2:]), pid
    assert not (root / "applications.xlsx").exists(), "events transport never writes the xlsx"
    evs = list((root / "runner" / "in" / "events").glob("*_tracker.json"))
    assert len(evs) == 1
    ev = json.loads(evs[0].read_text(encoding="utf-8"))
    assert ev["payload"] == {"argv": ["add", "--company", "Acme", "--role", "QC Micro", "--status", "Started"],
                             "pid": pid}, ev
    out = lines(run(root, "show", "--id", pid))
    assert out[1].startswith(f"{pid} | {iso()} | Acme | QC Micro |"), out
    out = run(root, "update", pid, "fit=good fit", "--transport", "events")
    assert out.strip() == f"UPDATED {pid} Fit:  -> Good Fit", out
    assert run(root, "update", pid, "status=Bogus", "--transport", "events", code=1).startswith("ERROR")
    assert len(list((root / "runner" / "in" / "events").glob("*.json"))) == 2, "a failed command writes no event"
    assert run(root, "apply-events").strip() == "APPLIED 2 PENDING 0 REJECTED 0"
    snap = json.loads((root / "runner" / "out" / "tracker.json").read_text(encoding="utf-8"))
    assert snap["refs"] == {pid: 1}, snap["refs"]
    assert snap["applications"][0]["ID"] == 1 and snap["applications"][0]["Fit"] == "Good Fit"
    assert "req_ids" in snap["applications"][0] and "simhash" in snap["applications"][0]
    assert snap["_sha256"] == jobfs.body_sha256(snap)
    # the P- ID keeps working after apply, and reads now come from the snapshot
    assert lines(run(root, "show", "--id", pid))[1].startswith(f"1 | {iso()} | Acme | QC Micro | Good Fit"), pid
    assert sheet_rows(root / "applications.xlsx")[0]["Fit"] == "Good Fit"
    assert run(root, "apply-events").strip() == "APPLIED 0 PENDING 0 REJECTED 0"


def test_events_locked():
    root = new_root()
    run(root, "add", "--company", "Acme", "--role", "QC Micro", "--transport", "events")
    lock = root / "~$applications.xlsx"
    lock.write_text("x", encoding="utf-8")
    out = run(root, "apply-events", code=4)
    assert out.startswith("LOCKED"), out
    assert len(list((root / "runner" / "in" / "events").glob("*.json"))) == 1, "event must stay pending"
    status = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    assert status["tracker_locked_since"], status
    since = status["tracker_locked_since"]
    run(root, "apply-events", code=4)
    status = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    assert status["tracker_locked_since"] == since, "the first lock time is kept"
    lock.unlink()
    assert run(root, "apply-events").strip() == "APPLIED 1 PENDING 0 REJECTED 0"
    assert len(sheet_rows(root / "applications.xlsx")) == 1
    status = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    assert status["tracker_locked_since"] is None
    # the same event file again: no second effect
    done = list((root / "runner" / "out" / "events_done").glob("*/*.json"))
    assert len(done) == 1
    shutil.copy2(done[0], root / "runner" / "in" / "events" / done[0].name)
    assert run(root, "apply-events").strip() == "APPLIED 0 PENDING 0 REJECTED 0"
    assert len(sheet_rows(root / "applications.xlsx")) == 1, "re-sent event applied twice"
    assert not list((root / "runner" / "in" / "events").glob("*.json"))


def test_auto_transport():
    root = new_root()
    (root / "config").mkdir()
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n', encoding="utf-8")
    out = run(root, "add", "--company", "Acme", "--role", "Dev")
    assert out.strip().startswith("ADDED P-"), "mode L writes go to events"
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "M"\n', encoding="utf-8")
    assert run(root, "add", "--company", "Globex", "--role", "Dev").strip() == "ADDED 1", "mode M writes direct"


def test_check_v12():
    root = new_root()
    text_a = (GLOBEX_TEXT + " ") * 2
    posting(root, "applications/a", text_a)
    run(root, "add", "--company", "Globex", "--role", "Data Analyst", "--folder", "applications/a",
        "--url", "https://boards.greenhouse.io/globex/jobs/555")
    # an agency repost: another company, the same text, found by SimHash
    newp = posting(root, "applications/b", text_a.replace("Globex is looking", "Our client is looking"))
    out = run(root, "check", "--company", "Staffing Partners", "--title", "Analyst", "--posting", newp)
    assert lines(out)[1].startswith("LIKELY REPOST ID 1") and "SimHash distance" in out, out
    other = posting(root, "applications/c", ACME_TEXT * 3)
    out = run(root, "check", "--company", "Staffing Partners", "--title", "Analyst", "--posting", other)
    assert lines(out)[1] == "NO MATCH", out
    # IN QUEUE when the job key is in today's queue
    q = root / "queue" / "2026-11-20"
    jobfs.atomic_write_json(q / "queue.json", {"v": 1, "date": "2026-11-20", "items": [
        {"n": 12, "job_key": "greenhouse:777", "company": "Initech"}]})
    env = {"JOB_SKILL_NOW": "2026-11-20T18:00:00Z"}
    out = run(root, "check", "--company", "Initech", "--title", "Dev", "--url",
              "https://boards.greenhouse.io/initech/jobs/777", env=env)
    assert lines(out)[:2] == ["JOB KEY greenhouse:777", "IN QUEUE 2026-11-20 #12"], out
    out = run(root, "check", "--company", "Initech", "--title", "Dev", "--job-key", "greenhouse:777", env=env)
    assert "IN QUEUE 2026-11-20 #12" in out
    assert "IN QUEUE" not in run(root, "check", "--company", "Initech", "--title", "Dev", "--job-key", "greenhouse:9",
                                 env=env)
    # a v1.1 row holds a url: fallback key for a host v1.2 now keys by ID: its URL still matches
    run(root, "add", "--company", "Hooli", "--role", "Analyst", "--url", "https://hooli.bamboohr.com/careers/321")
    wb = openpyxl.load_workbook(root / "applications.xlsx")
    ws = wb["Applications"]
    col = [c.value for c in ws[1]].index("Job key") + 1
    ws.cell(row=ws.max_row, column=col).value = "url:hooli.bamboohr.com/careers/321"
    wb.save(root / "applications.xlsx")
    out = run(root, "check", "--company", "Other", "--title", "Clerk", "--url", "https://hooli.bamboohr.com/careers/321")
    assert lines(out)[0] == "JOB KEY bamboohr:hooli:321" and "job key bamboohr:hooli:321" in out, out


def test_archive():
    root = new_root()
    run(root, "add", "--company", "OldCo", "--role", "R", "--status", "Rejected", "--url",
        "https://boards.greenhouse.io/old/jobs/42")
    run(root, "update", 1, "date=2025-03-01", "last_update=2025-04-01")
    run(root, "add", "--company", "LiveCo", "--role", "R", "--status", "Applied")
    run(root, "add", "--company", "NewerCo", "--role", "R", "--status", "Withdrawn")
    out = lines(run(root, "archive", "--before", "2026-01-01"))
    assert out == ["ARCHIVED 1 rows to applications_archive_2025.xlsx", "KEPT 2 rows"], out
    assert (root / "backups" / f"applications_{iso()}_before_archive.xlsx").exists()
    arch = sheet_rows(root / "applications_archive_2025.xlsx")
    assert len(arch) == 1 and arch[0]["Company"] == "OldCo" and arch[0]["ID"] == 1
    assert [r["Company"] for r in sheet_rows(root / "applications.xlsx")] == ["LiveCo", "NewerCo"]
    # the archived row still counts as a duplicate, and its ID is never reused
    out = run(root, "check", "--company", "OldCo", "--title", "R", "--url", "https://boards.greenhouse.io/old/jobs/42")
    assert lines(out)[1].startswith("SAME POSTING  ID 1"), out
    assert run(root, "add", "--company", "Fresh", "--role", "R").strip() == "ADDED 4"
    assert run(root, "archive", "--before", "2026-01-01").startswith("ARCHIVED 0 rows")
    run(root, "snapshot")
    snap = json.loads((root / "runner" / "out" / "tracker.json").read_text(encoding="utf-8"))
    assert [r["Company"] for r in snap["archived"]] == ["OldCo"] and len(snap["applications"]) == 3


def test_5000_rows():
    root = new_root()
    wb = tracker.build_workbook()
    ws = wb["Applications"]
    for i in range(1, 5001):
        ws.append([i, date(2026, 1, 1), f"Company {i}", "Role", "", "", "Applied", date(2026, 1, 2), None,
                   date(2026, 1, 2), None, "Skill", f"greenhouse:{i}", None, None, None, None,
                   f"https://example.com/j/{i}"])
    wb.save(root / "applications.xlsx")
    t = time.time()
    assert run(root, "add", "--company", "Acme", "--role", "Dev").strip() == "ADDED 5001"
    took = time.time() - t
    assert took < 5, f"add on 5,000 rows took {took:.1f}s"
    print(f"   (add on a 5,000-row workbook: {took:.1f}s)")


def main():
    test_cli_basics()
    print("ok cli_basics")
    root = test_main_flow()
    print("ok main_flow")
    for name, fn in (("workbook_shape", test_workbook_shape), ("accounts", test_accounts),
                     ("email_log", test_email_log)):
        fn(root)
        print(f"ok {name}")
    for fn in (test_validation_fallback, test_inbox_branches, test_stats, test_user_edited_workbook,
               test_text_is_never_a_formula):
        fn()
        print(f"ok {fn.__name__[5:]}")
    test_locked_file(root)
    print("ok locked_file")
    for fn in (test_v11_upgrade, test_upgrade_on_first_write, test_followup_tiers, test_new_sheets,
               test_events_transport, test_events_locked, test_auto_transport, test_check_v12, test_archive,
               test_5000_rows):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
