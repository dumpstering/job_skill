"""Check job/scripts/gates.py: G1-G12 on fixture postings, discards.csv, the
seen store, knockouts, freshness and reposts, undated boards, AI-text parking,
profile_hash re-evaluation, `gates.py explain`, and the life-science preset's
title families (the verifier's examples).

Usage: python tests/test_gates.py
Prints ok <name> per test and PASS last.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
FIX = HERE / "fixtures" / "pool"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(FIX))
os.environ["JOB_SKILL_NOW"] = "2026-11-06T16:00:00Z"
import gates  # noqa: E402
import jobfs  # noqa: E402
import make_gates_root  # noqa: E402
import netfetch  # noqa: E402

DAY = date(2026, 11, 6)
PRESET = HERE.parent / "job" / "templates" / "presets" / "lifesci_bayarea" / "families.toml"


def copy_root(tmp, name="root"):
    work = Path(tmp) / name
    shutil.copytree(FIX / "root", work)
    shutil.rmtree(work / "state", ignore_errors=True)
    return work


def run_all(root, today=DAY, postings=None):
    fetcher = netfetch.Fetcher(root=root, offline=FIX / "robots")
    run = gates.GateRun(root, today, fetcher=fetcher)
    for p in postings if postings is not None else [p for p, _ in make_gates_root.postings()]:
        run.detail_gates(p)
    run.save()
    return run


def discards(root, day=DAY):
    path = jobfs.Folder(root).discards_csv(day)
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pool(root):
    return {it["job_key"]: it for it in gates.load_pool(root)["items"]}


def test_every_gate():
    """Each gate has a pass and a fail fixture; every discard is one csv line."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run = run_all(root)
        rows = discards(root)
        by_key = {r["job_key"]: r for r in rows}
        assert len(rows) == len(by_key) == run.counts["discarded"] == 12, (len(rows), run.counts)
        items = pool(root)
        for p, expect in make_gates_root.postings():
            head = expect.split()[0]
            if head.startswith("G"):
                gid, reason = expect.split()
                r = by_key.get(p["job_key"])
                assert r and r["gate"] == gid and r["reason"] == reason, (p["job_key"], expect, r)
                assert p["job_key"] not in items
            else:
                assert p["job_key"] in items, (p["job_key"], expect)
                assert p["job_key"] not in by_key
        # the passes, gate by gate
        assert items["greenhouse:900002"]["flags"] == ["license_preferred:ca_cls"]
        assert items["greenhouse:900015"]["family"] == "qc_micro"  # remote with a US restriction (G3)
        assert items["smartrecruiters:initechco:7441"]["posting_status"] == "browser_read"  # R3 lead-only (G1)
        assert items["greenhouse:900011"]["diy"] == "chat_apply"  # G12 do-yourself
        assert items["greenhouse:900016"]["family"] == "research_assoc"
        # filter discards carry profile_hash; closed, host and route discards do not
        for r in rows:
            assert bool(r["profile_hash"]) == gates.is_filter(r["reason"]), r
        # a second run is silent: everything is already seen (G2), no new lines
        again = run_all(root)
        assert again.counts["skipped"] == len(make_gates_root.postings()) and len(discards(root)) == 12, again.counts


def test_knockouts():
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        md = (root / "profile.md").read_text(encoding="utf-8").replace("- Licenses held:", "- Licenses held: CA CLS")
        (root / "profile.md").write_text(md, encoding="utf-8")
        first = [p for p, _ in make_gates_root.postings()][:2]
        run_all(root, postings=first)
        items = pool(root)
        assert "greenhouse:900001" in items, "a held license is no knockout"
        assert items["greenhouse:900002"]["flags"] == [], "a held license is not flagged"
    # sponsorship (G8) against the profile
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        md = (root / "profile.md").read_text(encoding="utf-8").replace("above): no", "above): yes")
        (root / "profile.md").write_text(md, encoding="utf-8")
        p = make_gates_root.posting("greenhouse:900101", "QC Microbiologist", make_gates_root.gh(900101),
                                    text=make_gates_root.MICRO + " We are unable to sponsor work visas.")
        q = make_gates_root.posting("greenhouse:900102", "Microbiologist II", make_gates_root.gh(900102))
        run_all(root, postings=[p, q])
        rows = {r["job_key"]: r for r in discards(root)}
        assert rows["greenhouse:900101"]["gate"] == "G8" and rows["greenhouse:900101"]["reason"] == "knockout:sponsorship"
        assert "greenhouse:900102" in pool(root)


def test_freshness_and_repost():
    assert gates.band_of(0) == ("0-3", 40) and gates.band_of(3) == ("0-3", 40) and gates.band_of(4) == ("4-7", 25)
    assert gates.band_of(14) == ("8-14", 15) and gates.band_of(30) == ("15-30", 5) and gates.band_of(31) == (None, 0)
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        run_all(root, postings=[allp["greenhouse:900005"]])
        assert discards(root)[0]["reason"] == "stale"
        run_all(root, postings=[allp["greenhouse:900006"]])
        it = pool(root)["greenhouse:900006"]
        assert it["repost_of"] == "greenhouse:900005" and it["band"] == "0-3", it
        # the same job seen again through another lane (no newer date) is a duplicate, not a repost
        dup = dict(allp["greenhouse:900006"], job_key="url:careers.globex.example/jobs/r-5005", lane="adzuna",
                   url="https://careers.globex.example/jobs/r-5005")
        run = run_all(root, postings=[dup])
        assert run.counts["skipped"] == 1 and dup["job_key"] not in pool(root)


def test_undated_board():
    """Rippling: no dates; first_seen counts only after 2 earlier polls (3.10)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)

        def rip(n, title):
            return make_gates_root.posting(f"rippling:{n:08d}-0000-4000-8000-000000000000", title,
                                           f"https://ats.rippling.com/acmerip/jobs/{n:08d}-0000-4000-8000-000000000000",
                                           ats="rippling", board="acmerip", posted=None, posted_field="",
                                           text=make_gates_root.MICRO + f" Opening {n}.")
        run_all(root, today=date(2026, 11, 2), postings=[rip(1, "QC Microbiologist"), rip(2, "Microbiologist II")])
        bands = {it["job_key"]: it["band"] for it in pool(root).values()}
        assert bands and all(b == "undated" for b in bands.values()), bands
        run_all(root, today=date(2026, 11, 3), postings=[rip(3, "Microbiologist III")])
        assert pool(root)["rippling:00000003-0000-4000-8000-000000000000"]["band"] == "undated"  # 1 earlier poll
        run_all(root, today=date(2026, 11, 5), postings=[rip(4, "Senior Microbiologist")])
        assert pool(root)["rippling:00000004-0000-4000-8000-000000000000"]["band"] == "0-3"  # 2 earlier polls


def test_ai_text_parked_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        for day in (date(2026, 11, 5), DAY):
            run_all(root, today=day, postings=[allp["greenhouse:900010"], allp["greenhouse:900002"]])
        needs = gates.needs_you(root)
        assert [n["job_key"] for n in needs] == ["greenhouse:900010"], needs
        assert "banana" in needs[0]["quote"]
        assert pool(root)["greenhouse:900010"]["parked"] == "ai_text"
        r = subprocess.run([sys.executable, str(SCRIPTS / "jobqueue.py"), "build", "--root", str(root), "--date",
                            DAY.isoformat()], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout
        q = json.loads((root / "queue" / DAY.isoformat() / "queue.json").read_text(encoding="utf-8"))
        keys = [it["job_key"] for it in q["items"]]
        assert "greenhouse:900010" not in keys and "greenhouse:900002" in keys, keys


def test_profile_edit_reopens():
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        run_all(root, postings=[allp["greenhouse:900013"]])
        assert discards(root)[-1]["reason"] == "title"
        run = run_all(root, postings=[allp["greenhouse:900013"]])
        assert run.counts["skipped"] == 1, "no change: still seen"
        toml = (root / "config" / "search_profile.toml").read_text(encoding="utf-8")
        toml = toml.replace('title_any = ["research associate"]', 'title_any = ["research associate", "lab coordinator"]')
        (root / "config" / "search_profile.toml").write_text(toml, encoding="utf-8")
        run_all(root, postings=[allp["greenhouse:900013"]])
        assert pool(root)["greenhouse:900013"]["family"] == "research_assoc"


def test_two_stage_keep():
    """keep() (boards.poll's hook) judges list records once; the detail pass
    does not log a second line."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run = gates.GateRun(root, DAY)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        bad = jobfs.Posting.from_dict(allp["greenhouse:900004"])
        good = jobfs.Posting.from_dict(allp["greenhouse:900002"])
        assert run.keep(bad) is False and run.keep(good) is True
        run.process([allp["greenhouse:900004"], allp["greenhouse:900002"]])
        run.save()
        assert [r["job_key"] for r in discards(root)] == ["greenhouse:900004"]
        assert list(pool(root)) == ["greenhouse:900002"]


def test_dormant_board():
    """A board with no family match for 60 days turns dormant in board_health.csv;
    a match wakes it (3.7; boards.update_health keeps dormant on a good read)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        state = root / "state"
        state.mkdir()
        (state / "board_health.csv").write_text(
            "key,status,last_ok,last_count,last_error,consecutive_errors\n"
            "greenhouse:acmebio,active,2026-11-05,3,,0\ngreenhouse:other,active,2026-11-05,3,,0\n", encoding="utf-8")
        (state / "board_polls.json").write_text(json.dumps(
            {"polls": {"greenhouse:acmebio": ["2026-08-20", "2026-09-01"]},
             "matched": {"greenhouse:acmebio": "2026-09-01"}}), encoding="utf-8")
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        run_all(root, postings=[allp["greenhouse:900013"]])  # Lab Coordinator: no family
        rows = {r["key"]: r["status"] for r in csv.DictReader(open(state / "board_health.csv", encoding="utf-8"))}
        assert rows == {"greenhouse:acmebio": "dormant", "greenhouse:other": "active"}, rows
        run_all(root, postings=[allp["greenhouse:900002"]])
        rows = {r["key"]: r["status"] for r in csv.DictReader(open(state / "board_health.csv", encoding="utf-8"))}
        assert rows["greenhouse:acmebio"] == "active", rows


def test_explain():
    r = subprocess.run([sys.executable, str(SCRIPTS / "gates.py"), "explain", "--root", str(FIX / "root"),
                        "--job-key", "greenhouse:900001"], capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    assert r.returncode == 0 and lines[-1] == 'GATE G7 knockout:ca_cls required "CLS license required"', lines
    assert lines[0].startswith("EXPLAIN greenhouse:900001") and [ln.split()[1] for ln in lines[1:]] == \
        ["G1", "G2", "G3", "G4", "G5", "G6", "G7"], lines
    # the committed trail is what the code writes today
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run_all(root)
        fresh = gates.trail(root, "greenhouse:900001")["lines"]
        assert fresh == gates.trail(FIX / "root", "greenhouse:900001")["lines"], fresh
    r = subprocess.run([sys.executable, str(SCRIPTS / "gates.py"), "explain", "--root", str(FIX / "root"),
                        "--job-key", "greenhouse:1"], capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout.startswith("NOT FOUND"), r.stdout


def test_terms():
    tw = gates.words
    assert gates.term_hits("capa", tw("CAPA Specialist")) and not gates.term_hits("capa", tw("Capacity Planner"))
    assert gates.term_hits("microbiolog*", tw("Senior Microbiologist"))
    assert gates.term_hits("qc|quality control+micro*", tw("Quality Control Associate - Microbiology"))
    assert not gates.term_hits("qc|quality control+micro*", tw("QC Chemist"))
    assert gates.term_hits("co-op", tw("Co-op, Microbiology")) and not gates.term_hits("intern", tw("Internal Audit"))
    assert gates.exclude_hit("IT", "Associate Director, IT", tw("Associate Director, IT"))
    assert not gates.exclude_hit("IT", "Make it count", tw("Make it count"))
    assert gates.dice("Scientist II, Microbiology", "Scientist II - Microbiology") == 1.0


def test_preset():
    """The life-science preset loads (regex checks pass) and applies the verifier's fixes."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir()
        shutil.copyfile(PRESET, root / "config" / "search_profile.toml")
        cfg = jobfs.search_profile(root)
        assert cfg.source == "file", cfg.error
        rules = gates.Rules(root, DAY)
        wide = gates.Rules(root, DAY, widen=["adjacent_families"])

        def fam(title, r=rules):
            p = jobfs.Posting(job_key="k", title=title, company="X", company_norm="x")
            v = gates.Verdict()
            return None if gates.g4_title(p, v, r) else v.info["family"]

        def ko(title):
            return [k["id"] for k in rules.knockouts if any(__import__("re").search(t, title)
                                                            for t in k.get("title_patterns") or [])]
        want = {"Capacity Planning Analyst": None, "Escapade Coordinator": None, "CAPA Specialist": "quality_validation",
                "Deviation Investigator": "quality_validation", "Automation Scientist Graduate Intern": None,
                "Co-op, Microbiology": None, "Formulation Scientist": None, "Physical Scientist": None,
                "Technical Support Specialist - Tier 2": None, "Product Specialist, Apparel": None,
                "Applications Specialist, Flow Cytometry": "field_application_tech_support", "Technical Writer": None,
                "Chemistry Lecturer": None, "CPR Instructor": None, "Research Scientist, AI": None,
                "Life Science Research Professional 1": "academic_staff_research",
                "Social Science Research Professional 2": None, "Upstream Oil and Gas Analyst": None,
                "Downstream Purification Associate": "process_dev_fermentation", "QC Chemist": None,
                "Quality Control Associate - Microbiology": "qc_micro_sterility", "Clinical Trial Assistant": "clinical_research",
                "Research Associate, Microbiology (Clinical Laboratory)": None,
                "Clinical Microbiology Scientist": None, "Medical Lab Technician": None,
                "Associate Director, IT Infrastructure": None, "Postdoctoral Affairs Coordinator": None,
                "Biosafety Officer": "biosafety", "QC Microbiologist I": "qc_micro_sterility"}
        for title, family in want.items():
            assert fam(title) == family, (title, fam(title), family)
        assert fam("Lecturer in Microbiology", wide) == "lecturer_teaching"
        assert fam("CLS Trainee") is None and fam("CLS Trainee", wide) == "license_path_trainee"
        assert ko("Clinical Lab Scientist II, Microbiology") == ["ca_cls"] and ko("CLS Trainee") == []
        assert ko("Medical Lab Technician") == ["ca_mlt"] and ko("Public Health Microbiologist II") == ["ca_phm"]
        assert ko("Public Health Microbiologist Trainee") == [] and ko("Clinical Microbiology Scientist") == ["ca_cms"]
        assert "Redwood City" in rules.places and rules.home["label"] == "" and rules.home["zip"] == ""
    # the generic template ships empty values
    tpl = HERE.parent / "job" / "templates" / "search_profile.toml"
    import tomllib
    data = tomllib.loads(tpl.read_text(encoding="utf-8"))
    assert data["home"]["places"] == [] and data["home"]["label"] == "" and "family" not in data
    assert set(data) == set(jobfs.SEARCH_PROFILE_DEFAULTS) - {"knockout", "family"}, set(data)


def main():
    for fn in (test_every_gate, test_knockouts, test_freshness_and_repost, test_undated_board,
               test_ai_text_parked_once, test_profile_edit_reopens, test_two_stage_keep, test_dormant_board, test_explain, test_terms,
               test_preset):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
