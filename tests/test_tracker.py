"""Exercise tracker.py end to end on a temp JobSearch folder.

Usage: python tests/test_tracker.py
Needs openpyxl. Exits non-zero on the first failure; prints PASS at the end.
"""
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "job" / "scripts" / "tracker.py"
TODAY = date.today()


def iso(days=0):
    return (TODAY + timedelta(days=days)).isoformat()


def run(root, *args, code=0):
    cmd = [sys.executable, str(SCRIPT), args[0]]
    if args[0] != "statuses":
        cmd += ["--root", str(root)]
    cmd += [str(a) for a in args[1:]]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=tempfile.gettempdir())
    assert r.returncode == code, f"{' '.join(args)} exited {r.returncode} (want {code}):\n{r.stdout}{r.stderr}"
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
    assert lines(out)[0] == "Started" and lines(out)[-1] == "Closed" and len(lines(out)) == 16, out
    for cmd in ("init", "check", "add", "update", "show", "due", "account", "inbox-plan", "seen",
                "log-email", "inbox-done", "stats", "statuses"):
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
    assert wb.sheetnames == ["Applications", "Accounts", "Email log", "Settings", "Summary"], wb.sheetnames
    ws = wb["Applications"]
    heads = [c.value for c in ws[1]]
    assert heads == ["ID", "Date", "Company", "Role", "Location", "Fit", "Status", "Applied", "Follow-up",
                     "Last update", "Next step", "Source", "Job key", "Resume sent", "Contact", "Notes",
                     "Folder", "URL"], heads
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:R"), (ws.freeze_panes, ws.auto_filter.ref)
    assert all(c.font.bold for c in ws[1]), "header must be bold"
    col = {h: i + 1 for i, h in enumerate(heads)}
    dvs = {str(dv.sqref): dv for dv in ws.data_validations.dataValidation}
    status_dv = next(dv for k, dv in dvs.items() if k.startswith("G2"))
    fit_dv = next(dv for k, dv in dvs.items() if k.startswith("F2"))
    assert status_dv.type == "list" and "Filled - awaiting submit" in status_dv.formula1 \
        and "Blocked - CAPTCHA" in status_dv.formula1 and len(status_dv.formula1) <= 257, status_dv.formula1
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
    assert sm["A18"].value == "Total" and sm["B18"].value == "=SUM(B2:B17)", sm["B18"].value
    acc = [c.value for c in wb["Accounts"][1]]
    assert acc == ["Site", "Login email", "Created", "Notes"] and not any("pass" in str(h).lower() for h in acc)
    assert [c.value for c in wb["Email log"][1]] == ["Checked", "Message ID", "Email date", "From",
                                                     "Subject", "Job ID", "Classified as", "Quote", "Change"]
    assert [c.value for c in wb["Settings"][1]] == ["Key", "Value"] and wb["Settings"]["A2"].value == "Last inbox check"


def test_validation_fallback():
    sys.path.insert(0, str(SCRIPT.parent))
    import tracker
    wb = tracker.build_workbook()
    long_list = [f"A long status label number {i} with padding" for i in range(20)]
    f = tracker.list_validation(wb, wb["Applications"], "H2:H10", long_list, 6, "Test")
    assert f == "Settings!$F$2:$F$21", f
    assert wb["Settings"]["F2"].value == long_list[0] and wb["Settings"]["F21"].value == long_list[-1]
    sys.path.pop(0)


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
        and rows[0]["Email date"].date() == date(2026, 11, 1), rows
    # inbox-plan on the main root: earliest non-terminal Applied date, ACTIVE rows, LOGGED count
    out = lines(run(root, "inbox-plan"))
    assert out[0] == f"SINCE {iso(-10)}", out
    assert out[1] == ("ACTIVE ID 1 | Acme Corp | Senior IT Manager | No response | "
                      f"applied {iso(-10)} | greenhouse:1111111 | boards.greenhouse.io"), out
    assert out[2].startswith("ACTIVE ID 2 | Globex | Data Analyst | Filled - awaiting submit | applied - |"), out
    assert out[-1] == "LOGGED 1 emails" and len(out) == 4, out


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
        "--url", "file:///C:/x", "--location", "\t=cmd")
    run(root, "add", "--company", "Site Co", "--role", "Dev", "--url", "javascript:alert(1)")
    run(root, "add", "--company", "Web Co", "--role", "Dev", "--url", "https://web.example/j/1")
    run(root, "log-email", "--message-id", "=m9", "--email-date", "2026-11-01", "--from", "+cmd",
        "--subject", evil, "--class", "=Other", "--quote", "@x", "--change", "-1")
    run(root, "update", 2, "notes=" + evil, "contact=\r=1")
    run(root, "account", "--site", "=evil.com", "--email", "a@example.com", "--notes", "+x")
    wb = openpyxl.load_workbook(path)

    def cells(ws):
        heads = [c.value for c in ws[1]]
        return {h: ws.cell(2, i + 1) for i, h in enumerate(heads)}
    app = cells(wb["Applications"])
    for name, want in (("Company", "=1+1"), ("Role", "@SUM(A1)"), ("Notes", "-2+3"), ("Location", "=cmd"),
                       ("URL", "file:///C:/x")):
        assert app[name].value == want and app[name].data_type == "s", (name, app[name].value, app[name].data_type)
    assert app["URL"].hyperlink is None, "file: URL must not become a hyperlink"
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
    assert acc["Site"].value == "=evil.com" and acc["Site"].data_type == "s" and acc["Notes"].data_type == "s"
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


def main():
    test_cli_basics()
    root = test_main_flow()
    test_workbook_shape(root)
    test_validation_fallback()
    test_accounts(root)
    test_email_log(root)
    test_inbox_branches()
    test_stats()
    test_user_edited_workbook()
    test_text_is_never_a_formula()
    test_locked_file(root)
    print("PASS")


if __name__ == "__main__":
    main()
