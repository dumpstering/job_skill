"""Check job/scripts/network.py: import (layouts, allowlist, zip safety, formula
guard), matching tiers, and the day's referral asks (spec 6.3).

Usage: python tests/test_network.py
Synthetic fixtures only (tests/fixtures/network). Prints ok <name> per test and PASS last.
"""
import builtins
import contextlib
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
FIX = HERE / "fixtures" / "network"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-12T17:00:00Z"
import network  # noqa: E402

NEVER = ("profile.csv", "phonenumbers.csv", "job application saved answers.csv")
PII = ("1 Example Way", "Jan 2 1990", "62701", "555-555-0142", "555-555-0177", "555-555-0188", "555-555-0199",
       "9 Elm St", "1985-06-15", "1990-02-03", "linkedin.com/in/", "careful and kind")


def run(*args, code=0):
    r = subprocess.run([sys.executable, str(SCRIPTS / "network.py")] + [str(a) for a in args], capture_output=True,
                       text=True, encoding="utf-8", cwd=tempfile.gettempdir())
    assert r.returncode == code, f"{args} exited {r.returncode}:\n{r.stdout}{r.stderr}"
    return r.stdout.splitlines()


def call(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = network.main([str(a) for a in args])
    return code, out.getvalue().splitlines()


def new_root():
    root = Path(tempfile.mkdtemp(prefix="job_network_test_"))
    (root / "network" / "raw").mkdir(parents=True)
    return root


def export_zip(path, extra=()):
    """The fixture export as a LinkedIn zip; Connections.csv gets a UTF-8 BOM here
    (repo files stay ASCII)."""
    exp = FIX / "export"
    with zipfile.ZipFile(path, "w") as z:
        for p in sorted(exp.rglob("*")):
            if p.is_file() and p.name != "contacts.csv":
                data = p.read_bytes()
                if p.name == "Connections.csv":
                    data = b"\xef\xbb\xbf" + data
                z.writestr(p.relative_to(exp).as_posix(), data)
        for name, data in extra:
            z.writestr(name, data)


def read_net(root):
    with open(root / "network" / "network.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


@contextlib.contextmanager
def open_spy():
    """Record every file path and zip member opened."""
    seen = []
    real_open, real_zopen = builtins.open, zipfile.ZipFile.open

    def spy_open(file, *a, **k):
        seen.append(str(file))
        return real_open(file, *a, **k)

    def spy_zopen(self, name, *a, **k):
        seen.append(getattr(name, "filename", name))
        return real_zopen(self, name, *a, **k)

    builtins.open, zipfile.ZipFile.open = spy_open, spy_zopen
    try:
        yield seen
    finally:
        builtins.open, zipfile.ZipFile.open = real_open, real_zopen


def test_import_zip():
    root = new_root()
    export_zip(root / "network" / "raw" / "Basic_LinkedInDataExport_11-05-2026.zip")
    shutil.copy(FIX / "export" / "contacts.csv", root / "network" / "raw" / "contacts.csv")
    first = (FIX / "export" / "Connections.csv").read_text(encoding="ascii").splitlines()
    assert first[0] == "Notes:" and first[2] == "" and first[3].startswith("First Name,Last Name,URL"), first[:4]
    with open_spy() as seen:
        code, out = call("import", "--root", root)
    assert code == 0, out
    opened = [Path(str(s).replace("\\", "/")).name.lower() for s in seen]
    assert "connections.csv" in opened and "contacts.csv" in opened, seen  # the spy sees real opens
    for name in NEVER:
        assert name not in opened, (name, seen)
        assert any(ln.startswith(f"SKIP {name}") or ln.lower().startswith(f"skip {name}") for ln in out), out
    assert "READ Connections.csv (from Basic_LinkedInDataExport_11-05-2026.zip): 5 rows" in out, out
    head, rows = read_net(root)
    assert head == network.NET_COLS and not any("url" in h.lower() for h in head), head
    body = (root / "network" / "network.csv").read_text(encoding="utf-8")
    for value in PII:
        assert value not in body, value
    by = {(r["first"], r["last"]): r for r in rows}
    assert by[("Eve", "Hack")]["company_raw"] == "'=HYPERLINK(\"x\")", by[("Eve", "Hack")]
    assert by[("Eve", "Hack")]["position"] == "'+Analyst"
    assert by[("Jane", "Doe")]["strength"] == "rec" and by[("Jane", "Doe")]["connected_on"] == "2024-03-27"
    assert by[("Kim", "Ray")]["strength"] == "emailed" and by[("Kim", "Ray")]["email"] == "kim.ray@example.com"
    assert by[("Ana", "Ruiz")]["source"] == "google"
    assert by[("Bob", "Stone")]["company_norm"] == "acme bio"
    assert all(r["id"].startswith("N-") for r in rows)
    past = (root / "network" / "past_applications.csv").read_text(encoding="utf-8")
    assert "2025-10-01,Globex Labs" in past and "555-555-0142" not in past and "jordan.avery" not in past, past
    # the extract folder is gone, and the raw files were not copied into the folder
    assert sorted(p.name for p in (root / "network").iterdir()) == ["network.csv", "past_applications.csv", "raw"]
    # IDs survive a re-import
    ids = {(r["first"], r["last"]): r["id"] for r in rows}
    call("import", "--root", root)
    assert {(r["first"], r["last"]): r["id"] for r in read_net(root)[1]} == ids


def test_import_old_layout():
    root = new_root()
    shutil.copy(FIX / "old" / "Connections.csv", root / "network" / "raw" / "Connections.csv")
    (root / "network" / "raw" / "Profile.csv").write_text("Birth Date\nJan 2 1990\n", encoding="ascii")
    with open_spy() as seen:
        code, out = call("import", "--root", root)
    assert code == 0 and "IMPORTED 2 people: 2 connections, 0 recommendations, 0 contacts" in out, out
    assert not any(Path(s).name.lower() == "profile.csv" for s in seen), seen
    assert "SKIP Profile.csv (never opened)" in out, out
    _, rows = read_net(root)
    by = {r["first"]: r for r in rows}
    assert by["Max"]["email"] == "max.bell@example.com" and by["Lou"]["connected_on"] == "2020-09-09", rows
    assert by["Lou"]["company_norm"] == "hooli health"


def test_zip_traversal():
    root = new_root()
    z = root / "network" / "raw" / "export.zip"
    export_zip(z, extra=[("../evil/Recommendations_Received.csv", b"First Name,Last Name,Company\nA,B,C\n"),
                         ("C:/evil/Recommendations_Received.csv", b"First Name,Last Name,Company\nA,B,C\n")])
    code, out = call("import", "--root", root)
    assert code == 0
    refused = [ln for ln in out if ln.startswith("WARN") and "refused" in ln]
    assert len(refused) == 2, out
    assert not (root / "network" / "evil").exists() and not (root.parent / "evil").exists()


def test_matching():
    def P(first, company, position="", strength="connection"):
        return {"id": f"N-{abs(hash(first)) % 9000 + 1000}", "first": first, "last": "X", "company_raw": company,
                "company_norm": network.norm(company), "position": position, "strength": strength, "email": ""}
    people = [P("Ann", "Genentech, Inc."), P("Ben", "Self-employed"), P("Cat", "Stealth Startup"),
              P("Dan", "Magnit", "Magnit / Scientist @ Acme"), P("Eli", "Roche"), P("Fay", "")]
    hits = network.match_company(people, "Genentech")
    assert [(h["person"]["first"], h["tier"], h["label"]) for h in hits] == [("Ann", 1, "match")], hits
    for emp in ("Self-employed", "Stealth Startup", "Stealth", "Freelance"):
        assert network.match_company(people, emp) == [], emp
    assert all(h["person"]["first"] not in ("Ben", "Cat", "Fay")
               for e in ("Genentech", "Acme", "Roche", "Magnit") for h in network.match_company(people, e))
    hits = network.match_company(people, "Acme")
    assert [(h["person"]["first"], h["tier"], h["via"]) for h in hits] == [("Dan", 1, "position")], hits
    parents = {"genentech": "roche", "roche": "roche"}
    hits = network.match_company(people, "Genentech", parents)
    assert [(h["person"]["first"], h["tier"], h["label"]) for h in hits] == [("Ann", 1, "match"),
                                                                            ("Eli", 3, "maybe")], hits
    hits = network.match_company(people, "Roche", parents)  # a subsidiary is a maybe too
    assert [(h["person"]["first"], h["label"], h["via"]) for h in hits] == [("Eli", "match", "company"),
                                                                           ("Ann", "maybe", "parent")], hits
    alias = network.match_company([P("Gus", "Genentech, a member of the Roche Group")], "Genentech")
    assert [(h["tier"], h["label"]) for h in alias] == [(2, "maybe")], alias
    crowd = [P("c1", "Acme"), P("r1", "Acme Inc", strength="rec"), P("e1", "ACME", strength="emailed"),
             P("c2", "Acme LLC")]
    hits = network.match_company(crowd, "Acme")
    assert [h["person"]["first"] for h in hits] == ["r1", "e1"], hits


def test_match_cli():
    root = Path(tempfile.mkdtemp(prefix="job_network_test_")) / "root"
    shutil.copytree(FIX / "root", root)
    out = run("match", "--root", root, "--date", "2026-11-12")
    assert "MATCHES 2026-11-12 | 6 jobs | 6 with a tier-1 contact | 0 maybe" in out, out
    assert "REFERRAL #5 greenhouse:5000005 candidate N-0005" in out, out
    assert not any("Jane" in ln for ln in out), "names print only for one job"
    rows = list(csv.DictReader(open(root / "network" / "matches_2026-11-12.csv", encoding="utf-8")))
    per_job = {}
    for r in rows:
        per_job.setdefault(r["job_key"], []).append(r)
    assert max(len(v) for v in per_job.values()) == 2, rows
    out = run("match", "--root", root, "--date", "2026-11-12", "--n", 1)
    assert out == ["MATCH N-0001 | Jane Doe | Senior Scientist | tier 1 company | rec",
                   "MATCH N-0002 | Bob Stone | QC Lead | tier 1 company | connection"], out
    out = run("match", "--root", root, "--company", "Northwind Holdings")
    assert out == ["MATCH N-0007 | Max Bell | Director | tier 1 company | connection",
                   "MAYBE N-0001 | Jane Doe | Senior Scientist | tier 3 parent | rec"], out
    found = network.referrals_for_items(root, [{"job_key": "k", "company": "Acme Bio, Inc."}], date(2026, 11, 12))
    assert found["k"]["referral"] == {"state": "candidate", "contact_ref": "N-0001", "hold_until": ""}, found


def ask_lines(out):
    asks = [ln for ln in out if ln.startswith("ASK ")]
    texts = [ln[5:] for ln in out if ln.startswith("TEXT ")]
    return asks, texts


def test_asks():
    root = Path(tempfile.mkdtemp(prefix="job_network_test_")) / "root"
    shutil.copytree(FIX / "root", root)
    out = run("asks", "--root", root, "--date", "2026-11-12")
    asks, texts = ask_lines(out)
    assert 1 <= len(asks) <= 3 and out[-1] == "ASKS 3 of 3", out
    contacts = [a.split(" | ")[1] for a in asks]
    assert len(set(contacts)) == len(contacts), contacts
    assert not any("Sam Lee" in c for c in contacts), "Sam has an open ask for another job"
    assert "SKIP #3 Globex Labs | Sam Lee (N-0003): open ask for another job (Sent 2026-11-09)" in out, out
    companies = [a.split(" | ")[0].split(" ", 3)[3] for a in asks]
    for c in set(companies):
        bodies = [t.split(". ", 1)[1] for t, co in zip(texts, companies) if co == c]
        assert len(set(bodies)) == len(bodies), (c, bodies)
    assert "DROP #4 Initech Bio | Ana Ruiz (N-0004): a number is not in your sources" in out, out
    i = out.index("DROP #4 Initech Bio | Ana Ruiz (N-0004): a number is not in your sources")
    assert out[i + 1].startswith('FACT number "12"'), out
    assert not any("Initech" in a for a in asks)
    for t in texts:
        words = len(t.split())
        assert network.WORDS_MIN <= words <= network.WORDS_MAX, (words, t)
        assert "?" in t and "https://boards.greenhouse.io/example/jobs/" in t and "(req R-50" in t, t
    # asks_per_day lower: at most that many
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n[referrals]\nasks_per_day = 1\n',
                                                    encoding="ascii")
    asks, _ = ask_lines(run("asks", "--root", root, "--date", "2026-11-12"))
    assert len(asks) == 1, asks
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n[referrals]\nenabled = false\n',
                                                    encoding="ascii")
    assert run("asks", "--root", root, "--date", "2026-11-12") == ["ASKS off (referrals.enabled = false)"]


def test_same_text_guard():
    """Two queue items with the same title, link and req ID at one company: the
    second contact would get the same text, so that ask is skipped."""
    import json
    root = Path(tempfile.mkdtemp(prefix="job_network_test_")) / "root"
    shutil.copytree(FIX / "root", root)
    qp = root / "queue" / "2026-11-12" / "queue.json"
    q = json.loads(qp.read_text(encoding="utf-8"))
    twin = dict(q["items"][0], n=7, job_key="greenhouse:5000099", score=95)
    q["items"].append(twin)
    qp.write_text(json.dumps(q), encoding="utf-8")
    store = network.open_store(root)
    lines, asks, _ = network.plan_asks(root, date(2026, 11, 12), network.queue_items(root, date(2026, 11, 12)),
                                       network.load_people(root), store)
    assert [a["n"] for a in asks][:1] == [7] and "SKIP #1 Acme Bio | Bob Stone (N-0002): same text as another " \
           "ask at Acme Bio today" in lines, (lines, asks)
    bodies = [a["text"].split(". ", 1)[1] for a in asks if a["company"] == "Acme Bio"]
    assert len(set(bodies)) == len(bodies)


def main():
    for fn in (test_import_zip, test_import_old_layout, test_zip_traversal, test_matching, test_match_cli,
               test_asks, test_same_text_guard):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
