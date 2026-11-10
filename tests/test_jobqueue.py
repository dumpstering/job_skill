"""Check job/scripts/jobqueue.py: the queue build (size, caps, per-day limits,
Tier A, session order, account block), widen (on the usable supply), FROZEN
and WAIT, the variant rule (prep_failed fill, variant_wait, `more`), addenda
during a session and before the list, fold-events (old pending files), what
comes back the next day (parks, closed and knockout skips, user skips, a
locked tenant, a handover with no s), cap reservations for one day, referral
links, upload staging (the do-yourself files by the folder's date), the
_not_sent move, and the jobs from links (added events, addendum_user.json).

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
        # Shared decision 4: a first build never waits for a session; only a rebuild does.
        assert build(root)[0].startswith("QUEUE size=30")
        before = jobfs.Folder(root).queue_json(DAY).read_bytes()
        lines = build(root)
        assert lines[0].startswith("WAIT") and jobfs.Folder(root).queue_json(DAY).read_bytes() == before, lines
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
        # tomorrow: the applied job leaves the pool, the parked one is back (decision 6: a
        # park holds for its own day only), and the user's skip waits a week in the pool
        tomorrow = DAY + timedelta(days=1)
        code, lines = cli("build", "--root", str(root), "--date", tomorrow.isoformat(),
                          env={"JOB_SKILL_NOW": "2026-11-07T14:00:00Z"})
        assert code == 0, lines
        keys = {it["job_key"] for it in queue(root, tomorrow)["items"]}
        assert items[0]["job_key"] not in keys and items[2]["job_key"] in keys and items[1]["job_key"] not in keys
        pool = {it["job_key"] for it in gates.load_pool(root)["items"]}
        assert items[0]["job_key"] not in pool and items[1]["job_key"] in pool


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
    """With variants/ set up but no family approved (or an approved file changed),
    at most tier_a_per_day items are Tier A; the rest are not cut silently:
    they fill the free slots last as prep_failed (the card's re-approval line),
    counts.variant_wait says how many wait per family, and `more` names the
    re-approval instead of the caps (shared decision 2)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "variants").mkdir()
        lines = build(root)
        assert lines[0].startswith("QUEUE size=30 "), lines
        q = queue(root)
        assert jobqueue.check_queue(q) == [], jobqueue.check_queue(q)[:5]
        tier_a = [it for it in q["items"] if it["tier"] == "A"]
        assert len(tier_a) == 4 and all(it["tier_reason"] in ("no_variant", "usajobs") for it in tier_a), tier_a
        assert all(it["resume_status"] == "tier_a_pending" and it["variant"] == "" for it in tier_a)
        held = [it for it in q["items"] if it["tier"] == "B"]
        assert len(held) == 26 and all(it["resume_status"] == "prep_failed" for it in held)
        assert min(it["n"] for it in held) > max(it["n"] for it in tier_a), "the waiting items come last"
        wait = q["counts"]["variant_wait"]
        assert sum(wait.values()) == 67 and wait["qc_micro"] >= 1, wait
        assert f"WAIT_VARIANT qc_micro {wait['qc_micro']} jobs" in lines, lines
        # Nothing staged for them: no resume of an unapproved variant goes out.
        out = []
        jobqueue.stage_uploads(root, DAY, emit=out.append)
        assert not any(f"WARN #{it['n']:02d}" in ln for it in held for ln in out), out
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat())
        assert code == 0 and lines[0].startswith("ADDENDUM none: assay_dev needs a quick re-approval ("), lines
        assert "qc_micro needs a quick re-approval" in lines[0] and "caps" not in lines[0], lines


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


def emit_at(root, when, kind, it, reason="", day=DAY):
    """One queue event written at a pinned clock (event ids follow the clock)."""
    saved = os.environ["JOB_SKILL_NOW"]
    os.environ["JOB_SKILL_NOW"] = when
    try:
        events.emit(root, kind, {"reason": reason, "question_ids": [], "confirmation": "none"},
                    job_key=it["job_key"], n=it["n"], day=day)
    finally:
        os.environ["JOB_SKILL_NOW"] = saved


def build_on(root, day, pool=None):
    args = ["build", "--root", str(root), "--date", day.isoformat()] + (["--pool", str(pool)] if pool else [])
    code, lines = cli(*args, env={"JOB_SKILL_NOW": f"{day.isoformat()}T14:00:00Z"})
    assert code == 0, lines
    return lines


def track(root, it, status, day=DAY):
    """A tracker row for a queue item, written straight to applications.xlsx (no runner here)."""
    r = subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "add", "--root", str(root), "--transport",
                        "direct", "--company", it["company"], "--role", it["role"], "--url", it["apply_url"],
                        "--folder", it["folder"], "--job-key", it["job_key"], "--status", status,
                        "--family", it["family"], "--source", "Runner"], capture_output=True, text=True,
                       env=dict(os.environ, JOB_SKILL_NOW=f"{day.isoformat()}T18:00:00Z"))
    assert r.returncode == 0 and r.stdout.startswith("ADDED"), r.stdout + r.stderr
    return r.stdout.split()[1]


def test_outcomes_next_day():
    """PIPE-10: closed and knockout skips and an SSO block leave the pool for
    good, an account_locked block pauses its tenant for 7 days, a usage-limit
    park comes back the next day; the build folds the session's late events
    itself (no tick ran after them)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        items = queue(root)["items"]
        green = sorted((it for it in items if it["account"] == "none"), key=lambda it: -it["score"])
        locked = next(it for it in items if it["account"] == "new")
        tenant = locked["host"]
        siblings = [it for it in json.loads((POOL / "pool_71.json").read_text(encoding="utf-8"))["items"]
                    if netfetch_host(it) == tenant]
        assert len(siblings) >= 2, siblings
        # The session's last events, written after the last tick (no fold-events ran after them).
        emit_at(root, "2026-11-07T05:40:00Z", "parked", green[0], "usage_limit")
        emit_at(root, "2026-11-07T05:41:00Z", "skipped", green[1], "closed")
        emit_at(root, "2026-11-07T05:42:00Z", "skipped", green[2], "knockout_at_form")
        emit_at(root, "2026-11-07T05:43:00Z", "blocked", green[3], "sso_required")
        emit_at(root, "2026-11-07T05:44:00Z", "blocked", locked, "account_locked")
        day2 = DAY + timedelta(days=1)
        build_on(root, day2)
        q2 = queue(root, day2)
        keys = {it["job_key"] for it in q2["items"]}
        pool = {it["job_key"] for it in gates.load_pool(root)["items"]}
        for it in green[1:4]:
            assert it["job_key"] not in keys and it["job_key"] not in pool, it["job_key"]
        assert green[0]["job_key"] in keys, "a usage-limit park comes back the next day"
        assert not any(it["host"] == tenant for it in q2["items"]) and tenant not in q2["account_block"]["tenants"]
        assert locked["job_key"] in pool
        state = json.loads(jobfs.Folder(root).queue_state_json.read_text(encoding="utf-8"))["items"]
        assert state[locked["job_key"]]["tenant"] == tenant
        ctx6 = jobqueue.Context(root, DAY + timedelta(days=6))
        ctx7 = jobqueue.Context(root, DAY + timedelta(days=7))
        assert locked["job_key"] in ctx6.locked and ctx7.locked == {}, (ctx6.locked, ctx7.locked)


def netfetch_host(it):
    import netfetch
    return netfetch.host_of(it.get("apply_url") or it.get("url") or "")


def test_user_skip_week():
    """UD-15: `k` keeps the job in the pool but out of the next 7 days' lists;
    a second `k` on another day takes it out for good."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root)
        x = max(queue(root)["items"], key=lambda it: (it["posted"] or "", it["n"]))  # still fresh a week on
        emit_at(root, "2026-11-06T18:00:00Z", "skipped", x, "user")
        day2 = DAY + timedelta(days=1)
        build_on(root, day2)
        assert x["job_key"] not in {it["job_key"] for it in queue(root, day2)["items"]}
        pool = gates.load_pool(root)["items"]
        assert x["job_key"] in {it["job_key"] for it in pool}
        later = DAY + timedelta(days=jobqueue.USER_SKIP_HOLD_DAYS)
        os.environ["JOB_SKILL_NOW"], saved = f"{later.isoformat()}T14:00:00Z", os.environ["JOB_SKILL_NOW"]
        try:
            keep, drop = jobqueue.eligible([dict(it) for it in pool], jobqueue.Context(root, later))
            assert x["job_key"] in {it["job_key"] for it in keep}, "back after a week"
            emit_at(root, f"{later.isoformat()}T18:00:00Z", "skipped", x, "user", day=later)
            jobqueue.fold_events(root, later, emit=lambda s: None)
            keep, drop = jobqueue.eligible([dict(it) for it in pool], jobqueue.Context(root, later + timedelta(1)))
            assert x["job_key"] in {it["job_key"] for it in drop}, "a second k drops it"
        finally:
            os.environ["JOB_SKILL_NOW"] = saved


def test_handover_carry():
    """UD-11 and contracts-03: a job handed over with no `s` comes back first the
    next day, flagged unsubmitted (ask whether it went through); a job the user
    skipped or put off after its handover row was added goes back to the pool,
    and its row no longer counts for the caps."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, "pool_12.json")
        items = queue(root)["items"]
        a, b = items[3], items[5]
        for it, last in ((a, None), (b, "later")):
            emit_at(root, "2026-11-06T18:00:00Z", "started", it)
            track(root, it, "Filled - awaiting submit")
            emit_at(root, "2026-11-06T18:05:00Z", "handed_over", it)
            if last:
                emit_at(root, "2026-11-06T18:06:00Z", last, it, "user")
        day2 = DAY + timedelta(days=1)
        build_on(root, day2)
        q2 = queue(root, day2)["items"]
        assert q2[0]["job_key"] == a["job_key"] and q2[0]["flags"] == ["unsubmitted"], q2[0]
        assert b["job_key"] in {it["job_key"] for it in q2}, "put off after the handover: not lost"
        assert all("unsubmitted" not in it["flags"] for it in q2[1:])
        index = caps.load_index(root, day2)
        rows = {e["job_key"] for e in index if e["status"] != "Queued"}
        assert a["job_key"] in rows and b["job_key"] not in rows, rows
        (root / q2[0]["folder"] / "resume_B.pdf").write_bytes(b"%PDF-1.4 a")
        r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "next", "--root", str(root), "--date",
                            day2.isoformat()], capture_output=True, text=True,
                           env=dict(os.environ, JOB_SKILL_NOW=f"{day2.isoformat()}T17:00:00Z"))
        out = r.stdout.splitlines()
        assert out[0] == f"NEXT #01 {a['job_key']}" and any(ln.startswith("STATE unsubmitted") for ln in out), out


def test_numbers_around_addendum():
    """PIPE-03 / UD-03: `more` before today's list exists writes nothing; a
    list built after an addendum (an older runner) numbers after it and never
    repeats its jobs or upload numbers."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        gates.save_pool(root, json.loads((POOL / "pool_71.json").read_text(encoding="utf-8"))["items"], date_=DAY)
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--limit", "5")
        assert lines == ["ADDENDUM none: no list for 2026-11-06 yet (the daily builds it first)"], lines
        assert not jobfs.Folder(root).queue_dir(DAY).exists() or not list(jobfs.Folder(root).queue_dir(DAY).iterdir())
        build(root)
        code, lines = cli("addendum", "--root", str(root), "--date", DAY.isoformat(), "--limit", "3")
        assert lines[0].startswith("ADDENDUM addendum_0930.json items=31-33"), lines
        jobfs.Folder(root).queue_json(DAY).unlink()  # as if the addendum came first
        build(root)
        items = jobqueue.day_items(root, DAY)
        ns = [it["n"] for it in items]
        assert len(ns) == len(set(ns)) and len({it["job_key"] for it in items}) == len(items), ns
        q = queue(root)
        assert min(it["n"] for it in q["items"]) == 34, [it["n"] for it in q["items"]][:5]
        ups = [Path(it["upload"]).name[:2] for it in items]
        assert len(ups) == len(set(ups)), ups
        first_new = next(it for it in q["items"] if it["account"] == "new")
        assert q["account_block"]["after_n"] == first_new["n"] - 1, (q["account_block"], first_new["n"])


def test_reservation_release():
    """PIPE-12: a sibling deferred only by today's reservation waits until
    tomorrow, not 30 days, and the next build checks it again."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = json.loads((POOL / "pool_12.json").read_text(encoding="utf-8"))
        for it in data["items"][:3]:  # three QC roles at one company
            it.update(company="Acme Bio", company_norm="acme bio", family="qc_micro")
        (root / "p.json").write_text(json.dumps(data), encoding="utf-8")
        build(root, str(root / "p.json"))
        keys = {it["job_key"] for it in queue(root)["items"]}
        pool = {it["job_key"]: it for it in gates.load_pool(root)["items"]}
        rules = caps.load_rules(root)
        out = [k for k in ("greenhouse:910001", "greenhouse:910002", "greenhouse:910003") if k not in keys]
        assert len(out) == 3 - min(3, rules["per_family_per_company_30d"], rules["per_company_per_day"]), keys
        for k in out:
            assert pool[k]["deferred_until"] == "2026-11-07", pool[k]  # was 2026-12-06: the slot is free tomorrow
        ctx = jobqueue.Context(root, DAY + timedelta(days=1))
        keep, _ = jobqueue.eligible([dict(it) for it in pool.values()], ctx)
        assert set(out) <= {it["job_key"] for it in keep}


def test_fold_old_pending_event():
    """PIPE-07: an event file that waits in runner/in/events past the 8-day
    folded list never undoes a later state."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build(root, "pool_12.json")
        x = queue(root)["items"][4]
        emit_at(root, "2026-11-06T18:00:00Z", "parked", x, "unknown_required")
        jobqueue.fold_events(root, DAY, emit=lambda s: None)
        emit_at(root, "2026-11-08T18:00:00Z", "unparked", x, day=DAY + timedelta(days=2))
        jobqueue.fold_events(root, DAY + timedelta(days=2), emit=lambda s: None)
        for _ in range(2):  # the second fold after the 8 days is the one that used to re-apply it
            data = jobqueue.fold_events(root, DAY + timedelta(days=10), emit=lambda s: None)
        assert data["items"][x["job_key"]]["state"] == "unparked", data["items"][x["job_key"]]


def test_referral_link_in_queue():
    """contracts-07: `ref <n> <url>` stored in the tracker reaches the queue
    item's referral_url, which `next` opens."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def ref(key, url):
            r = subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "referral-add", "--root", str(root),
                                "--transport", "direct", "--contact", "Pat Lee", "--company", "Acme Bio", "--job-key",
                                key, "--status", "Sent", "--referral-url", url], capture_output=True, text=True)
            assert r.returncode == 0, r.stdout + r.stderr
        url = "https://job-boards.greenhouse.io/acme/jobs/910001?gh_src=ref123"
        ref("greenhouse:910001", url)
        build(root, "pool_12.json")
        items = queue(root)["items"]
        it = next(it for it in items if it["job_key"] == "greenhouse:910001")
        assert it["referral_url"] == url, it
        # stored after the list was built: `next` opens it all the same
        first = items[0] if items[0]["job_key"] != "greenhouse:910001" else items[1]
        for it in items[:items.index(first)]:
            emit_at(root, "2026-11-06T18:00:00Z", "applied", it)
        late = first["apply_url"] + "?gh_src=ref456"
        ref(first["job_key"], late)
        for it in items:  # pretend Tier B built everything
            (root / it["folder"] / "resume_B.pdf").write_bytes(b"%PDF-1.4 " + it["job_key"].encode())
        r = subprocess.run([sys.executable, str(SCRIPTS / "today_view.py"), "next", "--root", str(root), "--date",
                            DAY.isoformat()], capture_output=True, text=True)
        assert f"OPEN {late}" in r.stdout.splitlines(), r.stdout


def test_widen_on_usable_supply():
    """Shared decision 3: widening counts the pool items the caps allow with a
    resume to send, not the raw pool."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = json.loads((POOL / "pool_71.json").read_text(encoding="utf-8"))
        for it in data["items"]:
            it.update(company="Acme Bio", company_norm="acme bio")
        ctx = jobqueue.Context(root, DAY)
        ctx._approved = {f: f"{f}@v1" for f in {it["family"] for it in data["items"]}}
        cands, _ = jobqueue.eligible(data["items"], ctx)
        assert len(cands) >= 20
        r = ctx.rules
        fams = len({it["family"] for it in cands})
        want = min(r["per_company_30d"], fams * r["per_family_per_company_30d"])
        assert jobqueue.usable_supply(ctx, cands, 4) == want < 20, "one company: its 30-day cap, not the raw pool"
        ctx._approved = {f: "" for f in ctx._approved}
        ctx.variants_on = True
        assert jobqueue.usable_supply(ctx, cands, 4) == min(4, want), "no approved variant: only the Tier A places"


def test_account_block_best_only():
    """L3-05: a new-account tenant's jobs come forward right after batch 1
    (grouped, one account for several jobs) only when its best job is at or
    above the day's median fit. A tenant whose jobs are the day's weakest
    takes its places by fit, so the user makes no account early for them:
    account_block names only the block's tenants (their accounts are made
    together), and the weak tenant's account is made at its own job."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = json.loads((POOL / "pool_71.json").read_text(encoding="utf-8"))
        tenants = sorted({jobqueue.tenant_of(it) for it in data["items"] if it["ats"] == "workday"})
        weak = tenants[0]
        # a smaller pool (3 Workday tenants, 2 jobs each: within the day's account limits), so every
        # job fits in the day's list and only the order is judged
        data["items"] = [it for it in data["items"] if it["ats"] == "workday" and
                         jobqueue.tenant_of(it) in tenants[:3]] + \
            [it for it in data["items"] if it["ats"] in ("greenhouse", "lever")][:16]
        for it in data["items"]:
            if jobqueue.tenant_of(it) == weak:
                it["fit"] = 0.05
        (root / "p.json").write_text(json.dumps(data), encoding="utf-8")
        build(root, str(root / "p.json"))
        q = queue(root)
        items = q["items"]
        weak_ns = [it["n"] for it in items if it["host"] == weak]
        others = [it["n"] for it in items if it["host"] != weak]
        assert weak_ns and min(weak_ns) > max(others), (weak_ns, others)
        new = [it for it in items if it["account"] == "new" and it["host"] != weak]
        assert new and min(it["n"] for it in new) == 11 and q["account_block"]["after_n"] == 10, \
            [(it["n"], it["account"]) for it in items]
        assert weak not in q["account_block"]["tenants"] and len(q["account_block"]["tenants"]) == 2, \
            q["account_block"]


def test_borderline_seniority_last():
    """R6: a job whose seniority asks more than the user has (the gates'
    borderline mark) is listed after the rest and takes a slot only when the
    others run out, however high its fit. A pool item from before the mark
    is judged against the user's library on the first build."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data = json.loads((POOL / "pool_71.json").read_text(encoding="utf-8"))
        gh = [it for it in data["items"] if it["ats"] == "greenhouse"]
        marked = {it["job_key"] for it in gh[:2]}
        for it in data["items"]:
            it["seniority"] = "borderline" if it["job_key"] in marked else ""
            if it["job_key"] in marked:
                it["fit"] = 0.99
        (root / "p.json").write_text(json.dumps(data), encoding="utf-8")
        build(root, str(root / "p.json"))
        items = queue(root)["items"]
        late = [it["n"] for it in items if it["job_key"] in marked]
        rest = [it["n"] for it in items if it["job_key"] not in marked]
        assert not late or min(late) > max(rest), (late, rest)
        assert len(items) == 30 and not late, "41 others fill the 30 slots first"
        # an item pooled before the mark existed: judged now, against the synthetic person's library
        for name in ("achievement_library.md", "techniques.md"):
            (root / name).write_bytes((HERE / "fixtures" / "folder" / name).read_bytes())
        ctx = jobqueue.Context(root, DAY)
        old = {"job_key": "k1", "title": "Principal Scientist", "family": "assay_dev",
               "text": "Requirements\n- PhD with 10+ years of industry experience\n"}
        plain = {"job_key": "k2", "title": "Scientist II", "family": "assay_dev",
                 "text": "Requirements\n- PhD and 2+ years of industry experience; qPCR\n"}
        assert ctx.borderline(old) and old["seniority"] == "borderline"
        assert not ctx.borderline(plain) and plain["seniority"] == ""
        assert not ctx.borderline({"job_key": "k3", "title": "Principal Scientist", "seniority": ""})


def main():
    for fn in (test_build_71, test_small_pool_widens, test_frozen_and_wait, test_addendum_during_session,
               test_fold_events_idempotent, test_upload_staging, test_referral, test_variant_rule, test_not_sent,
               test_user_addendum, test_do_yourself_files_local_date, test_outcomes_next_day, test_user_skip_week,
               test_handover_carry, test_numbers_around_addendum, test_reservation_release,
               test_fold_old_pending_event, test_referral_link_in_queue, test_widen_on_usable_supply,
               test_account_block_best_only, test_borderline_seniority_last):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
