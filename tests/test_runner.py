"""Check job/scripts/jobrunner.py: the lock, the tick (session rules, leads into
an addendum, a pasted job into addendum_user.json, the idle exit, requests),
the code-execution boundary and the selftest.

Usage: python tests/test_runner.py
It runs one offline daily on a temp copy of tests/fixtures/folder (about 15 s)
and copies that day for each case. Every case pins the clock (JOB_SKILL_NOW)
and sets JOBRUNNER_HOME to a temp folder, so nothing touches the real
%LOCALAPPDATA%\\JobRunner. Exits non-zero on the first failure and prints PASS.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
RUNNER = SCRIPTS / "jobrunner.py"
FIXTURE = HERE / "fixtures" / "folder"
BOARDS = HERE / "fixtures" / "boards"
RESOLVE = HERE / "fixtures" / "resolve"
DAY = "2026-11-06"
NOW = "2026-11-06T17:00:00Z"          # 09:00 in Springfield's clock (America/Los_Angeles)
sys.path.insert(0, str(SCRIPTS))
import jobfs  # noqa: E402
import leads  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="job_runner_test_"))
LEAD_JOBS = [("Northstar Bio", "QC Microbiologist II", 880101), ("Orchard Labs", "QC Microbiologist I", 880202),
             ("Prairie Therapeutics", "Microbiology Analyst", 880303), ("Quarry Biosciences",
                                                                       "Sterility Assurance Microbiologist", 880404)]


def env(**extra):
    e = {k: v for k, v in os.environ.items() if k not in ("JOB_SKILL_NOW", "JOBRUNNER_HOME", "PYTHONPATH")}
    e.update(JOB_SKILL_NOW=NOW, JOBRUNNER_HOME=str(TMP / "runner_home"))
    e.update(extra)
    return e


def run(cmd, root, *extra, offline=BOARDS, now=NOW, flags=()):
    args = [sys.executable, *flags, str(RUNNER), cmd, "--root", str(root)]
    if offline:
        args += ["--offline", str(offline)]
    r = subprocess.run(args + list(extra), capture_output=True, text=True, encoding="utf-8",
                       env=env(JOB_SKILL_NOW=now), cwd=str(root))
    return r.returncode, r.stdout.splitlines(), r.stderr


_DAY_ROOT = None


def day_root():
    """A copy of the fixture after its daily (built once)."""
    global _DAY_ROOT
    if _DAY_ROOT is None:
        _DAY_ROOT = TMP / "day" / "JobSearch"
        shutil.copytree(FIXTURE, _DAY_ROOT)
        for p in _DAY_ROOT.rglob("*"):
            if p.is_file() and p.suffix in (".json", ".md", ".toml", ".csv", ".jsonl", ".txt"):
                b = p.read_bytes()
                if b"\r\n" in b:
                    p.write_bytes(b.replace(b"\r\n", b"\n"))
        code, lines, err = run("daily", _DAY_ROOT, "--date", DAY,
                               now="2026-11-06T13:30:00Z")
        assert code == 0 and lines[-1].startswith("DAILY ok queue=30 "), (lines[-6:], err[-1500:])
    return _DAY_ROOT


def copy_day(name):
    dest = TMP / name / "JobSearch"
    shutil.copytree(day_root(), dest)
    return dest


def beat(root, minutes_ago):
    t = datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(minutes=minutes_ago)
    jobfs.atomic_write_text(jobfs.Folder(root).session_json, json.dumps(
        {"task": "test", "started": t.isoformat(), "beat": t.isoformat(), "current_n": 3}) + "\n")


def snapshot(path):
    return {p.relative_to(path).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in Path(path).rglob("*") if p.is_file()}


def write_lock(root, started):
    lock = jobfs.Folder(root).state / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 99999, "started": started, "cmd": "daily"}), encoding="ascii")
    return lock


def request_file(root, op, args, req_id="20261106T170000Z_0000aaaa"):
    """A request written straight to runner/in/requests (as a careless writer would)."""
    path = jobfs.Folder(root).request(req_id, op)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"v": 1, "id": req_id, "op": op, "args": args})
    path.write_text(body, encoding="ascii")
    old = time.time() - 60  # not a file still arriving
    os.utime(path, (old, old))
    return path


def result_of(root, req_id):
    return json.loads(jobfs.Folder(root).result(req_id).read_text(encoding="utf-8"))


# ---------- the lock ----------

def test_locked():
    root = copy_day("locked")
    lock = write_lock(root, "2026-11-06T16:50:00+00:00")  # 10 minutes before NOW: held, not stale
    code, lines, err = run("daily", root)
    assert code == 8 and lines and lines[0].startswith("LOCKED"), (code, lines, err)
    assert lock.exists()
    code, lines, _ = run("tick", root)
    assert code == 8 and lines[0].startswith("LOCKED"), (code, lines)
    print("ok locked")


def test_stale_lock():
    root = copy_day("stale")
    old = (datetime.fromisoformat(NOW.replace("Z", "+00:00")) - timedelta(hours=3)).isoformat()
    lock = write_lock(root, old)
    code, lines, err = run("tick", root)
    assert code == 0, (code, lines, err)
    assert lines[0].startswith("STALE LOCK run.lock pid=99999 cmd=daily"), lines
    assert not lock.exists()
    log = (root / "logs" / f"runner_{DAY}.log").read_text(encoding="utf-8")
    assert "STALE LOCK" in log, log[-500:]
    print("ok stale lock")


# ---------- the tick ----------

def offline_leads(dest):
    """Greenhouse boards for the four lead companies (built from the resolve fixture)."""
    dest.mkdir(parents=True)
    base = json.loads((RESOLVE / "gh_acmebio_900101.json").read_text(encoding="utf-8"))
    index = {}
    for company, title, jid in LEAD_JOBS:
        slug = "".join(c for c in company.lower() if c.isalnum())
        job = dict(base, id=jid, title=title, company_name=company, requisition_id=f"R-{jid}",
                   location={"name": "Redwood City, CA"},
                   absolute_url=f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}",
                   first_published="2026-11-05T09:00:00-08:00", updated_at="2026-11-05T09:00:00-08:00",
                   content=(f"&lt;p&gt;{company} makes vaccines.&lt;/p&gt;&lt;h3&gt;Requirements&lt;/h3&gt;"
                            "&lt;ul&gt;&lt;li&gt;GMP, aseptic technique and environmental monitoring.&lt;/li&gt;"
                            f"&lt;/ul&gt;&lt;p&gt;Opening {jid}.&lt;/p&gt;"))
        (dest / f"{slug}_list.json").write_text(json.dumps({"jobs": [job], "meta": {"total": 1}}), encoding="ascii")
        (dest / f"{slug}_{jid}.json").write_text(json.dumps(dict(job, questions=[])), encoding="ascii")
        api = f"https://boards-api.greenhouse.io/v1/boards/{slug}"
        index[f"{api}/jobs?content=true"] = f"{slug}_list.json"
        index[f"{api}/jobs/{jid}?questions=true"] = f"{slug}_{jid}.json"
    (dest / "index.json").write_text(json.dumps(index, indent=1), encoding="ascii")
    return dest


def write_leads(root):
    salt = leads.salt(root)
    lines = []
    for i, (company, title, _) in enumerate(LEAD_JOBS):
        fields = {"company": company, "title": title, "location": "Redwood City, CA"}
        lines.append(json.dumps(leads.lead_line("linkedin_alert", fields, DAY, f"{i:016x}", salt)))
    path = jobfs.Folder(root).lead_file(DAY, "0900", "linkedin")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def test_tick_session_addendum():
    root = copy_day("leads")
    offline = offline_leads(TMP / "leads_offline")
    write_leads(root)
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    req_id, _ = jobrunner.make_request(root, "resolve_leads", {})
    beat(root, 5)
    qjson = jobfs.Folder(root).queue_json(DAY)
    q_before = qjson.read_bytes()
    up_before = snapshot(root / "upload")
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0, (code, lines, err[-2000:])
    adds = sorted(jobfs.Folder(root).queue_dir(DAY).glob("addendum_*.json"))
    assert len(adds) == 1, (adds, lines)
    data = json.loads(adds[0].read_text(encoding="utf-8"))
    assert [it["n"] for it in data["items"]] == [31, 32, 33, 34], ([it["n"] for it in data["items"]], lines)
    assert {it["company"] for it in data["items"]} == {c for c, _, _ in LEAD_JOBS}
    for it in data["items"]:
        folder = root / it["folder"]
        assert (folder / "resume_B.pdf").exists() and (folder / "fill_plan.json").exists(), it["folder"]
        assert it["resume_status"] in ("built", "fallback_base"), it["resume_status"]
    assert qjson.read_bytes() == q_before
    assert snapshot(root / "upload") == up_before
    res = result_of(root, req_id)
    assert res["ok"] and res["op"] == "resolve_leads" and res["result"]["new_items"] == 4, res
    assert not list(jobfs.Folder(root).requests.glob("*.json"))
    print("ok tick with an active session writes addendum 31-34")
    # Once the session goes stale, the next tick stages the new files.
    beat(root, 30)
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0, (lines, err[-1500:])
    names = {p.name for p in (root / "upload").iterdir() if p.is_file()}
    assert any(n.startswith("31_") for n in names) and any(n.startswith("34_") for n in names), sorted(names)
    assert len(names) == 34, sorted(names)
    request_file(root, "more", {"limit": 2}, "20261106T171000Z_0000bbbb")
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0 and any(ln.startswith("ADDENDUM ") and "items=35-36" in ln for ln in lines), lines
    assert len([p for p in (root / "upload").iterdir() if p.is_file()]) == 36
    print("ok stale session: addendum files staged; more gives 35-36")


def test_idle_tick():
    root = copy_day("idle")
    code, lines, err = run("tick", root)  # the first tick of the day checks the next items' liveness
    assert code == 0, (lines, err[-1000:])
    t0 = time.time()
    code, lines, err = run("tick", root)
    took = time.time() - t0
    assert code == 0 and lines == ["TICK idle"], (lines, err[-800:])
    # The process start is most of it; the tick's own work must stay under a second.
    assert took < 1.0, f"idle tick took {took:.2f} s"
    empty = TMP / "empty" / "JobSearch"
    empty.mkdir(parents=True)
    t0 = time.time()
    code, lines, _ = run("tick", empty, offline=None)
    assert code == 0 and lines == ["TICK idle"] and time.time() - t0 < 1.0, lines
    print(f"ok idle tick ({took:.2f} s)")


def test_stage_tier_a():
    root = copy_day("tier_a")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    it7 = next(it for it in q["items"] if it["n"] == 7)
    folder = root / it7["folder"]
    tier_a = b"%PDF-1.4\n% Tier A resume for the test\n%%EOF\n"
    (folder / "resume.pdf").write_bytes(tier_a)
    up = root / it7["upload"]
    assert up.exists() and up.read_bytes() != tier_a
    request_file(root, "stage_tier_a", {"n": 7}, "20261106T170500Z_0000cccc")
    code, lines, err = run("tick", root)
    assert code == 0, (lines, err[-1000:])
    assert up.read_bytes() == tier_a, "upload/07_* was not replaced"
    import hashlib
    digest = hashlib.sha256(tier_a).hexdigest()
    res = result_of(root, "20261106T170500Z_0000cccc")
    assert res["ok"] and res["result"]["sha256"] == digest and res["result"]["file"] == it7["upload"], res
    staged = json.loads((root / "runner" / "out" / "staged.json").read_text(encoding="utf-8"))
    assert staged["files"][Path(it7["upload"]).name]["sha256"] == digest, staged
    names = sorted(p.name for p in (root / "upload").iterdir() if p.is_file())
    assert sum(1 for n in names if n.startswith("07")) == 1 and len(names) == 30, names
    r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "next", "--root", str(root)],
                       capture_output=True, text=True, env=env())
    assert r.returncode == 0, r.stdout
    print("ok stage_tier_a replaces upload/07 and records the sha256")


def test_rejected_requests():
    root = copy_day("reject")
    outside = TMP / "outside_folder"
    outside.mkdir()
    request_file(root, "stage_tier_a", {"folder": str(outside)}, "20261106T170100Z_0000dddd")
    request_file(root, "build", {"folder": "../outside_folder"}, "20261106T170200Z_0000eeee")
    request_file(root, "probe_url", {"url": "https://www.linkedin.com/jobs/view/1"}, "20261106T170300Z_0000ffff")
    request_file(root, "resolve_leads", {"evil": "x"}, "20261106T170400Z_00001111")
    code, lines, err = run("tick", root)
    assert code == 0, (lines, err[-1000:])
    for rid, words in (("20261106T170100Z_0000dddd", "outside"), ("20261106T170200Z_0000eeee", "outside"),
                       ("20261106T170300Z_0000ffff", "DENY job-board"), ("20261106T170400Z_00001111", "no argument")):
        res = result_of(root, rid)
        assert res["ok"] is False and any(words in ln for ln in res["lines"]), (rid, res)
    assert not list(jobfs.Folder(root).requests.glob("*.json"))
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    try:
        jobrunner.make_request(root, "build", {"folder": str(outside)})
        raise AssertionError("make_request took a folder outside the root")
    except jobrunner.RequestError as e:
        assert "outside" in str(e), e
    print("ok requests with a folder outside the root are rejected")


def test_no_import_from_root():
    root = copy_day("boundary")
    trap = "raise RuntimeError('imported from the JobSearch folder')\n"
    for name in ("json", "jobfs", "events", "gates", "tracker", "statistics", "zoneinfo", "csv", "posting",
                 "sitecustomize"):
        (root / f"{name}.py").write_text(trap, encoding="ascii")
    write_leads(root)
    offline = offline_leads(TMP / "boundary_offline")
    code = ("import sys; sys.path.insert(0, %r); import jobrunner; "
            "rc = jobrunner.main(['tick', '--root', %r, '--offline', %r]); "
            "root = %r.lower(); "
            "bad = sorted(n for n, m in list(sys.modules.items()) "
            "if (getattr(m, '__file__', None) or '').lower().startswith(root)); "
            "inpath = [p for p in sys.path if p and p.lower().startswith(root)]; "
            "print('RC', rc); print('BAD', bad); print('PATH', inpath)") % (str(SCRIPTS), str(root), str(offline),
                                                                         str(root.resolve()))
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    jobrunner.make_request(root, "resolve_leads", {})
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, encoding="utf-8",
                       env=env(PYTHONPATH=str(root)), cwd=str(root))
    out = r.stdout.splitlines()
    assert r.returncode == 0 and "RC 0" in out, (out[-8:], r.stderr[-1500:])
    assert "BAD []" in out and "PATH []" in out, out[-4:]
    # The script form with the folder as the working directory, too.
    code_, lines, err = run("tick", root, flags=("-I",))
    assert code_ == 0, (lines, err[-800:])
    print("ok nothing is imported from the JobSearch folder")


def test_events_and_replan():
    """An applied event moves #1's upload file to _done; an unparked event
    gets #5 a fresh fill plan (the folder is not claimed)."""
    root = copy_day("events")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    one, five = q["items"][0], q["items"][4]
    plan5 = root / five["folder"] / "fill_plan.json"
    plan5.write_text('{"form_source": "stale"}', encoding="ascii")
    sys.path.insert(0, str(SCRIPTS))
    import events
    events.emit(root, "applied", {"reason": "", "question_ids": [], "confirmation": "page", "proof": ""},
                job_key=one["job_key"], n=1, day=DAY)
    events.emit(root, "unparked", {"reason": "unknown_required", "question_ids": ["Q-abc123"],
                                   "confirmation": "none", "proof": ""}, job_key=five["job_key"], n=5, day=DAY)
    for p in jobfs.Folder(root).events.glob("*.json"):
        old = time.time() - 60
        os.utime(p, (old, old))
    code, lines, err = run("tick", root)
    assert code == 0 and "APPLIED 2 PENDING 0 REJECTED 0" in lines, (lines, err[-1500:])
    assert f"REPLAN #05 {five['job_key']}" in lines, lines
    assert "fields" in json.loads(plan5.read_text(encoding="utf-8")), plan5.read_text()
    up = root / one["upload"]
    assert not up.exists() and (root / "upload" / "_done" / DAY / up.name).exists(), "#1 not moved to _done"
    assert len([p for p in (root / "upload").iterdir() if p.is_file()]) == 29
    assert not list(jobfs.Folder(root).events.glob("*.json"))
    import csv
    with open(root / "reports" / "funnel.csv", encoding="utf-8", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["date"] == DAY)
    assert row["submitted"] == "1" and row["confirmed"] == "1", row
    print("ok events: applied file moved, unparked job re-planned, funnel updated")


def test_tick_user_addendum():
    """A pasted job (events.py add) is #31 at once; the tick writes it into
    addendum_user.json with that number and writes nothing in its folder."""
    root = copy_day("user_add")
    folder = root / "applications" / f"{DAY} Acme Robotics - Lab Scientist"
    folder.mkdir(parents=True)
    (folder / "posting.md").write_text("URL: https://job-boards.greenhouse.io/acme/jobs/1234567\nCompany: Acme Robotics\n"
                                       "Role: Lab Scientist\nLocation: Springfield, IL\nJob key: greenhouse:1234567\n\n"
                                       "## Description\n\nSynthetic.\n", encoding="ascii")
    sys.path.insert(0, str(SCRIPTS))
    import events
    line, ev = events.add(root, folder, "pasted", day=DAY)
    assert line == "ADDED #31 greenhouse:1234567 (addendum_user)", line
    p = jobfs.Folder(root).event(ev["id"], "added")
    old = time.time() - 60
    os.utime(p, (old, old))
    code, lines, err = run("tick", root)
    assert code == 0 and "ADDENDUM addendum_user.json items=31" in lines, (lines, err[-1500:])
    data = json.loads((jobfs.Folder(root).queue_dir(DAY) / "addendum_user.json").read_text(encoding="utf-8"))
    assert [(it["n"], it["job_key"], it["added_by"]) for it in data["items"]] == [(31, "greenhouse:1234567", "pasted")]
    assert sorted(x.name for x in folder.iterdir()) == ["posting.md"], "the runner wrote in the session's folder"
    assert not list(jobfs.Folder(root).events.glob("*.json"))
    r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "progress", "--root", str(root)],
                       capture_output=True, text=True, env=env())
    assert r.stdout.splitlines()[0].endswith("left=31 of 31"), r.stdout
    print("ok tick writes a pasted job into addendum_user.json as #31")


def test_diy_staging():
    root = copy_day("diy")
    f = jobfs.Folder(root)
    state = json.loads((f.state / "leads_state.json").read_text(encoding="utf-8"))
    state["leads"]["feedfacecafebeef"] = {"lead_id": "feedfacecafebeef", "company": "Hooli Health",
                                          "title": "QC Microbiologist", "location": "Redwood City, CA",
                                          "source": "linkedin_alert", "seen": DAY, "status": "diy",
                                          "done_on": DAY, "tries": ["2026-11-05", DAY], "job_keys": []}
    (f.state / "leads_state.json").write_text(json.dumps(state), encoding="ascii")
    f.diy_md(DAY).write_text("# Do it yourself\n\n- E1 | Hooli Health | QC Microbiologist | Redwood City, CA\n",
                             encoding="ascii")
    code, lines, err = run("daily", root, "--date", DAY, now="2026-11-06T13:30:00Z")
    assert code == 0 and "DIY 1 item, 1 file staged" in lines, (lines[-5:], err[-800:])
    e1 = [p for p in f.upload.iterdir() if p.name.startswith("E1_")]
    assert [p.name for p in e1] == ["E1_Avery_Resume_HooliHealth.pdf"], e1
    sheet = f.diy_dir(DAY) / "Hooli Health.md"
    text = sheet.read_text(encoding="utf-8")
    assert "upload\\E1_Avery_Resume_HooliHealth.pdf" in text, text
    assert "{company}" not in text and "Hooli Health role" not in text, text
    assert "/jobs/" not in text and "jobs/view" not in text, "a posting link in the sheet"
    r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "diy", "--root", str(root)],
                       capture_output=True, text=True, env=env())
    assert r.stdout.strip() == ("DIY E1 | Hooli Health | QC Microbiologist | Redwood City, CA | "
                                "upload/E1_Avery_Resume_HooliHealth.pdf"), r.stdout
    print("ok do-yourself file and answer sheet")


# ---------- selftest ----------

def mock_net(dest, robots_ok=True):
    """Offline answers for the selftest's robots fetch and reach checks."""
    dest.mkdir(parents=True)
    (dest / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="ascii")
    (dest / "ok.json").write_text('{"jobs": [], "offers": [{"id": 1}], "total": 0, "jobPostings": []}', encoding="ascii")
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    index = {jobrunner.SELFTEST_ROBOTS: "robots.txt" if robots_ok else {"error": "connection refused"}}
    for _, method, url in jobrunner.SELFTEST_HOSTS:
        index[(f"POST {url}" if method == "POST" else url)] = "ok.json"
    index[jobrunner.SELFTEST_RECRUITEE] = "ok.json"
    (dest / "index.json").write_text(json.dumps(index, indent=1), encoding="ascii")
    return dest


def test_selftest():
    root = TMP / "selftest" / "JobSearch"
    root.mkdir(parents=True)
    code, lines, err = run("selftest", root, "--net", offline=mock_net(TMP / "net_ok"))
    assert code == 0, (lines, err[-1500:])
    for name in ("python", "packages", "tzdata", "folder", "robots", "build", "reach greenhouse", "reach lever",
                 "reach ashby", "reach workday", "reach usajobs", "reach recruitee"):
        assert any(ln.startswith(f"PASS {name}") for ln in lines), (name, lines)
    assert lines[-1] == "SELFTEST PASS", lines
    status = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    assert status["selftest"]["net"] == "PASS" and status["selftest"]["recruitee"] == "PASS", status
    code, lines, _ = run("selftest", root, offline=mock_net(TMP / "net_bad", robots_ok=False))
    assert code == 1 and any(ln.startswith("FAIL robots ") for ln in lines), lines
    assert lines[-1] == "SELFTEST FAIL robots", lines
    code, lines, _ = run("selftest", root, offline=None)
    assert code == 0 and any(ln.startswith("PASS robots parser") for ln in lines), lines
    print("ok selftest")


def test_request_cli():
    root = TMP / "cli" / "JobSearch"
    root.mkdir(parents=True)
    r = subprocess.run([sys.executable, str(RUNNER), "request", "more", "--root", str(root), "--json", '{"limit": 5}'],
                       capture_output=True, text=True, env=env())
    assert r.returncode == 0 and r.stdout.startswith("REQUEST 20261106T170000Z_"), r.stdout
    files = list(jobfs.Folder(root).requests.glob("*_more.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["args"] == {"limit": 5}
    r = subprocess.run([sys.executable, str(RUNNER), "request", "probe_url", "--root", str(root), "--json",
                        '{"url": "https://www.indeed.com/viewjob?jk=1"}'], capture_output=True, text=True, env=env())
    assert r.returncode == 1 and r.stdout.startswith("ERROR DENY"), r.stdout
    r = subprocess.run([sys.executable, str(RUNNER), "status", "--root", str(root)], capture_output=True, text=True,
                       env=env())
    assert r.returncode == 0 and r.stdout.startswith("STATUS none"), r.stdout
    print("ok request and status CLI")


# ---------- the installer (Windows) ----------

REPO = HERE.parent
PS1 = REPO / "runner" / "install_runner.ps1"
UNINSTALL = REPO / "runner" / "uninstall_runner.ps1"
CMD = REPO / "runner" / "install_runner.cmd"


def powershell(*args):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)


def our_tasks():
    r = powershell("-Command", "Get-ScheduledTask -TaskName 'JobRunner-*' -ErrorAction SilentlyContinue | "
                               "ForEach-Object { $_.TaskName }")
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def test_installer_dry_run():
    if os.name != "nt" or not shutil.which("powershell"):
        print("skip installer dry run (Windows PowerShell only)")
        return
    folder, home = TMP / "dry" / "JobSearch", TMP / "dry" / "JobRunner"
    before = our_tasks()
    r = powershell("-File", str(PS1), "-DryRun", "-Folder", str(folder), "-InstallRoot", str(home))
    out = r.stdout.splitlines()
    assert r.returncode == 0, (r.stdout[-1500:], r.stderr[-1500:])
    assert f"VENV {home / 'venv'}" in out, out
    pip = next(ln for ln in out if ln.startswith("PIP "))
    assert "-m pip install --require-hashes" in pip and "requirements.lock" in pip, pip
    daily = next(ln for ln in out if ln.startswith("TASK JobRunner-Daily: "))
    tick = next(ln for ln in out if ln.startswith("TASK JobRunner-Tick: "))
    for want in ("daily 05:30", "StartWhenAvailable", "WakeToRun", "pythonw.exe -I -m jobrunner daily"):
        assert want in daily, (want, daily)
    for want in ("every 15 minutes", "06:00-22:00", "IgnoreNew", "pythonw.exe -I -m jobrunner tick"):
        assert want in tick, (want, tick)
    assert out[-1].startswith("DRY RUN nothing was changed"), out[-2:]
    assert not home.exists() and not folder.exists(), "the dry run wrote files"
    assert our_tasks() == before, "the dry run registered a task"
    for path in (PS1, UNINSTALL, CMD):
        text = path.read_text(encoding="ascii").lower()
        for bad in ("runas", "-verb", "highest", "start-process"):
            assert bad not in text, (path.name, bad)
    r = powershell("-File", str(UNINSTALL), "-DryRun", "-NoTasks", "-InstallRoot", str(home))
    assert r.returncode == 0 and "KEPT the JobSearch folder" in r.stdout, r.stdout
    pssa = os.environ.get("JOB_SKILL_PSSA", "PSScriptAnalyzer")
    r = powershell("-Command", f"if (-not (Get-Module -ListAvailable '{pssa}')) {{ 'NOPSSA'; exit 0 }}; "
                               f"Import-Module '{pssa}'; "
                               f"Invoke-ScriptAnalyzer -Path '{PS1}','{UNINSTALL}' -Severity Error | "
                               "ForEach-Object { 'PSSA ' + $_.RuleName + ' line ' + $_.Line }")
    if "NOPSSA" in r.stdout:
        print("skip PSScriptAnalyzer (not installed; set JOB_SKILL_PSSA to its module path)")
    else:
        assert not [ln for ln in r.stdout.splitlines() if ln.startswith("PSSA")], r.stdout
        print("ok PSScriptAnalyzer: no errors")
    print("ok installer dry run registers nothing")


def test_cmd_hash_mismatch():
    if os.name != "nt":
        print("skip install_runner.cmd (Windows only)")
        return
    import hashlib
    import zipfile
    work = TMP / "cmd"
    work.mkdir()
    good = work / "jobrunner-0.0.0.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("app/jobrunner.py", "# test only\n")
    sha = hashlib.sha256(good.read_bytes()).hexdigest()
    cmd = work / "install_runner.cmd"
    cmd.write_bytes(CMD.read_bytes().replace(b"@@VERSION@@", b"0.0.0-test").replace(b"@@ZIP_SHA256@@", sha.encode()))
    bad = work / "changed.zip"
    data = bytearray(good.read_bytes())
    data[len(data) // 2] ^= 0x01
    bad.write_bytes(bytes(data))
    app = Path(os.environ.get("LOCALAPPDATA", str(work))) / "JobRunner" / "app" / "0.0.0-test"
    r = subprocess.run(["cmd", "/c", str(cmd), str(bad)], capture_output=True, text=True, errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 1 and "does not match its SHA-256" in r.stdout, (r.returncode, r.stdout[-800:])
    assert "Python" not in r.stdout.split("does not match")[0], r.stdout
    assert not app.exists(), app
    print("ok install_runner.cmd refuses a zip with one changed byte")


def test_live_install():
    """JOB_SKILL_LIVE=1: a real install (pip from PyPI) into a temp root, no tasks."""
    if os.environ.get("JOB_SKILL_LIVE") != "1" or os.name != "nt":
        return
    import zipfile
    src = TMP / "live_zip.zip"
    with zipfile.ZipFile(src, "w") as z:
        for p in sorted(SCRIPTS.glob("*.py")):
            z.write(p, f"app/{p.name}")
        for p in (PS1, UNINSTALL, REPO / "runner" / "requirements.lock"):
            z.write(p, p.name)
    home, folder = TMP / "live" / "JobRunner", TMP / "live" / "JobSearch"
    r = powershell("-File", str(PS1), "-NoTasks", "-NoKeys", "-NoSelftest", "-AppSource", str(src),
                   "-InstallRoot", str(home), "-Folder", str(folder))
    assert r.returncode == 0 and "DONE" in r.stdout, (r.stdout[-1500:], r.stderr[-800:])
    py = home / "venv" / "Scripts" / "python.exe"
    r = subprocess.run([str(py), "-I", "-m", "jobrunner", "selftest"], capture_output=True, text=True,
                       env=env(JOBRUNNER_HOME=str(home)), cwd=str(TMP))
    assert r.returncode == 0 and "SELFTEST PASS" in r.stdout, r.stdout
    r = powershell("-File", str(UNINSTALL), "-NoTasks", "-InstallRoot", str(home))
    assert r.returncode == 0 and not (home / "app").exists() and folder.exists(), r.stdout
    print("ok live install, selftest and uninstall")


def main():
    tests = [test_request_cli, test_selftest, test_installer_dry_run, test_cmd_hash_mismatch, test_live_install,
             test_locked, test_stale_lock, test_idle_tick, test_stage_tier_a, test_rejected_requests,
             test_tick_session_addendum, test_events_and_replan, test_tick_user_addendum, test_diy_staging,
             test_no_import_from_root]
    try:
        for t in tests:
            t()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("PASS")


if __name__ == "__main__":
    main()
