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
import time
from collections import Counter
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


LIVE_DAY = FIX / "live_day_2026-11-05.json"


def preset_rules(tmp, segments=None):
    """Rules over the life-science preset with a California home ('Springfield,
    CA' is made up; only its state matters)."""
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    text = PRESET.read_text(encoding="utf-8").replace('label = ""                  # setup fills',
                                                      'label = "Springfield, CA"  # setup fills', 1)
    assert 'label = "Springfield, CA"' in text
    (root / "config" / "search_profile.toml").write_text(text, encoding="utf-8")
    rows = ["employer,employer_norm,parent,ats,board,host,site,extra,careers_url,main_website,segment,city,families,"
            "priority,status,added_on,added_by,ref,notes"]
    for name, seg in (segments or {}).items():
        rows.append(f"{name},,,greenhouse,x,,,,,,{seg},,,1,active,2026-11-01,test,,")
    (root / "config" / "watchlist.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return gates.Rules(root, DAY, widen=[])


def list_verdict(rules, company, title, location, n=1):
    """G3 and G4 on a list-level record built the way the readers build one."""
    import posting
    import jobkey
    d = posting.new_posting("greenhouse-api")
    d.update(company=company, role=title, location=location)
    posting.clean_fields(d, posting.Report())
    rec = posting.to_record(d, f"https://job-boards.greenhouse.io/x/jobs/{7000000 + n}", lane="watch",
                            first_seen=DAY.isoformat(), ats="greenhouse")
    rec.company_norm = jobkey.norm_company(company)
    v = gates.Verdict()
    ok = gates.g3_location(rec, v, rules) is None and gates.g4_title(rec, v, rules) is None
    return ok, v


def test_live_day_corpus():
    """Round 1's live day (2026-11-05, real public postings: title, company,
    location): the 29 queued items with the live judge's verdict, 22 relevant
    postings the gates dropped, and 9 pooled postings that were out of area.
    Title and location gates alone must keep every relevant posting (no
    relevant queued item lost, every missed one kept), drop every out-of-area
    one, and leave at least 80 percent of what passes relevant. Five queued
    items the judge turned down cannot be told by title and place (an ML
    'Research Scientist', a physics 'Staff Scientist', a 119-day-old posting);
    G9 and G5 judge those on the detail."""
    data = json.loads(LIVE_DAY.read_text(encoding="ascii"))
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_rules(tmp, data["segments"])
        assert rules.home_states == {"CA"}, rules.home_states
        kept = {}
        for i, it in enumerate(data["items"]):
            ok, v = list_verdict(rules, it["company"], it["title"], it["location"], i)
            kept[i] = (ok, v)
        items = data["items"]
        lost = [it["title"] for i, it in enumerate(items) if it["relevant"] and not kept[i][0]]
        assert not lost, [(it["title"], kept[i][1].lines[-1]) for i, it in enumerate(items)
                          if it["relevant"] and not kept[i][0]]
        wrong = [(it["title"], it["location"]) for i, it in enumerate(items)
                 if it["group"] == "pooled_wrong" and kept[i][0]]
        assert not wrong, wrong
        passed = [it for i, it in enumerate(items) if kept[i][0]]
        share = sum(1 for it in passed if it["relevant"]) / len(passed)
        queued = [it for i, it in enumerate(items) if it["group"] == "queued" and kept[i][0]]
        q_share = sum(1 for it in queued if it["relevant"]) / len(queued)
        assert share >= 0.80, (share, [it["title"] for it in passed if not it["relevant"]])
        # the judged-wrong queued items title and place can tell apart are all out now
        still = sorted(it["n"] for it in queued if not it["relevant"])
        assert still == [3, 5, 14, 15, 23], still
        assert q_share >= 0.68, q_share  # 11 of 16 (was 11 of 29)
    print(f"   corpus: {sum(1 for it in passed if it['relevant'])} of {len(passed)} passing relevant "
          f"({share:.0%}); queued alone {q_share:.0%}")


LABELED = FIX / "labeled_2026-11-06.json"
WATCHLIST = PRESET.parent / "watchlist.csv"


def preset_root(tmp, widen=None):
    """Rules over the life-science preset and its real watch list (segments
    and cities), with a made-up California home."""
    root = Path(tmp)
    preset_rules(tmp)
    shutil.copyfile(WATCHLIST, root / "config" / "watchlist.csv")
    return gates.Rules(root, DAY, widen=widen or [])


def test_labeled_pool_d6():
    """D6 on live day 2 (2026-11-06): the 271 postings the round-2 gates
    pooled, each labeled by the question 'could a microbiology PhD, casting
    a wide net in the Bay Area, plausibly be hired for it?' (borderline is
    yes). Round 2 kept all 271 (150 yes, 121 no; the 30-item queue held 19
    yes and 11 no). Title and place rules alone (G3, G4: what this fixture
    can show without the texts) must keep every yes and now drop 71 of the
    no; G9, judged on the texts, drops 14 more (85 of 121; see
    test_g9_avoid). The queue keeps 19 of 19 yes and drops 6 of its 11 no
    here (10 with G9)."""
    data = json.loads(LABELED.read_text(encoding="ascii"))
    items = data["items"]
    assert len(items) == 271 and sum(1 for it in items if it["label"] == "yes") == 150
    assert sorted(it["queue"] for it in items if "queue" in it) == list(range(1, 31))
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_root(tmp)
        kept = [list_verdict(rules, it["company"], it["title"], it["location"], i)[0] for i, it in enumerate(items)]
    lost = [it["title"] for it, k in zip(items, kept) if it["label"] == "yes" and not k]
    assert not lost, lost
    no_kept = sum(1 for it, k in zip(items, kept) if it["label"] == "no" and k)
    q = [(it, k) for it, k in zip(items, kept) if "queue" in it]
    q_yes = sum(1 for it, k in q if it["label"] == "yes" and k)
    q_no = sum(1 for it, k in q if it["label"] == "no" and k)
    assert (no_kept, q_yes, q_no) == (50, 19, 5), (no_kept, q_yes, q_no)
    # left for G9 on the texts: Chai (machine learning), Prolific (analytical chemistry), Xaira
    # (SPR/BLI biophysics) and Nurix (LC-MS bioanalysis); #5 (Nurix, 5+ years in one niche) stays
    left = sorted(it["queue"] for it, k in q if it["label"] == "no" and k)
    assert left == [2, 5, 10, 20, 27], left
    print(f"   labeled pool: yes kept 150 of 150, no kept {no_kept} of 121 by title and place; "
          f"queue yes {q_yes} of 19, no {q_no} of 11")


LABELED_DAY3 = FIX / "labeled_day3_2026-11-06.json"
PERSON = HERE / "fixtures" / "folder"  # the synthetic person of both live days (14 years, 11 in industry)


def person_root(tmp, widen=None, settings=None):
    """preset_root plus the synthetic person's achievement library and
    techniques (their years and title levels: the seniority rule's input)."""
    rules = preset_root(tmp, widen)
    for name in ("achievement_library.md", "techniques.md"):
        shutil.copyfile(PERSON / name, Path(tmp) / name)
    if settings:
        (Path(tmp) / "config" / "settings.toml").write_text(settings, encoding="utf-8")
    return gates.Rules(Path(tmp), DAY, widen=widen or [])


def labeled_verdict(rules, it, i):
    """G3, G4 and the seniority rule on one labeled posting: the requirement
    lines that ask for years stand in for its text."""
    ok, v = list_verdict(rules, it["company"], it["title"], it["location"], i)
    if not ok:
        return v
    fam = next(f for f in rules.families if f.get("id") == v.info.get("family"))
    p = jobfs.Posting(job_key=f"k{i}", title=it["title"], company=it["company"],
                      text="Requirements\n" + "\n".join(it.get("years") or []) + "\n")
    gates.g9_seniority(p, v, fam, rules)
    return v


def test_labeled_pools_r6():
    """R6 on both live days' labeled pools, for the synthetic person whose
    pools they were (14 years after the PhD, 11 in industry, titles up to
    Scientist II, so judged at senior level). Title, place and the seniority
    rule (the requirement lines that ask for years stand in for the texts)
    keep every yes. Day 2 (title and place only kept 50 no of 121): the
    principal titles beyond the person drop 5 more (Cytokinetics, Denali x2,
    Genentech, Merck). Day 3 (all 177 had passed every round-3 gate, 52 no):
    those 5 and Fortrea's 4 regional field CRA jobs drop. In day 3's queue
    the gates keep all 30 but mark Nurix #10 (5+ years of drug discovery the
    library never names) and Stanford Health Care #13 (a senior staff
    title) borderline, so the queue lists them after the rest."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = person_root(tmp)
        me = rules.experience
        assert (me.level, me.effective, me.doctorate, me.source) == (2, 3, True, "library"), me
        assert 14 <= me.years < 15 and 10 <= me.industry_years < 11, me
        counts = {}
        for name, path in (("day2", LABELED), ("day3", LABELED_DAY3)):
            items = json.loads(path.read_text(encoding="ascii"))["items"]
            verdicts = [labeled_verdict(rules, it, i) for i, it in enumerate(items)]
            lost = [it["title"] for it, v in zip(items, verdicts) if it["label"] == "yes" and not v.ok]
            assert not lost, (name, lost)
            counts[name] = sum(1 for it, v in zip(items, verdicts) if it["label"] == "no" and v.ok)
            if name == "day3":
                q = {it["queue"]: (it, v) for it, v in zip(items, verdicts) if "queue" in it}
                counts["queue_yes"] = sum(1 for it, v in q.values() if it["label"] == "yes" and v.ok)
                counts["queue_no"] = sum(1 for it, v in q.values() if it["label"] == "no" and v.ok)
                counts["borderline"] = sorted(n for n, (it, v) in q.items() if v.info.get("seniority"))
                dropped = sorted(it["company"] for it, v in zip(items, verdicts) if not v.ok)
                assert dropped == ["Cytokinetics", "Denali Therapeutics", "Denali Therapeutics", "Fortrea",
                                   "Fortrea", "Fortrea", "Fortrea", "Genentech", "Merck (MSD)"], dropped
    assert counts == {"day2": 45, "day3": 43, "queue_yes": 22, "queue_no": 8, "borderline": [10, 13]}, counts
    print(f"   R6: day 2 no kept {counts['day2']} of 121 by title, place and seniority (yes 150 of 150); day 3 no "
          f"kept {counts['day3']} of 52 (yes 125 of 125); queue yes 22 of 22, no 8 of 8, borderline "
          f"#{', #'.join(str(n) for n in counts['borderline'])}")


def test_seniority_rules():
    """R6: seniority is judged against the user. Title levels, the years a
    posting asks (a PhD's path in a degree ladder, 'up to', a fixed term),
    the user's years from the library headings or settings, and the verdicts
    for a senior person and a junior one. A cv-mode family is never judged."""
    lv = gates.title_level
    assert [lv(t) for t in ("Scientist II", "Scientist 3 - Translational Medicine", "Senior Scientist",
                            "Scientist / Senior Scientist, Library Prep", "Staff / Senior Scientist, Assay",
                            "Senior Scientist / Principal Scientist, Induced Proximity", "Genomics Sr Staff Scientist",
                            "Associate Principal Scientist", "Senior Principal Scientist", "Senior Research Associate",
                            "Senior Associate Scientist", "Senior/Staff Research Associate",
                            "Senior Scientist I/II, In Vitro Pharmacology")] == \
        [0, 0, 3, 0, 3, 3, 5, 5, 6, 2, 2, 2, 3]

    def bar(*lines, doctorate=True):
        return gates.years_bar("Requirements\n" + "\n".join(lines) + "\n", doctorate)
    assert bar("- PhD in biology with 4+ years of relevant experience") == (4, False)
    assert bar("- PhD and 5+ years of industry experience") == (5, True)
    assert bar("- BS/MS/PhD with 10+/8+/6+ years of experience (for Staff Scientist)") == (6, False)
    assert bar("- Ph.D. or Master's plus 10 years of relevant experience") == (None, False)
    assert bar("- Bachelor's degree with 5+ years of experience") == (None, False)
    assert bar("- Bachelor's degree with 5+ years of experience", doctorate=False) == (5, False)
    assert bar("- PhD with up to 2 years of industry research experience") == (0, True)
    assert bar("- PhD with 0-3 years of relevant industry experience") == (0, True)
    assert bar("This is a two year, fixed term position.", "- 3+ years of experience preferred") == (None, False)
    assert bar("- 4 or more years of post-graduate industry or academic research experience") == (4, False)
    assert bar("- Four (4) or more years of work experience in genetics",
               "- One (1) year or more of work experience in a CLIA laboratory") == (4, False)
    import time
    start = time.perf_counter()  # bounded work (D3): long runs of spaces never make the scan slow
    gates.years_bar("Requirements\n" + ("1" + " " * 590 + "experience\n") * 300)
    assert time.perf_counter() - start < 2, time.perf_counter() - start
    with tempfile.TemporaryDirectory() as tmp:
        senior = person_root(tmp).experience
    with tempfile.TemporaryDirectory() as tmp:
        override = '[experience]\nyears = 2\nindustry_years = 0\n'
        junior = person_root(tmp, settings=override).experience
    assert junior.source == "settings" and (junior.years, junior.industry_years, junior.effective) == (2.0, 0.0, 2)
    fam = {"mode": "industry"}

    def verdict(title, me, *lines):
        return gates.seniority(title, "Requirements\n" + "\n".join(lines) + "\n", fam, me)[0]
    cases = [("Principal Scientist", "- PhD with 10+ years of industry experience", "beyond", "beyond"),
             ("Senior Principal Scientist", "- PhD and 3 years of relevant experience", "borderline", "beyond"),
             ("Senior Scientist", "- PhD and 2+ years of industry experience", "ok", "borderline"),
             ("Staff Scientist", "- PhD with 8 years of industry experience", "borderline", "beyond"),
             ("Scientist II", "- PhD with 5+ years of industry experience", "ok", "beyond"),
             ("Scientist", "- PhD; cell culture and qPCR", "ok", "ok"),
             ("Scientist", "- 5+ years of drug discovery experience", "borderline", "borderline"),
             ("Scientist", "- 5+ years of microbiology experience", "ok", "borderline"),
             ("Scientist", "- 8+ years of microbiology experience", "ok", "beyond")]
    for title, line, want_senior, want_junior in cases:
        assert verdict(title, senior, line) == want_senior, (title, line, verdict(title, senior, line))
        assert verdict(title, junior, line) == want_junior, (title, line, verdict(title, junior, line))
    assert gates.seniority("Principal Scientist", "", {"mode": "cv"}, junior) == ("ok", "")
    assert gates.seniority("Principal Scientist", "", fam, None) == ("ok", "")  # no library, no years: not judged
    # the gate: beyond is a filter discard (judged again when the person's years change); borderline is marked
    with tempfile.TemporaryDirectory() as tmp:
        rules = person_root(tmp)
        p = jobfs.Posting(job_key="k", title="Principal Scientist", text="Requirements\n- PhD with 10+ years\n")
        v = gates.Verdict()
        assert gates.g9_seniority(p, v, fam, rules) is not None and v.reason == "seniority" and gates.is_filter(v.reason)
        v = gates.Verdict()
        p.title = "Staff Scientist"
        assert gates.g9_seniority(p, v, fam, rules) is None and v.info["seniority"] == "borderline", v.lines
        v = gates.Verdict()
        p.title = "Principal Scientist"
        assert gates.g9_seniority(p, v, fam, rules, unread=True) is None and v.info["seniority"] == "borderline"
        plain = preset_root(Path(tmp) / "plain").hash
        assert rules.hash != plain, "the person's years and levels are part of the profile hash"


def test_shift_lengths_not_hr():
    """L3-03: an all-caps exclude word right after a number is a shift
    length ('08-HR', '8 HR'), not human resources; 'HR Coordinator' is still out."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_root(tmp)

        def fam(title):
            p = jobfs.Posting(job_key="k", title=title, company="Acme Bio", company_norm="acme bio")
            v = gates.Verdict()
            return None if gates.g4_title(p, v, rules) else v.info["family"]
        assert fam("Research Associate - 8 HR Days") == "research_associate_lab_tech"
        assert fam("Clinical Research Coordinator 2 (08-HR)") == "clinical_research"
        assert fam("Microbiology Technologist - Full Time, Days (08HR)") == "microbiologist"
        assert fam("HR Coordinator") is None and fam("Senior HR Business Partner") is None
        # Stanford Health Care's phlebotomy lab assistant stays out (no longer by the HR word)
        assert fam("Lab Assistant III-P - Full Time - 1.0 FTE, 08-HR, Day Shift") is None
    assert not gates.exclude_hit("HR", "Lab Tech - 12-HR Nights", gates.words("Lab Tech - 12-HR Nights"))
    assert gates.exclude_hit("IT", "Associate Director, IT", gates.words("Associate Director, IT"))


def test_exact_core_title_first():
    """L3-04: of the families a title matches, the closest core title wins
    before file order: a bare 'Research Associate' is the research associate
    family's, not clinical research's ('Clinical Research Associate')."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_root(tmp)

        def fam(title, company="BillionToOne"):
            p = jobfs.Posting(job_key="k", title=title, company=company, company_norm=jobkey_norm(company))
            v = gates.Verdict()
            return None if gates.g4_title(p, v, rules) else v.info["family"]
        assert fam("Research Associate") == "research_associate_lab_tech"
        assert fam("Clinical Research Associate") == "clinical_research"
        assert fam("QC Microbiologist") == "qc_micro_sterility"
        assert fam("Senior Research Associate, Assay Development") == "assay_development"
        assert fam("Research Scientist", "Stanford University") == "academic_staff_research"


def jobkey_norm(name):
    import jobkey
    return jobkey.norm_company(name)


def test_regional_remote():
    """L3-06: a remote location list that names states, none of them home,
    is a regional field job even when it also says 'Remote United States'.
    An office city beside a nationwide remote, or a home state among the
    remote ones, still passes."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_rules(tmp)
    for loc, ok in (("Durham | Remote Michigan | Remote Ohio | Remote United States | Remote Texas", False),
                    ("Durham | Remote United States | Remote Nevada", False),
                    ("Remote, Oregon, USA | Remote, Idaho, USA", False),
                    ("Durham | Remote Oregon | Remote United States | Remote California", True),
                    ("Durham | Remote United States", True), ("Remote United States | Boston, MA", True),
                    ("United States - Remote", True), ("South San Francisco, CA | Remote Texas", True)):
        got, v = list_verdict(rules, "Fortrea", "Clinical Research Associate II", loc)
        assert got == ok, (loc, v.lines)
        if not ok:
            assert v.reason == "location" and "remote, other states" in v.lines[-1], v.lines


def test_robots_refused_by_policy():
    """SV-07: a robots.txt read refused by policy is never 'allowed'. A
    redirect to a job board or a bad-url host discards the posting at G1
    (host:<domain> or host:bad-url); a non-public target or any other fetch
    failure is the robots-unreachable skip, as an unanswered robots.txt is."""
    url = "https://careers.example-bio.com/jobs/12"
    for exc, action, reason in ((netfetch.Denied("job-board linkedin.com"), "discard", "host:linkedin.com"),
                                (netfetch.Denied("bad-url host x"), "discard", "host:bad-url"),
                                (netfetch.Denied("non-public 10.0.0.1"), "skip", "robots-unreachable"),
                                (netfetch.FetchError("timed out"), "skip", "robots-unreachable")):
        f = netfetch.Fetcher(unattended=False, throttle=netfetch.Throttle(enabled=False))

        def boom(u, exc=exc):
            raise exc
        f.robots.fetch_text = boom
        p = jobfs.Posting(job_key="url:careers.example-bio.com/jobs/12", title="Microbiologist", company="Example Bio",
                          url=url, apply_url=url, lane="watch", ats="other")
        v = gates.Verdict()
        assert gates.g1_host(p, v, f) is not None and (v.action, v.reason) == (action, reason), (exc, v.lines)


def test_link_refusal_r1():
    """R1 and SV-08 (gates side): G1 and G12 refuse what netfetch.web_refusal
    refuses, so a link the queue would never show is discarded with a row in
    discards.csv, never left in the pool: userinfo, a backslash, whitespace,
    another port, an IP literal, a single-label host (host:bad-url) and the
    special-use names (host:non-public)."""
    bs = chr(92)
    gh = "https://boards.greenhouse.io/acmebio/jobs/1"
    for link, why in (("https://user@careers.example-bio.com/jobs/1", "bad-url"),
                      ("https://careers.example-bio.com" + bs + "jobs/1", "bad-url"),
                      ("https://careers.example-bio.com/jo bs/1", "bad-url"),
                      ("https://careers.example-bio.com:8443/jobs/1", "bad-url"),
                      ("https://13.107.42.14/jobs/1", "bad-url"), ("https://intranet/jobs/1", "bad-url"),
                      ("http://localhost/jobs/1", "non-public"), ("https://printer.local/jobs/1", "non-public")):
        p = jobfs.Posting(job_key="greenhouse:1", title="Microbiologist", company="Acme Bio", url=gh,
                          apply_url=link, lane="watch", ats="greenhouse")
        v = gates.Verdict()
        assert gates.g1_host(p, v, None) is not None and v.reason == f"host:{why}", (link, v.lines)
        v = gates.Verdict()
        assert gates.g12_route(p, v) is not None and v.reason == "apply_route", (link, v.lines)
    for link in ("https://careers.example-bio.com:443/jobs/1?q=a%20b", "https://careers.example-bio.com/jobs/1"):
        p = jobfs.Posting(job_key="greenhouse:1", title="Microbiologist", url=gh, apply_url=link, lane="watch",
                          ats="greenhouse")
        assert gates.g1_host(p, gates.Verdict(), None) is None and gates.g12_route(p, gates.Verdict()) is None, link


def test_fit_general_form():
    """L3-05: fit credits a technique's general form at half weight: a
    posting's 'PCR' for the user's 'real-time PCR', 'sequencing' for '16S
    rRNA sequencing'. 'culture' is no method on its own (a company culture),
    and a form the posting names in full is never counted twice."""
    import library
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "techniques.md"
        path.write_text("# Techniques\n\n"
                        "- real-time PCR | aliases: qPCR | level: hands-on | years_ok: yes | evidence: L-0001\n"
                        "- 16S rRNA sequencing | aliases: 16S sequencing | level: hands-on | years_ok: yes | "
                        "evidence: L-0002\n"
                        "- anaerobic culture | level: hands-on | years_ok: yes | evidence: L-0003\n"
                        "- confocal microscopy | level: familiar | years_ok: no | evidence: L-0004\n",
                        encoding="ascii")
        techniques = library.load_techniques(path)
    fam = {"terms": ["assay development", "NGS"]}
    text = "Requirements: PCR, cell culture and sequencing workflows; microscopy a plus. A great company culture."
    assert gates.overlap(text, fam, techniques) == 0.5 + 0.5 + 0.25, gates.overlap(text, fam, techniques)
    full = "Requirements: real-time PCR and qPCR; PCR primer design."
    assert gates.overlap(full, fam, techniques) == 1.0, gates.overlap(full, fam, techniques)
    assert gates.overlap("Requirements: a PhD and good communication.", fam, techniques) == 0


def test_posted_values():
    """R2: a pooled or queued posting's posted value is a YYYY-MM-DD date,
    'undated' or blank, never other text from a board."""
    assert gates.posted_value("2026-11-05") == "2026-11-05"
    assert gates.posted_value("2026-11-05T08:00:00Z") == "2026-11-05"
    assert gates.posted_value("Posted 3 days ago; rm") == "undated" and gates.posted_value("") is None
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        p = make_gates_root.posting("greenhouse:900401", "QC Microbiologist", make_gates_root.gh(900401))
        p["posted"] = "yesterday `x`"
        run_all(root, postings=[p])
        assert pool(root)["greenhouse:900401"]["posted"] == "undated", pool(root)


def test_d6_title_rules():
    """L2-02, L2-03 and the seniority words: a business-development title is
    out (not assay development), 'formulation*' also drops 'Formulations',
    'Reference Materials' no longer reads as materials science, and the
    other-field scientist titles are out; round 2's false negatives pass."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_root(tmp)

        def fam(title):
            p = jobfs.Posting(job_key="k", title=title, company="Acme Bio", company_norm="acme bio")
            v = gates.Verdict()
            return None if gates.g4_title(p, v, rules) else v.info["family"]
        want = {
            "CSD Diagnostic Partner - Business Development": None,
            "Senior Manager, Business Development, Diagnostics": None,
            "Scientist - Formulations, Drug Delivery": None, "Scientist, Formulation": None,
            "Senior Scientist, Immunoassay Development, Antibody Critical Reagents & Reference Materials":
                "assay_development",
            "Scientist, Materials Science": None, "Materials Scientist II": None,
            "Scientist II, Developability": None, "Scientist- Delivery Sciences-targeted LNPs (tLNPs)": None,
            "Scientist, FPD": None, "Packaging Scientist II": None, "Senior Scientist- Translational PKPD": None,
            "Associate Scientist - SSRL HAXPES Beamline": None, "SVP, Regulatory Affairs": None,
            "Senior Regulatory Affairs Specialist": None, "Regulatory Affairs Associate": "regulatory_affairs",
            "Senior Clinical Trial Manager": None, "Clinical Research Coordinator 2": "clinical_research",
            "Senior Medical Science Liaison - Rare Disease": None,
            "Sr. Staff Medical Science Liaison": "medical_science_liaison",
            "Research Associate I, Analytical Chemistry": None, "Social Science Research Assistant": None,
            "Scientist 1, Vector Engineering": "industry_rd_scientist",
            "Scientist, Process Engineering": None, "Scientist, Biomolecular Characterization": None,
            "Sr QA Reviewer, Biologics Approval": "quality_validation",
            "QC Supervisor, Sample Management and Stability Administration": "quality_validation",
            "Scientist II, Analytical Development": "industry_rd_scientist",
            "Scientist, Microglia Therapeutics Team": "industry_rd_scientist",
        }
        for title, family in want.items():
            assert fam(title) == family, (title, fam(title), family)


def test_g9_avoid():
    """D6 on the texts: when the requirements name at least two other-field
    words (avoid_any) and more of them than wet-lab words (require_any), the
    job's core training is another field's. A wet-lab job that also asks for
    HPLC or names a company culture keeps its place."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_root(tmp)
    fam = next(f for f in rules.families if f["id"] == "industry_rd_scientist")
    blurb = "About us: a biology company with a great culture, curing disease with cell therapies. " * 3

    def req(body):
        return blurb + "\n\nRequirements\n" + body + "\n\nBenefits\nHealth plan.\n"
    cases = {
        "bioanalytical LC-MS": (req("Develop quantitative LC-MS/MS assays for small molecules in plasma; extensive "
                                    "UPLC and mass spectrometry experience; support PK studies. " * 3), False),
        "machine learning": (req("Train and evaluate foundation models with PyTorch; deep learning research and "
                                 "machine learning engineering; work with the wet lab team on assay data. " * 3),
                             False),
        "biophysics": (req("Characterize antibody binding by SPR and BLI (Biacore, Carterra); DLS and DSF "
                           "stability; developability ranking of leads. " * 3), False),
        "formulation": (req("Formulation development of drug product in our lab; lyophilization cycles and "
                            "excipient screening; lipid nanoparticle work a plus. " * 3), False),
        "wet lab with HPLC": (req("Mammalian cell culture, qPCR and ELISA assay development; flow cytometry; "
                                  "HPLC and LC-MS experience is a plus. " * 3), True),
        "analytical biochemistry": (req("Protein biochemistry and enzymology; ELISA and CE-SDS in a GMP lab; "
                                        "good documentation practices. " * 3), True),
    }
    for name, (text, ok) in cases.items():
        p = jobfs.Posting(job_key="k", title="Scientist", text=text)
        v = gates.Verdict()
        assert (gates.g9_words(p, v, fam) is None) == ok, (name, v.lines)
        if not ok:
            assert v.reason == "title" and "another field" in v.lines[-1], v.lines


def test_other_field_postdocs():
    """R6/D6 for the round 4 readers (LBNL's job site lists every division):
    another field's postdocs and research roles (accelerator physics, quantum
    devices, electrochemistry, energy and earth systems) leave the families by
    title, and the postdoc family's G9 words need a biology word in the
    posting and drop one whose text names more of those fields. A biology,
    soil-microbe or metabolic-engineering postdoc keeps its place."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_rules(tmp)
        out = ["Earth System Sciences Postdoctoral Fellow", "Electrochemical Engineering Postdoctoral Scholar",
               "Experimental Postdoc - Quantum Computing and Networking", "Physicist Research Scientist",
               "Postdoctoral Scholar - Membrane Chemist", "Postdoctoral Scholar, Energy Systems",
               "Research Associate - Quantum Device Fabrication", "Accelerator Physics Postdoctoral Fellow"]
        keep = ["Biologist Postdoctoral Fellow", "Postdoctoral Researcher", "Postdoctoral Fellow, Microbiology",
                "Postdoctoral Scholar, Metabolic Engineering", "Senior Research Associate (Fermentation / Recovery)",
                "Postdoctoral Fellow - Biochemistry"]
        for i, title in enumerate(out + keep):
            ok, v = list_verdict(rules, "Lawrence Berkeley National Laboratory", title, "Berkeley, CA", i)
            assert ok == (title in keep), (title, v.lines)
    fam = next(f for f in rules.families if f["id"] == "postdoc")
    cases = {
        "accelerator RF": ("The Accelerator Technology and Applied Physics Division seeks a postdoc for RF design "
                           "of accelerator structures with beam physicists and mechanical engineers. "
                           "PhD in physics or electrical engineering; electromagnetic simulation. " * 2, False),
        "AI for accelerators": ("Develop machine learning and artificial intelligence methods for accelerator "
                                "physics: digital twins, surrogate models, beam diagnostics. PhD in physics. " * 2,
                                False),
        "plant microbes": ("Engineer key microbes in plant root communities; genetic, proteomic and metabolomic "
                           "approaches to microbial stress tolerance and biofilm formation. PhD in microbiology. " * 2,
                           True),
        "soil carbon": ("Investigate soil carbon and nutrient cycling in a grassland warming experiment; soil "
                        "biogeochemistry and plant-soil feedbacks; isotopic analysis and machine learning to scale "
                        "the results. PhD in ecology or soil science. " * 2, True),
        "structural biology": ("Determine structures of bacterial membrane proteins by x ray crystallography and "
                               "cryo-EM; protein expression and purification. PhD in biochemistry. " * 2, True),
    }
    for name, (text, ok) in cases.items():
        p = jobfs.Posting(job_key="k", title="Postdoctoral Researcher", text=text)
        v = gates.Verdict()
        assert (gates.g9_words(p, v, fam) is None) == ok, (name, v.lines)


def test_fit_overlap():
    """D6 order: fit counts the user's terms a posting names (hands-on
    techniques 1, familiar 0.5, family terms 1 when the library names them
    and 0.5 when not), so a posting naming the user's methods ranks above one
    naming none, whatever the share of hands-on terms."""
    import library
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "techniques.md"
        path.write_text("# Techniques\n\n"
                        "- real-time PCR | aliases: qPCR | level: hands-on | years_ok: yes | evidence: L-0001\n"
                        "- ELISA | level: hands-on | years_ok: no | evidence: L-0002\n"
                        "- confocal microscopy | level: familiar | years_ok: no | evidence: L-0003\n",
                        encoding="ascii")
        techniques = library.load_techniques(path)
    assert len(techniques) == 3, techniques
    fam = {"terms": ["aseptic technique", "GMP", "qPCR"]}
    rich = "Requirements: qPCR and ELISA; confocal microscopy; aseptic technique; GMP."
    bare = "Requirements: a PhD and good communication."
    lib = "- [L-0001] Daily aseptic technique in a BSL-2 lab."
    n_rich = gates.overlap(rich, fam, techniques, lib)
    assert n_rich == 1 + 1 + 0.5 + 1 + 0.5, n_rich  # qPCR counted once, as the technique
    assert gates.overlap(bare, fam, techniques, lib) == 0
    f_rich = gates.fit_of(rich, "Scientist", fam, 0.6, techniques, lib)[0]
    f_bare = gates.fit_of(bare, "Scientist", fam, 0.6, techniques, lib)[0]
    assert f_rich > f_bare, (f_rich, f_bare)


def test_place_rules():
    """G3 (LIVE-02, LIVE-04): any listed place counts, a place name needs no
    other state or country beside it, foreign remote is out, and 'N Locations'
    or a bare country is judged on the detail."""
    with tempfile.TemporaryDirectory() as tmp:
        rules = preset_rules(tmp)
        cases = {
            "Jersey City, NJ; Millbrae, CA": True, "Millbrae, CA": True, "2 Locations": "unknown",
            "US": "unknown", "United States": "unknown", "CA, United States": "unknown",
            "Dublin, Ireland": False, "Dublin, CA": True, "Richmond, VA": False, "Newark, NJ": False,
            "US - New Jersey - Newark": False, "Richmond - VA - US": False, "Brisbane, Australia": False,
            "Brisbane - CA - US": True, "Remote - Canada": False, "Remote (Vienna, Austria)": False,
            "TWN - Remote": False, "Remote (US)": True, "USA - Remote": True, "Remote, California, USA": True,
            "Remote, Florida, USA": False, "San Rafael, California, United States": False,
            "US - California - South San Francisco": True, "Boston, MA | South San Francisco, CA": True,
            "London, United Kingdom | South San Francisco, CA": True, "Vizag": False, "": "unknown",
            # L2-01: 'City ST' with no comma names its state (posting.region_of)
            "Dublin OH": False, "Newark DE": False, "Albany NY": False, "Richmond VA": False,
            "San Carlos CA": True, "Dublin CA": True}
        for loc, want in cases.items():
            ok, v = list_verdict(rules, "Acme Bio", "QC Microbiologist", loc)
            unknown = "location_unknown" in (v.info.get("flags") or [])
            got = "unknown" if ok and unknown else ok
            assert got == want, (loc, got, want, v.lines)
        # L2-01: a home place named Dublin, Newark or Albany does not pull in
        # the same name in another state; the verdict names that state
        for loc, st in (("Dublin OH", "OH"), ("Newark DE", "DE"), ("Albany NY", "NY")):
            assert gates.place_verdict(loc, rules) == ("out", f"state {st}"), (loc, gates.place_verdict(loc, rules))
        assert gates.place_verdict("Dublin CA", rules)[0] == "in"
    import posting
    assert posting.split_place("Jersey City, NJ; Millbrae, CA") == ("Jersey City", "NJ", "US")
    assert posting.split_place("Novato, California") == ("Novato", "CA", "US")
    assert posting.us_eligible("TWN - Remote", "", "remote") is False
    assert posting.us_eligible("Remote (Vienna, Austria)", "", "remote") is False
    assert posting.us_eligible("Remote", "", "remote") is None


def test_office_labels():
    """L2-08: a location that names an office or lab instead of a place ('MBC
    Lab', a street address, the employer's own name) is judged by the watch
    list's city for that employer: in when it is a home place, out when it
    is another, unknown when the row has none. Without a watch-list row it
    stays 'not a home place', and a city name alone is never an office label."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        preset_rules(tmp)
        rows = ["employer,employer_norm,parent,ats,board,host,site,extra,careers_url,main_website,segment,city,"
                "families,priority,status,added_on,added_by,ref,notes",
                "Acme Bio,,,greenhouse,acme,,,,,,biotech,San Francisco,,1,active,2026-11-01,test,,",
                "Bayside Bio,,,greenhouse,bayside,,,,,,biotech,Boston,,1,active,2026-11-01,test,,",
                "Museum of Natural Science,,,greenhouse,msm,,,,,,institute,,,1,active,2026-11-01,test,,"]
        (root / "config" / "watchlist.csv").write_text("\n".join(rows) + "\n", encoding="ascii")
        rules = gates.Rules(root, DAY, widen=[])
        cases = [("Acme Bio", "MBC Lab", True), ("Acme Bio", "300 Pasteur Dr - 12K Yard - Floor 01", True),
                 ("Bayside Bio", "MBC Lab", False), ("Museum of Natural Science", "MBC Lab", "unknown"),
                 ("Museum of Natural Science", "Museum of Natural Science", "unknown"),
                 ("Other Co", "MBC Lab", False), ("Acme Bio", "Salt Lake City", False),
                 ("Acme Bio", "Zurich", False), ("Acme Bio", "Boston Lab, MA", False)]
        for company, loc, want in cases:
            ok, v = list_verdict(rules, company, "QC Microbiologist", loc)
            got = "unknown" if ok and "location_unknown" in (v.info.get("flags") or []) else ok
            assert got == want, (company, loc, got, want, v.lines)


def test_second_ring_set_aside():
    """D8 and L2-05: a watch-list employer in a second_ring place (Novato) is
    still polled; its posting is set aside with reason location:second_ring
    when its title and age would pass (location when they would not), and
    pooled once the widen step is on. The weekly summary counts those and
    says how to include them."""
    import report
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        toml = (root / "config" / "search_profile.toml").read_text(encoding="utf-8")
        assert 'second_ring = ["Ogdenville"]' in toml
        toml = toml.replace('second_ring = ["Ogdenville"]', 'second_ring = ["Ogdenville", "North Haverbrook"]')
        (root / "config" / "search_profile.toml").write_text(toml, encoding="utf-8")
        mk = make_gates_root.posting
        a = mk("greenhouse:900401", "QC Microbiologist", make_gates_root.gh(900401), location="Ogdenville, IL",
               city="Ogdenville")
        b = mk("greenhouse:900402", "Sales Manager", make_gates_root.gh(900402), location="Ogdenville, IL",
               city="Ogdenville")
        c = mk("greenhouse:900403", "Microbiologist II", make_gates_root.gh(900403),
               location="North Haverbrook, Illinois, United States", city="North Haverbrook")
        run_all(root, postings=[a, b, c])
        rows = {r["job_key"]: r for r in discards(root)}
        assert rows["greenhouse:900401"]["reason"] == "location:second_ring", rows["greenhouse:900401"]
        assert rows["greenhouse:900402"]["reason"] == "location", rows["greenhouse:900402"]
        assert rows["greenhouse:900403"]["reason"] == "location:second_ring"
        assert gates.is_filter("location:second_ring")
        line = report.second_ring_line(root, DAY - __import__("datetime").timedelta(days=6), DAY, [])
        assert line == ("2 jobs were set aside for the place alone: they are in your wider ring "
                        "(Ogdenville 1, North Haverbrook 1). Say widen to include them."), line
        assert not report.check_wording(line)
        assert report.second_ring_line(root, DAY, DAY, ["second_ring"]) is None
        # widened: the same postings are judged again and pooled
        run = gates.GateRun(root, DAY, widen=["second_ring"])
        for p in (a, c):
            run.detail_gates(p)
        run.save()
        assert {"greenhouse:900401", "greenhouse:900403"} <= set(pool(root)), sorted(pool(root))


def detail_reading(base, **kw):
    import posting
    p = jobfs.Posting.from_dict(dict(base, **kw))
    return posting.Reading(p, "", "jobvite", [], [], 0)


def test_detail_rejudged():
    """LIVE-03: a list record with no location, no date or 'N Locations' is
    judged again by G3 and G5 on its detail: an Irish or 4-month-old detail is
    discarded, a dated one gets its real band, and a Workday 'N Locations'
    record whose detail names a home place is pooled."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run = gates.GateRun(root, DAY)
        mk = make_gates_root.posting

        def jv(n, **kw):
            return mk(f"jobvite:acmebio:o{n}", "QC Microbiologist", f"https://jobs.jobvite.com/acmebio/job/o{n}",
                      ats="jobvite", location="", city="", state="", country="", posted=None, posted_field="",
                      text="", **kw)
        cases = [(jv(1), {"location": "Shanbally, Cork, Ireland", "posted": "2026-11-01"}, "G3"),
                 (jv(2), {"location": "Springfield, IL", "city": "Springfield", "posted": "2026-07-08"}, "G5"),
                 (jv(3), {"location": "Springfield, IL", "city": "Springfield", "posted": "2026-11-04"}, None),
                 (jv(4), {"location": "Ogdenville, IL", "city": "Ogdenville", "posted": "2026-11-04"}, "G3")]
        for lst, det, _ in cases:
            assert run.keep(jobfs.Posting.from_dict(dict(lst))), lst["job_key"]
        wd = mk("workday:acmebio:R-1", "QC Microbiologist", "https://acmebio.wd1.myworkdayjobs.com/en-US/x/job/R-1",
                ats="workday", location="2 Locations", city="", state="", country="", posted=None, posted_field="",
                text="")
        assert run.keep(jobfs.Posting.from_dict(dict(wd)))
        run.process([detail_reading(lst, text=make_gates_root.MICRO, posted_field="datePosted",
                                    **{k: v for k, v in det.items()}) for lst, det, _ in cases] +
                    [detail_reading(wd, text=make_gates_root.MICRO, location="Boston, MA | Springfield, IL",
                                    city="Boston", state="MA", posted="2026-11-03", posted_field="startDate")])
        run.save()
        rows = {r["job_key"]: r for r in discards(root)}
        for lst, _, gate in cases:
            if gate:
                assert rows[lst["job_key"]]["gate"] == gate, (lst["job_key"], rows.get(lst["job_key"]))
        items = pool(root)
        assert set(items) == {"jobvite:acmebio:o3", "workday:acmebio:R-1"}, set(items)
        it = items["jobvite:acmebio:o3"]
        assert (it["band"], it["date_basis"], it["age_days"]) == ("0-3", "posted", 2), it
        assert items["workday:acmebio:R-1"]["band"] == "0-3" and "location_unknown" not in items[
            "workday:acmebio:R-1"]["flags"], items["workday:acmebio:R-1"]


def test_pool_age_basis():
    """LIVE-03, queue side: a pool item stored as 'undated' before its detail
    date was judged ages by that posted date, and an undated item first seen
    over 30 days ago is stale, so neither keeps 5 fresh points forever."""
    import jobqueue
    old = {"date_basis": "undated", "posted": "2026-07-09", "first_seen": "2026-11-05"}
    assert jobqueue.age_now(old, DAY)[1] is None, jobqueue.age_now(old, DAY)
    fresh = {"date_basis": "undated", "posted": "2026-11-05", "first_seen": "2026-11-05"}
    assert jobqueue.age_now(fresh, DAY)[1:] == ("0-3", 40), jobqueue.age_now(fresh, DAY)
    long_seen = {"date_basis": "undated", "posted": None, "first_seen": "2026-09-20"}
    assert jobqueue.age_now(long_seen, DAY)[1] is None, jobqueue.age_now(long_seen, DAY)
    new_seen = {"date_basis": "undated", "posted": None, "first_seen": "2026-11-05"}
    assert jobqueue.age_now(new_seen, DAY)[1:] == ("undated", 5)


def test_untitled_list_records():
    """Phenom and Workday sitemap lists carry no title or place: each record is
    judged on its detail, not skipped as a duplicate of the first one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run = gates.GateRun(root, DAY)
        mk = make_gates_root.posting
        recs = [mk(f"phenom:careers.acmebio.example:J{n}", "", f"https://careers.acmebio.example/job/J{n}",
                   ats="phenom", location="", city="", state="", country="", text="") for n in range(4)]
        assert all(run.keep(jobfs.Posting.from_dict(dict(r))) for r in recs)
        titles = ["QC Microbiologist", "Sales Manager", "Microbiologist II", "QC Microbiologist III"]
        run.process([detail_reading(r, title=t, title_norm=t.lower(), location="Springfield, IL",
                                    city="Springfield", text=make_gates_root.MICRO + f" Job {i}.")
                     for i, (r, t) in enumerate(zip(recs, titles))])
        run.save()
        assert sorted(pool(root)) == sorted(r["job_key"] for i, r in enumerate(recs) if i != 1), sorted(pool(root))
        assert [r["gate"] for r in discards(root)] == ["G4"], discards(root)


def test_untitled_detail_never_pooled():
    """L4-06: a Phenom list record with no title whose detail read failed (a
    closed job's page answers 404, and the list-level record is kept) used to
    pass G3 and G4 as unknown twice and be pooled with a blank title, place
    and family, so a thin day queued it as 'AbbVie - Role'. It is never
    pooled now: one SKIP line, no discard row, no seen record, so the next
    run reads its detail again; the titled sibling is pooled as before."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        lines = []
        run = gates.GateRun(root, DAY, emit=lines.append)
        mk = make_gates_root.posting
        recs = [mk(f"phenom:careers.abbvie.example:{j}", "", f"https://careers.abbvie.example/en/job/x-jid-{j}",
                   ats="phenom", company="AbbVie", location="", city="", state="", country="", text="")
                for j in ("29335", "34467", "40001")]
        assert all(run.keep(jobfs.Posting.from_dict(dict(r))) for r in recs)
        run.process([detail_reading(recs[0]), detail_reading(recs[1], title="  "),
                     detail_reading(recs[2], title="QC Microbiologist", title_norm="qc microbiologist",
                                    location="Springfield, IL", city="Springfield", text=make_gates_root.MICRO)])
        run.save()
        assert sorted(pool(root)) == [recs[2]["job_key"]], sorted(pool(root))
        assert not any(r["job_key"] in (recs[0]["job_key"], recs[1]["job_key"]) for r in discards(root)), \
            discards(root)
        assert lines == [f"SKIP {r['job_key']} no title: the detail read gave none; judged again next run"
                         for r in recs[:2]], lines
        run2 = gates.GateRun(root, DAY)
        assert run2.keep(jobfs.Posting.from_dict(dict(recs[0]))), "not recorded: judged again next run"
        assert not run2.keep(jobfs.Posting.from_dict(dict(recs[2]))), "the pooled one is seen"
        # a blank item pooled before this rule (the live day 4 pool held two) leaves the pool on the next save
        data = gates.load_pool(root)
        data["items"].append(dict(data["items"][0], job_key="phenom:careers.abbvie.example:1", title="",
                                  family="", location=""))
        jobfs.atomic_write_json(jobfs.Folder(root).pool_json, data)
        lines.clear()
        run3 = gates.GateRun(root, DAY, emit=lines.append)
        run3.save()
        assert sorted(pool(root)) == [recs[2]["job_key"]], sorted(pool(root))
        assert lines == ["SKIP 1 pool item with no title dropped from the pool"], lines


def test_pass_stops_halfway():
    """PIPE-05: a gate pass that stops partway (an exception, a kill) leaves
    no 'pooled' seen record for postings pool.json never got, so the next
    run pools them; discards.csv held open the way Excel holds it cannot stop
    the pass, and its rows are written once the file is free."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        batch = [allp["greenhouse:900002"], allp["greenhouse:900016"], allp["greenhouse:900004"]]
        real = gates.g12_route
        calls = []

        def boom(p, v):
            calls.append(p.job_key)
            if len(calls) == 2:
                raise KeyboardInterrupt("stopped halfway")  # a kill: not caught per posting
            return real(p, v)
        gates.g12_route = boom
        try:
            run = gates.GateRun(root, DAY)
            try:
                run.process(batch)
            except KeyboardInterrupt:
                pass
        finally:
            gates.g12_route = real
        seen = [r for p in (root / "state" / "seen").glob("*.jsonl") for r in jobfs.read_jsonl(p)] \
            if (root / "state" / "seen").exists() else []
        assert seen == [] and not (root / "state" / "pool.json").exists(), seen
        run2 = gates.GateRun(root, DAY)
        run2.process(batch)
        run2.save()
        assert sorted(pool(root)) == ["greenhouse:900002", "greenhouse:900016"], sorted(pool(root))
        # Excel holds today's discards.csv: the pass finishes, the row waits, then lands
        if os.name == "nt":
            disc = jobfs.Folder(root).discards_csv(DAY)
            before = len(discards(root))
            holder = hold_open(disc)
            try:
                run3 = gates.GateRun(root, DAY)
                run3.process([allp["greenhouse:900014"]])
                assert run3.save()["discarded"] == 1
                pending = root / "state" / "discards_pending.jsonl"
                assert pending.exists() and len(jobfs.read_jsonl(pending)) == 1
                assert "greenhouse:900014" in {r["job_key"] for r in jobfs.read_jsonl(next((root / "state" /
                                                                                          "seen").glob("*.jsonl")))}
            finally:
                close_handle(holder)
            assert gates.write_discards(root, DAY, []) == 0 and not pending.exists()
            assert len(discards(root)) == before + 1 and discards(root)[-1]["job_key"] == "greenhouse:900014"


def test_one_bad_posting():
    """D2: a posting whose gating raises (odd data, a refused host) is skipped
    with one ERROR line naming it and the error type; the other postings are
    pooled, keep() answers False, and nothing is recorded for the bad one, so
    the next run judges it again."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        allp = {p["job_key"]: p for p, _ in make_gates_root.postings()}
        real = gates.g9_words

        def odd(p, v, fam):
            if p.job_key == "greenhouse:900016":
                raise ValueError("odd record")
            return real(p, v, fam)
        lines = []
        gates.g9_words = odd
        try:
            run = gates.GateRun(root, DAY, emit=lines.append)
            run.process([allp["greenhouse:900002"], allp["greenhouse:900016"], object()])
            assert run.keep(object()) is False  # boards.poll's hook: a broken record gets no detail call
            run.save()
        finally:
            gates.g9_words = real
        assert "ERROR gate greenhouse:900016: ValueError" in lines, lines
        assert lines.count("ERROR gate (no job key): AttributeError") == 2, lines
        assert list(pool(root)) == ["greenhouse:900002"], list(pool(root))
        seen = [r["job_key"] for p in (root / "state" / "seen").glob("*.jsonl") for r in jobfs.read_jsonl(p)]
        assert "greenhouse:900016" not in seen, seen
        run2 = gates.GateRun(root, DAY, emit=lines.append)
        run2.process([allp["greenhouse:900016"]])
        run2.save()
        assert "greenhouse:900016" in pool(root)


def test_requirement_scan_bounded():
    """D3: the requirements scan reads a bounded slice and never copies the
    rest of the text per heading, so a posting made of thousands of headings
    is judged in well under a second."""
    import time
    text = "Requirements 1\n" * 60000 + "x" * 400000
    t0 = time.perf_counter()
    gates.requirement_text(text)
    gates.requirement_section(text)
    p = jobfs.Posting(job_key="k", title="Scientist", text=text)
    gates.g9_words(p, gates.Verdict(), {"require_any": ["assay*"], "avoid_any": ["lc ms"]})
    took = time.perf_counter() - t0
    assert took < 1.5, took
    # the sections it finds are what the old scan found
    doc = ("About us\nWe make tests.\n\nRequirements\n" + "PhD in microbiology and cell culture work. " * 8 +
           "\n\nBenefits\nHealth plan.\n")
    assert gates.requirement_text(doc).strip().startswith("PhD in microbiology") and "Health" not in \
        gates.requirement_text(doc)


def hold_open(path):
    """Open a file the way Excel holds a CSV: others may read, not write."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)  # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
    assert h != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return h


def close_handle(h):
    import ctypes
    ctypes.WinDLL("kernel32").CloseHandle(h)


def test_g1_lookalike_host():
    """A posting or apply URL on a lookalike host (a soft hyphen in
    'linkedin', or any host netfetch's canonical form refuses) is discarded at
    G1, never pooled as a browser read for the user to open."""
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        mk = make_gates_root.posting
        shy = chr(0xAD)
        a = mk("url:link" + shy + "edin.com/jobs/view/1", "QC Microbiologist",
               "https://www.link" + shy + "edin.com/jobs/view/1", ats="other", board="")
        b = mk("greenhouse:900301", "Microbiologist II", make_gates_root.gh(900301),
               apply_url="https://careers.exam" + shy + "ple.com/apply/1")
        run_all(root, postings=[a, b])
        rows = {r["job_key"]: r["reason"] for r in discards(root)}
        assert rows == {a["job_key"]: "host:linkedin.com", "greenhouse:900301": "host:bad-url"}, rows
        assert not pool(root)


def test_link_schemes_and_canonical_hosts():
    """SEC-11 and D1 (gates side): a posting or apply link that is not http(s)
    (file://, smb://, ftp://), names a host netfetch refuses (a soft hyphen,
    a fullwidth letter, a sharp s), or is a job board by any spelling (lnkd.in,
    linkedin.de, a trailing dot) is discarded at G1 and at G12, never raising.
    G12 judges the detail record, so an apply link that only the detail
    carries is caught there too. An http apply link from a board is opened as
    https; a pasted link keeps its own form."""
    shy, fw, sharp = chr(0xAD), chr(0xFF49), chr(0xDF)
    gh = "https://boards.greenhouse.io/acmebio/jobs/1"
    refused = {
        "https://www.link" + shy + "edin.com/jobs/view/1": "linkedin.com",
        "https://www.l" + fw + "nkedin.com/jobs/view/2": "linkedin.com",
        "https://stra" + sharp + "e.example.com/apply": "bad-url",
        "file://files.example.com/share/apply.html": "not-web",
        "smb://files.example.com/share": "not-web",
        "ftp://files.example.com/job.html": "not-web",
        "https://lnkd.in/abc": "lnkd.in",
        "https://de.linkedin.de/jobs/view/9": "linkedin.de",
        "https://www.linkedin.com./jobs/view/10": "linkedin.com",
        "https://[::1/x": "bad-url",
    }
    for link, why in refused.items():
        assert gates.url_refusal(link) == why, (link, gates.url_refusal(link), why)
        for url, apply in ((gh, link), (link, "")):
            p = jobfs.Posting(job_key="greenhouse:1", title="Microbiologist", company="Acme Bio", url=url,
                              apply_url=apply, lane="watch", ats="greenhouse")
            v = gates.Verdict()
            assert gates.g1_host(p, v, None) is not None and v.reason == f"host:{why}", (link, v.lines)
            v = gates.Verdict()
            assert gates.g12_route(p, v) is not None and v.reason == "apply_route", (link, v.lines)
    assert gates.url_refusal("https://careers.example.com/apply/1") is None
    p = jobfs.Posting(job_key="greenhouse:2", title="Microbiologist", url=gh, lane="watch", ats="greenhouse",
                      apply_url="http://www.example.com/careers/?gh_jid=2")
    v = gates.Verdict()
    assert gates.g1_host(p, v, None) is None and gates.g12_route(p, v) is None, v.lines
    assert p.apply_url == "https://www.example.com/careers/?gh_jid=2", p.apply_url
    pasted = jobfs.Posting(job_key="url:example.com/jobs/3", title="Microbiologist", lane="pasted",
                           url="http://example.com/jobs/3", apply_url="http://example.com/jobs/3")
    assert gates.g12_route(pasted, gates.Verdict()) is None and pasted.apply_url == "http://example.com/jobs/3"
    # discards.csv names no host for a refused one, and the pass never stops
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        mk = make_gates_root.posting
        bad = mk("greenhouse:900311", "Microbiologist II", make_gates_root.gh(900311),
                 apply_url="file://files.example.com/share/apply.html")
        good = mk("greenhouse:900312", "QC Microbiologist", make_gates_root.gh(900312))
        run_all(root, postings=[bad, good])
        rows = {r["job_key"]: r for r in discards(root)}
        assert rows["greenhouse:900311"]["reason"] == "host:not-web", rows
        assert "greenhouse:900312" in pool(root)


def test_g9_requirements():
    """LIVE-05: require_any is read in the requirements section, so a company
    blurb naming biology no longer lets a chemistry or statistics job through."""
    fam = {"id": "rd", "require_any": ["biolog*", "microb*", "cell*"]}
    blurb = "About us: we are a biology company curing disease with cell therapies. " * 4
    chem = blurb + ("\n\nBasic Qualifications\nPhD in organic chemistry with 5 years of process chemistry and "
                    "scale-up of small molecules; HPLC and NMR. Strong written and spoken communication.\n"
                    "\nPreferred Qualifications\nRoute scouting, crystallization and impurity control for "
                    "late-stage small-molecule programs; experience filing CMC sections.\n")
    bio = blurb + ("\n\nBasic Qualifications\nPhD and 2 years of industry experience.\n"
                   "\nPreferred Qualifications\nHands-on mammalian cell culture, cell biology or microbiology "
                   "methods, qPCR and ELISA; industry assay development; clear written communication.\n")
    short = blurb + "\n\nQualifications\nPhD and 2 years of industry experience.\n"
    for text, ok in ((chem, False), (bio, True), (blurb, True), (short, True)):
        p = jobfs.Posting(job_key="k", title="Scientist", text=text)
        v = gates.Verdict()
        assert (gates.g9_words(p, v, fam) is None) == ok, (ok, v.lines)


def test_two_openings_one_title():
    """One title open in two labs (a university posts each opening with its own
    requisition ID, same title and city): the second, seen a day later, is a
    new job, not 'same job as' the first. Without requisition IDs on both, the
    company+title+city rule still joins them; a shared ID always does."""
    from datetime import timedelta
    texts = ["The Ruiz lab studies gut microbes and needs help with anaerobic culture and 16S sequencing.",
             "The Okafor lab maps immune signaling in mouse models with flow cytometry and ELISA work.",
             "The Lindqvist lab builds CRISPR screens in human cell lines and runs the plate reader core."]

    def opening(n, req, text):
        return make_gates_root.posting(f"workday:stanford:R{n}", "Research Associate",
                                       f"https://stanford.wd1.myworkdayjobs.com/en-US/careers/job/R{n}",
                                       company="Stanford University", board="stanford", req_ids=req,
                                       text=text + " " + make_gates_root.MICRO)
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        run_all(root, postings=[opening(1, ["R1"], texts[0])])
        nxt = DAY + timedelta(days=1)
        run_all(root, today=nxt, postings=[opening(2, ["R2"], texts[1]), opening(3, [], texts[2]),
                                           opening(4, ["R1"], texts[2])])
        got = pool(root)
        assert "workday:stanford:R1" in got and "workday:stanford:R2" in got, sorted(got)
        assert "workday:stanford:R3" not in got and "workday:stanford:R4" not in got, sorted(got)


# ---------- round 6: a second kind of user (bioprocess), pay, work type, a second region ----------

BIO = FIX / "labeled_bioprocess_2026-11-10.json"
# What setup would set for a bioprocess user: their field's families active, the neighbours proposed (they
# join with the widen step), the rest paused. The preset itself ships the five new families paused.
BIO_STATUS = {
    "process_dev_fermentation": "active", "bioprocess_engineering_msat": "active", "gmp_manufacturing": "active",
    "cmc_writing_project": "active", "bioprocess_field_support": "active", "lab_operations": "active",
    "quality_validation": "active", "qc_micro_sterility": "active", "microbiologist": "active",
    "industry_rd_scientist": "active", "assay_development": "active", "research_associate_lab_tech": "active",
    "government_microbiologist": "active",
    "academic_staff_research": "proposed", "biosafety": "proposed", "regulatory_affairs": "proposed",
    "field_application_tech_support": "proposed",
    "study_director_assoc_director": "paused", "postdoc": "paused", "clinical_research": "paused",
    "medical_science_liaison": "paused", "medical_scientific_writing": "paused", "license_path_trainee": "paused",
    "lecturer_teaching": "paused", "bioinformatics_comp_bio": "paused"}
BIO_EXCLUDE = ["part-time", "part time", "per diem", "lab assistant", "laboratory assistant"]
# A made-up bioprocess person: about 3 industry years after 9 years running a university lab.
BIO_LIBRARY = ("# Achievement library\n\nSynthetic test data: every name and number is made up.\n\n"
               "### Scientist II, Process Development | Northwind Biologics | Feb 2020 - Dec 2022\n\n"
               "- [L-0001] Upstream process development and tech transfer of mammalian cell culture processes in "
               "single-use bioreactors; master batch records for GMP manufacturing.\n\n"
               "### Lab Manager | Springfield State University | Jan 2011 - Dec 2019\n\n"
               "- [L-0002] Ran a microbiology research lab: ordering, inventory, safety, HPLC upkeep.\n"
               "- [L-0003] Doctoral thesis on antibiotic natural products (PhD awarded 2019).\n"
               "<!-- last-id: L-0003 -->\n")
BIO_TECHNIQUES = ("# Techniques\n\n"
                  "- mammalian cell culture | aliases: cell culture | level: hands-on | years_ok: yes | evidence: L-0001\n"
                  "- single-use bioreactors | aliases: bioreactor | level: hands-on | years_ok: yes | evidence: L-0001\n"
                  "- tech transfer | aliases: technology transfer | level: hands-on | years_ok: yes | evidence: L-0001\n"
                  "- HPLC | level: hands-on | years_ok: no | evidence: L-0002\n")


def user_profile(status=None, exclude=(), min_terms=None, home_extra="", **gate_values):
    """The preset as one user's search_profile.toml: family statuses (and min_terms), [gates] values (pay_floor,
    shift_work, employment_types), more exclude words, and text added to [home] (a [[home.region]] table)."""
    out, fid, seen = [], None, set()
    for line in PRESET.read_text(encoding="utf-8").splitlines():
        if line.startswith('id = "'):
            fid = line.split('"')[1]
        if line.startswith('status = "') and fid in (status or {}):
            line = f'status = "{status[fid]}"'
            seen.add(fid)
            if fid in (min_terms or {}):
                line += f"\nmin_terms = {min_terms[fid]}"
        if line.startswith('label = ""                  # setup fills'):
            line = 'label = "Springfield, CA"'
        if line == "[gates]" and home_extra:
            line = home_extra.strip() + "\n\n[gates]"
        for key, value in gate_values.items():
            if line.startswith(f"{key} = "):
                line = f"{key} = {json.dumps(value)}"
        if line.endswith('"medical coding", "call center", "claims", "IT", "HR"]') and exclude:
            line = line[:-1] + ", " + ", ".join(json.dumps(w) for w in exclude) + "]"
        out.append(line)
    assert seen == set(status or {}), set(status or {}) - seen
    return "\n".join(out) + "\n"


def user_root(tmp, text, widen=None, library=None, techniques=None):
    """Rules for one user's profile text over the preset's watch list (and their library, when given)."""
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "search_profile.toml").write_text(text, encoding="utf-8")
    shutil.copyfile(WATCHLIST, root / "config" / "watchlist.csv")
    if library:
        (root / "achievement_library.md").write_text(library, encoding="ascii")
    if techniques:
        (root / "techniques.md").write_text(techniques, encoding="ascii")
    cfg = jobfs.search_profile(root)
    assert cfg.source == "file", cfg.error
    return gates.Rules(root, DAY, widen=widen or [])


def bioprocess_text(pay_floor=0, min_terms=None):
    return user_profile(BIO_STATUS, exclude=BIO_EXCLUDE, min_terms=min_terms, pay_floor=pay_floor,
                        employment_types=["full-time", "contract"], shift_work="last")


def test_bioprocess_labels_r6():
    """Round 6 on live day 4 (2026-11-10), labeled for a bioprocess user: the 215 pooled postings (83 yes: 48
    in the field, 35 in the wide net; 132 no) and the 492 distinct bioprocess-looking titles the v1.2 gates
    dropped by title or requirements (21 yes, 471 no). That day every pooled posting was kept and every one of
    those discards dropped. By title, place and work type (what this fixture can show without the texts):
    the preset as shipped (the new families paused) still keeps the pool and picks up one discarded yes (a
    CQV engineer); a bioprocess user's profile keeps 73 of the pool's 83 yes (the 10 it leaves are in the
    academic research and biosafety families, proposed for this user: all 83 with the widen step on) and
    drops 70 of its 132 no, and keeps all 21 discarded yes with 45 of the 471 no (4 of those yes and 25 of
    those no had been dropped by G9: they pass the title as they did that day and the texts decide them
    again). With the texts (after_text, judged against a person of this kind, no pay floor): 71 of the
    pool's yes and 39 of its no are kept, and of the 55 discards read again 17 of 17 yes and 3 of 38 no
    (4 more yes could not be read again). A pay floor of $125,000 then drops 27 of the pool's 71 kept yes
    and 7 of the discards' 17 (entry manufacturing, research associate, QC and lab operations roles state
    ranges that top out below it), and 5 of the 42 kept no; 19 of the 88 kept yes state no pay and stay."""
    data = json.loads(BIO.read_text(encoding="ascii"))
    items = data["items"]
    pool_items = [it for it in items if it["group"] == "pool"]
    disc_items = [it for it in items if it["group"] == "discard"]
    assert (len(pool_items), len(disc_items)) == (215, 492)
    assert Counter((it["group"], it["label"], it.get("tier", "")) for it in items) == {
        ("pool", "yes", "core"): 48, ("pool", "yes", "wide"): 35, ("pool", "no", ""): 132,
        ("discard", "yes", "core"): 21, ("discard", "no", ""): 471}

    def run(rules):
        return [list_verdict(rules, it["company"], it["title"], it["location"], i) for i, it in enumerate(items)]

    def tally(verdicts, group, label, origin=None):
        return sum(1 for it, (ok, _) in zip(items, verdicts)
                   if ok and it["group"] == group and it["label"] == label
                   and (origin is None or (it.get("was") == "G9") == (origin == "G9")))
    with tempfile.TemporaryDirectory() as tmp:
        shipped = run(preset_root(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        user = run(user_root(tmp, bioprocess_text()))
    with tempfile.TemporaryDirectory() as tmp:
        wide = run(user_root(tmp, bioprocess_text(), widen=["adjacent_families"]))
    got = {"shipped": [tally(shipped, "pool", "yes"), tally(shipped, "pool", "no"),
                       tally(shipped, "discard", "yes", "G4"), tally(shipped, "discard", "no", "G4")],
           "user": [tally(user, "pool", "yes"), tally(user, "pool", "no"),
                    tally(user, "discard", "yes"), tally(user, "discard", "no")],
           "user_g9_origin": [tally(user, "discard", "yes", "G9"), tally(user, "discard", "no", "G9")],
           "widened": [tally(wide, "pool", "yes"), tally(wide, "pool", "no")]}
    assert got == BIO_PINS, got
    # no yes in the field is lost by title or place, and the temporary and part-time ones are out
    lost = [it["title"] for it, (ok, _) in zip(items, user) if it["label"] == "yes" and not ok
            and it["family"] not in ("academic_staff_research", "biosafety")]
    assert not lost, lost
    for it, (ok, v) in zip(items, user):
        if "(Temporary)" in it["title"] or "Part-Time" in it["title"] or "Part-time" in it["title"]:
            assert not ok, (it["title"], v.lines)
    # the texts, as recorded when they were read (after_text)
    texts = Counter((it["group"], it["label"], it["after_text"] == "kept") for it in items if "after_text" in it)
    assert texts == {("pool", "yes", True): 71, ("pool", "yes", False): 12, ("pool", "no", True): 39,
                     ("pool", "no", False): 93, ("discard", "yes", True): 17, ("discard", "no", True): 3,
                     ("discard", "no", False): 35}, texts
    # the pay floor on the stated ranges of what the texts kept
    with tempfile.TemporaryDirectory() as tmp:
        rules = user_root(tmp, bioprocess_text(pay_floor=125000))
    assert rules.pay_floor == 125000 and not rules.temp_ok and rules.shift_last
    paid = Counter()
    for it in items:
        if it.get("after_text") != "kept":
            continue
        p = jobfs.Posting(job_key="k", title=it["title"],
                          text=f"Salary range: ${it['pay'][0]:,} to ${it['pay'][1]:,} a year." if "pay" in it else "")
        v = gates.Verdict()
        dropped = gates.g9_pay(p, v, rules) is not None
        assert dropped == ("pay" in it and it["pay"][1] < 125000), (it["title"], it.get("pay"), v.lines)
        paid[(it["group"], it["label"], "below" if dropped else "stated" if "pay" in it else "none")] += 1
    assert paid == BIO_PAY, paid
    print(f"   bioprocess labels: pool yes kept {got['user'][0]} of 83 by title (83 widened; 71 on the texts), no "
          f"kept {got['user'][1]} of 132 (39 on the texts); discards yes kept {got['user'][2]} of 21, no "
          f"{got['user'][3]} of 471; a $125,000 floor drops {paid[('pool', 'yes', 'below')]} of the pool's 71 kept "
          f"yes and {paid[('discard', 'yes', 'below')]} of the discards' 17")


# [pool yes, pool no, discard yes, discard no] kept by title, place and work type
BIO_PINS = {"shipped": [83, 132, 1, 0], "user": [73, 62, 21, 45], "user_g9_origin": [4, 25], "widened": [83, 66]}
# of the postings the texts kept: stated pay that tops out below $125,000, stated pay that reaches it, none stated
BIO_PAY = {("pool", "yes", "below"): 27, ("pool", "yes", "stated"): 29, ("pool", "yes", "none"): 15,
           ("pool", "no", "below"): 4, ("pool", "no", "stated"): 24, ("pool", "no", "none"): 11,
           ("discard", "yes", "below"): 7, ("discard", "yes", "stated"): 6, ("discard", "yes", "none"): 4,
           ("discard", "no", "below"): 1, ("discard", "no", "stated"): 2}


def test_bioprocess_families():
    """Round 6 title rules for a bioprocess user's profile, and the same titles under the preset as shipped
    (the new families paused): nothing changes for the user the earlier rounds tuned. Process engineer,
    manufacturing and lab-operations titles pass by title and are told apart on the requirements text (G9)."""
    with tempfile.TemporaryDirectory() as tmp:
        shipped = preset_root(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        user = user_root(tmp, bioprocess_text())

    def fam(title, rules):
        p = jobfs.Posting(job_key="k", title=title, company="Acme Bio", company_norm="acme bio")
        v = gates.Verdict()
        return None if gates.g4_title(p, v, rules) else v.info["family"]
    A, B, C, F, L = ("bioprocess_engineering_msat", "gmp_manufacturing", "cmc_writing_project",
                     "bioprocess_field_support", "lab_operations")
    want = {  # title: (the user's family, the shipped preset's)
        "Process Engineer II (Downstream Process Development)": (A, None),
        "Senior Process Engineer, MSAT": (A, None), "Process Engineer III": (A, None),
        "Associate Director, Drug Substance Manufacturing - Upstream Process": (A, None),
        "Director, MSAT": (None, None), "Senior Director, Technology Transfer & Business Development": (None, None),
        "Engineer II, Conjugation Development and Manufacturing": (A, None),
        "Technology Transfer Specialist": (A, None), "QMS Process Engineer": (None, None),
        "Process Control Engineer": (None, None), "Complex Facility Process Engineer": (None, None),
        "Manufacturing Engineer II": (None, None), "Development Engineer": (None, None),
        "Scientist, Process Engineering": (A, None),
        "Manufacturing Associate, NGS TE Production": (B, None), "MFG Senior/Master, Downstream, Day Shift": (B, None),
        "Drug Product Team Lead": (B, None), "Manager, Reagent Manufacturing": (B, None),
        "Oligonucleotide Manufacturing Specialist": (B, None), "Manufacturing Lead": (B, None),
        "Manufacturing Engineering Manager": (None, None), "Manager, Manufacturing Planning": (None, None),
        "Senior Counsel, Pharmaceutical Development and Manufacturing": (None, None),
        "(CW) Manufacturing Associate (Temporary/Contract)": (None, None),
        "QA Associate - Temporary and Full Time": ("quality_validation", "quality_validation"),
        "Associate Director, CMC Project Management": (C, None), "CMC Technical Writer": (C, None),
        "Technical Writer": (None, None), "Program Manager": (None, None),
        "Associate Director, CMC Regulatory": (None, None), "Sr Manager, Regulatory Affairs CMC": (None, None),
        "Senior Scientist, CMC Analytical Development": ("industry_rd_scientist", "industry_rd_scientist"),
        "Field Application Specialist, Bioprocess": (F, "field_application_tech_support"),
        "Applications Engineer, Chromatography": (F, None),
        "Lab Manager": (L, "research_associate_lab_tech"), "Lab Operations Specialist": (L, "research_associate_lab_tech"),
        "Operations Manager": (None, None), "Lab Supervisor - Molecular Genetic Pathology": (None, None),
        "Clinical Trials Regulatory Lab Manager": (None, None),
        "Lab Assistant": (None, "research_associate_lab_tech"),
        "Part-Time Clinical Research Coordinator": (None, "clinical_research"),
        "CQV Engineer": ("quality_validation", "quality_validation"),
        "Scientist, Natural Products Discovery": ("microbiologist", "microbiologist"),
        "Antimicrobial Stewardship Pharmacist": (None, None),
        "Scientist II, Downstream Viral Vector Process Development": ("process_dev_fermentation",) * 2,
        "Downstream Purification Associate": ("process_dev_fermentation",) * 2,
    }
    for title, (mine, theirs) in want.items():
        assert fam(title, user) == mine, (title, fam(title, user), mine)
        assert fam(title, shipped) == theirs, (title, fam(title, shipped), theirs)
    assert gates.set_aside("associate director msat", ["associate director"]) == "_ msat"
    assert gates.title_level("Associate Director, MSAT") == 5 and gates.title_level("Process Engineer II") == 0
    # G9 on the requirements: a bioprocess word is needed, and device, chip, refinery or clinical-lab words
    # must not outnumber them
    blurb = "About us: a great company with a great culture in a regulated industry. " * 3

    def text(body):
        return blurb + "\n\nRequirements\n" + body + "\n\nBenefits\nHealth plan.\n"
    cases = [
        (A, "single-use bioreactors, chromatography and TFF at pilot scale; tech transfer into cGMP "
            "manufacturing; BS in chemical or mechanical engineering. " * 2, True),
        (A, "medical device manufacturing under ISO 13485 and 21 CFR 820; injection molding, SolidWorks and "
            "GD&T; lean and Six Sigma; GMP. " * 2, False),
        (A, "semiconductor wafer processing, lithography and thin film deposition; statistical process "
            "control. " * 2, False),
        (A, "refinery process units, hydrocarbon processing and petroleum products; process safety. " * 2, False),
        (A, "NGS library prep workflows and sequencer flow cells; lab automation and LIMS. " * 2, False),
        (B, "cGMP biologics manufacturing: cell culture, buffer preparation, aseptic technique, batch "
            "records; BS in biology. " * 2, True),
        (B, "reagent manufacturing for NGS kits: pipetting, DNA and oligo handling, SOPs; BS in biology or "
            "biochemistry. " * 2, True),
        (B, "assemble medical devices under ISO 13485; soldering and mechanical assembly; device history "
            "records; GMP. " * 2, False),
        (L, "manage a BSL-2 molecular biology lab: inventory, vendors, equipment, biosafety and EHS. " * 2, True),
        (L, "supervise CLIA laboratory staff; Joint Commission and CAP readiness; CLS license; patient "
            "specimens. " * 2, False),
        ("process_dev_fermentation", "downstream process development for viral vectors (AAV, lentiviral): "
                                     "chromatography, TFF and depth filtration. " * 2, True),
        ("process_dev_fermentation", "process chemistry and organic synthesis of small molecule APIs; "
                                     "crystallization and scale-up in the pilot plant. " * 2, False),
    ]
    by_id = {f["id"]: f for f in user.families}
    for fid, body, ok in cases:
        p = jobfs.Posting(job_key="k", title="x", text=text(body))
        v = gates.Verdict()
        assert (gates.g9_words(p, v, by_id[fid]) is None) == ok, (fid, body[:50], v.lines)


def test_bioprocess_seniority():
    """R6 for a person with about 3 industry years after 9 years running a university lab (the doctorate
    earned along the way): the lab years count as years of experience (they are no student role) and at half
    weight toward the level, so a Scientist II is judged at senior level. A senior or staff title is kept
    (staff as borderline), a principal title is beyond unless the text asks a modest bar, a plain '8+ years
    of experience' is met, '5+ years in industry' is borderline and '8+ years in industry' beyond. With a
    senior scientist title on the library the same years are judged at staff level, and an associate
    director title (a principal-level one) is borderline, never beyond."""
    with tempfile.TemporaryDirectory() as tmp:
        me = user_root(tmp, bioprocess_text(), library=BIO_LIBRARY, techniques=BIO_TECHNIQUES).experience
    assert (me.level, me.effective, me.doctorate, me.source) == (2, 3, True, "library"), me
    assert (me.years, me.industry_years) == (11.9, 2.9), me
    fam = {"mode": "industry"}

    def verdict(title, line, who=me):
        return gates.seniority(title, "Requirements\n" + line + "\n", fam, who)[0]
    cases = [("Senior Scientist, Process Development", "- PhD with 2+ years of industry experience", "ok"),
             ("Senior Process Engineer", "- 8+ years of relevant experience", "ok"),
             ("Senior Scientist", "- PhD and 5+ years of industry experience", "borderline"),
             ("Staff Scientist, MSAT", "- PhD; process characterization", "borderline"),
             ("Process Engineer III", "- 8+ years of industry experience in biologics", "beyond"),
             ("Principal Scientist, Purification", "- PhD with 10+ years of experience", "beyond"),
             ("Principal Scientist, Purification", "- PhD with 3 years of experience", "borderline"),
             ("Associate Director, MSAT", "- PhD with 8 years of experience", "beyond"),
             ("Manufacturing Associate II", "- BS and 1 year of GMP experience", "ok")]
    for title, line, want in cases:
        assert verdict(title, line) == want, (title, line, verdict(title, line))
    senior = BIO_LIBRARY.replace("### Scientist II, Process Development | Northwind Biologics | Feb 2020 - Dec 2022",
                                 "### Senior Scientist | Northwind Biologics | Feb 2020 - Dec 2022")
    with tempfile.TemporaryDirectory() as tmp:
        sr = user_root(tmp, bioprocess_text(), library=senior).experience
    assert (sr.level, sr.effective) == (3, 4), sr
    assert verdict("Associate Director, MSAT", "- PhD with 8 years of experience", sr) == "borderline"
    assert verdict("Principal Scientist, Purification", "- PhD with 10+ years of experience", sr) == "borderline"
    assert verdict("Associate Director, MSAT", "- PhD and 10+ years of industry experience", sr) == "beyond"
    assert verdict("Staff Scientist, MSAT", "- PhD; process characterization", sr) == "ok"


def test_pay_floor():
    """[gates] pay_floor: a posting whose stated pay tops out below the user's floor is dropped (reason pay, a
    filter reason: judged again when the floor changes), one that states no pay is kept, hourly rates count
    at 2,080 hours, and the floor is the user's setting (0, the shipped value, is off). Dollar figures that
    are not pay (funding, revenue, a bonus, a 401k match) are not read as pay."""
    pt = gates.pay_in_text
    assert pt("Salary Range: $120,000-155,000*. Offers depend on experience.") == [(120000.0, 155000.0)]
    assert pt("The base compensation range for this role is $40-45. Base pay") == [(83200.0, 93600.0)]
    assert pt("The expected hourly range for this role is $50 to $60.50 depending on skills") == [(104000.0, 125840.0)]
    assert pt("BUDGETED SALARY: $32.43/hr POSITION SUMMARY") == [(32.43 * 2080, 32.43 * 2080)]
    assert pt("base salary $150k-$180k plus a sign-on bonus of $10,000") == [(150000.0, 180000.0)]
    assert pt("monthly salary of $6,500 - $8,200 per month") == [(78000.0, 98400.0)]
    assert pt("Compensation Range $18.33" + chr(0x2013) + "$25 USD") == [(18.33 * 2080, 25 * 2080.0)]  # an en dash
    assert pt("The base salary range for this position is $120,000 - $140,000 + bonus + equity.") == \
        [(120000.0, 140000.0)]
    assert pt("the base salary range is approximately $77,000 - $85,000 USD per hour or annually") == \
        [(77000.0, 85000.0)]
    long_title = "Senior Staff Medical Science Liaison in Genetic Disease Testing and Reproductive Health, US"
    assert pt(f"The estimated base salary range for the {long_title} role based in the United States of America "
              "is: $155,600 - $233,400. Should the level change") == [(155600.0, 233400.0)]
    assert pt("We offer competitive compensation and company-paid life insurance of $50,000.") == []
    assert pt("The salary range is $100,400 to $186,400 for a Scientist 3 and $124,800 to $231,800 for a "
              "Scientist 4.") == [(100400.0, 186400.0), (124800.0, 231800.0)]
    for not_pay in ("From $0 to $400M+ annualized revenue run-rate in 6 years.", "We raised >$80M from investors.",
                    "Our 401k match is up to $6,000 a year.", "A $20K sign-on bonus is offered.",
                    "Tuition reimbursement of $5,250 a year.", "No dollar figure here."):
        assert pt(not_pay) == [], (not_pay, pt(not_pay))
    start = time.perf_counter()  # bounded work: many amounts and long runs of spaces stay fast
    pt(("$1 " + " " * 300) * 5000 + "salary $100,000")
    assert time.perf_counter() - start < 2, time.perf_counter() - start
    P = jobfs.Posting
    assert gates.stated_pay(P(job_key="k", salary={"min": 45.0, "max": 50.0, "period": "per-hour-wage",
                                                   "currency": "USD"})) == (93600.0, 104000.0, "field")
    assert gates.stated_pay(P(job_key="k", salary={"min": 80000.0, "max": 150000.0, "period": None,
                                                   "currency": "USD"}))[:2] == (80000.0, 150000.0)
    assert gates.stated_pay(P(job_key="k", salary={"min": 70000, "max": 90000, "period": "year", "currency": "EUR"},
                              text="no pay words")) is None
    assert gates.stated_pay(P(job_key="k", text="Requirements: a PhD.")) is None
    with tempfile.TemporaryDirectory() as tmp:
        off = preset_root(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        rules = user_root(tmp, user_profile(pay_floor=125000))
    assert off.pay_floor == 0 and rules.pay_floor == 125000 and off.hash != rules.hash
    cases = [("Salary range: $105,000 - $115,000 a year.", False), ("Salary range: $110,000 - $140,000.", True),
             ("The hourly rate for this contract is $55 to $60 per hour.", False),   # $124,800 a year
             ("The hourly rate for this contract is $58 to $65 per hour.", True),    # $135,200 a year
             ("Requirements: cell culture. No pay is stated.", True),
             ("We raised $80M. Requirements: cell culture.", True)]
    for body, ok in cases:
        v = gates.Verdict()
        kept = gates.g9_pay(P(job_key="k", title="Scientist", text=body), v, rules) is None
        assert kept == ok, (body, v.lines)
        if not ok:
            assert v.reason == "pay" and gates.is_filter("pay") and "$125,000" in v.lines[-1], v.lines
        assert gates.g9_pay(P(job_key="k", title="Scientist", text=body), gates.Verdict(), off) is None
    # through a whole pass: one discard line with reason pay, and the kept posting carries its stated pay
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        path = root / "config" / "search_profile.toml"
        toml = path.read_text(encoding="utf-8")
        assert "[gates]" in toml and "pay_floor" not in toml
        path.write_text(toml.replace("[gates]", "[gates]\npay_floor = 125000", 1), encoding="utf-8")
        mk = make_gates_root.posting
        low = mk("greenhouse:900501", "QC Microbiologist", make_gates_root.gh(900501),
                 text=make_gates_root.MICRO + " The salary range for this role is $90,000 to $110,000.")
        high = mk("greenhouse:900502", "Microbiologist II", make_gates_root.gh(900502),
                  text=make_gates_root.MICRO + " The salary range for this role is $120,000 to $150,000.")
        none = mk("greenhouse:900503", "Senior Microbiologist", make_gates_root.gh(900503))
        run_all(root, postings=[low, high, none])
        rows = {r["job_key"]: r for r in discards(root)}
        assert rows["greenhouse:900501"]["gate"] == "G9" and rows["greenhouse:900501"]["reason"] == "pay", rows
        assert rows["greenhouse:900501"]["profile_hash"], "a pay discard is judged again when the floor changes"
        items = pool(root)
        assert items["greenhouse:900502"]["pay_stated"] == [120000, 150000] and "greenhouse:900503" in items
        assert "pay_stated" not in items["greenhouse:900503"]


def test_work_type():
    """Temporary jobs and shift work are the user's settings. With "temporary" out of [gates]
    employment_types a title or text marked temp or temporary is dropped (employment:temporary, a filter
    reason) unless it is also temp-to-hire, contract-to-hire or full-time; contract jobs stay. With
    shift_work = "last" a night, swing, weekend or 12-hour job is kept and marked low_rank for the queue.
    The shipped values change nothing."""
    tm, sm = gates.temp_marked, gates.shift_marked
    assert tm("(CW) Manufacturing Associate (Temporary/Contract)") and tm("Research Associate I/II (Temporary)")
    assert tm("Lab Support Specialist - Temp") and not tm("QA Associate - Temporary and Full Time")
    assert not tm("Manufacturing Associate (Temp-to-Hire)") and not tm("Bioprocess Specialist - CONTRACT")
    assert not tm("Contemporary Art Curator") and not tm("Scientist", "Maintain temporary storage of samples.")
    assert tm("Scientist", "This is a 6-month temporary position in our lab.")
    assert tm("Manufacturing Associate I (Contract)", "You will join us for a temporary assignment of 6 months.")
    assert not tm("Scientist", "This is a temporary position; contract-to-hire for the right person.")
    for title in ("MFG Associate, Downstream, Swing Shift, Fremont CA", "Hospital Lab Technician 1 - Evenings",
                  "Lab Tech - 12-HR Nights", "Clinical Lab Scientist (3rd Shift, Tues-Sat)",
                  "Laboratory Associate II, Pre-Clinical (Night Shift)", "Research Associate - PM Shift (Contract)"):
        assert sm(title), title
    for title in ("MFG Senior/Master, Downstream, Fremont CA, Day Shift", "Research Associate - 8 HR Days",
                  "Clinical Research Coordinator 2 (08-HR)", "Scientist, Process Development"):
        assert not sm(title), title
    assert sm("Scientist", "This is a swing shift position: Wednesday 3pm to 11:30pm.")
    assert not sm("Scientist", "Must be willing to work night shifts as needed.")
    with tempfile.TemporaryDirectory() as tmp:
        shipped = preset_root(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        user = user_root(tmp, user_profile(employment_types=["full-time", "contract"], shift_work="last"))
    assert shipped.temp_ok and not shipped.shift_last and not user.temp_ok and user.shift_last
    P = jobfs.Posting

    def g4(title, rules):
        v = gates.Verdict()
        gates.g4_title(P(job_key="k", title=title, company="Acme Bio", company_norm="acme bio"), v, rules)
        return v
    v = g4("Research Associate I/II (Temporary), Ex Vivo & Assays", user)
    assert not v.ok and v.reason == "employment:temporary" and gates.is_filter(v.reason), v.lines
    assert g4("Research Associate I/II (Temporary), Ex Vivo & Assays", shipped).ok
    assert g4("QA Associate - Temporary and Full Time", user).ok and g4("QC Analyst - CONTRACT", user).ok
    for rules, low in ((user, ["shift"]), (shipped, None)):
        v = gates.Verdict()
        p = P(job_key="k", title="QC Microbiologist (Night Shift)", text="Requirements: aseptic technique.")
        assert gates.g9_work_type(p, v, rules) is None and v.info.get("low_rank") == low, v.lines
    v = gates.Verdict()
    p = P(job_key="k", title="QC Microbiologist", text="This is a temporary position for six months.")
    assert gates.g9_work_type(p, v, user) is not None and v.reason == "employment:temporary", v.lines
    assert gates.g9_work_type(p, gates.Verdict(), shipped) is None


def test_second_region():
    """A second region the user would move to ([[home.region]], off unless enabled = true): its places pass
    G3 marked relocate, are pooled with low_rank relocate (the queue lists them after the home jobs) and are
    judged by the region's own pay_floor; there a job that states no pay is dropped unless unstated_pay is
    "keep". Home places come first, another state's or country's namesake is still out, and a region that is
    not enabled changes nothing."""
    region = ('[[home.region]]\nlabel = "Boston area"\nenabled = {on}\nstates = ["MA"]\n'
              'places = ["Boston", "Cambridge", "Waltham"]\npay_floor = 200000\n\n'
              '[[home.region]]\nlabel = "North Carolina"\nenabled = {on}\nstates = ["NC"]\nunstated_pay = "keep"\n')
    with tempfile.TemporaryDirectory() as tmp:
        off = user_root(tmp, user_profile(home_extra=region.format(on="false"), pay_floor=125000))
    with tempfile.TemporaryDirectory() as tmp:
        on = user_root(tmp, user_profile(home_extra=region.format(on="true"), pay_floor=125000))
    assert off.regions == [] and [r["label"] for r in on.regions] == ["Boston area", "North Carolina"]
    assert on.regions[0]["pay_floor"] == 200000 and on.regions[0]["unstated_pay"] == "drop"
    assert on.regions[1]["pay_floor"] == 0 and on.regions[1]["unstated_pay"] == "keep" and off.hash != on.hash
    cases = {"Cambridge, MA": "Boston area", "Boston, Massachusetts, United States": "Boston area",
             "Waltham": "Boston area", "Durham, NC": "North Carolina", "US - North Carolina - Sanford": "North Carolina",
             "Cambridge, United Kingdom": None, "Boston, Lincolnshire, UK": None, "Worcester, MA": None,
             "Austin, TX": None, "Remote - Massachusetts": None}
    for loc, want in cases.items():
        ok, v = list_verdict(on, "Acme Bio", "QC Microbiologist", loc)
        assert (v.info.get("region") if ok else None) == want, (loc, ok, v.lines)
        assert not list_verdict(off, "Acme Bio", "QC Microbiologist", loc)[0], loc
    ok, v = list_verdict(on, "Acme Bio", "QC Microbiologist", "South San Francisco, CA | Cambridge, MA")
    assert ok and "region" not in v.info, v.lines  # a home place among them: a home job
    P = jobfs.Posting

    def pay(text, where):
        v = gates.Verdict(info={"region": where} if where else {})
        return gates.g9_pay(P(job_key="k", title="Scientist", text=text), v, on) is None, v
    assert pay("Salary range: $150,000 to $190,000.", None)[0]              # home: over the $125,000 floor
    kept, v = pay("Salary range: $150,000 to $190,000.", "Boston area")     # moving needs $200,000
    assert not kept and v.reason == "pay" and "Boston area" in v.lines[-1], v.lines
    assert pay("Salary range: $180,000 to $230,000.", "Boston area")[0]
    kept, v = pay("Requirements: cell culture.", "Boston area")             # no stated pay: not worth a move
    assert not kept and "no stated pay" in v.lines[-1], v.lines
    assert pay("Requirements: cell culture.", "North Carolina")[0]           # unstated_pay = "keep"
    assert not pay("Salary range: $90,000 to $110,000.", "North Carolina")[0]  # the [gates] floor applies there
    # through a whole pass, and the places the board readers walk
    with tempfile.TemporaryDirectory() as tmp:
        root = copy_root(tmp)
        path = root / "config" / "search_profile.toml"
        toml = path.read_text(encoding="utf-8")
        assert "[gates]" in toml
        path.write_text(toml.replace("[gates]", '[[home.region]]\nlabel = "Brockway"\nenabled = true\n'
                                                'places = ["Brockway"]\nunstated_pay = "keep"\n\n[gates]', 1),
                        encoding="utf-8")
        there = make_gates_root.posting("greenhouse:900601", "QC Microbiologist", make_gates_root.gh(900601),
                                        location="Brockway, IL", city="Brockway")
        run_all(root, postings=[there])
        item = pool(root)["greenhouse:900601"]
        assert item["relocate"] == "Brockway" and item["low_rank"] == ["relocate"], item
        import boards
        places, states, _ = boards.home_area(root, jobfs.search_profile(root))
        assert "Brockway" in places and "Springfield" in places
    assert gates.search_area({"places": ["Springfield"], "states": ["IL"], "region": [
        {"label": "x", "enabled": True, "states": ["MA"], "places": ["Boston"]},
        {"label": "y", "enabled": False, "states": ["NC"], "places": ["Durham"]}]}) == (
        ["Springfield", "Boston"], frozenset({"IL", "MA"}))


def test_min_terms():
    """A family's min_terms (a user's setting for a broad family): a posting that names fewer of the user's
    own terms than that is another specialty's and is dropped (reason title); not judged while techniques.md
    is empty, and off in the preset as shipped. The profile hash follows techniques.md once it is set."""
    import library
    with tempfile.TemporaryDirectory() as tmp:
        rules = user_root(tmp, bioprocess_text(min_terms={"industry_rd_scientist": 2}), library=BIO_LIBRARY,
                          techniques=BIO_TECHNIQUES)
        techniques = library.load_techniques(Path(tmp) / "techniques.md")
        plain = user_root(Path(tmp) / "plain", bioprocess_text(), library=BIO_LIBRARY, techniques=BIO_TECHNIQUES)
        assert rules.hash != plain.hash
    fam = next(f for f in rules.families if f["id"] == "industry_rd_scientist")
    assert fam["min_terms"] == 2 and "min_terms" not in next(f for f in plain.families if f["id"] == fam["id"])
    P = jobfs.Posting
    mine = P(job_key="k", title="Scientist", text="Requirements: mammalian cell culture in single-use bioreactors; HPLC.")
    theirs = P(job_key="k", title="Scientist", text="Requirements: multiparameter flow cytometry of immune cells.")
    assert gates.g9_terms(mine, gates.Verdict(), fam, techniques, BIO_LIBRARY) is None
    v = gates.Verdict()
    assert gates.g9_terms(theirs, v, fam, techniques, BIO_LIBRARY) is not None and v.reason == "title", v.lines
    assert "names 0.5 of your terms" in v.lines[-1], v.lines  # 'flow cytometry' is a family term, not one of theirs
    assert gates.g9_terms(theirs, gates.Verdict(), fam, [], BIO_LIBRARY) is None       # no techniques yet
    assert gates.g9_terms(theirs, gates.Verdict(), dict(fam, min_terms=0), techniques) is None


def main():
    for fn in (test_every_gate, test_knockouts, test_freshness_and_repost, test_undated_board,
               test_ai_text_parked_once, test_profile_edit_reopens, test_two_stage_keep, test_dormant_board, test_explain, test_terms,
               test_preset, test_live_day_corpus, test_place_rules, test_detail_rejudged, test_untitled_list_records,
               test_untitled_detail_never_pooled, test_pass_stops_halfway, test_g9_requirements, test_pool_age_basis, test_g1_lookalike_host,
               test_two_openings_one_title, test_link_schemes_and_canonical_hosts, test_one_bad_posting,
               test_requirement_scan_bounded, test_labeled_pool_d6, test_d6_title_rules, test_g9_avoid,
               test_fit_overlap, test_office_labels, test_second_ring_set_aside, test_labeled_pools_r6,
               test_seniority_rules, test_shift_lengths_not_hr, test_exact_core_title_first, test_regional_remote,
               test_robots_refused_by_policy, test_link_refusal_r1, test_fit_general_form, test_posted_values,
               test_other_field_postdocs, test_bioprocess_labels_r6, test_bioprocess_families,
               test_bioprocess_seniority, test_pay_floor, test_work_type, test_second_region, test_min_terms):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
