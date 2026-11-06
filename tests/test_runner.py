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


HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import jobfs
lock = jobfs.RunLock(sys.argv[2], cmd=sys.argv[3]).acquire()
print("HELD", jobfs.os.getpid(), flush=True)
time.sleep(float(sys.argv[4]))
lock.release()
"""


def hold_lock(root, cmd, seconds):
    """Another runner process that holds root/state/run.lock (the --offline lock)
    for `seconds`. The base interpreter, so its pid is the holder's pid."""
    lock = jobfs.Folder(root).state / "run.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    exe = getattr(sys, "_base_executable", None) or sys.executable
    p = subprocess.Popen([exe, "-I", "-c", HOLDER, str(SCRIPTS), str(lock), cmd, str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    line = p.stdout.readline().split()
    assert line[:1] == ["HELD"], line
    p.holder_pid = int(line[1])
    return p, lock


def wait_for(check, seconds=300, what="the condition"):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if check():
            return
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def status_of(root):
    try:
        return json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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
    """A held lock: a tick and a second daily print LOCKED (exit 8); a daily
    that meets a tick waits for it (PIPE-01, WR-02), within a bound."""
    root = copy_day("locked")
    holder, lock = hold_lock(root, "daily", 60)
    try:
        code, lines, err = run("tick", root)
        assert code == 8 and lines and lines[0].startswith("LOCKED the runner is busy"), (code, lines, err)
        assert f"pid {holder.holder_pid}, daily since" in lines[0], lines
        code, lines, err = run("daily", root)
        assert code == 8 and lines[0].startswith("LOCKED"), (code, lines, err)  # a daily never waits for a daily
    finally:
        holder.kill()
        holder.wait()
    # A tick holds the lock for 9 s as the daily starts: past the 5 s grace, the daily waits and runs.
    holder, _ = hold_lock(root, "tick", 9)
    code, lines, err = run("daily", root)
    holder.wait()
    assert code == 0 and lines[0].startswith("WAIT the runner is busy") and "tick since" in lines[0], (lines, err)
    assert lines[-1] == "DAILY ok queue=30 unchanged", lines
    # The wait is bounded (12 minutes; 2 s here): then LOCKED and the next tick starts it again.
    holder, _ = hold_lock(root, "tick", 60)
    try:
        r = subprocess.run([sys.executable, str(RUNNER), "daily", "--root", str(root), "--offline", str(BOARDS)],
                           capture_output=True, text=True, env=env(JOBRUNNER_LOCK_WAIT="2"), cwd=str(root))
    finally:
        holder.kill()
        holder.wait()
    out = r.stdout.splitlines()
    assert r.returncode == 8 and out[-1].startswith("LOCKED the runner is still busy"), out
    print("ok locked: a tick and a second daily stop, a daily waits for a tick")


def test_killed_lock():
    """A run stopped by a time limit or a shutdown frees the lock at once
    (PIPE-02, WR-05); the next run only notes it."""
    root = copy_day("killed")
    holder, lock = hold_lock(root, "daily", 120)
    holder.kill()
    holder.wait()
    code, lines, err = run("tick", root)
    assert code == 0, (code, lines, err)
    assert lines[0].startswith(f"STALE LOCK run.lock pid={holder.holder_pid} cmd=daily"), lines
    assert "ended without finishing" in lines[0], lines
    assert not lock.exists()
    log = (root / "logs" / f"runner_{DAY}.log").read_text(encoding="utf-8")
    assert "STALE LOCK" in log, log[-500:]
    print("ok a killed run's lock is free at once")


def test_missed_daily():
    """The PC slept through 05:30 (or the daily lost the lock): the first tick
    starts the daily outside itself and returns; it never runs it inline."""
    root = copy_day("missed")
    day2, now2 = "2026-11-07", "2026-11-07T17:00:00Z"
    t0 = time.time()
    code, lines, err = run("tick", root, now=now2)
    assert code == 0 and "TICK started the daily (missed; a process)" in lines, (lines, err[-1500:])
    assert time.time() - t0 < 20, "the tick waited for the daily"
    st = status_of(root)
    assert st["daily_starts"]["count"] == 1 and st["daily_starts"]["why"] == "missed", st.get("daily_starts")
    wait_for(lambda: (status_of(root).get("last_daily") or {}).get("date") == day2, what="the started daily")
    assert status_of(root)["last_daily"]["ok"] and (root / "queue" / day2 / "queue.json").exists()
    wait_for(lambda: status_of(root).get("daily_running", 1) is None, 60, "daily_running to clear")
    log = (root / "logs" / f"runner_{day2}.log").read_text(encoding="utf-8")
    assert " daily DAILY ok queue=30 " in log, log[-800:]
    code, lines, _ = run("tick", root, now="2026-11-07T17:15:00Z")
    assert code == 0 and not any(ln.startswith("TICK started") for ln in lines), lines
    # Starts are spaced out: a failed daily is started again only 30 minutes on, 3 times a day.
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import jobrunner
    os.environ["JOB_SKILL_NOW"] = "2026-11-07T18:00:00Z"
    try:
        r = jobrunner.Run(argparse.Namespace(root=str(root), offline=str(BOARDS), date=None, mode=None), "tick")
        failed = {"last_daily": {"date": day2, "ok": False}}
        assert jobrunner.start_reason(r, failed) == ("failed", True)
        recent = dict(failed, daily_starts={"date": day2, "count": 1, "at": "2026-11-07T09:50:00-08:00"})
        assert jobrunner.start_reason(r, recent) == ("failed", False)
        third = dict(failed, daily_starts={"date": day2, "count": 3, "at": "2026-11-07T08:00:00-08:00"})
        assert jobrunner.start_reason(r, third) == ("failed", False)
        waited = {"last_daily": {"date": day2, "ok": True}, "daily_pending": day2}
        assert jobrunner.start_reason(r, waited) == ("waited", True)
        os.environ["JOB_SKILL_NOW"] = "2026-11-07T13:00:00Z"  # 05:00: before the daily's time
        assert jobrunner.start_reason(jobrunner.Run(argparse.Namespace(
            root=str(root), offline=str(BOARDS), date=None, mode=None), "tick"), {})[0] == ""
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok a missed daily is started by the next tick, outside it")


def test_session_before_queue():
    """A session beat before today's queue exists never holds the daily back
    (UD-02, WR-03); only a rebuild of a queue that exists waits."""
    root = copy_day("early_session")
    beat(root, -24 * 60 + 2)  # 2026-11-07T16:58Z: two minutes before the run
    code, lines, err = run("daily", root, now="2026-11-07T17:00:00Z")
    assert code == 0 and lines[-1].startswith("DAILY ok queue=30 "), (lines[-4:], err[-1500:])
    assert (root / "queue" / "2026-11-07" / "queue.json").exists()
    st = jobfs.read_json_verified(jobfs.Folder(root).status_json, retries=0)
    st["last_daily"]["ok"] = False  # a queue that exists, from a daily that did not finish
    jobfs.atomic_write_json(jobfs.Folder(root).status_json, st)
    code, lines, err = run("daily", root, now="2026-11-07T17:01:00Z")
    assert code == 0 and lines[-1].startswith("DAILY WAIT a session is active"), (lines[-3:], err[-800:])
    assert status_of(root)["daily_pending"] == "2026-11-07"
    print("ok a session before the queue does not hold the daily; a rebuild waits")


def test_held_files():
    """A PDF open in a viewer (upload/) and funnel.csv open in Excel: the tick
    still answers requests, and the next daily still stages today's files."""
    if os.name != "nt":
        print("skip held files (Windows sharing modes)")
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]

    def hold(path):  # read-only, share read: as Acrobat Reader and Excel open a file
        h = k32.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
        assert h not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
        return h

    root = copy_day("held")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    one, five = q["items"][0], q["items"][4]
    handles = [hold(root / one["upload"]), hold(root / "reports" / "funnel.csv")]
    try:
        sys.path.insert(0, str(SCRIPTS))
        import events
        os.environ["JOB_SKILL_NOW"] = NOW
        try:
            events.emit(root, "applied", {"reason": "", "question_ids": [], "confirmation": "page", "proof": ""},
                        job_key=one["job_key"], n=1, day=DAY)
        finally:
            os.environ.pop("JOB_SKILL_NOW", None)
        for p in jobfs.Folder(root).events.glob("*.json"):
            os.utime(p, (time.time() - 60, time.time() - 60))
        request_file(root, "more", {"limit": 2}, "20261106T165000Z_0000beef")
        code, lines, err = run("tick", root)
        assert code == 0, (lines, err[-1500:])
        assert "REQUEST 20261106T165000Z_0000beef more ok" in lines, lines
        assert any(ln.startswith(f"WARN upload/{Path(one['upload']).name} is open in another program") for ln in lines)
        assert any(ln.startswith("WARN funnel failed: PermissionError") for ln in lines), lines
        assert status_of(root).get("last_tick"), "last_tick not written"
        handles.append(hold(root / five["upload"]))
        code, lines, err = run("daily", root, now="2026-11-07T13:30:00Z")
        assert code == 0 and lines[-1].startswith("DAILY ok queue=30 "), (lines[-4:], err[-1500:])
        staged = next(ln for ln in lines if ln.startswith("STAGED "))
        assert staged.startswith("STAGED 30 files"), staged
        assert (root / "queue" / "2026-11-07" / "today.md").exists() and status_of(root)["last_daily"]["ok"]
    finally:
        for h in handles:
            k32.CloseHandle(h)
    print("ok files open in other programs never stop the tick or the daily")


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
    # resolve_leads is long: the tick starts the daily for it and never runs it inline.
    assert "TICK started the daily (requests; a process)" in lines, lines
    assert not jobfs.Folder(root).result(req_id).exists()
    wait_for(lambda: jobfs.Folder(root).result(req_id).exists(), what="the resolve_leads result")
    wait_for(lambda: not (jobfs.Folder(root).state / "run.lock").exists(), 60, "the daily to finish")
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
    # UD-18: while the session is still active, the next tick adds the
    # addendum's upload copies under their new numbers; the 30 files already
    # in upload/ stay exactly as they were (2.7.3: no reorder, nothing moved).
    beat(root, 5)
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0, (lines, err[-1500:])
    after = snapshot(root / "upload")
    assert {k: v for k, v in after.items() if k in up_before} == up_before, "a file in upload/ changed in the session"
    new = sorted(set(after) - set(up_before))
    assert [n[:3] for n in new] == ["31_", "32_", "33_", "34_"], new
    assert qjson.read_bytes() == q_before
    print("ok during the session the next tick stages only the new numbers 31-34")
    beat(root, 30)
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0, (lines, err[-1500:])
    names = {p.name for p in (root / "upload").iterdir() if p.is_file()}
    assert len(names) == 34, sorted(names)
    # `more` during a session: its files are staged at once, under 35 and 36.
    beat(root, 5)
    before_more = snapshot(root / "upload")
    request_file(root, "more", {"limit": 2}, "20261106T171000Z_0000bbbb")
    code, lines, err = run("tick", root, "--date", DAY, offline=offline)
    assert code == 0 and any(ln.startswith("ADDENDUM ") and "items=35-36" in ln for ln in lines), lines
    after = snapshot(root / "upload")
    assert {k: v for k, v in after.items() if k in before_more} == before_more
    assert sorted(n[:3] for n in set(after) - set(before_more)) == ["35_", "36_"], sorted(after)
    print("ok more in a session: 35-36 staged at once, nothing else touched")


def test_offline_morning():
    """PIPE-06: a morning when no job board answers ends DAILY ok on the carried
    pool, with status discovery_offline and a card line. A tick probes one
    board's robots.txt (30 minutes apart); once it answers, the tick starts
    the daily, which searches again and adds the new postings as an addendum
    after #30 while queue.json stays as it is."""
    root = TMP / "offline" / "JobSearch"
    shutil.copytree(FIXTURE, root)
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md", ".toml", ".csv", ".jsonl", ".txt"):
            b = p.read_bytes()
            if b"\r\n" in b:
                p.write_bytes(b.replace(b"\r\n", b"\n"))
    down = TMP / "offline_net"
    down.mkdir()
    (down / "index.json").write_text(json.dumps({"https://boards-api.greenhouse.io/robots.txt": {"error": "no route"}}),
                                     encoding="ascii")
    code, lines, err = run("daily", root, "--date", DAY, offline=down, now="2026-11-06T13:30:00Z")
    assert code == 0 and lines[-1].startswith("DAILY ok queue=30 "), (lines[-6:], err[-1500:])
    assert any(ln.startswith("WARN offline: no board answered") for ln in lines), lines
    st = status_of(root)
    assert st["discovery_offline"] == DAY and any(e.startswith(f"offline {DAY}") for e in st["errors"]), st
    qjson = jobfs.Folder(root).queue_json(DAY)
    q_before = qjson.read_bytes()
    r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "card", "--root", str(root), "--date", DAY],
                       capture_output=True, text=True, encoding="utf-8", env=env())
    sys.path.insert(0, str(SCRIPTS))
    import today_view
    assert today_view.OFFLINE_RETRY in r.stdout.splitlines(), r.stdout
    # The probe fails: nothing starts, and the next probe waits 30 minutes.
    code, lines, err = run("tick", root, offline=down)
    assert code == 0 and not any(ln.startswith("TICK started") for ln in lines), (lines, err[-800:])
    assert status_of(root)["offline_probe"]["ok"] is False
    code, lines, _ = run("tick", root, offline=BOARDS, now="2026-11-06T17:10:00Z")
    assert not any(ln.startswith("TICK started") for ln in lines), "a probe 10 minutes after the last one"
    # 30 minutes on, the board answers: the tick starts the daily, outside itself.
    code, lines, err = run("tick", root, offline=BOARDS, now="2026-11-06T17:31:00Z")
    assert code == 0 and "TICK started the daily (offline; a process)" in lines, (lines, err[-1500:])
    qd = jobfs.Folder(root).queue_dir(DAY)
    wait_for(lambda: list(qd.glob("addendum_*.json")), what="the rediscovery addendum")
    wait_for(lambda: status_of(root).get("daily_running", 1) is None, 120, "the started daily to finish")
    adds = sorted(qd.glob("addendum_*.json"))
    data = json.loads(adds[0].read_text(encoding="utf-8"))
    assert [it["n"] for it in data["items"]] == [31, 32, 33, 34], [it["n"] for it in data["items"]]
    assert qjson.read_bytes() == q_before
    st = status_of(root)
    assert st["discovery_offline"] is None and not any(e.startswith("offline ") for e in st["errors"]), st
    log = (root / "logs" / f"runner_{DAY}.log").read_text(encoding="utf-8")
    assert " daily REDISCOVER 4 new postings, addendum_" in log, log[-1500:]
    print("ok an offline morning: the tick probes, starts the daily once a board answers, and 31-34 are added")


def test_liveness_hosts():
    """SEC liveness_host: the tick re-reads a posting only on a host the
    unattended policy allows or the host the board listed it on; a
    board-supplied apply_url on an ad or tracking host is never fetched."""
    root = copy_day("liveness")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    own = q["items"][0]
    own["apply_url"] = "https://track.example.net/r?id=1"       # its posting.json url is on a board host
    stray = q["items"][1]
    stray["apply_url"] = "https://track.example.net/r?id=2"
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), q)
    pj = root / stray["folder"] / "posting.json"
    rec = json.loads(pj.read_text(encoding="utf-8"))
    rec["url"] = ""
    pj.write_text(json.dumps(rec), encoding="ascii")
    empty = TMP / "liveness_net"
    empty.mkdir()
    (empty / "index.json").write_text("{}", encoding="ascii")
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import jobrunner
    os.environ["JOB_SKILL_NOW"] = NOW
    try:
        r = jobrunner.Run(argparse.Namespace(root=str(root), offline=str(empty), date=None, mode=None), "tick")
        jobrunner.liveness(r, [own, stray])
        hosts = {u.split("/")[2] for _, u in r.fetcher().offline.requests}
        assert "track.example.net" not in hosts and hosts, hosts
        live = json.loads((jobfs.Folder(root).runner_out / "liveness.json").read_text(encoding="utf-8"))["items"]
        # round 3 (SEC-05): only the URL discovery read; an apply_url is never a fallback
        assert live[stray["job_key"]]["why"] == "skip: no posting url from discovery", live[stray["job_key"]]
        assert not live[own["job_key"]]["why"].startswith("skip"), live[own["job_key"]]
        ok, res = jobrunner.req_fetch_posting(r, {"n": stray["n"]}, [])
        assert not ok and res["deny"].startswith("skip"), res  # the same rule for a queue item's re-read
        r.flush_log()
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok liveness reads only job-site hosts and the posting's own host")


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
    # A build that failed --strict left a PDF but no final.json: never staged (shared decision 1).
    (folder / "resume.pdf").write_bytes(b"%PDF-1.4\n% failed build\n%%EOF\n")
    up = root / it7["upload"]
    before = up.read_bytes()
    request_file(root, "stage_tier_a", {"n": 7}, "20261106T170400Z_0000c0c0")
    code, lines, err = run("tick", root)
    res = result_of(root, "20261106T170400Z_0000c0c0")
    assert res["ok"] is False and any("no final.json" in ln for ln in res["lines"]), res
    assert up.read_bytes() == before
    (folder / "Jordan Q. Avery Resume.pdf").write_bytes(tier_a)
    (folder / "final.json").write_text(json.dumps({"v": 1, "resume": "Jordan Q. Avery Resume.pdf", "letter": None,
                                                   "built_at": "2026-11-06T08:55:00-08:00", "verdict": "ok"}),
                                       encoding="ascii")
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
    # The daily answers resolve_leads (a long request), then a tick runs.
    code = ("import sys; sys.path.insert(0, %r); import jobrunner; "
            "rc = jobrunner.main(['daily', '--root', %r, '--offline', %r]); "
            "rc = rc or jobrunner.main(['tick', '--root', %r, '--offline', %r]); "
            "root = %r.lower(); "
            "bad = sorted(n for n, m in list(sys.modules.items()) "
            "if (getattr(m, '__file__', None) or '').lower().startswith(root)); "
            "inpath = [p for p in sys.path if p and p.lower().startswith(root)]; "
            "print('RC', rc); print('BAD', bad); print('PATH', inpath)") % (str(SCRIPTS), str(root), str(offline),
                                                                         str(root), str(offline),
                                                                         str(root.resolve()))
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    jobrunner.make_request(root, "resolve_leads", {})
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, encoding="utf-8",
                       env=env(PYTHONPATH=str(root)), cwd=str(root))
    out = r.stdout.splitlines()
    assert r.returncode == 0 and "RC 0" in out, (out[-8:], r.stderr[-1500:])
    assert "BAD []" in out and "PATH []" in out, out[-4:]
    results = [json.loads(p.read_text(encoding="utf-8")) for p in jobfs.Folder(root).results.glob("*.json")]
    assert [(x["op"], x["ok"]) for x in results] == [("resolve_leads", True)], results
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


def test_carried_folder_reapproval():
    """TR-13: a job folder carried to another day keeps resume_B.pdf only while
    its variant's approval holds; after the variant changes, the old files are
    set aside (never staged) and the family is named once (LIVE-11)."""
    root = copy_day("carried")
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import jobrunner
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    micro = [it for it in q["items"] if it["family"] == "qc_micro" and it["tier"] == "B"][:2]
    assert len(micro) == 2, [it["family"] for it in q["items"]]
    os.environ["JOB_SKILL_NOW"] = "2026-11-07T13:30:00Z"
    try:
        r = jobrunner.Run(argparse.Namespace(root=str(root), offline=str(BOARDS), date=None, mode=None), "daily")
        prep = jobrunner.Prep(r)
        item = dict(micro[0])
        prep.item(item, root / item["folder"])
        assert prep.tally["kept"] == 1 and item["resume_status"] in ("built", "fallback_base"), prep.tally
        # An item the queue held (prep_failed) never keeps an old file: only tier_b's verdict counts.
        held = dict(micro[1], resume_status="prep_failed")
        prep = jobrunner.Prep(r)
        prep.item(held, root / held["folder"])
        assert prep.tally["kept"] == 0 and held["resume_status"] in ("built", "fallback_base"), (prep.tally, held)
        assert any(ln.startswith("TIER-B ") for ln in r.log_lines), r.log_lines[-4:]
        # The user corrects a number in the approved variant: v1 is no longer approved.
        vres = root / "variants" / "qc_micro" / "v1" / "resume.json"
        text = vres.read_text(encoding="utf-8")
        assert "Wrote 12 environmental" in text
        vres.write_text(text.replace("Wrote 12 environmental", "Wrote 11 environmental"), encoding="utf-8")
        prep = jobrunner.Prep(r)
        for it in micro:
            it = dict(it)
            folder = root / it["folder"]
            old = (folder / "resume_B.pdf").read_bytes()
            prep.item(it, folder)
            assert it["resume_status"] == "prep_failed", it["resume_status"]
            assert not (folder / "resume_B.pdf").exists(), "a resume from an unapproved variant stayed in place"
            assert (folder / "_unapproved" / "resume_B.pdf").read_bytes() == old
        assert prep.tally["kept"] == 0 and prep.tally["prep_failed"] == 2, prep.tally
        assert sum(1 for ln in r.log_lines if ln.startswith("NO_VARIANT qc_micro")) == 1, r.log_lines[-6:]
        assert not any(ln.startswith("PREP_FAILED") for ln in r.log_lines), r.log_lines[-6:]
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok a carried folder is rebuilt or set aside when its variant loses approval")


def test_relative_root_and_log():
    """contracts-16 / PIPE-13: a relative --root still builds Tier B files.
    LIVE-10: each log line is written as it happens, with its own time."""
    root = copy_day("relative")
    q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
    one = next(it for it in q["items"] if it["tier"] == "B")
    (root / one["folder"] / "resume_B.pdf").unlink()
    r = subprocess.run([sys.executable, str(RUNNER), "daily", "--root", "JobSearch", "--offline", str(BOARDS),
                        "--date", DAY], capture_output=True, text=True, encoding="utf-8", cwd=str(root.parent),
                       env=env(JOB_SKILL_NOW="2026-11-06T13:30:00Z"))
    out = r.stdout.splitlines()
    assert r.returncode == 0 and out[-1].startswith("DAILY ok queue=30 ") and "tier_b_built=1 prep_failed=0" in out[-1], \
        (out[-3:], r.stderr[-800:])
    assert (root / one["folder"] / "resume_B.pdf").exists()
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import jobrunner
    os.environ["JOB_SKILL_NOW"] = NOW
    try:
        run_ = jobrunner.Run(argparse.Namespace(root=str(root), offline=None, date=None, mode=None), "tick")
        run_.out("PROBE one")
        log = root / "logs" / f"runner_{DAY}.log"
        assert log.read_text(encoding="utf-8").splitlines()[-1] == "2026-11-06T09:00:00-08:00 tick PROBE one"
        os.environ["JOB_SKILL_NOW"] = "2026-11-06T17:01:00Z"
        run_.log("PROBE two")
        assert log.read_text(encoding="utf-8").splitlines()[-1] == "2026-11-06T09:01:00-08:00 tick PROBE two"
        run_.flush_log()
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok a relative root builds Tier B; log lines are written as they happen")


def test_docs():
    """The references say what the runner now does (contracts-08, UD-13, UD-21)."""
    refs = REPO / "job" / "references"

    def flat(name):
        return " ".join((refs / name).read_text(encoding="utf-8").split())

    batch, runner_md, setup, daily_md = flat("apply_batch.md"), flat("runner.md"), flat("setup.md"), flat("daily.md")
    # an unparked item in a claimed folder fills from a fresh plan, not the stale fill_plan.json
    assert 'after `answers.py unpark`' in batch and 'answers.py plan --root R --job "<job folder>" --dry-run' in batch
    # the lock is an OS lock; nothing is "older than 2 hours"
    assert "older than 2 hours" not in runner_md and "an OS lock" in runner_md
    # a selftest FAIL has a path; the sandbox check has its own record and a use
    assert "On `SELFTEST FAIL`" in setup and "selftest --net --mode S" in setup
    assert "a FAIL leaves mode M" not in runner_md and "selftest_s" in runner_md
    assert "`selftest_s` `net` is PASS" in daily_md and "jobrunner.py daily --mode S" in daily_md
    print("ok the references match the runner")


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
    sheet = f.diy_dir(DAY) / "E1_Hooli Health.md"   # E<n>_<Company>.md, the n of its upload file (SEC-12)
    text = sheet.read_text(encoding="utf-8")
    assert "upload\\E1_Avery_Resume_HooliHealth.pdf" in text, text
    assert "{company}" not in text and "Hooli Health role" not in text, text
    assert "/jobs/" not in text and "jobs/view" not in text, "a posting link in the sheet"
    r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "diy", "--root", str(root)],
                       capture_output=True, text=True, env=env())
    assert r.stdout.strip() == ("DIY E1 | Hooli Health | QC Microbiologist | Redwood City, CA | "
                                "upload/E1_Avery_Resume_HooliHealth.pdf"), r.stdout
    # contracts-11: a job logged by hand keeps its E number but is not staged
    # or sheeted again (the lead keeps status diy, so it is listed every day).
    import argparse
    import jobrunner
    import today_view
    e1[0].unlink()
    sheet.unlink()
    real = today_view.logged_by_hand
    today_view.logged_by_hand = lambda _root: {("hooli health", "qc microbiologist")}
    os.environ["JOB_SKILL_NOW"] = "2026-11-06T13:30:00Z"
    try:
        r_ = jobrunner.Run(argparse.Namespace(root=str(root), offline=str(BOARDS), date=None, mode=None), "daily")
        assert jobrunner.stage_diy(r_) == 0
        assert "DIY 0 items, 0 files staged (1 logged by hand)" in r_.log_lines, r_.log_lines[-3:]
        assert not any(p.name.startswith("E1_") for p in f.upload.iterdir()) and not sheet.exists()
        r_.flush_log()
    finally:
        today_view.logged_by_hand = real
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok do-yourself file and answer sheet; a job logged by hand is not staged again")


# ---------- selftest ----------

def mock_net(dest, robots_ok=True, down=()):
    """Offline answers for the selftest's robots fetch and reach checks; the
    hosts named in `down` (greenhouse, recruitee, ...) do not answer."""
    dest.mkdir(parents=True)
    (dest / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="ascii")
    (dest / "ok.json").write_text('{"jobs": [], "offers": [{"id": 1}], "total": 0, "jobPostings": []}', encoding="ascii")
    sys.path.insert(0, str(SCRIPTS))
    import jobrunner
    refused = {"error": "connection refused"}
    index = {jobrunner.SELFTEST_ROBOTS: "robots.txt" if robots_ok else refused}
    for name, method, url in jobrunner.SELFTEST_HOSTS:
        index[(f"POST {url}" if method == "POST" else url)] = refused if name in down else "ok.json"
    index[jobrunner.SELFTEST_RECRUITEE] = refused if "recruitee" in down else "ok.json"
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
    # UD-13: the optional Recruitee check, or one site that does not answer, is a WARN, not a FAIL.
    code, lines, _ = run("selftest", root, "--net", offline=mock_net(TMP / "net_some", down=("recruitee", "lever")))
    assert code == 0 and lines[-1] == "SELFTEST PASS", lines
    assert any(ln.startswith("WARN reach recruitee ") and "optional" in ln for ln in lines), lines
    assert any(ln.startswith("WARN reach lever ") for ln in lines), lines
    sel = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))["selftest"]
    assert sel["net"] == "PASS" and sel["recruitee"] == "FAIL" and sel["checks"]["reach lever"] == "WARN", sel
    # UD-21: the sandbox check (mode S) has its own record and leaves the PC's alone.
    before = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    code, lines, _ = run("selftest", root, "--net", "--mode", "S", offline=mock_net(TMP / "net_sandbox",
                                                                                  down=("recruitee",)))
    after = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
    assert code == 0 and after["selftest_s"]["net"] == "PASS", after.get("selftest_s")
    assert after["selftest"] == before["selftest"] and after.get("mode") == before.get("mode"), after
    every = ("greenhouse", "lever", "ashby", "workday", "usajobs", "recruitee")
    code, lines, _ = run("selftest", root, "--net", offline=mock_net(TMP / "net_none", robots_ok=False, down=every))
    assert code == 1 and lines[-1] == "SELFTEST FAIL net", lines
    assert any(ln.startswith("FAIL net no job site answered") for ln in lines), lines
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
    for want in ("daily 05:30", "StartWhenAvailable", "WakeToRun", "pythonw.exe -I -m jobrunner daily",
                 "AllowStartIfOnBatteries", "DontStopIfGoingOnBatteries", "ExecutionTimeLimit=2h"):
        assert want in daily, (want, daily)
    for want in ("every 15 minutes", "06:00-22:00", "IgnoreNew", "pythonw.exe -I -m jobrunner tick",
                 "AllowStartIfOnBatteries", "DontStopIfGoingOnBatteries", "ExecutionTimeLimit=10m"):
        assert want in tick, (want, tick)
    assert any(ln.startswith("CREATE ") and ln.endswith(f"-I -m venv {home / 'venv'}") for ln in out), out
    assert "-I -m pip install" in pip, pip
    # What the dry run prints is what Register-RunnerTask registers (PIPE-09, WR-01).
    text = PS1.read_text(encoding="ascii")
    sets = [ln for ln in text.splitlines() if "New-ScheduledTaskSettingsSet" in ln or "-ExecutionTimeLimit" in ln]
    assert sum("-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries" in ln for ln in sets) == 2, sets
    assert "(New-TimeSpan -Hours 2)" in text and "(New-TimeSpan -Minutes 10)" in text
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


def test_cmd_planted_files():
    """A certutil.bat (curl, tar, py, powershell, winget, where) saved beside
    the installer in Downloads never runs, and Python never imports a venv.py
    from the folder the installer starts in (round 1 security probes)."""
    if os.name != "nt":
        print("skip planted files (Windows only)")
        return
    import hashlib
    import re
    import zipfile
    work = TMP / "plant"
    for d in ("localappdata", "temp"):
        (work / d).mkdir(parents=True)
    for tool in ("certutil", "curl", "tar", "py", "powershell", "where", "winget"):
        (work / f"{tool}.bat").write_text(f'@echo PLANTED {tool} %* > "%~dp0PLANTED_{tool}.txt"\n@exit /b 0\n',
                                          encoding="ascii")
    good = work / "jobrunner-0.0.0.zip"
    with zipfile.ZipFile(good, "w") as z:  # no install_runner.ps1 inside: nothing gets installed
        z.writestr("app/jobrunner.py", "# test only\n")
    sha = hashlib.sha256(good.read_bytes()).hexdigest()
    cmd = work / "install_runner.cmd"
    cmd.write_bytes(CMD.read_bytes().replace(b"@@VERSION@@", b"0.0.0-test").replace(b"@@ZIP_SHA256@@", sha.encode()))
    (work / "changed.zip").write_bytes(good.read_bytes()[:-1] + b"\x00")
    e = {k: v for k, v in os.environ.items() if k.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"}
    e.update(LOCALAPPDATA=str(work / "localappdata"), TEMP=str(work / "temp"), TMP=str(work / "temp"))
    for arg in ("changed.zip", good.name):
        r = subprocess.run(["cmd", "/c", str(cmd), arg], cwd=str(work), env=e, capture_output=True, text=True,
                           errors="replace", stdin=subprocess.DEVNULL, timeout=300)
        assert r.returncode == 1 and "The install stopped" in r.stdout, (arg, r.stdout[-800:])
    assert "SHA-256 ok." in r.stdout and "Done." not in r.stdout, r.stdout[-800:]
    ran = sorted(p.name for p in work.glob("PLANTED_*"))
    assert not ran, f"planted files ran: {ran}"
    text = PS1.read_text(encoding="ascii")
    calls = re.findall(r"& \$(basePython|venvPython|py) ([^\r\n]*)", text)
    assert calls and all("-I" in rest.split() or rest.startswith("@pipArgs") for _, rest in calls), calls
    assert "'-I', '-m', 'pip'" in text
    flags = re.search(r"\n\s*& \$basePython (.*?) \$venv\n", text).group(1).split()
    (work / "venv.py").write_text("import sys\nprint('PLANTED venv.py')\nsys.exit(3)\n", encoding="ascii")
    base = getattr(sys, "_base_executable", None) or sys.executable
    r = subprocess.run([base, *flags, "--help"], cwd=str(work), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "PLANTED" not in r.stdout and "usage" in r.stdout.lower(), (flags, r.stdout[:300])
    print("ok planted .bat files and a planted venv.py never run")


def test_config_non_ascii():
    """WR-06: a user name with an accent (and a quote) reaches config.toml and
    the .pth intact, as UTF-8 that Python reads."""
    if os.name != "nt" or not shutil.which("powershell"):
        print("skip config encoding (Windows PowerShell only)")
        return
    import re
    import tomllib
    text = PS1.read_text(encoding="ascii")
    funcs = re.search(r"function Get-TomlString.*?\n}\n\nfunction Write-Utf8.*?\n}\n", text, re.S).group(0)
    folder = "C:\\Users\\Jos\u00e9 O'Neil\\OneDrive - S\u00fcd\\Documents\\JobSearch"
    out = TMP / "enc"
    out.mkdir()
    script = funcs + ("Write-Utf8 $args[0] @('# test', ('folder = ' + (Get-TomlString $args[1])))\n"
                      "Write-Utf8 $args[2] @($args[1])\n")
    ps1 = out / "enc.ps1"
    ps1.write_text(script, encoding="ascii")
    r = powershell("-File", str(ps1), str(out / "config.toml"), folder, str(out / "app.pth"))
    assert r.returncode == 0, r.stderr[-800:]
    assert tomllib.loads((out / "config.toml").read_bytes().decode("utf-8"))["folder"] == folder
    assert (out / "app.pth").read_bytes().decode("utf-8").strip() == folder
    assert not (out / "config.toml").read_bytes().startswith(b"\xef\xbb\xbf"), "a BOM"
    print("ok config.toml and the .pth keep an accented user name")


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


# ---------- round 3: fail soft, bounded work, hosts (shared decisions 1-3) ----------

def in_process(root, cmd="tick", offline=None, date=None):
    """A jobrunner.Run in this process, the clock pinned to NOW and the runner
    home a temp folder (nothing under the real %LOCALAPPDATA%)."""
    sys.path.insert(0, str(SCRIPTS))
    import argparse
    import jobrunner
    os.environ["JOB_SKILL_NOW"] = NOW if date is None else "2026-11-06T13:30:00Z"
    os.environ["JOBRUNNER_HOME"] = str(TMP / "runner_home")
    return jobrunner, jobrunner.Run(argparse.Namespace(root=str(root), offline=str(offline) if offline else None,
                                                       date=date, mode=None), cmd)


def test_request_attempts():
    """SEC-04 / decision 3: an attempt marker is written before a request runs
    and removed when it ends; a request whose run was stopped twice is
    answered ok=false with one plain reason and never run a third time."""
    root = copy_day("attempts")
    jobrunner, r = in_process(root)
    try:
        seen = {}

        def probe(run_, args, lines):
            seen["marker"] = json.loads(jobrunner.request_start_path(run_, rid).read_text(encoding="utf-8"))
            return True, {}
        real = jobrunner.REQUEST_HANDLERS["more"]
        jobrunner.REQUEST_HANDLERS["more"] = probe
        rid = "20261106T170000Z_0000aaa1"
        assert jobrunner.handle_request(r, request_file(root, "more", {}, rid), in_tick=True)
        assert seen["marker"]["starts"] == 1 and seen["marker"]["op"] == "more", seen
        assert not jobrunner.request_start_path(r, rid).exists() and result_of(root, rid)["ok"] is True
        # a run stopped while answering it leaves the marker; after two such starts it is answered, not run
        calls = []
        jobrunner.REQUEST_HANDLERS["more"] = lambda run_, args, lines: calls.append(1) or (True, {})
        rid = "20261106T170000Z_0000aaa2"
        path = request_file(root, "more", {}, rid)
        jobrunner.note_request_start(r, rid, "more", 2)
        assert jobrunner.handle_request(r, path, in_tick=True)
        res = result_of(root, rid)
        assert res["ok"] is False and not calls, res
        assert res["lines"] == ["REJECTED this request was started 2 times and never finished (the runner was "
                                "stopped while working on it), so it is not tried again"], res["lines"]
        assert not path.exists() and not jobrunner.request_start_path(r, rid).exists()
        # one stopped start is tried again (a PC that slept mid-request)
        rid = "20261106T170000Z_0000aaa3"
        path = request_file(root, "more", {}, rid)
        jobrunner.note_request_start(r, rid, "more", 1)
        assert jobrunner.handle_request(r, path, in_tick=True) and calls == [1] and result_of(root, rid)["ok"]
        jobrunner.REQUEST_HANDLERS["more"] = real
        r.flush_log()
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok a request stopped twice is answered ok=false, not retried forever")


def test_request_hosts():
    """SEC-01 / SEC-05 / decision 1: a request's URL is judged on netfetch's
    canonical host (lookalike spellings, lnkd.in, licdn.com, non-public
    addresses refused when written); a fetch_posting url is allowed for that
    request only, never on the run's shared fetcher; liveness reads only the
    URL discovery read, without adding any host to the policy."""
    root = copy_day("req_hosts")
    jobrunner, r = in_process(root)
    try:
        for url in ("https://www.link" + chr(0xAD) + "edin.com/jobs/view/1", "https://lnkd.in/abc",
                    "https://media.licdn.com/x.png", "https://indeed.jobs/x", "https://ex" + chr(0xE0) + "mple.com/j",
                    "https://192.168.1.10/jobs/1"):
            try:
                jobrunner.make_request(root, "probe_url", {"url": url})
                raise AssertionError(f"accepted {ascii(url)}")
            except jobrunner.RequestError as e:
                assert str(e).startswith("DENY "), (ascii(url), e)
        net = TMP / "req_hosts_net"
        net.mkdir()
        page = ("<html><head><title>Scientist</title></head><body><h1>Scientist</h1><p>"
                + "Run assays. " * 80 + "</p></body></html>")
        (net / "p.html").write_text(page, encoding="ascii")
        (net / "index.json").write_text(json.dumps({"https://careers.example-bio.com/jobs/1": {"file": "p.html"}}),
                                        encoding="ascii")
        jobrunner, r = in_process(root, offline=net)
        rid = "20261106T170000Z_0000bbb1"
        assert jobrunner.handle_request(r, request_file(root, "fetch_posting",
                                                        {"url": "https://careers.example-bio.com/jobs/1"}, rid))
        assert result_of(root, rid)["ok"] is True, result_of(root, rid)
        assert r.fetcher().policy.reason("https://careers.example-bio.com/jobs/2") == \
            "host-not-allowed careers.example-bio.com", "the request's host stayed allowed for the run"
        # liveness: a posting.json url on that host (no ATS endpoint) is skipped, nothing is fetched
        q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
        it = dict(q["items"][0])
        pj = root / it["folder"] / "posting.json"
        rec = json.loads(pj.read_text(encoding="utf-8"))
        rec["url"] = "https://careers.example-bio.com/jobs/1"
        pj.write_text(json.dumps(rec), encoding="ascii")
        before = len(r.fetcher().offline.requests)
        jobrunner.liveness(r, [it])
        live = json.loads((jobfs.Folder(root).runner_out / "liveness.json").read_text(encoding="utf-8"))["items"]
        assert live[it["job_key"]]["why"] == "skip host careers.example-bio.com: not a job-site host", live
        assert len(r.fetcher().offline.requests) == before
        assert r.fetcher().policy.reason("https://careers.example-bio.com/jobs/1").startswith("host-not-allowed")
        r.flush_log()
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    print("ok request URLs on the canonical host; a request's host is allowed for that request only")


def test_fail_soft_units():
    """Decision 2 / SEC-08 / SEC-10: a liveness item whose read raises is its
    own ERROR line and the others still run; a network import that raises is
    one ERROR line and its fingerprint is recorded, so the next tick is idle
    about it; a discovery stage that raises still lets the daily build the
    queue. L2-07: the RESOLVE line is logged once."""
    root = copy_day("fail_soft")
    empty = TMP / "fail_soft_net"
    empty.mkdir()
    (empty / "index.json").write_text("{}", encoding="ascii")
    jobrunner, r = in_process(root, offline=empty)
    import network
    import posting
    try:
        q = json.loads(jobfs.Folder(root).queue_json(DAY).read_text(encoding="utf-8"))
        a, b = dict(q["items"][0]), dict(q["items"][1])
        real_read = posting.read_posting
        bad_url = json.loads((root / a["folder"] / "posting.json").read_text(encoding="utf-8"))["url"]

        def read(url, *args, **kw):
            if url == bad_url:
                raise TypeError("expected string or bytes-like object, got 'list'")
            return real_read(url, *args, **kw)
        posting.read_posting = read
        try:
            jobrunner.liveness(r, [a, b])
        finally:
            posting.read_posting = real_read
        live = json.loads((jobfs.Folder(root).runner_out / "liveness.json").read_text(encoding="utf-8"))["items"]
        assert live[a["job_key"]]["why"] == "error TypeError" and b["job_key"] in live, live
        assert any(ln.startswith(f"ERROR liveness #{a['n']:02d} {a['job_key']}: TypeError") for ln in r.log_lines)
        # a network export that breaks the reader
        raw = jobfs.Folder(root).network_raw
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "export.zip").write_bytes(b"PK\x03\x04 not really a zip")
        real_main = network.main
        network.main = lambda argv: (_ for _ in ()).throw(RuntimeError("Error -3 while decompressing data"))
        try:
            assert jobrunner.network_import(r) == 1
        finally:
            network.main = real_main
        assert any(ln.startswith("ERROR network import: RuntimeError") for ln in r.log_lines), r.log_lines[-3:]
        rec = json.loads((jobfs.Folder(root).state / "network_raw.json").read_text(encoding="utf-8"))
        assert rec["fingerprint"] == jobrunner.raw_fingerprint(jobfs.Folder(root)) and rec["exit"] == 1, rec
        assert jobrunner.network_import(r) is None  # the same drop is not tried again
        r.flush_log()
    finally:
        os.environ.pop("JOB_SKILL_NOW", None)
    # the daily's own log has the RESOLVE line once (resolve logs it; the daily only prints it)
    log = (day_root() / "logs" / f"runner_{DAY}.log").read_text(encoding="utf-8")
    assert sum(1 for ln in log.splitlines() if " daily RESOLVE " in ln) == 1, log[-2000:]
    # a discovery stage that raises: the daily still builds today's list
    fresh = TMP / "fail_soft_daily" / "JobSearch"
    shutil.copytree(FIXTURE, fresh)
    jobrunner, r = in_process(fresh, cmd="daily", offline=BOARDS, date=DAY)
    import aggregators
    real_run = aggregators.run
    aggregators.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lane exploded"))
    try:
        assert jobrunner.daily(r) == 0
    finally:
        aggregators.run = real_run
        os.environ.pop("JOB_SKILL_NOW", None)
        r.flush_log()
    assert "ERROR aggregators: RuntimeError: lane exploded" in r.log_lines, [ln for ln in r.log_lines if "ERROR" in ln]
    assert jobfs.Folder(fresh).queue_json(DAY).exists() and r.log_lines[-1].startswith("DAILY ok queue="), \
        r.log_lines[-3:]
    print("ok a failing liveness item, network import or discovery stage is one ERROR line; the run goes on")


def test_diy_sheets_per_item():
    """SEC-12: two do-it-yourself jobs at one employer get one answer sheet
    each, named E<n>_<Company>.md with the n of its upload file."""
    root = copy_day("diy_two")
    jobrunner, r = in_process(root)
    import today_view
    real = today_view.diy_rows
    today_view.diy_rows = lambda _root, _day, _limit: [("Genentech", "Senior Research Associate", "Springfield"),
                                                      ("Genentech", "QC Microbiologist", "Springfield")]
    try:
        jobrunner.stage_diy(r)
        r.flush_log()
    finally:
        today_view.diy_rows = real
        os.environ.pop("JOB_SKILL_NOW", None)
    d = jobfs.Folder(root).diy_dir(DAY)
    sheets = sorted(p.name for p in d.glob("*.md"))
    assert sheets == ["E1_Genentech.md", "E2_Genentech.md"], sheets
    assert (d / "E1_Genentech.md").read_text(encoding="utf-8").startswith("# Genentech - Senior Research Associate")
    assert (d / "E2_Genentech.md").read_text(encoding="utf-8").startswith("# Genentech - QC Microbiologist")
    print("ok one answer sheet per do-it-yourself job, even at one employer")


def main():
    tests = [test_request_cli, test_selftest, test_installer_dry_run, test_cmd_hash_mismatch, test_cmd_planted_files,
             test_config_non_ascii, test_live_install, test_locked, test_killed_lock, test_idle_tick,
             test_stage_tier_a, test_rejected_requests, test_tick_session_addendum, test_events_and_replan,
             test_tick_user_addendum, test_diy_staging, test_no_import_from_root, test_missed_daily,
             test_session_before_queue, test_held_files, test_carried_folder_reapproval, test_relative_root_and_log,
             test_offline_morning, test_liveness_hosts, test_docs, test_request_attempts, test_request_hosts,
             test_fail_soft_units, test_diy_sheets_per_item]
    if sys.argv[1:]:  # python tests/test_runner.py test_locked test_missed_daily
        tests = [t for t in tests if t.__name__ in sys.argv[1:]]
    try:
        for t in tests:
            t()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("PASS")


if __name__ == "__main__":
    main()
