"""Check job/scripts/events.py and the tracker's event transport (spec 2.5-2.7, C2).

Usage: python tests/test_events.py
Needs openpyxl (the runner side). Prints ok <name> per test and PASS last.

The parity test runs one 12-command script with --transport direct on one folder,
and with --transport events plus `tracker.py apply-events` on another, and wants
the same cell values in every sheet and the same stdout apart from P- IDs.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
TRACKER = SCRIPTS / "tracker.py"
EVENTS = SCRIPTS / "events.py"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
import events  # noqa: E402
import jobfs  # noqa: E402

TODAY = jobfs.today().isoformat()
EVIL = '=HYPERLINK("x")'


def run(script, *args, code=0):
    r = subprocess.run([sys.executable, str(script)] + [str(a) for a in args], capture_output=True, text=True,
                       encoding="utf-8", cwd=tempfile.gettempdir())
    assert r.returncode == code, f"{args} exited {r.returncode} (want {code}):\n{r.stdout}{r.stderr}"
    return r.stdout


def new_root():
    return Path(tempfile.mkdtemp(prefix="job_events_test_"))


def lines(out):
    return [ln for ln in out.splitlines() if ln.strip()]


def script(ids):
    """The 12 commands; ids maps 1..3 to the IDs `add` printed in this folder."""
    return [
        (["init"], 0),
        (["add", "--company", "Acme Bio", "--role", "QC Microbiologist", "--url",
          "https://boards.greenhouse.io/acmebio/jobs/4812345", "--folder", "applications/acme", "--tier", "A",
          "--family", "qc_micro", "--ats", "greenhouse", "--found-via", "watch"], 0),
        (["add", "--company", "Globex", "--role", "Lab Tech", "--status", "Filled - awaiting submit",
          "--tier", "B", "--queue-date", TODAY], 0),
        (["add", "--company", "Initech", "--role", "Analyst", "--notes", "met at fair", "--source", "By hand"], 0),
        (lambda: ["update", ids[1], "status=Applied", "applied=" + TODAY, "confirmed=" + TODAY,
                  "proof=submitted/confirmation.txt"], 0),
        (["account", "--site", "acmebio.wd5.myworkdayjobs.com", "--email", "jqa@example.com", "--employer",
          "Acme Bio", "--ats", "workday"], 0),
        (lambda: ["log-email", "--message-id", "m-1", "--email-date", "2026-11-01", "--from", "hr@example.com",
                  "--subject", EVIL, "--class", "Acknowledgement", "--job-id", ids[1]], 0),
        (["inbox-done"], 0),
        (["referral-add", "--contact", "Jordan Q. Avery", "--company", "Acme Bio", "--job-key",
          "greenhouse:4812345", "--status", "Sent"], 0),
        (["employer-set", "--employer", "Acme Bio", "--city", "Springfield", "--state", "IL"], 0),
        (lambda: ["update", ids[2], "status=Rejected"], 0),
        (lambda: ["update", ids[2], "status=Applied"], 5),  # a refused backward move
    ]


def play(root, transport):
    """Run the script; returns (stdout lines, ids)."""
    ids, out = {}, []
    for step, (argv, code) in enumerate(script(ids)):
        argv = argv() if callable(argv) else argv
        text = run(TRACKER, argv[0], "--root", root, "--transport", transport, *argv[1:], code=code)
        if argv[0] == "add":
            ids[len(ids) + 1] = text.split()[1]
        out += lines(text)
    return out, ids


def sheet_values(path):
    wb = openpyxl.load_workbook(path)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def test_parity():
    a, b = new_root(), new_root()
    out_a, ids_a = play(a, "direct")
    out_b, ids_b = play(b, "events")
    assert ids_a == {1: "1", 2: "2", 3: "3"}, ids_a
    assert all(re.fullmatch(r"P-[0-9a-f]{8}", v) for v in ids_b.values()), ids_b
    assert not (b / "applications.xlsx").exists(), "the events transport never writes the xlsx"
    pending = list((b / "runner" / "in" / "events").glob("*_tracker.json"))
    assert len(pending) == 11, f"want 11 events (the refused move writes none), got {len(pending)}"
    norm_b = []
    for ln in out_b:
        for n, pid in ids_b.items():
            ln = ln.replace(f"{pid} (pending)", ids_a[n]).replace(pid, ids_a[n])
        norm_b.append(ln.replace(str(b), "<ROOT>"))
    assert [ln.replace(str(a), "<ROOT>") for ln in out_a] == norm_b, "\n".join(
        f"{x!r} | {y!r}" for x, y in zip(out_a, norm_b))
    assert lines(run(TRACKER, "apply-events", "--root", b)) == ["APPLIED 11 PENDING 0 REJECTED 0"]
    va, vb = sheet_values(a / "applications.xlsx"), sheet_values(b / "applications.xlsx")
    assert list(va) == list(vb), (list(va), list(vb))
    for sheet in va:
        assert va[sheet] == vb[sheet], f"{sheet} differs:\n{va[sheet]}\n{vb[sheet]}"
    # the formula-looking subject is text in both
    for root in (a, b):
        ws = openpyxl.load_workbook(root / "applications.xlsx")["Email log"]
        head = [c.value for c in ws[1]]
        cell = ws.cell(2, head.index("Subject") + 1)
        assert cell.value == EVIL and cell.data_type == "s", (cell.value, cell.data_type)
    snap = json.loads((b / "runner" / "out" / "tracker.json").read_text(encoding="utf-8"))
    assert snap["refs"] == {ids_b[1]: 1, ids_b[2]: 2, ids_b[3]: 3}, snap["refs"]
    assert snap["email_log_ids"] == ["m-1"] and snap["settings"]["Last inbox check"] == TODAY
    assert snap["referrals"][0]["Status"] == "Sent" and snap["employers"][0]["City"] == "Springfield"
    return b


def test_overlay_reads_done(b):
    """A stale snapshot plus events_done still gives the current view (2.7.4)."""
    snap = b / "runner" / "out" / "tracker.json"
    stale = snap.read_text(encoding="utf-8")
    pid = run(TRACKER, "add", "--root", b, "--company", "Umbrella", "--role", "Chemist",
              "--transport", "events").split()[1]
    assert run(TRACKER, "apply-events", "--root", b).strip() == "APPLIED 1 PENDING 0 REJECTED 0"
    for p in (snap, jobfs.prev_path(snap)):
        p.write_text(stale, encoding="utf-8")  # the bridge still shows the old copy
    out = lines(run(TRACKER, "show", "--root", b, "--company", "Umbrella"))
    assert len(out) == 2 and out[1].startswith(f"{pid} | ") and "Chemist" in out[1], out
    run(TRACKER, "snapshot", "--root", b)
    out = lines(run(TRACKER, "show", "--root", b, "--company", "Umbrella"))
    assert len(out) == 2 and out[1].startswith("4 | "), out


def put_event(root, ev, name=None, old=True):
    path = jobfs.Folder(root).events / (name or events.file_name(ev))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ev if isinstance(ev, str) else json.dumps(ev), encoding="utf-8")
    if old:
        t = time.time() - 60
        os.utime(path, (t, t))
    return path


def tracker_event(root, argv, eid="20261112T171503Z_a91c33d0"):
    return {"v": 1, "id": eid, "type": "tracker", "at": "2026-11-12T09:15:03-08:00", "session": "",
            "job_key": "", "n": None, "date": "2026-11-12", "payload": {"argv": argv}}


def test_rejections():
    root = new_root()
    run(TRACKER, "init", "--root", root, "--transport", "direct")
    bad = put_event(root, '{"v":1, "id": "20261112T171503Z_0000000', name="20261112T171503Z_00000001_tracker.json")
    for n in (1, 2):
        assert run(TRACKER, "apply-events", "--root", root).strip() == "APPLIED 0 PENDING 1 REJECTED 0", n
        assert bad.exists()
    out = lines(run(TRACKER, "apply-events", "--root", root))
    assert out[-1] == "APPLIED 0 PENDING 0 REJECTED 1" and "unreadable after 3 tries" in out[0], out
    assert out[0].startswith(f"ERROR bad event file {bad.name}: ") and out[0].endswith("moved to runner/in/bad/"), out
    aside = jobfs.Folder(root).runner_in / "bad"
    assert (aside / bad.name).exists() and "not JSON" in (aside / (bad.name + ".reason.txt")).read_text(
        encoding="utf-8")
    rej = jobfs.Folder(root).events_rejected
    # a file still arriving (young) is pending without counting a try
    young = put_event(root, "{", name="20261112T171504Z_00000002_tracker.json", old=False)
    assert run(TRACKER, "apply-events", "--root", root).strip() == "APPLIED 0 PENDING 1 REJECTED 0"
    young.unlink()
    # a folder outside the root
    ev = tracker_event(root, ["add", "--company", "X", "--role", "Y", "--folder", "../../outside"])
    put_event(root, ev)
    out = lines(run(TRACKER, "apply-events", "--root", root))
    assert out[-1] == "APPLIED 0 PENDING 0 REJECTED 1" and "folder outside the root" in out[0], out
    ev = tracker_event(root, ["update", "1", "folder=C:/Windows"], "20261112T171503Z_a91c33d1")
    put_event(root, ev)
    assert "folder outside the root" in run(TRACKER, "apply-events", "--root", root)
    q = {"v": 1, "id": "20261112T171503Z_a91c33d2", "type": "applied", "at": "2026-11-12T09:15:03-08:00",
         "session": "", "job_key": "greenhouse:1", "n": 7, "date": "2026-11-12",
         "payload": {"proof": "../../etc/passwd", "confirmation": "page"}}
    put_event(root, q)
    assert "proof path outside the root" in run(TRACKER, "apply-events", "--root", root)
    # schema problems, a command that fails, a name that does not match
    cases = [
        (dict(tracker_event(root, ["show"], "20261112T171503Z_a91c33d3")), "not a write command"),
        (dict(tracker_event(root, ["add", "--root", "C:/x"], "20261112T171503Z_a91c33d4")), "may not hold --root"),
        (dict(tracker_event(root, ["update", "99", "notes=x"], "20261112T171503Z_a91c33d5")), "ERROR no row with ID 99"),
        (dict(tracker_event(root, ["add"], "20261112T171503Z_a91c33d6"), v=2), "schema version"),
        (dict(tracker_event(root, ["add"], "20261112T171503Z_a91c33d7"), type="hacked"), "unknown type"),
    ]
    for ev, why in cases:
        put_event(root, ev, name=f"{ev['id']}_tracker.json")
        out = run(TRACKER, "apply-events", "--root", root)
        assert why in out and "REJECTED 1" in out, (why, out)
    assert len(list(rej.glob("*.reason.txt"))) == 8
    snap = json.loads((root / "runner" / "out" / "tracker.json").read_text(encoding="utf-8"))
    assert len(snap["pending_rejected"]) == 9 and snap["applications"] == [], "the file set aside counts too"


def test_emit_and_overlay():
    root = new_root()
    ids = []
    for t, payload, n in (("started", {}, 7), ("handed_over", {}, 7), ("applied", {"confirmation": "page"}, 7),
                          ("started", {}, 8), ("parked", {"reason": "unknown_required", "question_ids": ["Q-12"]}, 8),
                          ("usage", {"session_pct": 34, "weekly_pct": 12, "after_n": 5, "minutes": 31}, None),
                          ("answer_used", {"answer_id": "ANS-012"}, 7), ("answer_used", {"answer_id": "ANS-012"}, 8)):
        args = ["emit", "--root", root, "--type", t, "--json", json.dumps(payload), "--job-key",
                f"greenhouse:{n or 0}"]
        if n is not None:
            args += ["--n", n]
        out = run(EVENTS, *args).strip()
        assert re.fullmatch(r"EVENT \d{8}T\d{6}Z_[0-9a-f]{8}", out), out
        ids.append(out.split()[1])
    # the clock is pinned, so all share one second; names still sort in creation order
    names = sorted(p.name for p in jobfs.Folder(root).events.glob("*.json"))
    assert [events.NAME_RE.match(n).group(1) for n in names] == ids and len(set(ids)) == len(ids), (names, ids)
    run(TRACKER, "add", "--root", root, "--company", "Acme", "--role", "R", "--transport", "events")
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert out == [f"OVERLAY {TODAY}", "ITEM #7 greenhouse:7 applied confirmation=page",
                   "ITEM #8 greenhouse:8 parked reason=unknown_required questions=Q-12",
                   "TRACKER PENDING 1", "TRACKER REJECTED 0",
                   "USAGE after #5 session 34% weekly 12% minutes 31", "ANSWER USED ANS-012 x2"], out
    view = events.overlay(root)
    assert view["items"][(7, "greenhouse:7")]["history"] == ["started", "handed_over", "applied"]
    # the runner moves them; the overlay still reads them from events_done
    assert run(TRACKER, "apply-events", "--root", root).strip() == "APPLIED 9 PENDING 0 REJECTED 0"
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert out[1] == "ITEM #7 greenhouse:7 applied confirmation=page" and "TRACKER PENDING 0" in out, out
    assert lines(run(EVENTS, "overlay", "--root", root, "--date", "2026-01-01"))[1] == "ITEMS none"
    # an envelope form, and bad input
    out = run(EVENTS, "emit", "--root", root, "--type", "skipped", "--json",
              json.dumps({"payload": {"reason": "user"}, "n": 9, "job_key": "lever:x"}))
    assert out.startswith("EVENT ")
    for args, why in ((["--type", "parked", "--json", '{"reason": "bored"}'], "ERROR unknown reason 'bored'"),
                      (["--type", "nope", "--json", "{}"], "ERROR argument --type"),
                      (["--type", "applied", "--json", "[1]"], "ERROR --json must be a JSON object"),
                      (["--type", "applied", "--json", '{"folder": "../x"}'], "ERROR folder path outside the root"),
                      (["--type", "tracker", "--json", '{"argv": ["add", "--transport", "direct"]}'],
                       "ERROR payload.argv may not hold --transport"),
                      (["--type", "usage", "--json", '{"minutes": "31"}'], "ERROR payload.minutes must be a number")):
        out = run(EVENTS, "emit", "--root", root, *args, code=1)
        assert out.startswith(why), (args, out)
    big = root / "claude" / "big.json"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text(json.dumps({"notes": "x" * 65400}), encoding="utf-8")  # under the file cap, over the event cap
    out = run(EVENTS, "emit", "--root", root, "--type", "started", "--json-file", "claude/big.json", code=1)
    assert out.strip() == "ERROR event over 64 KB", out
    assert not big.exists(), "--json-file is read once and removed, like --args-file"


def test_json_file_confined():
    """G3-05 / R4: events.py emit --json-file reads only a file under
    <root>/claude/ (at most 64 KB, nested at most 64 deep) and removes it once read."""
    root = new_root()
    (root / "claude").mkdir()
    outside = Path(tempfile.mkdtemp(prefix="job_events_outside_")) / "payload.json"
    outside.write_text('{"reason": "user"}', encoding="utf-8")
    loose = root / "loose.json"
    loose.write_text('{"reason": "user"}', encoding="utf-8")
    for path in (outside, loose, "claude/../loose.json"):
        out = run(EVENTS, "emit", "--root", root, "--type", "skipped", "--json-file", path, code=1)
        assert out.startswith("ERROR --json-file ") and "outside claude/" in out, (path, out)
    assert outside.exists() and loose.exists(), "a refused file outside claude/ is left alone"
    assert not jobfs.Folder(root).events.exists() or not list(jobfs.Folder(root).events.glob("*.json"))
    deep = root / "claude" / "deep.json"
    deep.write_bytes(b'{"payload": ' + b"[" * 20000 + b"]" * 20000 + b"}")
    out = run(EVENTS, "emit", "--root", root, "--type", "skipped", "--json-file", "claude/deep.json", code=1)
    assert out.startswith("ERROR --json-file ") and "nested deeper than 64" in out and not deep.exists(), out
    (root / "claude" / "ok.json").write_text('{"payload": {"reason": "user"}, "n": 3}', encoding="utf-8")
    out = run(EVENTS, "emit", "--root", root, "--type", "skipped", "--json-file", "claude/ok.json")
    assert out.startswith("EVENT ") and not (root / "claude" / "ok.json").exists(), out
    print("ok emit --json-file: only under claude/, at most 64 KB and 64 deep, read once and removed")


def test_printed_values():
    """R2: a job key events.py or tracker.py prints for Claude to put inline
    holds only A-Z a-z 0-9 . _ : / - (an unsafe stored key is never printed),
    and a posted value is a YYYY-MM-DD date or "undated"."""
    import jobkey
    root = new_root()
    run(TRACKER, "init", "--root", root, "--transport", "direct")
    bad_key = "url:example.com/j?a=1&b=$(x)`y`"
    put_event(root, {"v": 1, "id": "20261112T171505Z_0000abcf", "type": "started", "at": "2026-11-12T09:15:05-08:00",
                     "session": "", "job_key": bad_key, "n": 5, "date": TODAY, "payload": {}})
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert out[1] == f"ITEM #5 {events.KEY_REFUSED} started" and not any("$(" in ln for ln in out), out
    out = lines(run(TRACKER, "check", "--root", root, "--job-key", bad_key, "--company", "Acme", "--title", "R"))
    key = out[0].split(" ", 2)[2]
    assert out[0].startswith("JOB KEY ") and jobkey.inline_ok(key) and key == jobkey.safe_key(bad_key), out
    for value, want in (("2026-10-01", "2026-10-01"), ("2026-10-01T08:00:00Z", "2026-10-01"), ("Nov 3", "undated"),
                        ("2026-02-30", "undated"), (None, "undated"), ("$(x)", "undated")):
        assert events.posted_value(value) == want, (value, events.posted_value(value))
    ev = {"v": 1, "id": "20261112T171506Z_0000abd0", "type": "added", "at": "2026-11-12T09:15:06-08:00",
          "session": "", "job_key": "greenhouse:9", "n": 9, "date": TODAY,
          "payload": {"via": "add", "item": {"folder": "applications/x", "posted": "last week"}}}
    assert events.validate(ev, root) == "payload.item.posted must be a YYYY-MM-DD date or \"undated\"", \
        events.validate(ev, root)
    print("ok printed values: an unsafe stored key is refused in print, tracker check prints the safe key, "
          "posted is a date or undated")


def test_deep_event_file():
    """SV-02: a dropped event file nested 20,000 deep (40 KB, under every size
    cap) never stops a reader: the overlay skips it, and apply-events moves it
    to runner/in/bad/ with one ERROR line."""
    root = new_root()
    run(TRACKER, "init", "--root", root, "--transport", "direct")
    deep = b'{"v": 1, "x": ' + b"[" * 20000 + b"]" * 20000 + b"}"
    path = jobfs.Folder(root).events / "20261112T171503Z_0000abcd_started.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(deep)
    ok = put_event(root, {"v": 1, "id": "20261112T171504Z_0000abce", "type": "started",
                          "at": "2026-11-12T09:15:04-08:00", "session": "", "job_key": "greenhouse:7", "n": 7,
                          "date": TODAY, "payload": {}})
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert out[:2] == [f"OVERLAY {TODAY}", "ITEM #7 greenhouse:7 started"], out
    assert [ev["id"] for ev in events.recent(root)] == ["20261112T171504Z_0000abce"]
    assert events.read_file(path)[2] is True, "nested too deep is permanent"
    out = lines(run(TRACKER, "apply-events", "--root", root))
    assert out == [f"ERROR bad event file {path.name}: not JSON: nested deeper than 64; moved to runner/in/bad/",
                   "APPLIED 1 PENDING 0 REJECTED 1"], out
    assert (jobfs.Folder(root).runner_in / "bad" / path.name).exists() and not path.exists() and not ok.exists()
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert "TRACKER REJECTED 1" in out, out
    print("ok a deeply nested event file: the overlay skips it, apply-events sets it aside in runner/in/bad/")


POSTING_MD = """URL: https://job-boards.greenhouse.io/acme/jobs/1234567
Retrieved: {day}
Company: Acme Robotics
Role: Senior Lab Scientist
Location: Springfield, IL
Job key: greenhouse:1234567
Posted: 2026-10-01
Source: greenhouse-api

## Description

Synthetic posting text.
"""


def job_folder(root, name, text=POSTING_MD):
    folder = Path(root) / "applications" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "posting.md").write_text(text.format(day=TODAY), encoding="ascii")
    return folder


def test_add_from_link():
    """events.py add: a job from a link joins today's list at once, numbered
    after the highest; the overlay and tracker.py check see it before any tick."""
    root = new_root()
    f = jobfs.Folder(root)
    f.config.mkdir(parents=True)
    f.settings_toml.write_text('[runner]\nmode = "L"\n', encoding="ascii")
    folder = job_folder(root, f"{TODAY} Acme Robotics - Senior Lab Scientist")
    rel = folder.relative_to(root).as_posix()
    out = run(EVENTS, "add", "--root", root, "--folder", rel, "--via", "pasted").strip()
    assert out == f"NO LIST {TODAY} (no list built yet): the tracker row is the record", out
    items = [{"n": n, "job_key": f"lever:item-{n}", "company": f"Co {n}", "role": "Scientist",
              "folder": f"applications/{TODAY} Co {n} - Scientist"} for n in (1, 2, 3)]
    items.append({"n": 7, "job_key": "lever:item-7", "company": "Co 7", "role": "Scientist", "folder": ""})
    jobfs.atomic_write_json(f.queue_json(TODAY), {"v": 1, "date": TODAY, "items": items[:3]})
    jobfs.atomic_write_json(f.addendum(TODAY, "0930"), {"v": 1, "date": TODAY, "items": items[3:]})
    out = run(EVENTS, "add", "--root", root, "--folder", str(folder), "--via", "pasted", "--session", "s-1").strip()
    assert out == "ADDED #8 greenhouse:1234567 (addendum_user)", out
    ev = json.loads(next(f.events.glob("*_added.json")).read_text(encoding="utf-8"))
    assert ev["n"] == 8 and ev["session"] == "s-1" and ev["payload"]["via"] == "pasted", ev
    it = ev["payload"]["item"]
    assert (it["company"], it["role"], it["ats"], it["account"], it["tier"], it["folder"]) == \
        ("Acme Robotics", "Senior Lab Scientist", "greenhouse", "none", "A", rel), it
    assert run(EVENTS, "add", "--root", root, "--folder", rel, "--via", "add").strip() == f"IN QUEUE {TODAY} #8"
    out = lines(run(EVENTS, "overlay", "--root", root))
    assert "ADDED #8 greenhouse:1234567 pasted" in out, out
    out = lines(run(TRACKER, "check", "--root", root, "--company", "Acme Robotics", "--title", "Senior Lab Scientist",
                    "--url", "https://job-boards.greenhouse.io/acme/jobs/1234567"))
    assert f"IN QUEUE {TODAY} #8" in out, out
    # a second link gets the next number; the runner's apply moves both, the overlay still has them
    other = job_folder(root, f"{TODAY} Globex - Analyst", POSTING_MD.replace("1234567", "7654321"))
    out = run(EVENTS, "add", "--root", root, "--folder", other.relative_to(root).as_posix(), "--via", "add").strip()
    assert out == "ADDED #9 greenhouse:7654321 (addendum_user)", out
    assert run(TRACKER, "apply-events", "--root", root).strip() == "APPLIED 2 PENDING 0 REJECTED 0"
    assert [i["n"] for i in events.user_items(root)] == [8, 9]
    # refusals: mode M has no list; a folder outside applications/; a bad payload by hand
    for args, why in ((["--folder", "config", "--via", "add"], "ERROR --folder config is not a job folder"),
                      (["--folder", rel, "--via", "maybe"], "ERROR argument --via"),
                      (["--folder", "../elsewhere", "--via", "add"], "ERROR --folder ../elsewhere is outside")):
        out = run(EVENTS, "add", "--root", root, *args, code=1)
        assert out.startswith(why), (args, out)
    out = run(EVENTS, "emit", "--root", root, "--type", "added", "--n", "10", "--job-key", "x:1", "--json",
              json.dumps({"via": "pasted", "item": {"applications/x": 1}}), code=1)
    assert out.startswith("ERROR argument --type: invalid choice: 'added'"), out  # SEC-14: only `add` writes it
    ev = events.make(root, "added", {"via": "pasted", "item": {"folder": "state"}}, job_key="x:1", n=10)
    assert events.validate(ev, root).startswith("payload.item.folder must be a job folder"), events.validate(ev, root)
    board = job_folder(root, f"{TODAY} Board Co - Analyst", POSTING_MD.replace(
        "https://job-boards.greenhouse.io/acme/jobs/1234567", "https://www.linkedin.com/jobs/view/1234").replace(
        "Job key: greenhouse:1234567\n", ""))
    out = run(EVENTS, "add", "--root", root, "--folder", board.relative_to(root).as_posix(), "--via", "add", code=1)
    assert out.startswith("ERROR posting.md's URL is a job board"), out
    f.settings_toml.write_text('[runner]\nmode = "M"\n', encoding="ascii")
    third = job_folder(root, f"{TODAY} Initech - Tech", POSTING_MD.replace("1234567", "1111111"))
    out = run(EVENTS, "add", "--root", root, "--folder", third.relative_to(root).as_posix(), "--via", "pasted")
    assert out.strip() == f"NO LIST {TODAY} (mode M): the tracker row is the record", out


def test_added_trust():
    """SEC-14 and shared decision 1: an added event is trusted for nothing it
    could turn against another item. `emit` cannot write one; a hand-written
    one with another number's upload name, a refused link or a host that is
    not its link's fails validation; one that reuses a number a queue file
    gave is never used; and the tick takes the link, its host and the upload
    name from posting.md and the item's own number, never from the event."""
    import jobqueue
    root = new_root()
    f = jobfs.Folder(root)
    f.config.mkdir(parents=True)
    f.settings_toml.write_text('[runner]\nmode = "L"\n', encoding="ascii")
    first = {"n": 1, "job_key": "lever:item-1", "company": "Genentech", "role": "Scientist",
             "folder": f"applications/{TODAY} Genentech - Scientist", "upload": "upload/01_Avery_Resume_Genentech.pdf"}
    jobfs.atomic_write_json(f.queue_json(TODAY), {"v": 1, "date": TODAY, "items": [first]})
    rel = job_folder(root, f"{TODAY} Acme Robotics - Senior Lab Scientist").relative_to(root).as_posix()

    def hand(n, item, key="greenhouse:1234567"):
        ev = events.make(root, "added", {"via": "add", "item": dict({"folder": rel}, **item)}, job_key=key, n=n)
        return ev, events.validate(ev, root, events.file_name(ev))

    _, why = hand(5, {"upload": "upload/01_Avery_Resume_Genentech.pdf"})
    assert why and why.startswith("payload.item.upload must be upload/05_"), why
    for url in ("https://www.link" + chr(0xAD) + "edin.com/jobs/view/1", "https://lnkd.in/abc",
                "https://uk.indeed.com/viewjob?jk=1", "https://ex" + chr(0xE0) + "mple.com/jobs/1",
                "http://192.168.1.10/jobs/1"):
        _, why = hand(5, {"apply_url": url})
        assert why and why.startswith("payload.item.apply_url refused:"), (ascii(url), why)
    _, why = hand(5, {"apply_url": "https://job-boards.greenhouse.io/acme/jobs/1234567",
                      "host": "tracking.example.net"})
    assert why == "payload.item.host must be the apply_url's host", why
    # A hand-written event reusing #1 and its upload name is well formed, but #1 is queue.json's.
    reuse, why = hand(1, {"upload": "upload/01_Avery_Resume_Genentech.pdf"}, key="url:careers.example.com/jobs/9")
    assert why is None, why
    jobfs.create_only(f.event(reuse["id"], "added"), json.dumps(reuse) + "\n")
    # One with its own number but a tracking link, host and upload of its choosing.
    odd, why = hand(5, {"apply_url": "https://tracking.example.net/r/1", "host": "tracking.example.net",
                        "upload": "upload/05_Avery_Resume_Tracking.pdf"})
    assert why is None, why
    jobfs.create_only(f.event(odd["id"], "added"), json.dumps(odd) + "\n")
    items = jobqueue.day_items(root, TODAY)
    assert [(it["n"], it["job_key"]) for it in items] == [(1, "lever:item-1"), (5, "greenhouse:1234567")], items
    # every reader of the added jobs (today_view, tracker.py check, the overlay) sees one #1 only
    assert [it["n"] for it in events.user_items(root, TODAY)] == [5], events.user_items(root, TODAY)
    path = jobqueue.user_addendum(root, jobfs.today(), emit=lambda _l: None)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [it["n"] for it in data["items"]] == [5], data["items"]
    it = data["items"][0]
    assert (it["apply_url"], it["host"], it["upload"]) == (
        "https://job-boards.greenhouse.io/acme/jobs/1234567", "job-boards.greenhouse.io",
        "upload/05_Applicant_Resume_AcmeRobotics.pdf"), it
    # events.py add: a link netfetch refuses never joins the list (shared decision 1)
    for url, why in (("https://www.link" + chr(0xAD) + "edin.com/jobs/view/1", "ERROR posting.md's URL is a job board"),
                     ("https://ex" + chr(0xE0) + "mple.com/jobs/1", "ERROR posting.md's URL is refused (bad-url")):
        folder = Path(root) / "applications" / f"{TODAY} Odd {len(why)} - Analyst"
        folder.mkdir(parents=True)
        (folder / "posting.md").write_text(POSTING_MD.format(day=TODAY).replace(
            "https://job-boards.greenhouse.io/acme/jobs/1234567", url).replace("Job key: greenhouse:1234567\n", ""),
            encoding="utf-8")
        out = run(EVENTS, "add", "--root", root, "--folder", folder.relative_to(root).as_posix(), "--via", "add",
                  code=1)
        assert out.startswith(why), (ascii(url), out)


def test_broken_unicode():
    """V4-02: an event holding half of a UTF-16 pair (a lone surrogate) is
    refused by validate and never written by emit, since no UTF-8 writer (the
    workbook) takes it; the same text arriving as a JSON escape in a dropped
    file reads back as U+FFFD (jobfs.loads_json) and is a valid event."""
    root = new_root()
    lone = chr(0xD800)
    ev = tracker_event(root, ["add", "--company", "Acme" + lone, "--role", "QC"])
    assert events.validate(ev, root) == "holds a broken character (half of an emoji): not valid Unicode text", \
        events.validate(ev, root)
    ev2 = dict(tracker_event(root, ["add", "--company", "Acme", "--role", "QC"]), session="s" + lone)
    assert events.validate(ev2, root).startswith("holds a broken character"), "any field, not only argv"
    try:
        events.emit(root, "tracker", {"argv": ["add", "--company", "Acme" + lone, "--role", "QC"]})
        raise AssertionError("emit wrote an event with a lone surrogate")
    except events.EventError as e:
        assert str(e).startswith("holds a broken character"), e
    assert not jobfs.Folder(root).events.exists() or not list(jobfs.Folder(root).events.glob("*.json"))
    path = put_event(root, json.dumps(ev, ensure_ascii=True), name=events.file_name(ev))  # the escape on disk
    read, err, _ = events.read_file(path)
    assert err is None and events.validate(read, root, path.name) is None, (err, events.validate(read, root))
    assert read["payload"]["argv"][2] == "Acme" + chr(0xFFFD), ascii(read["payload"]["argv"])
    print("ok a lone surrogate: refused in an event made in memory, U+FFFD in one read from a file")


def test_linkedin_settings():
    """[linkedin] in settings.toml (settings.md): off unless the live file
    holds the TOML boolean true; per_day at most 50; never under 4 seconds
    between actions; a file with a typo never turns LinkedIn on, even when
    the runner's last good copy had it on."""
    root = new_root()
    f = jobfs.Folder(root)
    off = {"browser": False, "per_day": 25, "min_seconds_between_actions": 4, "stop_on_warning": True}
    assert events.linkedin_settings(root) == off, "no settings file: the defaults, off"
    f.config.mkdir(parents=True)
    template = (SCRIPTS.parent / "templates" / "settings.toml").read_text(encoding="utf-8")
    f.settings_toml.write_text(template, encoding="utf-8")
    assert events.linkedin_settings(root) == off, "the shipped template: off, 25 a day, 4 seconds, stop on a warning"

    def with_table(body):
        f.settings_toml.write_text('[runner]\nmode = "M"\n[linkedin]\n' + body, encoding="ascii")
        return events.linkedin_settings(root)
    assert with_table("browser = true\n") == dict(off, browser=True)
    assert with_table("browser = true\nper_day = 10\nmin_seconds_between_actions = 9\nstop_on_warning = false\n") == {
        "browser": True, "per_day": 10, "min_seconds_between_actions": 9, "stop_on_warning": False}
    for body in ('browser = "true"\n', "browser = 1\n", 'browser = "yes"\n', "per_day = 25\n"):
        assert with_table(body)["browser"] is False, body  # only the boolean true is a yes
    for body, want in (("per_day = 250\n", 50), ("per_day = 0\n", 0), ("per_day = -3\n", 25),
                       ('per_day = "40"\n', 25), ("per_day = 2.5\n", 25), ("per_day = true\n", 25)):
        assert with_table("browser = true\n" + body)["per_day"] == want, body
    for body, want in (("min_seconds_between_actions = 1\n", 4), ("min_seconds_between_actions = 0\n", 4),
                       ("min_seconds_between_actions = 4.2\n", 5), ("min_seconds_between_actions = 600\n", 120),
                       ('min_seconds_between_actions = "fast"\n', 4), ("min_seconds_between_actions = inf\n", 4)):
        assert with_table("browser = true\n" + body)["min_seconds_between_actions"] == want, body
    assert with_table('stop_on_warning = "no"\n')["stop_on_warning"] is True
    # a typo while the last good copy says on: off (the live file must parse and say true)
    good = f.last_good("settings.toml")
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text("[linkedin]\nbrowser = true\n", encoding="ascii")
    f.settings_toml.write_text("[linkedin]\nbrowser = true\nper_day = \n", encoding="ascii")
    assert jobfs.settings(root).source == "last_good" and events.linkedin_settings(root)["browser"] is False


def test_linkedin_stop():
    """events.py linkedin-stop: the stop-for-the-day record. Written once
    (the first reason stands), never cleared by any command, read back by
    linkedin_stop; a file that cannot be read still closes the day; another
    day is open again."""
    root = new_root()
    stop_file = jobfs.Folder(root).claude / TODAY / "linkedin_stop.json"
    assert events.linkedin_stop(root) is None and not stop_file.exists()
    out = run(EVENTS, "linkedin-stop", "--root", root, "--reason", "captcha", "--session", "s-1").strip()
    assert out == f"LINKEDIN STOPPED {TODAY} captcha: no LinkedIn page is opened again today", out
    rec = json.loads(stop_file.read_text(encoding="utf-8"))
    assert (rec["v"], rec["date"], rec["reason"], rec["session"]) == (1, TODAY, "captcha", "s-1") and rec["at"], rec
    out = run(EVENTS, "linkedin-stop", "--root", root, "--reason", "limit").strip()
    assert out == f"LINKEDIN STOPPED {TODAY} captcha: no LinkedIn page is opened again today", "the first stop stands"
    assert json.loads(stop_file.read_text(encoding="utf-8")) == rec
    assert events.linkedin_stop(root) == {"at": rec["at"], "reason": "captcha"}
    assert events.linkedin_stop(root, "2026-01-02") is None, "a stop holds for its own day"
    out = run(EVENTS, "linkedin-stop", "--root", root, "--reason", "bored", code=1)
    assert out.startswith("ERROR argument --reason: invalid choice"), out
    for reason in events.LINKEDIN_STOPS:
        r2 = new_root()
        assert run(EVENTS, "linkedin-stop", "--root", r2, "--reason", reason, "--date", "2026-03-04").strip() == \
            f"LINKEDIN STOPPED 2026-03-04 {reason}: no LinkedIn page is opened again today"
        assert events.linkedin_stop(r2, "2026-03-04")["reason"] == reason
    # fail closed: a stop file that is not JSON, or holds something else, is still a stop
    for body in ("{not json", "[]", '{"reason": "made up"}', ""):
        stop_file.write_text(body, encoding="ascii")
        assert events.linkedin_stop(root) == {"at": "", "reason": "other"}, body
    # no event file is written: the record holds at once, whatever the runner's version
    assert not jobfs.Folder(root).events.exists() or not list(jobfs.Folder(root).events.glob("*.json"))


def test_diy_add():
    """events.py diy-add: a job the user applies for by hand (a pasted
    posting, a LinkedIn Easy Apply job) joins the do-yourself list at once,
    in every mode: its resume staged as upload/E<n>_..., an answer sheet with
    no link, numbers from E51 that never meet the runner's E1.. and are
    never reused that day."""
    root = new_root()
    f = jobfs.Folder(root)
    (root / "profile.md").write_text("# Application profile\n\n## Contact\n- Legal first name: Jordan\n"
                                     "- Legal last name: Avery\n- Email: jordan.q.avery@example.com\n",
                                     encoding="ascii")
    first = job_folder(root, f"{TODAY} Acme Bio - QC Scientist", POSTING_MD.replace(
        "https://job-boards.greenhouse.io/acme/jobs/1234567", "https://www.linkedin.com/jobs/view/4012345678").replace(
        "greenhouse:1234567", "linkedin:4012345678").replace("Acme Robotics", "Acme Bio"))
    rel = first.relative_to(root).as_posix()
    out = run(EVENTS, "diy-add", "--root", root, "--folder", rel, code=1)
    assert out.startswith(f"ERROR --folder {rel} has no built resume yet"), out
    assert not f.upload.exists() and not (f.claude / TODAY / "diy.json").exists(), "a refusal writes nothing"
    (first / "resume_B.pdf").write_bytes(b"%PDF-1.4 base\n")
    f.upload.mkdir(parents=True)
    (f.upload / "E1_Avery_Resume_HooliHealth.pdf").write_bytes(b"%PDF E1")  # the runner's own first item
    out = lines(run(EVENTS, "diy-add", "--root", root, "--folder", rel))
    up = "upload/E51_Avery_Resume_AcmeBio.pdf"
    assert out == [f"DIY E51 | Acme Bio | Senior Lab Scientist | Springfield, IL | {up} | {rel}",
                   f"SHEET applications/_diy/{TODAY}/E51_Acme Bio.md"], out
    assert (root / up).read_bytes() == b"%PDF-1.4 base\n"
    sheet = (f.diy_dir(TODAY) / "E51_Acme Bio.md").read_text(encoding="utf-8")
    assert "say `log e51`" in sheet and "upload\\E51_Avery_Resume_AcmeBio.pdf" in sheet, sheet
    assert "jordan.q.avery@example.com" in sheet and "http" not in sheet and "linkedin.com" not in sheet, sheet
    # a tailored build replaces the base under the same number; a second job takes the next one
    (first / "Jordan Avery Resume.pdf").write_bytes(b"%PDF-1.4 tailored\n")
    assert lines(run(EVENTS, "diy-add", "--root", root, "--folder", str(first)))[0].startswith("DIY E51 | Acme Bio")
    assert (root / up).read_bytes() == b"%PDF-1.4 tailored\n"
    second = job_folder(root, f"{TODAY} Globex Labs - Analyst", POSTING_MD.replace("Acme Robotics", "Globex Labs"))
    (second / "resume_B.pdf").write_bytes(b"%PDF-1.4 two\n")
    out = lines(run(EVENTS, "diy-add", "--root", root, "--folder", second.relative_to(root).as_posix()))
    assert out[0].startswith("DIY E52 | Globex Labs | Senior Lab Scientist | Springfield, IL | "
                             "upload/E52_Avery_Resume_GlobexLabs.pdf | "), out
    assert [it["e"] for it in events.paste_items(root)] == [51, 52]
    assert (f.upload / "E1_Avery_Resume_HooliHealth.pdf").read_bytes() == b"%PDF E1", "the runner's file is untouched"
    # mode L and no runner at all: the same, at once (no event file, no request)
    assert not f.events.exists() and not f.requests.exists()
    for args, why in ((["--folder", "config"], "ERROR --folder config is not a job folder"),
                      (["--folder", "../elsewhere"], "ERROR --folder ../elsewhere is outside"),
                      (["--folder", "applications/_diy"], "ERROR --folder applications/_diy is not a job folder")):
        out = run(EVENTS, "diy-add", "--root", root, *args, code=1)
        assert out.startswith(why), (args, out)
    # the record is a file under claude/: only entries of the expected shape are ever listed
    path = f.claude / TODAY / "diy.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"] += [{"e": 3, "folder": rel, "company": "Low Number"},
                      {"e": 60, "folder": "../outside", "company": "Outside"},
                      {"e": 61, "folder": "applications/_diy", "company": "Not A Job"},
                      {"e": 51, "folder": rel, "company": "Twice"}, "text", {"e": True, "folder": rel},
                      {"e": 62, "folder": rel, "company": "Odd Upload", "upload": "upload/01_Avery_Resume_X.pdf"}]
    path.write_text(json.dumps(data), encoding="ascii")
    got = events.paste_items(root)
    assert [(it["e"], it["company"], it["upload"]) for it in got] == [
        (51, "Acme Bio", up), (52, "Globex Labs", "upload/E52_Avery_Resume_GlobexLabs.pdf"),
        (62, "Odd Upload", "")], got
    path.write_text("{not json", encoding="ascii")
    assert events.paste_items(root) == []


def main():
    b = test_parity()
    print("ok parity")
    test_overlay_reads_done(b)
    print("ok overlay_reads_done")
    for fn in (test_rejections, test_emit_and_overlay, test_json_file_confined, test_deep_event_file,
               test_printed_values,
               test_add_from_link, test_added_trust, test_broken_unicode,
               test_linkedin_settings, test_linkedin_stop, test_diy_add):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
