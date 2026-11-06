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
    assert (jobfs.VERSION, jobfs.EVENT_SCHEMA) == ("1.2.0", 1)
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        f = jobfs.Folder(tmp)
        f.config.mkdir()
        assert jobfs.settings(tmp)["daily"]["target"] == 20  # no file yet: defaults
        f.settings_toml.write_text("[daily]\ntarget = 25\n[[caps.employer]]\nname = \"Acme\"\nmatch = [\"acme\"]\n"
                                   "per_30d = 2\nsame_job_reapply_days = 60\n", encoding="utf-8")
        s = jobfs.settings(tmp, keep_last_good=True)
        assert s.source == "file" and s["daily"]["target"] == 25 and s["daily"]["batch_size"] == 10, s
        assert [e["name"] for e in s["caps"]["employer"]] == ["Acme"] and s["caps"]["per_company_30d"] == 3
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


def test_run_lock():
    with tempfile.TemporaryDirectory(prefix="jobfs_") as tmp:
        lock = Path(tmp) / "run.lock"
        with jobfs.RunLock(lock, cmd="daily"):
            info = json.loads(lock.read_text(encoding="ascii"))
            assert info["pid"] == os.getpid() and info["cmd"] == "daily" and info["started"]
            try:
                jobfs.RunLock(lock, stale_after=7200, cmd="tick").acquire()
                raise AssertionError("second lock acquired while fresh")
            except jobfs.LockBusy as e:
                assert e.info["cmd"] == "daily"
        assert not lock.exists()
        # a lock left behind 3 hours ago by a dead runner is stale
        old = (jobfs.utcnow() - timedelta(hours=3)).replace(microsecond=0).isoformat()
        lock.write_text(json.dumps({"pid": 999999, "started": old, "cmd": "daily"}), encoding="ascii")
        with jobfs.RunLock(lock, stale_after=7200, cmd="tick") as held:
            assert held.held and json.loads(lock.read_text(encoding="ascii"))["cmd"] == "tick"
        # one 1 hour old is not
        recent = (jobfs.utcnow() - timedelta(hours=1)).isoformat()
        lock.write_text(json.dumps({"pid": 999999, "started": recent, "cmd": "daily"}), encoding="ascii")
        try:
            jobfs.RunLock(lock, stale_after=7200).acquire()
            raise AssertionError("1 h old lock treated as stale")
        except jobfs.LockBusy:
            pass
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


def main():
    for t in (test_time, test_settings, test_atomic_json, test_create_only_and_jsonl, test_run_lock,
              test_posting_record, test_names_and_folder, test_ascii):
        t()
        print("ok", t.__name__)
    print("PASS")


if __name__ == "__main__":
    main()
