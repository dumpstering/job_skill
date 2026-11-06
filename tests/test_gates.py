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
            "London, United Kingdom | South San Francisco, CA": True, "Vizag": False, "": "unknown"}
        for loc, want in cases.items():
            ok, v = list_verdict(rules, "Acme Bio", "QC Microbiologist", loc)
            unknown = "location_unknown" in (v.info.get("flags") or [])
            got = "unknown" if ok and unknown else ok
            assert got == want, (loc, got, want, v.lines)
    import posting
    assert posting.split_place("Jersey City, NJ; Millbrae, CA") == ("Jersey City", "NJ", "US")
    assert posting.split_place("Novato, California") == ("Novato", "CA", "US")
    assert posting.us_eligible("TWN - Remote", "", "remote") is False
    assert posting.us_eligible("Remote (Vienna, Austria)", "", "remote") is False
    assert posting.us_eligible("Remote", "", "remote") is None


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
                raise RuntimeError("stopped halfway")
            return real(p, v)
        gates.g12_route = boom
        try:
            run = gates.GateRun(root, DAY)
            try:
                run.process(batch)
            except RuntimeError:
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
    'linkedin', or any non-ASCII host) is discarded at G1, never pooled as a
    browser read for the user to open."""
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
        assert rows == {a["job_key"]: "host:linkedin.com", "greenhouse:900301": "host:not-ascii"}, rows
        assert not pool(root)


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


def main():
    for fn in (test_every_gate, test_knockouts, test_freshness_and_repost, test_undated_board,
               test_ai_text_parked_once, test_profile_edit_reopens, test_two_stage_keep, test_dormant_board, test_explain, test_terms,
               test_preset, test_live_day_corpus, test_place_rules, test_detail_rejudged, test_untitled_list_records,
               test_pass_stops_halfway, test_g9_requirements, test_pool_age_basis, test_g1_lookalike_host,
               test_two_openings_one_title):
        fn()
        print(f"ok {fn.__name__[5:]}")
    print("PASS")


if __name__ == "__main__":
    main()
