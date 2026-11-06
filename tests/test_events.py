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
    rej = jobfs.Folder(root).events_rejected
    assert (rej / bad.name).exists() and "not JSON" in (rej / (bad.name + ".reason.txt")).read_text(encoding="utf-8")
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
    assert len(list(rej.glob("*.reason.txt"))) == 9
    snap = json.loads((root / "runner" / "out" / "tracker.json").read_text(encoding="utf-8"))
    assert len(snap["pending_rejected"]) == 9 and snap["applications"] == []


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
    big = root / "big.json"
    big.write_text(json.dumps({"notes": "x" * 70000}), encoding="utf-8")
    out = run(EVENTS, "emit", "--root", root, "--type", "started", "--json-file", big, code=1)
    assert out.strip() == "ERROR event over 64 KB", out


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


def main():
    b = test_parity()
    print("ok parity")
    test_overlay_reads_done(b)
    print("ok overlay_reads_done")
    for fn in (test_rejections, test_emit_and_overlay, test_add_from_link, test_added_trust):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
