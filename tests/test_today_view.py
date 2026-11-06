"""Check job/scripts/today_view.py: the 1.2 card word for word, its zero-count
and time-sensitive rules, the catch-up note, the wording check, next (resume
at the first item without a final event; the Tier A file from final.json and
its sha256 check; the SENSITIVE and Check lines from sensitive_ok.json; the
cover letter; --short), the card and next agreeing on the first item, items
waiting for a base resume, the short-day card, progress, the do-yourself
list, a job from a link counted at once, the no-runner line, config typos
and the drafts.

Usage: python tests/test_today_view.py
Every root is synthetic and built in a temp folder (a hand-made queue.json,
morning.json, fill plans, tracker snapshot and status.json for Jordan Q.
Avery); no runner or network is needed. Stdlib only. Exits non-zero on the
first failure and prints PASS at the end.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
Sensitive answers today: salary $95,000-$110,000 on #3, #7, #12; "no sponsorship needed" on all. Reply yes, or name the ones to leave blank.
3 people you could ask about a job: drafts after #10.
Starting #1 now. Say "short" any time for an easy day."""
MORNING = {
    "v": 1, "date": DAY,
    "urgent": [{"text": 'Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply)'}],
    "questions": [{"id": "Q-1a2b3c", "text": "Have you worked in a BSL-2 lab? Where, and about how long?",
                   "jobs": [3, 7, 12]},
                  {"id": "Q-4d5e6f", "text": "Earliest start date?", "jobs": [], "all_jobs": True, "saved": True}],
    "waiting": 5,
    "referral_drafts": [{"n": 3, "contact_ref": "N-0004"}, {"n": 8, "contact_ref": "N-0011"},
                        {"n": 15, "contact_ref": "N-0020"}],
}
QUIET_MORNING = {"v": 1, "date": DAY, "urgent": [], "questions": [], "waiting": 0, "referral_drafts": []}
COMPANIES = ["Acme Bio", "Globex Therapeutics", "Initech Labs", "Umbrella Biosciences", "Hooli Health",
             "Vandelay Diagnostics", "Stark Biologics", "Wayne Genomics", "Tyrell Bio", "Cyberdyne Labs",
             "Soylent Sciences", "Wonka Bioworks", "Gringotts Pharma", "Monarch Bio", "Oscorp Therapeutics",
             "Piedpiper Bio", "Massive Dynamic", "Aperture Sciences", "Blackmesa Labs", "Dunder Diagnostics",
             "Prestige Bio", "Sterling Genomics", "Bluth Biologics", "Kramerica Labs"]
TENANTS = ["genentech.wd5.myworkdayjobs.com", "gilead.wd1.myworkdayjobs.com"]
SALARY = {"label": "Desired salary", "status": "sensitive_confirm", "answer": "$95,000-$110,000",
          "answer_source": "profile:salary"}
SPONSOR = {"label": "Will you require sponsorship?", "status": "sensitive_confirm", "answer": "No",
           "answer_source": "profile:sponsorship"}


def slug(s):
    return "".join(c for c in s.lower() if c.isalnum())


def item(n, company, ats="greenhouse", account="none", host="job-boards.greenhouse.io", tier="B"):
    s = slug(company)
    folder = f"applications/{DAY} {company} - Scientist" + (f" ({n})" if ats == "workday" else "")
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


def write_plan(root, it, fields):
    """The item's fill_plan.json, and its queue record of answers, as the runner writes them."""
    (root / it["folder"] / "fill_plan.json").write_text(json.dumps({"form_source": "page", "fields": fields}),
                                                        encoding="ascii")
    sens = [("salary" if "salary" in f["answer_source"] else "sponsorship" if "sponsor" in f["answer_source"]
             else f["label"].lower().replace(" ", "_")) for f in fields if f["status"] == "sensitive_confirm"]
    it["sensitive"] = sens
    if sens and it["answers_status"] == "complete":
        it["answers_status"] = "sensitive_confirm"


def make_root(name, morning=MORNING, applied_yesterday=21, contacted=2, workday=True, plans=False):
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
        if plans:
            write_plan(root, it, ([SALARY] if it["n"] in (3, 7, 12) else []) + [SPONSOR])
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


def card(root, *extra):
    r = subprocess.run([sys.executable, str(SCRIPT), "card", "--root", str(root), *extra], capture_output=True,
                       text=True, encoding="utf-8", env=dict(os.environ, JOB_SKILL_NOW=NOW))
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = r.stdout.rstrip("\n")
    CARDS.append(out)
    assert "WARN wording" not in r.stderr, r.stderr
    return out


def run_args(root, *args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args[:1], "--root", str(root), *args[1:]], capture_output=True,
                       text=True, encoding="utf-8", env=dict(os.environ, JOB_SKILL_NOW=NOW))
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.rstrip("\n").splitlines()


def queue_items(root):
    return json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))["items"]


def write_queue(root, items):
    f = jobfs.Folder(root)
    q = json.loads(f.queue_json(DAY).read_text(encoding="utf-8"))
    q["items"], q["size"] = items, len(items)
    jobfs.atomic_write_json(f.queue_json(DAY), q)


def emit(root, kind, n, key=None):
    key = key or next(it["job_key"] for it in queue_items(root) if it["n"] == n)
    events.emit(root, kind, {"reason": "user", "question_ids": [], "confirmation": "page" if kind == "applied"
                             else "none"}, job_key=key, n=n, day=DAY)


def ok_file(root, data):
    p = jobfs.Folder(root).claude / DAY / "sensitive_ok.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict({"v": 1, "date": DAY}, **data)), encoding="ascii")


def test_spec_card():
    root = make_root("spec", plans=True)
    out = card(root)
    assert out == SPEC_CARD, "\n" + out
    print("ok the 1.2 card word for word (the sensitive line from the fill plans, confirmed with yes)")


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
    import today_view
    assert today_view.CATCH_UP_NOTE in lines, out
    assert lines[-1] == 'Starting #1 now. Say "short" any time for an easy day.', out
    assert not any(ln.startswith("Needs you") for ln in lines), out
    print("ok missing morning.json: the catch-up note and #1")


def test_resume_and_next():
    root = make_root("next")
    out, _ = run("next", root)
    assert out.splitlines()[0].startswith("NEXT #01 greenhouse:9001"), out
    emit(root, "applied", 1)
    out, _ = run("next", root)
    lines = out.splitlines()
    assert lines[0] == "NEXT #02 greenhouse:9002", out
    assert "UPLOAD upload/02_Avery_Resume_Globextherap.pdf (Tier B file, sha256 ok)" in lines, out
    assert "ATTACH Attach the file starting with 02 in JobSearch\\upload, then type ok." in lines, out
    assert [ln for ln in lines if ln.startswith("HANDOVER")] == [
        "HANDOVER #02 Globex Therapeutics - Scientist (Greenhouse)", "HANDOVER Then click Submit and type s."], out
    out = card(root)
    assert out.splitlines()[-1].startswith("Starting #2 now."), out
    assert "Ready: 29. No account needed for 23;" in out, out
    out, _ = run("progress", root)
    assert out.splitlines() == ["PROGRESS applied=1 handed_over=0 skipped=0 later=0 parked=0 blocked=0 left=29 of 30",
                                "Today: 1 application sent."], out
    print("ok next after an applied event for #1 is #2; an attach-first handover names no file")


def tier_a_build(root, it, name="Jordan Q. Avery Resume.pdf", letter=None, verdict="ok"):
    folder = root / it["folder"]
    (folder / name).write_bytes(b"%PDF-1.4 Tier A " + it["job_key"].encode() + b"\n")
    if letter:
        (folder / letter).write_bytes(b"%PDF-1.4 Tier A letter\n")
    (folder / "final.json").write_text(json.dumps({"v": 1, "resume": name, "letter": letter,
                                                   "built_at": "2026-11-12T06:40:00-08:00", "verdict": verdict}),
                                       encoding="ascii")
    return folder / name


def test_tier_a_final():
    """contracts-01: the Tier A resume is the file final.json names (a strict
    build keeps '<Name> Resume.pdf'); no final.json, or one that is not ok,
    means the Tier B file; the upload copy is checked by sha256."""
    root = make_root("tier_a")
    it3 = next(it for it in queue_items(root) if it["n"] == 3)
    folder, up = root / it3["folder"], root / it3["upload"]
    for n in (1, 2):
        emit(root, "skipped", n)
    out = run_args(root, "next")
    assert out[0].startswith("NEXT #03"), out
    assert any("Tier A resume is not ready, so this is the fallback" in ln for ln in out), out
    (folder / "Jordan Q. Avery Resume.pdf").write_bytes(b"%PDF-1.4 strict build that failed\n")
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4 an old name nobody reads\n")
    assert any("this is the fallback" in ln for ln in run_args(root, "next")), "a PDF with no final.json is not Tier A"
    src = tier_a_build(root, it3, verdict="failed")
    assert any("this is the fallback" in ln for ln in run_args(root, "next")), "a final.json that is not ok"
    (folder / "final.json").write_text(json.dumps({"v": 1, "resume": "../../upload/x.pdf", "verdict": "ok"}),
                                       encoding="ascii")
    assert any("this is the fallback" in ln for ln in run_args(root, "next")), "a path in final.json is refused"
    src = tier_a_build(root, it3)
    out = run_args(root, "next")
    assert (f"UPLOAD {it3['folder']}/Jordan Q. Avery Resume.pdf (Tier A file in the job folder (the upload copy "
            "is not it yet))") in out, out
    assert f"ATTACH Attach {it3['folder'].replace('/', chr(92))}\\Jordan Q. Avery Resume.pdf (in the JobSearch " \
           "folder), then type ok." in out, out
    shutil.copyfile(src, up)
    jobfs.atomic_write_json(jobfs.Folder(root).runner_out / "staged.json", {"v": 1, "files": {
        up.name: {"sha256": hashlib.sha256(up.read_bytes()).hexdigest()}}})
    out = run_args(root, "next")
    assert f"UPLOAD {it3['upload']} (Tier A file, sha256 ok)" in out, out
    print("ok Tier A: the file final.json names, checked by sha256; a failed build falls back to Tier B")


def test_letter():
    """contracts-12: a built cover letter is named (LETTER), and the attach
    text tells the resume and the letter apart."""
    root = make_root("letter", morning=QUIET_MORNING, workday=False)
    items = queue_items(root)
    it1, it2 = items[0], items[1]
    shutil.copyfile(root / it1["upload"], root / it1["folder"] / "cover_B.pdf")
    shutil.copyfile(root / it1["folder"] / "cover_B.pdf", root / "upload" / "01c_Avery_Cover_Acmebio.pdf")
    out = run_args(root, "next")
    assert "LETTER upload/01c_Avery_Cover_Acmebio.pdf (Tier B letter, sha256 ok)" in out, out
    assert ("ATTACH Attach the files starting with 01 in JobSearch\\upload (01_ is the resume, 01c_ the cover "
            "letter), then type ok.") in out, out
    it2["attach"] = "last"
    shutil.copyfile(root / it2["upload"], root / it2["folder"] / "cover_B.pdf")
    write_queue(root, items)
    emit(root, "applied", 1)
    out = run_args(root, "next")
    assert f"LETTER {it2['folder']}/cover_B.pdf (Tier B letter in the job folder (not staged in upload yet))" in out
    assert not any(ln.startswith("ATTACH") for ln in out), out
    assert (f"HANDOVER Attach upload\\02_Avery_Resume_Globextherap.pdf and "
            f"{it2['folder'].replace('/', chr(92))}\\cover_B.pdf (in the JobSearch folder).") in out, out
    print("ok a cover letter is named, apart from the resume")


def test_one_selector():
    """UD-08, contracts-14: without morning prep the card's Starting #N and
    next agree; Tier A items not built yet come after the rest, never dropped."""
    root = make_root("selector", morning=None, workday=False)
    for n in (1, 2):
        emit(root, "skipped", n)
    lines = card(root).splitlines()
    assert lines[-1].startswith("Starting #4 now."), lines
    assert run_args(root, "next")[0] == "NEXT #04 greenhouse:9004"
    for n in [n for n in range(4, 25) if n != 7]:
        emit(root, "skipped", n)
    lines = card(root).splitlines()
    assert lines[-1].startswith("Starting #3 now."), lines
    assert run_args(root, "next")[0] == "NEXT #03 greenhouse:9003", "Tier A items fill with their Tier B files"
    jobfs.Folder(root).day_marker(DAY, "tier_a").parent.mkdir(parents=True, exist_ok=True)
    jobfs.Folder(root).day_marker(DAY, "tier_a").write_text("", encoding="ascii")
    root2 = make_root("selector2", morning=None, workday=False)
    jobfs.Folder(root2).day_marker(DAY, "tier_a").parent.mkdir(parents=True, exist_ok=True)
    jobfs.Folder(root2).day_marker(DAY, "tier_a").write_text("", encoding="ascii")
    for n in (1, 2):
        emit(root2, "skipped", n)
    assert card(root2).splitlines()[-1].startswith("Starting #3 now."), "after the catch-up, number order"
    assert run_args(root2, "next")[0] == "NEXT #03 greenhouse:9003"
    print("ok card and next pick the same item; un-prepped Tier A items come last")


def test_waiting_variants():
    """UD-05: items whose base resume is not approved wait (no Submit with no
    file); the card names the family and how many jobs wait."""
    root = make_root("waiting", morning=QUIET_MORNING, workday=False)
    items = queue_items(root)
    for it in items[:4]:
        it["resume_status"] = "prep_failed"
        (root / it["folder"] / "resume_B.pdf").unlink()
        (root / it["upload"]).unlink()
    items[3]["family"] = "assay_dev"
    write_queue(root, items)
    sp = jobfs.Folder(root).config / "search_profile.toml"
    sp.write_text('[[family]]\nid = "qc_micro"\nlabel = "QC microbiology"\n', encoding="ascii")
    lines = card(root).splitlines()
    assert "3 jobs wait for a QC microbiology base resume (type /job variants)." in lines, lines
    assert "1 job waits for a assay_dev base resume (type /job variants)." in lines, lines
    assert "Ready: 20. No account needed for any of them." in lines, lines
    assert lines[-1].startswith("Starting #5 now."), lines
    out = run_args(root, "next")
    assert out[0] == "NEXT #05 greenhouse:9005" and out[-1] == "WAIT 4 items with no resume yet: assay_dev 1, qc_micro 3"
    # The builder's count (queue.json counts.variant_wait) names the jobs it could not queue too.
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    q["counts"]["variant_wait"] = {"qc_micro": 40, "postdoc": 7}
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), q)
    lines = card(root).splitlines()
    assert "40 jobs wait for a QC microbiology base resume (type /job variants)." in lines, lines
    assert "7 jobs wait for a postdoc base resume (type /job variants)." in lines, lines
    q["counts"]["variant_wait"] = {}
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), q)
    v1 = jobfs.Folder(root).variant("qc_micro", 1)
    v1.mkdir(parents=True)
    assert "3 jobs wait for your ok on the QC microbiology base resume (type /job variants)." in card(root).splitlines()
    (v1 / "approved.json").write_text("{}", encoding="ascii")
    assert ("The QC microbiology base resume changed, so 3 jobs wait for your ok again (type /job variants)."
            in card(root).splitlines())
    import today_view
    real = today_view.approved_now
    today_view.approved_now = lambda root, fam: fam == "qc_micro"
    try:
        lines = today_view.card(root, jobfs.today(root))
    finally:
        today_view.approved_now = real
    assert ("3 jobs wait for resumes from the QC microbiology base you approved; the job finder builds them on "
            "its next run.") in lines, lines
    for n in range(5, 25):
        emit(root, "applied", n)
    out = run_args(root, "next")
    assert out == ["NEXT none", "WAIT 4 items with no resume yet: assay_dev 1, qc_micro 3"], out
    lines = card(root).splitlines()
    assert lines[-1] == "Nothing else is ready until then.", lines
    print("ok jobs waiting for a base resume: named, counted, never handed over without a file")


def test_notes_and_diy():
    root = make_root("notes", morning=QUIET_MORNING, workday=False)
    f = jobfs.Folder(root)
    st = json.loads(f.status_json.read_text(encoding="utf-8"))
    st["last_daily"]["at"] = "2026-11-10T05:30:00-08:00"
    st["tracker_locked_since"] = "2026-11-12T05:00:00-08:00"
    jobfs.atomic_write_json(f.status_json, st)
    out = card(root)
    lines = out.splitlines()
    assert "Your PC's job finder last ran Tue 05:30. Double-click 'Run job finder now' on the desktop." in lines, out
    assert "When you're done looking at the spreadsheet, close it and I'll update it." in lines, out
    print("ok one-line notes")


def test_diy():
    """contracts-11: the do-yourself list is what the runner stages (leads
    given up, then chat-apply pool items), with or without diy.md; a job
    logged by hand leaves it."""
    root = make_root("diy", morning=QUIET_MORNING, workday=False)
    f = jobfs.Folder(root)
    leads = {"leads": {"a1": {"lead_id": "a1", "status": "diy", "company": "Globex Labs", "title": "QC Analyst",
                              "location": "Springfield, IL", "done_on": "2026-11-10"},
                       "b2": {"lead_id": "b2", "status": "diy", "company": "Hooli Health", "title": "Lab Scientist",
                              "location": "Springfield, IL", "done_on": "2026-11-11"},
                       "c3": {"lead_id": "c3", "status": "resolved", "company": "Initech", "title": "X"}}}
    f.state.mkdir(parents=True, exist_ok=True)
    (f.state / "leads_state.json").write_text(json.dumps(leads), encoding="ascii")
    (f.upload / "E1_Avery_Resume_HooliHealth.pdf").write_bytes(b"%PDF E1")
    (f.upload / "E2_Avery_Resume_GlobexLabs.pdf").write_bytes(b"%PDF E2")
    assert run_args(root, "diy") == [
        "DIY E1 | Hooli Health | Lab Scientist | Springfield, IL | upload/E1_Avery_Resume_HooliHealth.pdf",
        "DIY E2 | Globex Labs | QC Analyst | Springfield, IL | upload/E2_Avery_Resume_GlobexLabs.pdf"]
    tr = json.loads(f.tracker_json.read_text(encoding="utf-8"))
    tr["applications"].append({"ID": 200, "Company": "Hooli Health", "Role": "Lab Scientist", "Status": "Applied",
                               "Applied": "2026-11-11", "Source": "By hand"})
    jobfs.atomic_write_json(f.tracker_json, tr)
    assert run_args(root, "diy") == [
        "DIY E2 | Globex Labs | QC Analyst | Springfield, IL | upload/E2_Avery_Resume_GlobexLabs.pdf"], \
        "a job logged by hand leaves the list; the others keep their numbers and files"
    print("ok the do-yourself list follows the runner's staging and drops jobs logged by hand")


def test_job_from_link():
    """A pasted /job <link> (events.py add) is #25 at once: the card, next and
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
                                                     "blocked=0 left=25 of 25", "Today: no applications sent yet."]
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
    """next's SENSITIVE and Check lines read claude/<date>/sensitive_ok.json:
    a job confirmed today is entered with no Check line (UD-17); otherwise
    the fields stay blank and the Check line says why."""
    root = make_root("sensitive", morning=QUIET_MORNING, workday=False)
    items = [item(n, COMPANIES[n - 1]) for n in (1, 2)]
    write_queue(root, items)
    write_plan(root, items[0], [SALARY, SPONSOR])
    write_queue(root, items)

    def lines():
        return [ln for ln in run_args(root, "next") if ln.startswith(("SENSITIVE", "HANDOVER Check:"))]
    assert lines() == ["SENSITIVE leave blank salary, sponsorship (not confirmed today)",
                       "HANDOVER Check: salary, sponsorship left blank (not confirmed today)."], lines()
    ok_file(root, {"ok": [1], "blank": []})
    assert lines() == ['SENSITIVE enter salary "$95,000-$110,000", sponsorship "No" (confirmed today)'], lines()
    ok_file(root, {"ok": "all", "blank": [1]})
    assert lines() == ["SENSITIVE leave blank salary, sponsorship (the user asked)",
                       "HANDOVER Check: salary, sponsorship left blank, as you asked."], lines()
    ok_file(root, {"ok": "all", "blank": []})
    p = jobfs.Folder(root).claude / DAY / "sensitive_ok.json"
    p.write_text(json.dumps({"v": 1, "date": "2026-11-11", "ok": "all", "blank": []}), encoding="ascii")
    assert lines()[-1].endswith("left blank (not confirmed today)."), "yesterday's yes does not count"
    print("ok the SENSITIVE and Check lines follow sensitive_ok.json; a confirmed job has no Check line")


def test_sensitive_line():
    """UD-14, contracts-17: 'all but #2' instead of a wall of numbers, a count
    past 6 numbers, never a list cut mid-number; settled jobs drop out."""
    root = make_root("sens_line", morning=QUIET_MORNING, workday=False)
    items = queue_items(root)
    for it in items:
        write_plan(root, it, ([] if it["n"] == 2 else [SALARY]) + [SPONSOR])
    write_queue(root, items)
    line = next(ln for ln in card(root).splitlines() if ln.startswith("Sensitive"))
    assert line == ('Sensitive answers today: salary $95,000-$110,000 on all but #2; "no sponsorship needed" on all. '
                    "Reply yes, or name the ones to leave blank."), line
    for it in items:
        write_plan(root, it, ([SALARY] if it["n"] % 2 else []) + [SPONSOR])
    write_queue(root, items)
    line = next(ln for ln in card(root).splitlines() if ln.startswith("Sensitive"))
    assert line.startswith("Sensitive answers today: salary $95,000-$110,000 on 12 jobs; "), line
    ok_file(root, {"ok": list(range(1, 23)), "blank": []})
    line = next(ln for ln in card(root).splitlines() if ln.startswith("Sensitive"))
    assert line.startswith('Sensitive answers today: salary $95,000-$110,000 on #23; "no sponsorship needed" on all.'), line
    ok_file(root, {"ok": "all", "blank": []})
    assert not any(ln.startswith("Sensitive") for ln in card(root).splitlines()), "a yes today is never asked again"
    print("ok the sensitive line: all but, counts, settled jobs left out, never cut")


def test_short_day():
    """next --short: no-account items only, easiest ATS first, an item in
    progress before them; items with a question, a sensitive field beyond the
    profile's salary and sponsorship, or no resume yet are passed over."""
    root = make_root("short", morning=QUIET_MORNING, workday=False)
    plan = [(1, "workday", "new"), (2, "workable", "none"), (3, "greenhouse", "none"), (4, "lever", "none"),
            (5, "ashby", "none"), (6, "greenhouse", "none"), (7, "breezy", "none"), (8, "greenhouse", "none")]
    items = []
    for n, ats, account in plan:
        it = item(n, COMPANIES[n - 1], account=account)
        it["ats"] = ats
        items.append(it)
    write_plan(root, items[2], [{"label": "Visa type", "status": "sensitive_confirm", "answer": "H-1B",
                                 "answer_source": "answers:ANS-003"}])
    write_plan(root, items[7], [SALARY, SPONSOR])
    items[4]["needs"], items[4]["answers_status"] = ["Q-1"], "needs_you"
    (root / items[6]["upload"]).unlink()
    (root / items[6]["folder"] / "resume_B.pdf").unlink()
    write_queue(root, items)

    def first():
        return run_args(root, "next", "--short")[0]
    assert first() == "NEXT #06 greenhouse:9006", first()
    emit(root, "applied", 6)
    assert first() == "NEXT #08 greenhouse:9008", "salary and sponsorship from the profile stay in a short day"
    emit(root, "applied", 8)
    assert first() == "NEXT #04 greenhouse:9004", first()
    emit(root, "started", 2)
    assert first() == "NEXT #02 greenhouse:9002", "an item in progress comes first"
    for n in (2, 4):
        emit(root, "applied", n)
    assert run_args(root, "next", "--short") == ["NEXT none", "WAIT 1 item with no resume yet: qc_micro 1"]
    assert run_args(root, "next")[0] == "NEXT #01 greenhouse:9001", "the passed-over items stay, with no event"
    # A sensitive field the user settled today (yes or blank) needs no question: #3 comes back.
    ok_file(root, {"ok": [], "blank": [3]})
    assert first() == "NEXT #03 greenhouse:9003", first()
    print("ok next --short: easiest no-account items first; the profile's salary and sponsorship stay")


def test_short_card():
    """UD-01: a short day on a list where every job asks salary and
    sponsorship still serves them: the short card shows the one sensitive
    line once, and the closing line has words when nothing was sent."""
    root = make_root("short_card", morning=MORNING, workday=True, plans=True)
    lines = card(root, "--short").splitlines()
    assert lines == ["Short day: I'll do the quick ones.",
                     'Needs you: Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply).',
                     'Sensitive answers today: salary $95,000-$110,000 on #3, #7; "no sponsorship needed" on all. '
                     "Reply yes, or name the ones to leave blank.",
                     'Starting #1 now. Say "stop" any time.'], lines
    assert run_args(root, "next", "--short")[0] == "NEXT #01 greenhouse:9001"
    ok_file(root, {"ok": "all", "blank": []})
    lines = card(root, "--short").splitlines()
    assert not any(ln.startswith("Sensitive") for ln in lines), lines
    assert run_args(root, "progress")[-1] == "Today: no applications sent yet."
    for it in queue_items(root):
        if it["account"] == "none":
            emit(root, "applied", it["n"])
    assert card(root, "--short").splitlines()[-1] == "No quick ones are left today. Type /job for the rest of the list."
    print("ok the short day's card: one sensitive line, then the quick items")


def test_stale_morning():
    """contracts-05: a morning.json written before today's list (or marked
    prepared false) does not hide the list's questions or the catch-up."""
    root = make_root("stale", morning=QUIET_MORNING, workday=False)
    it = queue_items(root)[2]
    (root / it["folder"] / "fill_plan.json").write_text(json.dumps({"fields": [
        {"label": "Have you worked in a BSL-3 lab?", "status": "needs_you", "required": True, "qid": "Q-eb58cc"}]}),
        encoding="ascii")
    mj = jobfs.Folder(root).morning_json(DAY)
    past = time.time() - 3600
    os.utime(mj, (past, past))
    lines = card(root).splitlines()
    assert "1. Have you worked in a BSL-3 lab? (1 job)" in lines, lines
    import today_view
    assert today_view.CATCH_UP_NOTE in lines, lines
    os.utime(mj, None)
    lines = card(root).splitlines()
    assert not any(ln.startswith("Questions") for ln in lines), "a fresh morning.json is this morning's batch"
    mj.write_text(json.dumps(dict(QUIET_MORNING, prepared=False)), encoding="ascii")
    marker = jobfs.Folder(root).day_marker(DAY, "inbox")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="ascii")
    lines = card(root).splitlines()
    assert "1. Have you worked in a BSL-3 lab? (1 job)" in lines and today_view.CATCH_UP_TIER_A in lines, lines
    jobfs.Folder(root).day_marker(DAY, "tier_a").write_text("", encoding="ascii")
    lines = card(root).splitlines()
    assert today_view.CATCH_UP_TIER_A not in lines and today_view.CATCH_UP_NOTE not in lines, lines
    print("ok a morning prep older than the list: the list's questions and the catch-up still show")


def test_card_after_break():
    """UD-10: the card in a new task drops what is done: answered questions,
    drafts for finished jobs, the referral line after the batch, the weekly
    and away lines, the catch-up note."""
    morning = dict(MORNING, away=[{"text": "Acme Bio sent an assessment due Fri"}],
                   weekly=["Last week: 12 applications sent, 2 people contacted."],
                   drafts=[{"n": 3, "label": "Why this role", "text": "Four years of GMP sterility testing."},
                           {"n": 12, "label": "Cover note", "text": "Short note."}])
    root = make_root("after_break", morning=morning, workday=False)
    before = card(root).splitlines()
    assert "While you were away:" in before and "Last week: 12 applications sent, 2 people contacted." in before
    for n in range(1, 11):
        emit(root, "applied", n)
    (root / "answers.md").write_text("# Answers\n\n- ANS-001 | Earliest start date? | 2026-12-01 | date | any | no | "
                                     "user:2026-11-12 | 2026-11-12\n", encoding="ascii")
    lines = card(root).splitlines()
    assert not any(ln.startswith(("While you were away", "Last week", "3 people")) for ln in lines), lines
    assert not any("Earliest start date" in ln for ln in lines), "the bank answers it now"
    assert "1. Have you worked in a BSL-2 lab? Where, and about how long? (1 job)" in lines, lines
    assert not any(ln.startswith("Draft for #3") for ln in lines) and any(ln.startswith("Draft for #12") for ln in lines)
    assert lines[-1].startswith("Starting #11 now."), lines
    print("ok the card after the break drops what is done")


def test_drafts():
    """The card's draft line points to `today_view.py drafts`, which prints the
    full texts from morning.json; a drafted field is not asked (contracts-09,
    UD-12), and next names the draft for the fill and the handover."""
    text = ("I have run GMP sterility and endotoxin testing for four years, and the {role} role asks for exactly "
            "that.\nThe second paragraph of the draft.")
    morning = dict(QUIET_MORNING, questions=[{"id": "Q-0b9578", "text": "Why are you interested in this role?",
                                              "jobs": [7]}], waiting=1,
                   drafts=[{"n": 7, "label": "Why this role", "question_id": "Q-0b9578", "text": text},
                           {"n": 9, "label": "Cover note", "text": "Short note."}])
    root = make_root("drafts", morning=morning, workday=False)
    lines = card(root).splitlines()
    want = f"Draft for #7 (Why this role): {' '.join(text.split())[:90]} (say \"drafts\" for the full text)"
    assert want in lines and not any("today.md" in ln for ln in lines), lines
    assert not any(ln.startswith("Questions") or "Why are you interested" in ln for ln in lines), \
        "a drafted field is not asked again"
    assert run_args(root, "drafts") == ["DRAFT #7 (Why this role)"] + text.splitlines() + \
        ["", "DRAFT #9 (Cover note)", "Short note."]
    assert run_args(root, "drafts", "--n", "9") == ["DRAFT #9 (Cover note)", "Short note."]
    assert run_args(make_root("no_drafts", morning=QUIET_MORNING), "drafts") == ["DRAFTS none"]
    for n in range(1, 7):
        emit(root, "applied", n)
    out = run_args(root, "next")
    assert "DRAFT Why this role" in out and "HANDOVER Check: your Why this role answer (my draft)." in out, out
    print("ok drafts: in full on request, not asked again, checked at the handover")


def test_config_typo():
    """contracts-13: a config file the user broke in Notepad gets one card line."""
    root = make_root("cfg", morning=QUIET_MORNING, workday=False)
    f = jobfs.Folder(root)
    good = '[[family]]\nid = "qc_micro"\nlabel = "QC microbiology"\n'
    f.last_good("search_profile.toml").parent.mkdir(parents=True, exist_ok=True)
    f.last_good("search_profile.toml").write_text(good, encoding="ascii")
    (f.config / "search_profile.toml").write_text(good + 'places = ["Oakland"\n', encoding="ascii")
    lines = card(root).splitlines()
    want = [ln for ln in lines if ln.startswith("Your search_profile.toml has a typo")]
    assert want and "the last copy that worked" in want[0] and want[0].endswith("Type /job settings to fix it."), lines
    (f.config / "search_profile.toml").write_text(good, encoding="ascii")
    assert not any("typo" in ln for ln in card(root).splitlines())
    print("ok a config typo gets one card line")


def test_no_runner_card():
    """No runner/out/status.json, or mode M: one line on installing the job
    finder (/job setup), never the desktop shortcut."""
    root = make_root("no_runner", morning=QUIET_MORNING, applied_yesterday=0, contacted=0, workday=False)
    f = jobfs.Folder(root)
    f.status_json.unlink()
    lines = card(root).splitlines()
    import today_view
    assert today_view.NOT_INSTALLED in lines and not any("Double-click" in ln for ln in lines), lines
    f.settings_toml.write_text('[runner]\nmode = "M"\n', encoding="ascii")
    f.queue_json(DAY).unlink()
    lines = card(root).splitlines()
    assert lines[1:] == [today_view.NOT_INSTALLED, "No list today yet. Paste a job link with /job <link>."], lines
    jobfs.atomic_write_json(f.status_json, {"version": "1.2.0", "mode": "L", "min_skill": "1.2.0",
                                            "last_daily": {"at": "2026-11-12T05:41:07-08:00", "ok": True}})
    lines = card(root).splitlines()
    assert today_view.NOT_SWITCHED_ON in lines and not any("Double-click" in ln for ln in lines), lines
    print("ok no job finder installed: the /job setup line")


def test_runner_state_lines():
    """UD-02/WR-03/WR-04, PIPE-06: while a daily builds today's list the card
    says so instead of the double-click advice; a daily that stopped early, or
    a morning no job site answered, gets one line that is true for the
    restarts a tick still makes."""
    import today_view
    root = make_root("building", morning=QUIET_MORNING, applied_yesterday=0, contacted=0, workday=False)
    f = jobfs.Folder(root)
    f.queue_json(DAY).unlink()
    base = {"version": "1.2.0", "mode": "L", "min_skill": "1.2.0", "tracker_locked_since": None,
            "last_daily": {"at": "2026-11-11T05:41:07-08:00", "date": "2026-11-11", "ok": True}}
    jobfs.atomic_write_json(f.status_json, dict(base, daily_running={"at": "2026-11-12T08:30:00-08:00", "pid": 4}))
    lines = card(root).splitlines()
    assert lines[-1] == today_view.BUILDING and not any("Double-click" in ln for ln in lines), lines
    jobfs.atomic_write_json(f.status_json, dict(base, daily_running={"at": "2026-11-12T06:30:00-08:00", "pid": 4}))
    lines = card(root).splitlines()
    assert lines[-1] != today_view.BUILDING and any("Double-click" in ln for ln in lines), "a daily over 2 hours old"
    root = make_root("stopped", morning=QUIET_MORNING, workday=False)
    f = jobfs.Folder(root)
    stopped = dict(base, last_daily={"at": "2026-11-12T05:41:07-08:00", "date": DAY, "ok": False})
    jobfs.atomic_write_json(f.status_json, stopped)
    assert today_view.STOPPED_RETRY in card(root).splitlines()
    jobfs.atomic_write_json(f.status_json, dict(stopped, daily_running={"at": "2026-11-12T08:50:00-08:00", "pid": 9}))
    assert not any("stopped early" in ln for ln in card(root).splitlines()), "the restarted daily is running now"
    jobfs.atomic_write_json(f.status_json, dict(stopped, daily_starts={"date": DAY, "count": 3,
                                                                        "at": "2026-11-12T08:00:00-08:00"}))
    lines = card(root).splitlines()
    assert today_view.STOPPED_MANUAL in lines and today_view.STOPPED_RETRY not in lines, lines
    offline = dict(base, last_daily={"at": "2026-11-12T05:41:07-08:00", "date": DAY, "ok": True},
                   errors=[f"offline {DAY}: no job board answered this morning"])
    jobfs.atomic_write_json(f.status_json, offline)
    assert today_view.OFFLINE_RETRY in card(root).splitlines()
    jobfs.atomic_write_json(f.status_json, dict(offline, errors=["offline 2026-11-11: no job board answered"]))
    assert not any("could not be reached" in ln for ln in card(root).splitlines()), "yesterday's offline morning"
    f.settings_toml.write_text('[runner]\nmode = "S"\n', encoding="ascii")
    jobfs.atomic_write_json(f.status_json, dict(stopped, mode="S"))
    assert not any("stopped early" in ln for ln in card(root).splitlines()), "mode S has no tick to restart it"
    print("ok the card says the list is being built, or that the morning run stopped or found no job site")


def test_computed_checks():
    """TR-02 / spec 4.6: a computed answer (status check, such as years of a
    technique) is shown on the handover's Check line with its arithmetic."""
    root = make_root("computed", morning=QUIET_MORNING, workday=False)
    items = [item(n, COMPANIES[n - 1]) for n in (1, 2)]
    years = {"label": "Years of real-time PCR experience", "status": "check", "answer": "16",
             "answer_source": "years:real-time PCR",
             "note": "YEARS real-time PCR 16 (192 months: May 2007 - Apr 2023; from L-0004, L-0010, L-0014)"}
    write_plan(root, items[0], [SALARY, years, {"label": "Social Security number", "status": "check", "answer": ""}])
    write_queue(root, items)
    want = ('Years of real-time PCR experience "16" (YEARS real-time PCR 16 (192 months: May 2007 - Apr 2023; '
            'from L-0004, L-0010, L-0014))')
    lines = [ln for ln in run_args(root, "next") if ln.startswith("HANDOVER Check:")]
    assert lines == [f"HANDOVER Check: salary left blank (not confirmed today); {want}."], lines
    ok_file(root, {"ok": [1], "blank": []})
    lines = [ln for ln in run_args(root, "next") if ln.startswith("HANDOVER Check:")]
    assert lines == [f"HANDOVER Check: {want}."], "a confirmed sensitive field drops out; the computed one stays"
    print("ok computed answers are on the Check line with their arithmetic")


def test_stale_upload_copy():
    """An upload copy that is not this job's resume_B.pdf (an earlier day's
    file under today's name) is never handed over: next names the job-folder
    file, and the attach line says to attach from the folder."""
    root = make_root("stale_upload", morning=QUIET_MORNING, workday=False)
    it = queue_items(root)[0]
    (root / it["upload"]).write_bytes(b"%PDF-1.4 another day's resume\n")
    lines = run_args(root, "next")
    assert f"UPLOAD {it['folder']}/resume_B.pdf (the upload copy is not this job's file; attach it from the job " \
           "folder)" in lines, lines
    assert any(ln.startswith("ATTACH Attach ") and "resume_B.pdf" in ln and "JobSearch\\upload" not in ln
               for ln in lines), lines
    print("ok an upload copy that differs is never handed over")


def test_wording():
    for text in CARDS:
        assert report.check_wording(text) == [], report.check_wording(text)
    assert report.check_wording("Only 3 left") != []  # the check itself works
    print(f"ok no banned word in {len(CARDS)} cards")


def main():
    only = sys.argv[1:]
    tests = (test_spec_card, test_zero_lines, test_missing_morning, test_resume_and_next, test_tier_a_final,
             test_letter, test_one_selector, test_waiting_variants, test_notes_and_diy, test_diy, test_job_from_link,
             test_sensitive_check, test_sensitive_line, test_short_day, test_short_card, test_stale_morning,
             test_card_after_break, test_drafts, test_config_typo, test_no_runner_card, test_runner_state_lines,
             test_computed_checks, test_stale_upload_copy, test_wording)
    try:
        for t in tests:
            if not only or t.__name__ in only or t.__name__.removeprefix("test_") in only:
                t()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("PASS")


if __name__ == "__main__":
    main()
