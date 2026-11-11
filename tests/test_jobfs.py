"""Check job/scripts/jobfs.py: time zone dates, settings, verified JSON, JSONL,
create-only files, the runner lock, the Posting record and folder names.

Usage: python tests/test_jobfs.py
Stdlib only (plus tzdata on Windows). Exits non-zero on the first failure and
prints PASS at the end.
"""
import json
import os
import re
import sys
import tempfile
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "job" / "scripts"))
import jobfs  # noqa: E402

UTC = timezone.utc


def mock_clock(when):
    real = jobfs.utcnow
    jobfs.utcnow = lambda: when
    return real


def test_time():
    real = mock_clock(datetime(2026, 11, 6, 6, 30, tzinfo=UTC))
    try:
        assert jobfs.today() == date(2026, 11, 5), jobfs.today()
        assert jobfs.now_iso() == "2026-11-05T22:30:00-08:00", jobfs.now_iso()
        assert jobfs.stamp() == "20261106T063000Z"
        assert re.fullmatch(r"20261106T063000Z_[0-9a-f]{8}", jobfs.new_id())
        with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
            cfg = Path(tmp) / "config"
            cfg.mkdir()
            (cfg / "settings.toml").write_text('[time]\ntimezone = "America/New_York"\n', encoding="utf-8")
            assert jobfs.today(tmp) == date(2026, 11, 6) and jobfs.now_iso(tmp).endswith("-05:00")
        # without tzdata the US zones use built-in rules, with the same answers
        la = jobfs.USRules("America/Los_Angeles", -8)
        assert jobfs.utcnow().astimezone(la).date() == date(2026, 11, 5)
        summer = datetime(2026, 7, 1, 6, 30, tzinfo=UTC).astimezone(la)
        assert summer.utcoffset() == timedelta(hours=-7) and summer.date() == date(2026, 6, 30), summer
        assert datetime(2026, 11, 1, 9, 30, tzinfo=UTC).astimezone(la).utcoffset() == timedelta(hours=-8)
        assert datetime(2026, 3, 8, 10, 30, tzinfo=UTC).astimezone(la).utcoffset() == timedelta(hours=-7)
    finally:
        jobfs.utcnow = real
    # JOB_SKILL_NOW pins the clock for subprocess tests
    os.environ["JOB_SKILL_NOW"] = "2026-11-06T06:30:00Z"
    try:
        assert jobfs.today() == date(2026, 11, 5)
    finally:
        del os.environ["JOB_SKILL_NOW"]
    try:
        jobfs.zone("Mars/Olympus_Mons")
        raise AssertionError("unknown zone accepted")
    except jobfs.ConfigError:
        pass


def test_settings():
    template = (ROOT / "job" / "templates" / "settings.toml").read_text(encoding="ascii")
    assert tomllib.loads(template) == jobfs.SETTINGS_DEFAULTS
    assert jobfs.settings() == jobfs.SETTINGS_DEFAULTS and jobfs.settings().source == "defaults"
    assert (jobfs.VERSION, jobfs.EVENT_SCHEMA) == ("1.3.0", 1)
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        f = jobfs.Folder(tmp)
        f.config.mkdir()
        assert jobfs.settings(tmp)["daily"]["target"] == 20  # no file yet: defaults
        f.settings_toml.write_text("[daily]\ntarget = 25\n[[caps.employer]]\nname = \"Acme\"\nmatch = [\"acme\"]\n"
                                   "per_30d = 2\nsame_job_reapply_days = 60\n", encoding="utf-8")
        s = jobfs.settings(tmp, keep_last_good=True)
        assert s.source == "file" and s["daily"]["target"] == 25 and s["daily"]["batch_size"] == 10, s
        assert [e["name"] for e in s["caps"]["employer"]] == ["Acme"] and s["caps"]["per_company_30d"] == 5
        assert f.last_good("settings.toml").exists()
        f.settings_toml.write_text("[daily\ntarget = ", encoding="utf-8")  # a broken hand edit
        s = jobfs.settings(tmp)
        assert s.source == "last_good" and s["daily"]["target"] == 25 and "settings.toml" in s.error, (s.source, s.error)
        f.last_good("settings.toml").unlink()
        s = jobfs.settings(tmp)
        assert s.source == "defaults" and s["daily"]["target"] == 20 and s.error

        # search profile: keys fixed, regex limited
        sp = jobfs.search_profile(tmp)
        assert sp["home"]["label"] == "" and sp["family"] == [] and sp["widen"]["enabled"] is True
        good = "[[knockout]]\nid = \"cls\"\nrequired_patterns = ['(?i)\\bCLS\\b.{0,40}(licen[cs]e|required)']\n"
        f.search_profile_toml.write_text(good, encoding="utf-8")
        sp = jobfs.search_profile(tmp)
        assert sp.source == "file" and sp["knockout"][0]["id"] == "cls", (sp.source, sp.error)
        for bad in ("(a+)+$", "(\\w*){2,}x", "a" * 201):
            f.search_profile_toml.write_text(f"[[knockout]]\nid = \"x\"\ntitle_patterns = ['{bad}']\n",
                                             encoding="utf-8")
            sp = jobfs.search_profile(tmp)
            assert sp.source == "defaults" and sp["knockout"] == [] and sp.error, (bad, sp.source)


def test_atomic_json():
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        p = Path(tmp) / "out" / "queue.json"
        first = jobfs.atomic_write_json(p, {"v": 1, "items": [1]})
        assert first["_rev"] == 1 and first["_sha256"] == jobfs.body_sha256(first)
        second = jobfs.atomic_write_json(p, {"v": 1, "items": [1, 2]})
        assert second["_rev"] == 2 and jobfs.prev_path(p).name == "queue.prev.json"
        assert json.loads(jobfs.prev_path(p).read_text(encoding="utf-8"))["items"] == [1]

        # killed between the temp write and the rename: the old file still reads
        real = jobfs.os.replace

        def dies(src, dst):
            if Path(dst) == p:
                raise KeyboardInterrupt("killed")
            return real(src, dst)

        jobfs.os.replace = dies
        try:
            jobfs.atomic_write_json(p, {"v": 1, "items": [1, 2, 3]})
            raise AssertionError("the write was not interrupted")
        except KeyboardInterrupt:
            pass
        finally:
            jobfs.os.replace = real
        assert jobfs.read_json_verified(p, retries=0, fallback=False)["items"] == [1, 2]
        assert not [x for x in p.parent.iterdir() if x.name.endswith(".tmp")], list(p.parent.iterdir())

        # a Windows sharing violation (a scanner holding the new file) is waited out, not fatal
        if os.name == "nt":
            busy = [2]

            def held(src, dst):
                if busy[0]:
                    busy[0] -= 1
                    raise PermissionError(13, "in use")
                return real(src, dst)
            jobfs.os.replace = held
            other = p.with_name("held.json")
            try:
                jobfs.atomic_write_json(other, {"v": 1, "items": [4]})
            finally:
                jobfs.os.replace = real
            assert jobfs.read_json_verified(other, retries=0, fallback=False)["items"] == [4] and busy[0] == 0

        # a truncated copy: NotReady first, then the .prev.json on fallback
        jobfs.atomic_write_json(p, {"v": 1, "items": [1, 2, 3]})
        whole = p.read_bytes()
        p.write_bytes(whole[:len(whole) // 2])
        try:
            jobfs.read_json_verified(p, retries=0, fallback=False)
            raise AssertionError("truncated file read as good")
        except jobfs.NotReady:
            pass
        waits = []
        got = jobfs.read_json_verified(p, sleep=waits.append)
        assert got["items"] == [1, 2] and waits == [5.0], (got, waits)
        # a hash mismatch is not ready either
        bad = dict(second, items=[9])
        p.write_text(json.dumps(bad), encoding="utf-8")
        try:
            jobfs.read_json_verified(p, retries=0, fallback=False)
            raise AssertionError("hash mismatch accepted")
        except jobfs.NotReady as e:
            assert "hash" in str(e)
        # files with no hash only have to parse; missing files are NotReady
        plain = Path(tmp) / "plain.json"
        plain.write_text('{"a": 1}', encoding="utf-8")
        assert jobfs.read_json_verified(plain, retries=0) == {"a": 1}
        try:
            jobfs.read_json_verified(Path(tmp) / "none.json", retries=0)
            raise AssertionError("missing file read")
        except jobfs.NotReady:
            pass

        t = Path(tmp) / "today.md"
        jobfs.atomic_write_text(t, "line one\nline two\n")
        jobfs.atomic_write_text(t, "replaced\n")
        assert t.read_bytes() == b"replaced\n"


def test_create_only_and_jsonl():
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        ev = Path(tmp) / "runner" / "in" / "events" / "20261112T171503Z_a91c33d0_applied.json"
        jobfs.create_only(ev, {"v": 1, "id": "20261112T171503Z_a91c33d0", "type": "applied"})
        assert json.loads(ev.read_text(encoding="utf-8"))["type"] == "applied"
        try:
            jobfs.create_only(ev, "other")
            raise AssertionError("create_only overwrote a file")
        except FileExistsError:
            pass
        assert json.loads(ev.read_text(encoding="utf-8"))["type"] == "applied"
        assert sorted(x.name for x in ev.parent.iterdir()) == [ev.name]  # no temp left behind

        j = Path(tmp) / "state" / "seen" / "2026-11.jsonl"
        for i in range(3):
            jobfs.append_jsonl(j, {"job_key": f"greenhouse:{i}"})
        with open(j, "ab") as f:
            f.write(b'{"job_key": "greenhouse:3", "ur')  # the copy was cut mid-line
        assert [r["job_key"] for r in jobfs.read_jsonl(j)] == ["greenhouse:0", "greenhouse:1", "greenhouse:2"]
        jobfs.append_jsonl(j, {"job_key": "greenhouse:4"})
        assert [r["job_key"] for r in jobfs.read_jsonl(j)][-1] == "greenhouse:4"
        assert len(jobfs.read_jsonl(j)) == 4 and jobfs.read_jsonl(Path(tmp) / "missing.jsonl") == []


HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import jobfs
lock = jobfs.RunLock(sys.argv[2], cmd=sys.argv[3]).acquire()
print("HELD", jobfs.os.getpid(), flush=True)
time.sleep(float(sys.argv[4]))
lock.release()
"""


def hold_in_child(lock, cmd, seconds, now=None):
    """Another process that takes the lock and keeps it for `seconds`."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "JOB_SKILL_NOW"}
    if now:
        env["JOB_SKILL_NOW"] = now
    # the base interpreter, not a venv's python.exe (a redirector with its own pid)
    exe = getattr(sys, "_base_executable", None) or sys.executable
    p = subprocess.Popen([exe, "-I", "-c", HOLDER, str(ROOT / "job" / "scripts"), str(lock), cmd,
                          str(seconds)], stdout=subprocess.PIPE, text=True, env=env)
    line = p.stdout.readline().split()
    assert line[:1] == ["HELD"], "the child did not take the lock"
    p.holder_pid = int(line[1])
    return p


def test_run_lock():
    import time
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        lock = Path(tmp) / "run.lock"
        with jobfs.RunLock(lock, cmd="daily") as held:
            info = json.loads(lock.read_text(encoding="ascii"))
            assert info["pid"] == os.getpid() and info["cmd"] == "daily" and info["started"]
            assert held.left_by is None
            try:
                jobfs.RunLock(lock, cmd="tick").acquire()
                raise AssertionError("second lock acquired while held")
            except jobfs.LockBusy as e:
                assert e.info["cmd"] == "daily"
            t0 = time.monotonic()
            try:
                jobfs.RunLock(lock, cmd="tick").acquire(wait=0.4, poll=0.1)
                raise AssertionError("second lock acquired while held")
            except jobfs.LockBusy:
                assert time.monotonic() - t0 >= 0.35
        assert not lock.exists(), "a clean release leaves no file"
        # A live holder keeps the lock however old it is (3 hours by its clock).
        old = (jobfs.utcnow() - timedelta(hours=3)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        child = hold_in_child(lock, "daily", 30, now=old)
        try:
            jobfs.RunLock(lock, cmd="tick").acquire(wait=0.3, poll=0.1)
            raise AssertionError("the lock was taken from a live holder")
        except jobfs.LockBusy as e:
            assert e.info["pid"] == child.holder_pid and e.info["cmd"] == "daily", e.info
        # Killed (a time limit, a shutdown): the OS frees the lock at once.
        child.kill()
        child.wait()
        assert lock.exists(), "the killed holder's file stays"
        # (Windows frees a dead process's locks within moments, not always at once)
        with jobfs.RunLock(lock, cmd="tick").acquire(wait=10, poll=0.05) as held:
            assert held.left_by and held.left_by["pid"] == child.holder_pid and held.left_by["cmd"] == "daily", held.left_by
            assert json.loads(lock.read_text(encoding="ascii"))["cmd"] == "tick"
        assert not lock.exists()
        # A holder that finishes within the wait hands the lock over.
        child = hold_in_child(lock, "tick", 0.8)
        t0 = time.monotonic()
        with jobfs.RunLock(lock, cmd="daily").acquire(wait=20, poll=0.1) as held:
            assert 0.3 < time.monotonic() - t0 < 15 and held.left_by is None
        child.wait()
        # A lock file from v1.2.0 (O_EXCL, no process behind it) is no lock.
        lock.write_text(json.dumps({"pid": 999999, "started": "2026-11-06T13:30:00+00:00", "cmd": "daily"}),
                        encoding="ascii")
        with jobfs.RunLock(lock, cmd="tick") as held:
            assert held.left_by["pid"] == 999999
        os.environ["JOBRUNNER_HOME"] = tmp
        try:
            assert jobfs.RunLock().path == Path(tmp) / "run.lock"
        finally:
            del os.environ["JOBRUNNER_HOME"]


def test_posting_record():
    p = jobfs.Posting(job_key="greenhouse:4812345", ats="greenhouse", company="Genentech", title="QC Micro II",
                      req_ids=["R-1"], salary={"min": 1.0, "max": 2.0, "period": "year", "currency": "USD"})
    d = p.to_dict()
    assert list(d) == ["job_key", "ats", "board", "company", "company_norm", "title", "title_norm", "location",
                       "city", "state", "country", "remote", "us_eligible", "posted", "posted_field", "first_seen",
                       "url", "apply_url", "req_ids", "text", "salary", "questions", "employer_site", "lane",
                       "lead_ids", "simhash", "closed"], list(d)
    assert (d["remote"], d["us_eligible"], d["posted"], d["closed"]) == ("unknown", None, None, False)
    assert jobfs.Posting.from_dict(dict(d, extra="ignored")) == p
    assert jobfs.Posting().req_ids is not jobfs.Posting().req_ids  # no shared lists


def test_names_and_folder():
    assert jobfs.ascii_name("Caf\u00e9: R&D / QC?") == "Cafe R&D QC"
    assert jobfs.ascii_name("con") == "_con" and jobfs.ascii_name("Acme...") == "Acme"
    assert jobfs.ascii_name("Genentech, Inc.", 12, alnum=True) == "GenentechInc"
    assert jobfs.upload_name(7, "Doe", "Genentech") == "07_Doe_Resume_Genentech.pdf"
    assert jobfs.upload_name(7, "Doe", "Genentech", "cover") == "07c_Doe_Cover_Genentech.pdf"
    assert jobfs.upload_name(7, "Doe", "Genentech", "word") == "07w_Doe_Resume_Genentech.docx"
    assert jobfs.upload_name(1, "Doe", "Genentech", diy=True) == "E1_Doe_Resume_Genentech.pdf"
    long = jobfs.upload_name(12, "Vanderbilt-Montgomery-Smythe", "Thermo Fisher Scientific")
    assert len(long) <= 40 and long.startswith("12_Vanderbilt") and long.endswith("_Resume_ThermoFisher.pdf"), long
    assert jobfs.job_folder_name("2026-11-12", "Genentech", "QC Microbiologist II") == \
        "2026-11-12 Genentech - QC Microbiologist II"

    f = jobfs.Folder("C:/JobSearch")
    r = Path("C:/JobSearch")
    assert f.queue_json("2026-11-12") == r / "queue" / "2026-11-12" / "queue.json"
    assert f.addendum(date(2026, 11, 12), "1015") == r / "queue" / "2026-11-12" / "addendum_1015.json"
    assert f.seen(date(2026, 11, 5)) == r / "state" / "seen" / "2026-11.jsonl"
    assert f.event("20261112T171503Z_a91c33d0", "applied") == \
        r / "runner" / "in" / "events" / "20261112T171503Z_a91c33d0_applied.json"
    assert f.request("20261112T171503Z_a91c33d0", "probe_url").parent == r / "runner" / "in" / "requests"
    assert f.lead_file("2026-11-12", "0630", "linkedin") == r / "runner" / "in" / "leads" / "2026-11-12_0630_linkedin.jsonl"
    assert f.day_marker("2026-11-12", "morning") == r / "claude" / "day_2026-11-12" / "morning.done"
    assert f.morning_json("2026-11-12") == r / "claude" / "2026-11-12" / "morning.json"
    assert f.upload_file(7, "Doe", "Genentech") == r / "upload" / "07_Doe_Resume_Genentech.pdf"
    assert f.upload_done("2026-11-12") == r / "upload" / "_done" / "2026-11-12"
    assert f.variant("qc_micro", 2) == r / "variants" / "qc_micro" / "v2"
    assert f.worksearch_file("CA", "2026-11-01", "2026-11-14", "csv") == r / "worksearch" / "CA_2026-11-01_2026-11-14.csv"
    assert f.robots_cache_json == r / "state" / "robots_cache.json" and f.tracker_json == r / "runner" / "out" / "tracker.json"
    assert f.last_good("settings.toml") == r / "state" / "last_good" / "settings.toml"
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        g = jobfs.Folder(tmp)
        assert g.inside("applications/x") and not g.inside("../outside") and not g.inside("C:/Windows")


def test_ascii():
    for name in ("job/scripts/jobfs.py", "job/scripts/netfetch.py", "job/scripts/posting.py",
                 "job/scripts/jobkey.py", "job/templates/settings.toml", "tests/test_jobfs.py",
                 "tests/test_netfetch.py", "tests/test_posting.py", "tests/test_jobkey.py"):
        (ROOT / name).read_bytes().decode("ascii")
    for folder in ("tests/fixtures/robots", "tests/fixtures/posting_v12"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file():
                path.read_bytes().decode("ascii")


def test_safe_json():
    """R3 / SV-02: read_json and loads_json, the one way to read a JSON file
    from runner/in, claude, state, queue, applications or upload: a size limit
    per caller, nesting at most 64 deep (checked by a linear scan before
    parsing), and one plain BadJson for every way a file can be bad."""
    import time
    deep = '{"v": 1, "x": ' + "[" * 20000 + "]" * 20000 + "}"
    for text, kind in ((deep, "depth"), ("[" * 65 + "]" * 65, "depth"), ('{"a": ', "json"),
                       (b'{"a": "\xff"}', "utf8"), ("[" + "9" * 5000 + "]", "json")):
        try:
            jobfs.loads_json(text)
            raise AssertionError(f"not refused: {text[:20]!r}")
        except jobfs.BadJson as e:
            assert isinstance(e, ValueError) and e.kind == kind, (e.kind, kind, str(e))
    assert jobfs.loads_json("[" * 64 + "]" * 64) is not None, "64 deep is allowed"
    assert jobfs.loads_json('{"a": "' + "[" * 5000 + '"}') == {"a": "[" * 5000}, "brackets in strings do not count"
    assert jobfs.loads_json(b'\xef\xbb\xbf{"a": 1}') == {"a": 1} and jobfs.loads_json(chr(0xFEFF) + '[1]') == [1]
    try:
        jobfs.loads_json('{"n": NaN}', allow_nan=False)
        raise AssertionError("NaN not refused")
    except jobfs.BadJson:
        pass
    assert jobfs.BadJson("x", "depth").permanent and not jobfs.BadJson("x", "json").permanent
    # linear on hostile shapes: unterminated strings full of escaped quotes, long escape runs, wide arrays
    for text in ('"' + '\\"' * 200000, '"\\"' * 100000 + "[" * 100, '["' + "\\\\" * 200000 + '"]',
                 "[" + ",".join(["[1,{\"a\":[2]}]"] * 50000) + "]"):
        t0 = time.perf_counter()
        try:
            jobfs.loads_json(text)
        except jobfs.BadJson:
            pass
        assert time.perf_counter() - t0 < 1.0, f"slow on {text[:12]!r}: {time.perf_counter() - t0:.2f} s"
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        p = Path(tmp) / "x.json"
        p.write_text(deep, encoding="utf-8")
        for fn in (lambda: jobfs.read_json(p, 64 * 1024), lambda: jobfs.read_json(p, 1000)):
            try:
                fn()
                raise AssertionError("not refused")
            except jobfs.BadJson as e:
                assert e.kind in ("depth", "size"), e.kind
        try:
            jobfs.read_json(Path(tmp) / "none.json", 100)
            raise AssertionError("a missing file is the caller's case")
        except FileNotFoundError:
            pass
        lines = []
        jobfs.set_bad_file_emit(lines.append)
        try:
            assert jobfs.read_json_or(p, jobfs.MAX_FILE, {}) == {} and jobfs.read_json_or(p, jobfs.MAX_FILE, {}) == {}
            assert jobfs.read_json_or(Path(tmp) / "none.json", jobfs.MAX_FILE, {"d": 1}) == {"d": 1}
            assert lines == [f"ERROR bad file {p.parent.parent.name}/{p.parent.name}/x.json: nested deeper than 64 "
                             "(treated as missing)"], lines
            # a verified file that is bad, with no good .prev.json: NotReady, one ERROR line
            q = Path(tmp) / "queue.json"
            q.write_text(deep, encoding="utf-8")
            try:
                jobfs.read_json_verified(q, retries=0)
                raise AssertionError("not refused")
            except jobfs.NotReady as e:
                assert not e.missing
            assert len(lines) == 2 and "queue.json: " in lines[1] and "nested deeper than 64" in lines[1], lines
            try:
                jobfs.read_json_verified(Path(tmp) / "gone.json", retries=0)
            except jobfs.NotReady as e:
                assert e.missing and len(lines) == 2, "a missing file is silent"
        finally:
            jobfs.set_bad_file_emit(None)
        # a small file read under the large limit costs its own size, never a max_bytes buffer
        small = Path(tmp) / "small.json"
        small.write_text('{"a": [1, 2, 3]}', encoding="utf-8")
        t0 = time.perf_counter()
        for _ in range(200):
            assert jobfs.read_json(small, jobfs.MAX_STATE) == {"a": [1, 2, 3]}
        assert time.perf_counter() - t0 < 1.0, f"200 small reads took {time.perf_counter() - t0:.2f} s"
        # a JSONL line nested too deep is skipped like a cut one; atomic_write_json over a bad file still writes
        j = Path(tmp) / "x.jsonl"
        j.write_text('{"a": 1}\n' + deep + "\n" + '{"b": 2}\n', encoding="utf-8")
        assert jobfs.read_jsonl(j) == [{"a": 1}, {"b": 2}]
        assert jobfs.atomic_write_json(p, {"k": 1})["_rev"] == 1


def test_lone_surrogates():
    """V4-01 / V4-02: half of a UTF-16 pair in JSON ("\\ud83d", an emoji a
    server cut in two) comes back from loads_json and read_json as U+FFFD, in
    values and keys at any depth, while a whole escaped pair stays its one
    character; a verified file written before the fix (its hash over the
    surrogate) still verifies and reads back cleaned; atomic_write_text and
    create_only write U+FFFD instead of raising UnicodeEncodeError."""
    bs, lone, fffd = "\\", chr(0xD83D), chr(0xFFFD)
    text = ('{"t": "a' + bs + 'ud83d b", "k' + bs + 'uDC00": [{"x": "' + bs + 'ud83d' + bs + 'ude00"}, "'
            + bs + 'udfff"]}')
    want = {"t": "a" + fffd + " b", "k" + fffd: [{"x": chr(0x1F600)}, fffd]}
    assert jobfs.loads_json(text) == want, ascii(jobfs.loads_json(text))
    assert jobfs.loads_json(text.encode("ascii")) == want
    assert jobfs.loads_json(text, clean=False)["t"] == "a" + lone + " b", "clean=False keeps the value as parsed"
    assert jobfs.loads_json('["a' + lone + '"]') == ["a" + fffd], "a raw surrogate in a str too"
    assert jobfs.clean_text("x" + lone + chr(0xDE00) + "y") == "x" + chr(0x1F600) + "y", "a raw pair is joined"
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        p = Path(tmp) / "pool.json"
        jobfs.atomic_write_json(p, {"items": [{"title": "QC " + lone}]})  # as a pre-fix runner wrote it
        assert jobfs.read_json(p, jobfs.MAX_STATE)["items"][0]["title"] == "QC " + fffd
        assert jobfs.read_json_verified(p, retries=0)["items"][0]["title"] == "QC " + fffd, "hash checked on the raw value"
        jobfs.atomic_write_text(Path(tmp) / "posting.md", "Role: QC " + lone + "\n")
        assert (Path(tmp) / "posting.md").read_bytes() == ("Role: QC " + fffd + "\n").encode("utf-8")
        jobfs.create_only(Path(tmp) / "c.md", "x" + lone)
        assert (Path(tmp) / "c.md").read_bytes() == ("x" + fffd).encode("utf-8")


def main():
    for t in (test_time, test_settings, test_atomic_json, test_create_only_and_jsonl, test_run_lock,
              test_posting_record, test_names_and_folder, test_ascii, test_safe_json, test_lone_surrogates):
        t()
        print("ok", t.__name__)
    print("PASS")


if __name__ == "__main__":
    main()
