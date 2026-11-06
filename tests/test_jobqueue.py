"""Check job/scripts/jobqueue.py: the queue build (size, caps, per-day limits,
Tier A, session order, account block), widen, FROZEN and WAIT, the variant
rule, addenda during a session, fold-events, upload staging (the do-yourself
files by the folder's date), the _not_sent move, and the jobs from links
(added events, addendum_user.json).

Usage: python tests/test_jobqueue.py
Needs openpyxl only if a tracker exists (the fixtures have none). Prints ok
<name> per test and PASS last.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
POOL = HERE / "fixtures" / "pool"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-06T17:30:00Z"  # 09:30 in Los Angeles
import caps  # noqa: E402
import events  # noqa: E402
import gates  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import jobqueue  # noqa: E402

DAY = date(2026, 11, 6)
UPLOAD_RE = re.compile(r"^\d{2}[cw]?_[A-Za-z0-9]{1,20}_(Resume|Cover)_[A-Za-z0-9]{1,12}\.(pdf|docx)$")
RULES = {"per_company_30d": 3, "per_family_per_company_30d": 1, "reject_reapply_days": 90, "ashby_one_open": True,
         "per_company_per_day": 2, "parents": {},
         "employer": [{"name": "Google", "match": ["google"], "per_30d": 3, "same_job_reapply_days": 90}]}
NON_ASCII_EMPLOYER = "Soci\u00e9t\u00e9 \u00c9nergie Bio"


def cli(*args, env=None):
    r = subprocess.run([sys.executable, str(SCRIPTS / "jobqueue.py"), *args], capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    return r.returncode, r.stdout.splitlines()


def queue(root, day=DAY):
    return json.loads(jobfs.Folder(root).queue_json(day).read_text(encoding="utf-8"))


def build(root, pool="pool_71.json", day=DAY):
    code, lines = cli("build", "--root", str(root), "--date", day.isoformat(), "--pool", str(POOL / pool))
    assert code == 0, lines
    return lines


def set_session(root, minutes_ago):
    beat = jobfs.now(root) - timedelta(minutes=minutes_ago)
    (root / "claude").mkdir(exist_ok=True)
    (root / "claude" / "session.json").write_text(json.dumps(
        {"task": "t-1", "started": beat.isoformat(), "beat": beat.replace(microsecond=0).isoformat(), "current_n": 3}),
        encoding="utf-8")


def test_build_71():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lines = build(root)
        assert lines[0] == "QUEUE size=30 pool_left=41 new_account_tenants<=3 account_required<=6 usajobs<=1", lines
        q = queue(root)
        assert jobqueue.check_queue(q) == [], jobqueue.check_queue(q)[:5]
        items = q["items"]
        assert [it["n"] for it in items] == list(range(1, 31)) and q["size"] == 30 and q["pool_left"] == 41
        assert q["_sha256"] == jobfs.body_sha256(q) and q["runner_version"] == jobfs.VERSION
        # session order: no two adjacent employers, at most 3 in a row on one host
        norms = [jobkey.norm_company(it["company"]) for it in items]
        assert all(a != b for a, b in zip(norms, norms[1:])), norms
        hosts = [it["host"] for it in items]
        assert not any(len(set(hosts[i:i + 4])) == 1 for i in range(len(hosts) - 3)), hosts
        # per-day limits
        new = [it for it in items if it["account"] == "new"]
        tenants = list(dict.fromkeys(it["host"] for it in new))
        assert len(tenants) <= 3 and sum(1 for it in items if it["account"] != "none") <= 6
        assert sum(1 for it in items if it["ats"] == "usajobs") <= 1
        # batch 1 is no-account items; the account block follows #10, grouped by tenant
        assert all(it["account"] == "none" for it in items[:10])
        assert q["account_block"] == {"after_n": 10, "tenants": tenants} and new and min(it["n"] for it in new) == 11
        block = [it for it in items if it["n"] > 10][:len(new)]
        assert all(it["account"] == "new" for it in block), [it["account"] for it in block]
        # Tier A: 4 items, each with a reason
        tier_a = [it for it in items if it["tier"] == "A"]
        assert len(tier_a) == 4 and all(it["tier_reason"] for it in tier_a), tier_a
        assert all(it["resume_status"] == "tier_a_pending" for it in tier_a)
        for it in items:
            parts = it["score_parts"]
            assert abs(sum(parts.values()) - it["score"]) < 0.01, it
            assert UPLOAD_RE.match(Path(it["upload"]).name) and len(Path(it["upload"]).name) <= 40
            folder = root / it["folder"]
            assert {"posting.md", "posting.json"} <= set(jobqueue.runner_names(folder)), folder
            rec = json.loads((folder / "posting.json").read_text(encoding="utf-8"))
            assert set(rec) == {f.name for f in jobfs.Posting.__dataclass_fields__.values()}, set(rec)
        # replaying every item as Applied through caps_verdict never breaks a cap
        index = []
        for it in items:
            cn = jobkey.norm_company(it["company"])
            v = caps.caps_verdict(index, cn, it["family"], it["job_key"], set(), it["host"], DAY, RULES)
            assert v == ("ALLOW", None), (it["n"], it["company"], v)
            index.append({"company_norm": cn, "family": it["family"], "job_key": it["job_key"], "host": it["host"],
                          "ats": it["ats"], "status": "Applied", "date": DAY, "rejected_on": None, "req_ids": set(),
                          "simhash": ""})
        # the pool keeps every item; deferred ones carry their first allowed day
        left = gates.load_pool(root)["items"]
        assert len(left) == 71
        deferred = [it for it in left if it.get("deferred_until")]
        assert deferred and all(it["deferred_rule"] for it in deferred) and q["counts"]["deferred"] == len(deferred)
        assert sum(1 for it in left if it.get("queued_on") == DAY.isoformat()) == 30
        seen = gates.Seen(root, DAY)
        assert seen.get(items[0]["job_key"])["outcome"] == "queued"


def test_small_pool_widens():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lines = build(root, "pool_12.json")
        assert lines[0].startswith("QUEUE size=12 pool_left=0 ") and "WIDEN adjacent_families" in lines, lines
        state = json.loads(jobfs.Folder(root).queue_state_json.read_text(encoding="utf-8"))
        assert state["widen"]["on"] == ["adjacent_families"]
        assert gates.widen_steps(root) == ["adjacent_families"]
        # one step a day: a rebuild the same day adds no second step
        lines = build(root, "pool_12.json")
        assert not any(ln.startswith("WIDEN") for ln in lines), lines


def test_frozen_and_wait():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        events.emit(root, "started", {"reason": "", "question_ids": [], "confirmation": "none"},
                    job_key=queue(root)["items"][0]["job_key"], n=1, day=DAY)
        before = jobfs.Folder(root).queue_json(DAY).read_bytes()
        lines = build(root)
        assert lines[0].startswith("FROZEN 2026-11-06") and jobfs.Folder(root).queue_json(DAY).read_bytes() == before
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        set_session(root, 3)
        lines = build(root)
        assert lines[0].startswith("WAIT") and not jobfs.Folder(root).queue_json(DAY).exists(), lines
        set_session(root, 45)
        assert build(root)[0].startswith("QUEUE size=30")


def test_addendum_during_session():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        jobqueue.stage_uploads(root, DAY, emit=lambda s: None)
        f = jobfs.Folder(root)
        before = f.queue_json(DAY).read_bytes()
        listing = sorted(p.name for p in f.upload.rglob("*"))
        set_session(root, 5)
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--pool", str(POOL / "new4.json"))
        assert code == 0 and lines[0] == "ADDENDUM addendum_0930.json items=31-34", lines
        assert "SESSION active: upload/ left as it is" in lines
        add = json.loads(f.addendum(DAY, "0930").read_text(encoding="utf-8"))
        assert [it["n"] for it in add["items"]] == [31, 32, 33, 34] and jobqueue.check_queue(add) == []
        assert all(it["tier"] == "B" for it in add["items"])
        assert f.queue_json(DAY).read_bytes() == before
        assert sorted(p.name for p in f.upload.rglob("*")) == listing
        # the same late finds again: already numbered, so nothing new
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--pool", str(POOL / "new4.json"))
        assert lines == ["ADDENDUM none: nothing new passes the caps"], lines
        # `more`: the next items from the pool, numbered after 34, in their own file
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--limit", "3")
        assert lines[0].startswith("ADDENDUM addendum_0930_2.json items=35-37"), lines


def test_fold_events_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        items = queue(root)["items"]
        for it, kind, reason in ((items[0], "started", ""), (items[0], "applied", ""), (items[1], "skipped", "user"),
                                 (items[2], "parked", "unknown_required")):
            events.emit(root, kind, {"reason": reason, "question_ids": [], "confirmation": "none"},
                        job_key=it["job_key"], n=it["n"], day=DAY)
        code, lines = cli("fold-events", "--root", str(root), "--date", DAY.isoformat())
        assert code == 0 and lines == ["FOLDED 4 events items=3"], lines
        path = jobfs.Folder(root).queue_state_json
        first = path.read_bytes()
        code, lines = cli("fold-events", "--root", str(root), "--date", DAY.isoformat())
        assert lines == ["FOLDED 0 events items=3"] and path.read_bytes() == first
        st = json.loads(first)["items"]
        assert st[items[0]["job_key"]]["state"] == "applied"
        assert st[items[0]["job_key"]]["history"] == ["started", "applied"]
        # tomorrow: the applied job leaves the pool, the parked one waits, the skipped one is back
        tomorrow = DAY + timedelta(days=1)
        code, lines = cli("build", "--root", str(root), "--date", tomorrow.isoformat(),
                          env={"JOB_SKILL_NOW": "2026-11-07T14:00:00Z"})
        assert code == 0, lines
        keys = {it["job_key"] for it in queue(root, tomorrow)["items"]}
        assert items[0]["job_key"] not in keys and items[2]["job_key"] not in keys
        assert items[0]["job_key"] not in {it["job_key"] for it in gates.load_pool(root)["items"]}


def test_upload_staging():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = json.loads((POOL / "pool_12.json").read_text(encoding="utf-8"))
        data["items"][0]["company"] = NON_ASCII_EMPLOYER
        (root / "pool.json").write_text(json.dumps(data), encoding="utf-8")
        (root / "profile.md").write_text("- Legal last name: Avery-Quint\n", encoding="utf-8")
        code, lines = cli("build", "--root", str(root), "--date", DAY.isoformat(), "--pool", str(root / "pool.json"))
        assert code == 0, lines
        items = queue(root)["items"]
        for it in items:  # pretend Tier B built everything
            (root / it["folder"] / "resume_B.pdf").write_bytes(b"%PDF-1.4 " + it["job_key"].encode())
        lines = []
        jobqueue.stage_uploads(root, DAY, emit=lines.append)
        names = sorted(p.name for p in (root / "upload").iterdir() if p.is_file())
        assert len(names) == 12 and all(UPLOAD_RE.match(n) and len(n) <= 40 and n.isascii() for n in names), names
        assert names == sorted(Path(it["upload"]).name for it in items), "sorted names follow the queue order"
        assert names == [Path(it["upload"]).name for it in items]
        assert all(n.split("_")[1] == "AveryQuint" for n in names)
        soc = next(it for it in items if it["company"] == NON_ASCII_EMPLOYER)
        assert Path(soc["upload"]).name.endswith("_Resume_SocieteEnerg.pdf"), soc["upload"]
        # an applied item's file moves to upload/_done/<date>/
        events.emit(root, "applied", {"reason": "", "question_ids": [], "confirmation": "page"},
                    job_key=items[0]["job_key"], n=1, day=DAY)
        jobqueue.stage_uploads(root, DAY, emit=lines.append)
        assert (root / "upload" / "_done" / DAY.isoformat() / Path(items[0]["upload"]).name).exists()
        # the next day's staging moves the leftovers, deletes nothing
        total = sum(1 for p in (root / "upload").rglob("*") if p.is_file())
        nxt = DAY + timedelta(days=1)
        jobqueue.stage_uploads(root, nxt, emit=lines.append)
        top = [p for p in (root / "upload").iterdir() if p.is_file()]
        done = list((root / "upload" / "_done" / DAY.isoformat()).iterdir())
        assert top == [] and len(done) == 12 and sum(1 for p in (root / "upload").rglob("*") if p.is_file()) == total


def test_referral():
    """A tier-1 contact (network.py) adds 20 points and makes the item Tier A first."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "network").mkdir()
        (root / "network" / "network.csv").write_text(
            "id,first,last,company_raw,company_norm,position,connected_on,email,source,strength\n"
            "N-0001,Sam,Rivera,Kramerica Labs,kramerica labs,Scientist,2024-01-02,,linkedin,connection\n",
            encoding="utf-8")
        build(root)
        items = queue(root)["items"]
        k = next(it for it in items if it["company"] == "Kramerica Labs")
        assert k["referral"] == {"state": "candidate", "contact_ref": "N-0001", "hold_until": ""}, k["referral"]
        assert k["score_parts"]["referral"] == 20 and k["tier"] == "A" and k["tier_reason"] == "referral", k
        assert all(it["referral"]["state"] == "none" for it in items if it["company"] != "Kramerica Labs")


def test_variant_rule():
    """With variants/ set up but no family approved, items can only be Tier A."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "variants").mkdir()
        lines = build(root)
        assert lines[0].startswith("QUEUE size=4 "), lines
        q = queue(root)
        assert all(it["tier"] == "A" and it["tier_reason"] in ("no_variant", "usajobs") for it in q["items"]), q["items"]
        assert all(it["resume_status"] == "tier_a_pending" and it["variant"] == "" for it in q["items"])


def test_not_sent():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "applications" / "2026-09-20 Acme Bio - QC Microbiologist"
        old.mkdir(parents=True)
        (old / "posting.md").write_text("x\n", encoding="utf-8")
        (old / ".runner_files").write_text("posting.md\n", encoding="utf-8")
        claimed = root / "applications" / "2026-09-21 Globex Bio - Scientist"
        claimed.mkdir()
        (claimed / ".runner_files").write_text("posting.md\n", encoding="utf-8")
        (claimed / ".claimed").write_text("", encoding="utf-8")
        mine = root / "applications" / "2026-09-22 Hooli - Scientist"
        mine.mkdir()  # no .runner_files: not a runner folder
        lines = build(root, "pool_12.json")
        assert any(ln.startswith("NOT_SENT moved 1") for ln in lines), lines
        assert (root / "applications" / "_not_sent" / "2026-09" / old.name / "posting.md").exists()
        assert claimed.exists() and mine.exists() and not old.exists()


def link_folder(root, company, url, key, files=()):
    """A job folder the session made from a link: posting.md (posting.py's head) plus files."""
    folder = root / "applications" / f"{DAY.isoformat()} {company} - Lab Scientist"
    folder.mkdir(parents=True)
    (folder / "posting.md").write_text(f"URL: {url}\nRetrieved: {DAY.isoformat()}\nCompany: {company}\n"
                                       f"Role: Lab Scientist\nLocation: Springfield, IL\nJob key: {key}\n"
                                       f"Posted: 2026-11-01\n\n## Description\n\nSynthetic.\n", encoding="ascii")
    for name, body in files:
        (folder / name).write_text(body, encoding="ascii")
    return folder


def test_user_addendum():
    """Jobs from links (added events) keep the numbers the session gave them: a
    runner addendum numbers after them, also when an add lands while it is being
    built; user-addendum writes them as full 2.5 items and never writes in
    their folders; staging copies only the /job add item's Tier B file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        pasted = link_folder(root, "Acme Robotics", "https://job-boards.greenhouse.io/acme/jobs/5550001",
                             "greenhouse:5550001", [("Jordan Avery Resume.pdf", "%PDF-1.4 A\n")])
        plan = {"form_source": "page", "fields": [
            {"label": "Desired salary", "name": "salary", "status": "sensitive_confirm", "answer": "$95,000",
             "answer_source": "profile:salary"}]}
        added = link_folder(root, "Globex Bio", "https://jobs.lever.co/globex/aaaa-bbbb", "lever:aaaa-bbbb",
                            [("resume_B.pdf", "%PDF-1.4 B\n"), ("fill_plan.json", json.dumps(plan)),
                             ("tune_log.json", json.dumps({"status": "tuned", "family": "qc_micro",
                                                           "variant": "qc_micro@v1"}))])
        third = link_folder(root, "Initech Labs", "https://job-boards.greenhouse.io/initech/jobs/5550003",
                            "greenhouse:5550003")
        assert events.add(root, pasted, "pasted")[0] == "ADDED #31 greenhouse:5550001 (addendum_user)"
        assert events.add(root, added, "add", family="qc_micro")[0] == "ADDED #32 lever:aaaa-bbbb (addendum_user)"
        # The runner's late finds number after them, and one already added from a link (the
        # /job add probe_url that pools it for tomorrow) is not queued twice.
        late = json.loads((POOL / "new4.json").read_text(encoding="utf-8"))
        late["items"][0]["job_key"] = "lever:aaaa-bbbb"
        (root / "late.json").write_text(json.dumps(late), encoding="utf-8")
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--pool", str(root / "late.json"))
        assert code == 0 and lines[0] == "ADDENDUM addendum_0930.json items=33-35", lines
        keys = [it["job_key"] for it in json.loads(jobfs.Folder(root).addendum(DAY, "0930").read_text(encoding="utf-8"))["items"]]
        assert "lever:aaaa-bbbb" not in keys, keys
        # An add lands between the addendum's count and its write: its items move up after it.
        orig, calls = jobqueue.numbers_used, []

        def racing(r, d):
            got = orig(r, d)
            if not calls:
                calls.append(events.add(root, third, "pasted")[0])
            return got
        jobqueue.numbers_used = racing
        try:
            path = jobqueue.addendum(root, DAY, limit=2, emit=lambda s: None)
        finally:
            jobqueue.numbers_used = orig
        assert calls == ["ADDED #36 greenhouse:5550003 (addendum_user)"], calls
        assert [it["n"] for it in json.loads(path.read_text(encoding="utf-8"))["items"]] == [37, 38]
        out = []
        upath = jobqueue.user_addendum(root, DAY, emit=out.append)
        assert out == ["ADDENDUM addendum_user.json items=31,32,36"], out
        data = json.loads(upath.read_text(encoding="utf-8"))
        assert jobqueue.check_queue(data) == [], jobqueue.check_queue(data)
        byn = {it["n"]: it for it in data["items"]}
        assert (byn[31]["tier"], byn[31]["resume_status"], byn[31]["added_by"]) == ("A", "tier_a_ready", "pasted")
        assert (byn[32]["tier"], byn[32]["resume_status"], byn[32]["variant"], byn[32]["sensitive"],
                byn[32]["answers_status"], byn[32]["account"]) == \
            ("B", "built", "qc_micro@v1", ["salary"], "sensitive_confirm", "none"), byn[32]
        assert byn[36]["resume_status"] == "tier_a_pending" and not jobfs.prev_path(upath).exists()
        assert jobqueue.user_addendum(root, DAY, emit=out.append) is None, "nothing changed: no write"
        code, lines = cli("user-addendum", "--root", str(root), "--date", DAY.isoformat())
        assert code == 0 and lines == ["ADDENDUM addendum_user.json unchanged"], lines
        assert sorted(p.name for p in pasted.iterdir()) == ["Jordan Avery Resume.pdf", "posting.md"]
        top, keys = jobqueue.numbers_used(root, DAY)
        assert top == 38 and "greenhouse:5550003" in keys
        lines = []
        jobqueue.stage_uploads(root, DAY, emit=lines.append)
        names = [p.name for p in (root / "upload").iterdir() if p.is_file()]
        assert any(n.startswith("32_") for n in names) and not any(n.startswith(("31_", "36_")) for n in names), names
        assert not any(ln.startswith(("WARN #31", "WARN #36")) for ln in lines), lines


def test_do_yourself_files_local_date():
    """stage-uploads keeps today's E<n>_ files by the folder's [time] date, not
    the PC's, and sends an earlier day's to that day's _done folder."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        noon = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        pc_day = datetime.fromtimestamp(noon.timestamp()).date()
        # A zone whose date at that moment differs from the PC's, whatever the PC's zone is.
        zone, local_day = (("Pacific/Kiritimati", date(2026, 11, 7)) if pc_day == date(2026, 11, 6)
                           else ("Pacific/Pago_Pago", date(2026, 11, 6)))
        (root / "config").mkdir()
        (root / "config" / "settings.toml").write_text(f'[time]\ntimezone = "{zone}"\n', encoding="ascii")
        up = root / "upload"
        up.mkdir()
        today_e, old_e = up / "E1_Avery_Resume_HooliHealth.pdf", up / "E2_Avery_Resume_Initech.pdf"
        for p, t in ((today_e, noon), (old_e, noon - timedelta(days=1))):
            p.write_bytes(b"%PDF-1.4 E\n")
            os.utime(p, (t.timestamp(), t.timestamp()))
        saved = os.environ["JOB_SKILL_NOW"]
        os.environ["JOB_SKILL_NOW"] = noon.isoformat()
        try:
            assert jobfs.today(root) == local_day
            jobqueue.stage_uploads(root, emit=lambda s: None)
        finally:
            os.environ["JOB_SKILL_NOW"] = saved
        assert today_e.exists(), f"today's E1 moved (folder date {local_day}, PC date {pc_day})"
        assert (up / "_done" / (local_day - timedelta(days=1)).isoformat() / old_e.name).exists()


def main():
    for fn in (test_build_71, test_small_pool_widens, test_frozen_and_wait, test_addendum_during_session,
               test_fold_events_idempotent, test_upload_staging, test_referral, test_variant_rule, test_not_sent,
               test_user_addendum, test_do_yourself_files_local_date):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
