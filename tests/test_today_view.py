"""Check job/scripts/today_view.py: the 1.2 card word for word, its zero-count
and time-sensitive rules, the catch-up note, the wording check, next (resume
at the first item without a final event; the Tier A sha256 check; the Check
line from sensitive_ok.json; --short), progress, the do-yourself list, a job
from a link counted at once, the no-runner line and the drafts.

Usage: python tests/test_today_view.py
Every root is synthetic and built in a temp folder (a hand-made queue.json,
morning.json, tracker snapshot and status.json for Jordan Q. Avery); no
runner or network is needed. Stdlib only. Exits non-zero on the first
failure and prints PASS at the end.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
SCRIPT = SCRIPTS / "today_view.py"
DAY = "2026-11-12"
NOW = "2026-11-12T17:00:00Z"   # Thursday 09:00 in America/Los_Angeles
os.environ["JOB_SKILL_NOW"] = NOW
sys.path.insert(0, str(SCRIPTS))
import events  # noqa: E402
import jobfs  # noqa: E402
import report  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="job_today_view_"))
SPEC_CARD = """Thursday, November 12
Yesterday: 21 applications sent, 2 people contacted.
Needs you: Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply).
Ready: 30. No account needed for 24; 2 new Workday sites come after #10 (about 5 minutes each).
Questions (answer any time in one reply; 5 jobs wait for them):
1. Have you worked in a BSL-2 lab? Where, and about how long? (3 jobs)
2. Earliest start date? (all jobs; saved for next time)
Sensitive answers today: salary $95,000-$110,000 on #3, #7, #12; "no sponsorship needed" on all. Reply ok, or name the ones to leave blank.
3 people you could ask about a job: drafts after #10.
Starting #1 now. Say "short" any time for an easy day."""
MORNING = {
    "v": 1, "date": DAY,
    "urgent": [{"text": 'Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply)'}],
    "questions": [{"id": "Q-1a2b3c", "text": "Have you worked in a BSL-2 lab? Where, and about how long?",
                   "jobs": [3, 7, 12]},
                  {"id": "Q-4d5e6f", "text": "Earliest start date?", "jobs": [], "all_jobs": True, "saved": True}],
    "waiting": 5,
    "sensitive": 'salary $95,000-$110,000 on #3, #7, #12; "no sponsorship needed" on all',
    "referral_drafts": [{"n": 3, "contact_ref": "N-0004"}, {"n": 8, "contact_ref": "N-0011"},
                        {"n": 15, "contact_ref": "N-0020"}],
}
QUIET_MORNING = {"v": 1, "date": DAY, "urgent": [], "questions": [], "waiting": 0, "sensitive": "",
                 "referral_drafts": []}
COMPANIES = ["Acme Bio", "Globex Therapeutics", "Initech Labs", "Umbrella Biosciences", "Hooli Health",
             "Vandelay Diagnostics", "Stark Biologics", "Wayne Genomics", "Tyrell Bio", "Cyberdyne Labs",
             "Soylent Sciences", "Wonka Bioworks", "Gringotts Pharma", "Monarch Bio", "Oscorp Therapeutics",
             "Piedpiper Bio", "Massive Dynamic", "Aperture Sciences", "Blackmesa Labs", "Dunder Diagnostics",
             "Prestige Bio", "Sterling Genomics", "Bluth Biologics", "Kramerica Labs"]
TENANTS = ["genentech.wd5.myworkdayjobs.com", "gilead.wd1.myworkdayjobs.com"]


def slug(s):
    return "".join(c for c in s.lower() if c.isalnum())


def item(n, company, ats="greenhouse", account="none", host="job-boards.greenhouse.io", tier="B"):
    s = slug(company)
    folder = f"applications/{DAY} {company} - Scientist"
    url = (f"https://{host}/en-US/External/job/X_{n}" if ats == "workday"
           else f"https://job-boards.greenhouse.io/{s}/jobs/{9000 + n}")
    key = f"workday:{host.split('.')[0]}:R-{n}" if ats == "workday" else f"greenhouse:{9000 + n}"
    return {"n": n, "job_key": key, "company": company, "role": "Scientist", "location": "Springfield, IL",
            "ats": ats, "host": host, "apply_url": url, "referral_url": "", "folder": folder,
            "family": "qc_micro", "mode": "industry", "tier": tier, "tier_reason": "fit" if tier == "A" else "",
            "variant": "qc_micro@v1", "posted": "2026-11-11", "age_days": 1, "band": "0-3", "repost_of": None,
            "score": 80 - n, "score_parts": {"fresh": 40, "fit": 20, "referral": 0, "priority": 10, "family_w": 0,
                                             "cost": 0, "repeat_company": 0},
            "account": account, "attach": "first", "captcha_risk": "low", "est_user_min": 1.2,
            "upload": f"upload/{n:02d}_Avery_Resume_{s[:12].title()[:12]}.pdf",
            "resume_status": "tier_a_pending" if tier == "A" else "built", "answers_status": "complete",
            "needs": [], "sensitive": [], "referral": {"state": "none", "contact_ref": "", "hold_until": ""},
            "posting_status": "read", "flags": []}


def make_root(name, morning=MORNING, applied_yesterday=21, contacted=2, workday=True):
    root = TMP / name / "JobSearch"
    f = jobfs.Folder(root)
    f.config.mkdir(parents=True)
    f.settings_toml.write_text('[runner]\nmode = "L"\n', encoding="ascii")
    items = []
    n = 0
    plan = [("none", c) for c in COMPANIES]
    if workday:
        plan = plan[:10] + [("new", "Genentech"), ("new", "Gilead Sciences"), ("new", "Genentech"),
                            ("new", "Gilead Sciences"), ("new", "Genentech"), ("new", "Gilead Sciences")] + plan[10:]
    for account, company in plan:
        n += 1
        if account == "new":
            host = TENANTS[0] if company == "Genentech" else TENANTS[1]
            items.append(item(n, company, "workday", "new", host))
        else:
            items.append(item(n, company, tier="A" if n in (3, 7) else "B"))
    for it in items:
        folder = root / it["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "resume_B.pdf").write_bytes(b"%PDF-1.4 B " + it["job_key"].encode() + b"\n")
        up = root / it["upload"]
        up.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / "resume_B.pdf", up)
    q = {"v": 1, "date": DAY, "built_at": "2026-11-12T05:41:07-08:00", "runner_version": "1.2.0", "target": 20,
         "size": len(items), "pool_left": 41, "items": items,
         "account_block": {"after_n": 10, "tenants": TENANTS if workday else []},
         "counts": {"seen": 212, "passed": 71, "discarded": 141, "deferred": 0}}
    jobfs.atomic_write_json(f.queue_json(DAY), q)
    if morning is not None:
        f.morning_json(DAY).parent.mkdir(parents=True, exist_ok=True)
        f.morning_json(DAY).write_text(json.dumps(morning), encoding="ascii")
    apps = [{"ID": i + 1, "Company": f"Past {i}", "Role": "Scientist", "Status": "Applied",
             "Applied": "2026-11-11", "Follow-up": None} for i in range(applied_yesterday)]
    apps.append({"ID": 99, "Company": "Old Co", "Role": "Scientist", "Status": "Applied", "Applied": "2026-11-02",
                 "Follow-up": None})
    refs = [{"ID": f"R-{i}", "Date": "2026-11-11", "Contact": f"Person {i}", "Company": "Acme Bio",
             "Status": "Sent"} for i in range(contacted)]
    refs.append({"ID": "R-9", "Date": "2026-11-11", "Contact": "Drafty", "Company": "Acme Bio", "Status": "Drafted"})
    jobfs.atomic_write_json(f.tracker_json, {"v": 1, "applications": apps, "archived": [], "accounts": [],
                                             "email_log_ids": [], "referrals": refs, "employers": [],
                                             "activities": [], "settings": {}, "refs": {}, "applied_ids": [],
                                             "pending_rejected": []})
    jobfs.atomic_write_json(f.status_json, {"version": "1.2.0", "mode": "L", "min_skill": "1.2.0",
                                            "last_daily": {"at": "2026-11-12T05:41:07-08:00", "ok": True},
                                            "last_tick": "2026-11-12T08:45:00-08:00", "tracker_locked_since": None})
    return root


def run(cmd, root):
    r = subprocess.run([sys.executable, str(SCRIPT), cmd, "--root", str(root)], capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, JOB_SKILL_NOW=NOW))
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    return r.stdout.rstrip("\n"), r.stderr


CARDS = []


def card(root):
    out, err = run("card", root)
    CARDS.append(out)
    assert "WARN wording" not in err, err
    return out


def test_spec_card():
    root = make_root("spec")
    out = card(root)
    assert out == SPEC_CARD, "\n" + out
    print("ok the 1.2 card word for word")


def test_zero_lines():
    root = make_root("quiet", morning=QUIET_MORNING, applied_yesterday=0, contacted=0, workday=False)
    out = card(root)
    assert not any(ln.startswith("Yesterday") for ln in out.splitlines()), out
    assert not any(ln.startswith("Needs you") for ln in out.splitlines()), out
    assert not any(ln.startswith(("Questions", "Sensitive", "3 people")) for ln in out.splitlines()), out
    assert out.splitlines()[1] == "Ready: 24. No account needed for any of them.", out
    print("ok zero-count and not time-sensitive lines stay out")
    root = make_root("people_only", morning=QUIET_MORNING, applied_yesterday=0, contacted=1, workday=False)
    out = card(root)
    assert "Yesterday: 1 person contacted." in out.splitlines(), out
    print("ok a yesterday line with one nonzero count")


def test_missing_morning():
    root = make_root("no_morning", morning=None)
    out = card(root)
    lines = out.splitlines()
    sys.path.insert(0, str(SCRIPTS))
    import today_view
    assert today_view.CATCH_UP_NOTE in lines, out
    assert lines[-1] == 'Starting #1 now. Say "short" any time for an easy day.', out
    assert not any(ln.startswith("Needs you") for ln in lines), out
    print("ok missing morning.json: the catch-up note and #1")


def test_resume_and_next():
    root = make_root("next")
    out, _ = run("next", root)
    assert out.splitlines()[0].startswith("NEXT #01 greenhouse:9001"), out
    events.emit(root, "applied", {"reason": "", "question_ids": [], "confirmation": "page", "proof": ""},
                job_key="greenhouse:9001", n=1, day=DAY)
    out, _ = run("next", root)
    lines = out.splitlines()
    assert lines[0] == "NEXT #02 greenhouse:9002", out
    assert "UPLOAD upload/02_Avery_Resume_Globextherap.pdf (Tier B file, sha256 ok)" in lines, out
    assert "HANDOVER Attach the file starting with 02 in JobSearch\\upload." in lines, out
    assert lines[-1] == "HANDOVER Then click Submit and type s.", out
    assert sum(1 for ln in lines if ln.startswith("HANDOVER")) <= 5
    out = card(root)
    assert out.splitlines()[-1].startswith("Starting #2 now."), out
    assert "Ready: 29. No account needed for 23;" in out, out
    out, _ = run("progress", root)
    assert out.splitlines() == ["PROGRESS applied=1 handed_over=0 skipped=0 later=0 parked=0 blocked=0 left=29 of 30",
                                "Today: 1 application sent."], out
    print("ok next after an applied event for #1 is #2")


def test_tier_a_sha():
    root = make_root("tier_a")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    it3 = next(it for it in q["items"] if it["n"] == 3)
    folder, up = root / it3["folder"], root / it3["upload"]
    for n in (1, 2):
        events.emit(root, "skipped", {"reason": "user", "question_ids": [], "confirmation": "none"},
                    job_key=q["items"][n - 1]["job_key"], n=n, day=DAY)
    out, _ = run("next", root)
    assert out.splitlines()[0].startswith("NEXT #03"), out
    assert "Tier A resume is not ready, so this is the fallback" in out, out
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4 Tier A\n")
    out, _ = run("next", root)
    assert f"UPLOAD {it3['folder']}/resume.pdf (Tier A file in the job folder (the upload copy is not it yet))" in \
        out.splitlines(), out
    shutil.copyfile(folder / "resume.pdf", up)
    jobfs.atomic_write_json(jobfs.Folder(root).runner_out / "staged.json", {"v": 1, "files": {
        up.name: {"sha256": hashlib.sha256(up.read_bytes()).hexdigest()}}})
    out, _ = run("next", root)
    assert f"UPLOAD {it3['upload']} (Tier A file, sha256 ok)" in out.splitlines(), out
    print("ok Tier A upload checked by sha256")


def test_notes_and_diy():
    root = make_root("notes", morning=QUIET_MORNING, workday=False)
    f = jobfs.Folder(root)
    st = json.loads(f.status_json.read_text(encoding="utf-8"))
    st["last_daily"]["at"] = "2026-11-10T05:30:00-08:00"
    st["tracker_locked_since"] = "2026-11-12T05:00:00-08:00"
    jobfs.atomic_write_json(f.status_json, st)
    q = json.loads(f.queue_json(DAY).read_text(encoding="utf-8"))
    q["items"][4]["resume_status"] = "prep_failed"
    jobfs.atomic_write_json(f.queue_json(DAY), q)
    out = card(root)
    lines = out.splitlines()
    assert "Your PC's job finder last ran Tue 05:30. Double-click 'Run job finder now' on the desktop." in lines, out
    assert "When you're done looking at the spreadsheet, close it and I'll update it." in lines, out
    assert "qc_micro needs a quick re-approval." in lines, out
    f.diy_md(DAY).write_text("# Do it yourself\n\n- E1 | Hooli Health | Lab Scientist | Springfield, IL\n",
                             encoding="ascii")
    out, _ = run("diy", root)
    assert out == "DIY E1 | Hooli Health | Lab Scientist | Springfield, IL", out
    print("ok one-line notes and the do-yourself list")


def run_args(root, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args[:1], "--root", str(root), *args[1:]], capture_output=True,
                       text=True, encoding="utf-8", env=dict(os.environ, JOB_SKILL_NOW=NOW))
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.rstrip("\n").splitlines()


def write_queue(root, items):
    f = jobfs.Folder(root)
    q = json.loads(f.queue_json(DAY).read_text(encoding="utf-8"))
    q["items"], q["size"] = items, len(items)
    jobfs.atomic_write_json(f.queue_json(DAY), q)


def test_job_from_link():
    """A pasted /job <link> (events.py add) is #31 at once: the card, next and
    progress count it before any tick, with the single-job flow's resume."""
    root = make_root("link", morning=QUIET_MORNING, workday=False)
    folder = root / "applications" / f"{DAY} Acme Robotics - Lab Scientist"
    folder.mkdir(parents=True)
    (folder / "posting.md").write_text("URL: https://job-boards.greenhouse.io/acme/jobs/1234567\nCompany: Acme Robotics\n"
                                       "Role: Lab Scientist\nLocation: Springfield, IL\nJob key: greenhouse:1234567\n\n"
                                       "## Description\n\nSynthetic.\n", encoding="ascii")
    line, _ = events.add(root, folder, "pasted")
    assert line == "ADDED #25 greenhouse:1234567 (addendum_user)", line
    assert "Ready: 25. No account needed for any of them." in card(root).splitlines()
    assert run("progress", root)[0].splitlines() == ["PROGRESS applied=0 handed_over=0 skipped=0 later=0 parked=0 "
                                                     "blocked=0 left=25 of 25"]
    for n in range(1, 25):
        events.emit(root, "skipped", {"reason": "user", "question_ids": [], "confirmation": "none"},
                    job_key=f"greenhouse:{9000 + n}", n=n, day=DAY)
    out = run_args(root, "next")
    assert out[0] == "NEXT #25 greenhouse:1234567" and "UPLOAD none (no built resume yet)" in out, out
    (folder / "Jordan Avery Resume.pdf").write_bytes(b"%PDF-1.4 A\n")
    out = run_args(root, "next")
    assert (f"UPLOAD applications/{DAY} Acme Robotics - Lab Scientist/Jordan Avery Resume.pdf "
            "(the resume written for this job, in its folder)") in out, out
    assert "RESUME tier_a_ready tier A" in out, out
    events.emit(root, "applied", {"reason": "", "question_ids": [], "confirmation": "page", "proof": ""},
                job_key="greenhouse:1234567", n=25, day=DAY)
    out = run_args(root, "progress")
    assert out == ["PROGRESS applied=1 handed_over=0 skipped=24 later=0 parked=0 blocked=0 left=0 of 25",
                   "Today: 1 application sent."], out
    print("ok a job from a link joins the list at once and counts in progress")


def test_sensitive_check():
    """next's Check line reads claude/<date>/sensitive_ok.json: the confirmed
    values, or left blank (not confirmed, or the user asked)."""
    root = make_root("sensitive", morning=QUIET_MORNING, workday=False)
    items = [item(n, COMPANIES[n - 1]) for n in (1, 2)]
    items[0]["sensitive"], items[0]["answers_status"] = ["salary", "sponsorship"], "sensitive_confirm"
    write_queue(root, items)
    plan = {"form_source": "greenhouse_api", "fields": [
        {"label": "Desired salary", "status": "sensitive_confirm", "answer": "$95,000-$110,000",
         "answer_source": "profile:salary"},
        {"label": "Will you require sponsorship?", "status": "sensitive_confirm", "answer": "No",
         "answer_source": "profile:sponsorship"}]}
    (root / items[0]["folder"] / "fill_plan.json").write_text(json.dumps(plan), encoding="ascii")
    ok_file = jobfs.Folder(root).claude / DAY / "sensitive_ok.json"
    ok_file.parent.mkdir(parents=True, exist_ok=True)

    def check():
        return next(ln for ln in run_args(root, "next") if ln.startswith("HANDOVER Check:"))
    assert check() == "HANDOVER Check: salary, sponsorship left blank (not confirmed today).", check()
    ok_file.write_text(json.dumps({"v": 1, "date": DAY, "ok": [1], "blank": []}), encoding="ascii")
    assert check() == 'HANDOVER Check: salary "$95,000-$110,000", sponsorship "No" as confirmed today.', check()
    ok_file.write_text(json.dumps({"v": 1, "date": DAY, "ok": "all", "blank": [1]}), encoding="ascii")
    assert check() == "HANDOVER Check: salary, sponsorship left blank, as you asked.", check()
    ok_file.write_text(json.dumps({"v": 1, "date": "2026-11-11", "ok": "all", "blank": []}), encoding="ascii")
    assert check().endswith("left blank (not confirmed today)."), "yesterday's ok does not count"
    print("ok the Check line follows sensitive_ok.json")


def test_short_day():
    """next --short: no-account items only, easiest ATS first, an item in
    progress before them; items with a question, a sensitive field or no
    resume yet are passed over and stay for a normal day."""
    root = make_root("short", morning=QUIET_MORNING, workday=False)
    plan = [(1, "workday", "new"), (2, "workable", "none"), (3, "greenhouse", "none"), (4, "lever", "none"),
            (5, "ashby", "none"), (6, "greenhouse", "none"), (7, "breezy", "none")]
    items = []
    for n, ats, account in plan:
        it = item(n, COMPANIES[n - 1], account=account)
        it["ats"] = ats
        items.append(it)
    items[2]["sensitive"] = ["salary"]
    items[4]["needs"], items[4]["answers_status"] = ["Q-1"], "needs_you"
    (root / items[6]["upload"]).unlink()
    (root / items[6]["folder"] / "resume_B.pdf").unlink()
    write_queue(root, items)

    def first():
        return run_args(root, "next", "--short")[0]
    assert first() == "NEXT #06 greenhouse:9006", first()
    events.emit(root, "applied", {"confirmation": "page"}, job_key="greenhouse:9006", n=6, day=DAY)
    assert first() == "NEXT #04 greenhouse:9004", first()
    events.emit(root, "started", {}, job_key="greenhouse:9002", n=2, day=DAY)
    assert first() == "NEXT #02 greenhouse:9002", "an item in progress comes first"
    for n in (2, 4):
        events.emit(root, "applied", {"confirmation": "page"}, job_key=f"greenhouse:{9000 + n}", n=n, day=DAY)
    assert run_args(root, "next", "--short") == ["NEXT none"]
    assert run_args(root, "next")[0] == "NEXT #01 greenhouse:9001", "the passed-over items stay, with no event"
    # A sensitive field the user settled today (ok or blank) needs no question: #3 comes back.
    ok_file = jobfs.Folder(root).claude / DAY / "sensitive_ok.json"
    ok_file.write_text(json.dumps({"v": 1, "date": DAY, "ok": [], "blank": [3]}), encoding="ascii")
    assert first() == "NEXT #03 greenhouse:9003", first()
    print("ok next --short: easiest no-account items first; the rest stay")


def test_no_runner_card():
    """No runner/out/status.json, or mode M: one line on installing the job
    finder (/job setup), never the desktop shortcut."""
    root = make_root("no_runner", morning=QUIET_MORNING, applied_yesterday=0, contacted=0, workday=False)
    f = jobfs.Folder(root)
    f.status_json.unlink()
    lines = card(root).splitlines()
    assert today_view_mod().NOT_INSTALLED in lines and not any("Double-click" in ln for ln in lines), lines
    f.settings_toml.write_text('[runner]\nmode = "M"\n', encoding="ascii")
    f.queue_json(DAY).unlink()
    lines = card(root).splitlines()
    assert lines[1:] == [today_view_mod().NOT_INSTALLED, "No list today yet. Paste a job link with /job <link>."], lines
    jobfs.atomic_write_json(f.status_json, {"version": "1.2.0", "mode": "L", "min_skill": "1.2.0",
                                            "last_daily": {"at": "2026-11-12T05:41:07-08:00", "ok": True}})
    lines = card(root).splitlines()
    assert today_view_mod().NOT_SWITCHED_ON in lines and not any("Double-click" in ln for ln in lines), lines
    print("ok no job finder installed: the /job setup line")


def test_drafts():
    """The card's draft line points to `today_view.py drafts`, which prints the
    full texts from morning.json."""
    text = ("I have run GMP sterility and endotoxin testing for four years, and the {role} role asks for exactly "
            "that.\nThe second paragraph of the draft.")
    morning = dict(QUIET_MORNING, drafts=[{"n": 7, "label": "Why this role", "text": text},
                                          {"n": 9, "label": "Cover note", "text": "Short note."}])
    root = make_root("drafts", morning=morning, workday=False)
    lines = card(root).splitlines()
    want = f"Draft for #7 (Why this role): {' '.join(text.split())[:90]} (say \"drafts\" for the full text)"
    assert want in lines and not any("today.md" in ln for ln in lines), lines
    assert run_args(root, "drafts") == ["DRAFT #7 (Why this role)"] + text.splitlines() + \
        ["", "DRAFT #9 (Cover note)", "Short note."]
    assert run_args(root, "drafts", "--n", "9") == ["DRAFT #9 (Cover note)", "Short note."]
    assert run_args(make_root("no_drafts", morning=QUIET_MORNING), "drafts") == ["DRAFTS none"]
    print("ok drafts: the card points to them, drafts prints them in full")


def today_view_mod():
    sys.path.insert(0, str(SCRIPTS))
    import today_view
    return today_view


def test_wording():
    for text in CARDS:
        assert report.check_wording(text) == [], report.check_wording(text)
    assert report.check_wording("Only 3 left") != []  # the check itself works
    print(f"ok no banned word in {len(CARDS)} cards")


def main():
    try:
        for t in (test_spec_card, test_zero_lines, test_missing_morning, test_resume_and_next, test_tier_a_sha,
                  test_notes_and_diy, test_job_from_link, test_sensitive_check, test_short_day, test_no_runner_card,
                  test_drafts, test_wording):
            t()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("PASS")


if __name__ == "__main__":
    main()
