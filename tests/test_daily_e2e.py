"""The offline end-to-end day (spec 9 gate): jobrunner.py daily on a temp copy
of tests/fixtures/folder against the board fixtures, then the card.

Usage:
    python tests/test_daily_e2e.py          run the day and check it
    python tests/test_daily_e2e.py --make   rebuild tests/fixtures/folder (deterministic)

The fixture folder is a mode-L JobSearch root for the synthetic applicant
Jordan Q. Avery (tests/fixtures/variant_example's files) on 2026-11-06:
- config/watchlist.csv names every board in tests/fixtures/boards;
- config/search_profile.toml has two families (qc_micro, assay_dev), each with
  an approved variant (variants/<family>/v1/approved.json);
- state/pool.json carries 66 gated postings from earlier days (made-up
  companies), and state/leads_state.json holds 92 alert leads that were tried
  once on 2026-11-05 and that no board answers for. Today they are listed in
  runner/out/needs_search.json (10 companies, the day's web-search limit) for
  job-morning to search; a lead that needs a web search is given up only
  after needs_search.json listed it on 2 different days (contracts-06).
The day: 4 board postings pass the gates (Gilead's R0055911 was posted
2026-10-01, so its detail date makes it G5 stale: LIVE-03), the rest of the
boards give 52 discards; the queue takes 30 of the 70 eligible postings.
Stdlib only for the checks (the daily itself needs the runner packages).
Exits non-zero on the first failure and prints PASS at the end.
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPTS = REPO / "job" / "scripts"
RUNNER = SCRIPTS / "jobrunner.py"
TODAY_VIEW = SCRIPTS / "today_view.py"
FIXTURE = HERE / "fixtures" / "folder"
BOARDS = HERE / "fixtures" / "boards"
EXAMPLE = HERE / "fixtures" / "variant_example"
DAY = date(2026, 11, 6)
DAILY_RE = re.compile(r"^DAILY ok queue=(\d+) pool_left=(\d+) discards=(\d+) tier_b_built=(\d+) prep_failed=(\d+)$")
TEXT_SUFFIXES = {".md", ".json", ".toml", ".csv", ".jsonl", ".txt"}

# ---------- the fixture (python tests/test_daily_e2e.py --make) ----------

WATCHLIST = [
    # employer, ats, board, host, site
    ("Vaxcyte", "greenhouse", "vaxcyte", "", ""), ("Synthego", "lever", "synthego", "", ""),
    ("insitro", "ashby", "insitro", "", ""), ("Gilead Sciences", "workday", "gilead", "wd1", "gileadcareers"),
    ("Acme Biologics", "workday", "acmebio", "wd1", "External"),
    ("Mango Materials", "bamboohr", "mangomaterials", "", ""), ("Vaxart", "breezy", "vaxart-inc", "", ""),
    ("Hugging Face", "workable", "huggingface", "", ""), ("SETI Institute", "rippling", "seti-institute", "", ""),
    ("Pinpoint", "pinpoint", "workwithus", "", ""), ("Teamtailor", "teamtailor", "career", "career.teamtailor.com", ""),
    ("Capmo", "personio", "capmo", "", ""), ("Doyensec", "manatal", "doyensec-llc", "", ""),
    ("Jaguar Land Rover Coral Gables", "hireology", "jaguarlandrover", "", ""),
    ("Pinnacle Home Care", "comeet", "pinnaclehomecare", "", "1B.00E"),
    ("Cytek Biosciences", "adp", "a21467f2-5c74-46bf-bd5c-61cdb598d8b3", "", "19000101_000001"),
    ("Hologic", "oracle", "ebwb", "ebwb.fa.us2.oraclecloud.com", "CX"),
    ("Codexis", "paylocity", "fb344149-f240-4648-a99b-7e27510b63c2", "", "Codexis-Inc"),
    ("Asbury Automotive", "dayforce", "asburyauto", "", "ASBURYCAREERSITE"),
    ("Sutro Biopharma", "jobvite", "sutrobio", "", ""), ("Buck Institute", "jazzhr", "buckinstitute", "", ""),
    ("AbbVie", "phenom", "abbvie", "careers.abbvie.com", ""), ("Acme Bio", "recruitee", "acmebio", "", ""),
]
CAREERS = {"abbvie": "https://careers.abbvie.com/en"}   # a careers_url host joins the allowlist (8.2)
PAUSED = {"ebwb": "fixture list was recorded without a family keyword"}
WATCH_COLS = ["employer", "employer_norm", "parent", "ats", "board", "host", "site", "extra", "careers_url",
              "main_website", "segment", "city", "families", "priority", "status", "added_on", "added_by", "ref",
              "notes"]
SETTINGS = """# Synthetic test settings (tests/test_daily_e2e.py --make).
[runner]
mode = "L"

[sources]
adzuna = false
usajobs = false

[time]
timezone = "America/Los_Angeles"
"""
PROFILE = """# Synthetic test profile (tests/test_daily_e2e.py --make).
version = 1

[home]
label = "Springfield, IL"
zip = ""
radius_miles = 40
places = ["Springfield", "Redwood City", "Foster City", "Santa Clara", "South San Francisco", "San Carlos",
          "Mountain View", "Novato", "Fremont"]
remote = "us"

[gates]
exclude_title_words = ["Director", "Vice President", "VP", "Intern", "Internship", "Chief", "Head of"]

[[knockout]]
id = "ca_cls"
name = "Clinical Laboratory Scientist license"
required_patterns = ['(?i)\\bCLS\\b.{0,40}(licen[cs]e|required)']
title_patterns = ['(?i)\\bCLS\\b']

[[family]]
id = "qc_micro"
label = "QC / Microbiology"
status = "active"
mode = "industry"
core_titles = ["QC Microbiologist"]
adjacent_titles = ["Microbiology Analyst"]
title_any = ["microbiolog*", "sterility", "laboratory", "lab"]
title_none = ["sales", "manager"]
terms = ["GMP", "aseptic technique", "environmental monitoring", "endotoxin testing", "real-time PCR"]
priority = 1
min_share = 0.10

[[family]]
id = "assay_dev"
label = "Scientist / Assay development"
status = "active"
mode = "industry"
core_titles = ["Scientist, Assay Development"]
title_any = ["scientist"]
title_none = ["machine learning", "data"]
terms = ["real-time PCR", "ELISA", "assay development", "16S rRNA sequencing"]
priority = 2
min_share = 0.10

[widen]
enabled = true
steps = ["adjacent_families", "remote_us", "second_ring"]
"""
CITIES = [("Redwood City", "CA"), ("South San Francisco", "CA"), ("Foster City", "CA"), ("San Carlos", "CA"),
          ("Mountain View", "CA"), ("Santa Clara", "CA"), ("Fremont", "CA"), ("Novato", "CA")]
WORDS_A = ["Acorn", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hazel", "Ivy", "Juniper", "Kelp",
           "Larch", "Maple", "Nettle", "Oak", "Pine", "Quince", "Rowan", "Sage", "Teak", "Umber", "Vetch",
           "Willow", "Yarrow", "Zinnia"]
WORDS_B = ["Bio", "Labs", "Therapeutics", "Biosciences", "Diagnostics", "Genomics"]
TITLES = {"qc_micro": ["QC Microbiologist II", "Microbiology Analyst", "QC Microbiologist I",
                       "Sterility Assurance Microbiologist"],
          "assay_dev": ["Scientist, Assay Development", "Scientist II, Molecular Assays", "Associate Scientist",
                        "Senior Scientist, Assay Development"]}
TEXT = {"qc_micro": ("Requirements: GMP experience, aseptic technique, environmental monitoring of cleanrooms, "
                     "endotoxin testing and real-time PCR release methods. Hands-on BSL-2 lab work."),
        "assay_dev": ("Requirements: assay development for real-time PCR and ELISA, 16S rRNA sequencing "
                      "experience, and careful records in a GMP setting.")}
LEAD_COMPANIES = ["Quillon Bio", "Ravelin Labs", "Sorrel Therapeutics", "Tamsin Diagnostics", "Ulmer Genomics",
                  "Verdigris Bio", "Wexley Labs", "Xandar Biosciences", "Yellowfin Bio", "Zephyr Labs"]
LEAD_TITLES = ["Scientist I", "Scientist II", "Research Associate", "QC Analyst", "Lab Technician",
               "Senior Scientist", "Associate Scientist", "Process Scientist", "Microbiologist"]
CARRY = 66        # pooled on earlier days, still eligible today
LEADS = 92        # alert leads given up today (unresolved on a second day)


def slug(s):
    return "".join(c for c in s.lower() if c.isalnum())


def carry_item(i):
    """One gated posting from an earlier day (made-up company, no network)."""
    family = "qc_micro" if i % 2 == 0 else "assay_dev"
    company = f"{WORDS_A[i % len(WORDS_A)]} {WORDS_B[(i // len(WORDS_A)) % len(WORDS_B)]}"
    s = slug(company)
    title = TITLES[family][(i // 2) % len(TITLES[family])]
    city, st = CITIES[i % len(CITIES)]
    age = 1 + (i * 5) % 20
    posted = (DAY - timedelta(days=age)).isoformat()
    kind = i % 10
    if kind in (0, 1, 2, 3):
        jid = str(7100000 + i)
        url, key, ats = f"https://job-boards.greenhouse.io/{s}/jobs/{jid}", f"greenhouse:{jid}", "greenhouse"
    elif kind in (4, 5):
        jid = f"22222222-0000-4000-8000-{i:012d}"
        url, key, ats = f"https://jobs.lever.co/{s}/{jid}", f"lever:{jid}", "lever"
    elif kind in (6, 7):
        jid = f"33333333-0000-4000-8000-{i:012d}"
        url, key, ats = f"https://jobs.ashbyhq.com/{s}/{jid}", f"ashby:{jid}", "ashby"
    elif kind == 8:
        jid = f"B{i:05d}C"
        url, key, ats = f"https://apply.workable.com/{s}/j/{jid}/", f"workable:{s}:{jid}", "workable"
    else:
        rid = f"R-{40000 + i}"
        url = f"https://{s}.wd5.myworkdayjobs.com/en-US/External/job/{city.replace(' ', '-')}/Job_{rid}"
        key, ats = f"workday:{s}:{rid}", "workday"
    text = (f"{company} is hiring a {title} in {city}. {TEXT[family]} "
            f"Synthetic posting {i} for the runner tests.")
    fit = round(0.40 + ((i * 17) % 50) / 100, 2)
    return {"job_key": key, "ats": ats, "board": s, "company": company, "company_norm": company.lower(),
            "title": title, "title_norm": title.lower().replace(",", ""), "location": f"{city}, {st}",
            "city": city, "state": st, "country": "US", "remote": "onsite", "us_eligible": True,
            "posted": posted, "posted_field": "fixture", "first_seen": posted, "url": url, "apply_url": url,
            "req_ids": [f"REQ-{5000 + i}"], "text": text, "salary": None, "questions": False,
            "employer_site": "", "lane": "watch", "lead_ids": [],
            "simhash": f"{(i * 0x9E3779B97F4A7C15 + 12345) & 0xFFFFFFFFFFFFFFFF:016x}", "closed": False,
            "family": family, "mode": "industry", "family_priority": 1 if family == "qc_micro" else 2,
            "title_match": 1.0, "fit": fit, "coverage": round(fit - 0.1, 2), "level_match": 1.0, "flags": [],
            "band": "", "age_days": age, "date_basis": "posted", "repost_of": None, "segment": "biotech",
            "posting_status": "read", "markdown": "", "ai_text": [], "hidden": [], "pooled_on": posted,
            "folder": ""}


def lead(i, salt):
    import hashlib
    company = LEAD_COMPANIES[i % len(LEAD_COMPANIES)]
    title = LEAD_TITLES[(i // len(LEAD_COMPANIES)) % len(LEAD_TITLES)]
    city = CITIES[i % len(CITIES)][0]
    lid = hashlib.sha256(f"{salt}|biospace_alert|{company}|{title}|{city}".encode()).hexdigest()[:16]
    return {"v": 1, "lead_id": lid, "source": "biospace_alert", "company": company, "title": title,
            "location": f"{city}, CA", "seen": "2026-11-05", "msg_ref": f"{i:016x}"}


def make_fixture():
    sys.path.insert(0, str(SCRIPTS))
    import jobfs
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    root = FIXTURE
    (root / "config").mkdir(parents=True)
    for name in ("profile.md", "master_resume.md", "achievement_library.md", "answers.md", "publications.md",
                 "techniques.md"):
        shutil.copyfile(EXAMPLE / name, root / name)
    (root / "config" / "settings.toml").write_text(SETTINGS, encoding="ascii", newline="\n")
    (root / "config" / "search_profile.toml").write_text(PROFILE, encoding="ascii", newline="\n")
    salt = "0123456789abcdef0123456789abcdef"
    (root / "config" / "lead_salt.txt").write_text(salt + "\n", encoding="ascii", newline="\n")
    with open(root / "config" / "watchlist.csv", "w", encoding="ascii", newline="") as f:
        w = csv.DictWriter(f, WATCH_COLS, lineterminator="\n")
        w.writeheader()
        for emp, ats, board, host, site in WATCHLIST:
            w.writerow({"employer": emp, "employer_norm": emp.lower(), "ats": ats, "board": board, "host": host,
                        "site": site, "segment": "biotech", "priority": "2",
                        "status": "paused" if board in PAUSED else "active", "careers_url": CAREERS.get(board, ""),
                        "added_on": "2026-10-01", "added_by": "user", "notes": PAUSED.get(board, "test fixture")})
    for fam in ("qc_micro", "assay_dev"):
        v1 = root / "variants" / fam / "v1"
        v1.mkdir(parents=True)
        for name in ("resume.json", "pool.json", "letter.json", "review.md"):
            shutil.copyfile(EXAMPLE / "variants" / "qc_micro" / "v1" / name, v1 / name)
    env = dict(os.environ, JOB_SKILL_NOW="2026-11-05T17:00:00Z")
    for fam in ("qc_micro", "assay_dev"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "variants.py"), "approve", "--root", str(root),
                            "--family", fam], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
    items = [carry_item(i) for i in range(CARRY)]
    state = root / "state"
    jobfs.atomic_write_json(state / "pool.json", {"v": 1, "date": "2026-11-05", "counts": {}, "needs_you": [],
                                                  "items": items})
    for it in items:
        rec = {"job_key": it["job_key"], "url_norm": it["url"].replace("https://", "https://"),
               "cnt": f"{it['company_norm']}|{it['title_norm']}|{it['city'].lower()}", "req_ids": it["req_ids"],
               "simhash": it["simhash"], "first_seen": it["first_seen"], "last_seen": it["first_seen"],
               "posted": it["posted"], "lane": "watch", "outcome": "pooled", "profile_hash": ""}
        jobfs.append_jsonl(state / "seen" / f"{it['first_seen'][:7]}.jsonl", rec)
    leads = [lead(i, salt) for i in range(LEADS)]
    assert len({ld["lead_id"] for ld in leads}) == LEADS
    st = {ld["lead_id"]: {"lead_id": ld["lead_id"], "company": ld["company"], "title": ld["title"],
                          "location": ld["location"], "source": ld["source"], "seen": ld["seen"],
                          "status": "pending", "tries": ["2026-11-05"], "job_keys": []} for ld in leads}
    jobfs.atomic_write_text(state / "leads_state.json", json.dumps({"leads": st}, indent=1, sort_keys=True) + "\n")
    lead_file = root / "runner" / "in" / "leads" / "2026-11-05_0630_biospace.jsonl"
    lead_file.parent.mkdir(parents=True)
    lead_file.write_text("".join(json.dumps(ld, separators=(",", ":")) + "\n" for ld in leads),
                         encoding="ascii", newline="\n")
    print(f"WROTE {FIXTURE} ({CARRY} carried postings, {LEADS} leads)")


# ---------- the test ----------

def fresh_copy():
    tmp = Path(tempfile.mkdtemp(prefix="job_e2e_"))
    root = tmp / "JobSearch"
    shutil.copytree(FIXTURE, root)
    # A checkout with core.autocrlf may hand us CRLF files; approvals hash LF bytes.
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            b = p.read_bytes()
            if b"\r\n" in b:
                p.write_bytes(b.replace(b"\r\n", b"\n"))
    return tmp, root


def run_daily(root, *extra):
    env = {k: v for k, v in os.environ.items() if k not in ("JOB_SKILL_NOW", "JOBRUNNER_HOME")}
    env["JOBRUNNER_HOME"] = str(root.parent / "runner_home")  # never the real %LOCALAPPDATA%
    r = subprocess.run([sys.executable, str(RUNNER), "daily", "--root", str(root), "--offline", str(BOARDS),
                        "--date", DAY.isoformat(), *extra], capture_output=True, text=True, encoding="utf-8",
                       env=env)
    return r.returncode, r.stdout.splitlines(), r.stderr


def tree(root):
    return {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()
            and "logs" not in p.relative_to(root).parts and not p.name.startswith("status.")}


def test_day():
    tmp, root = fresh_copy()
    try:
        t0 = time.time()
        code, lines, err = run_daily(root)
        took = time.time() - t0
        assert code == 0, (code, lines[-15:], err[-2000:])
        assert lines and lines[-1] == "DAILY ok queue=30 pool_left=40 discards=52 tier_b_built=30 prep_failed=0", \
            (lines[-12:], err[-1500:])
        assert DAILY_RE.match(lines[-1])
        qd = root / "queue" / DAY.isoformat()
        for name in ("queue.json", "today.md", "review.html", "discards.csv"):
            assert (qd / name).exists(), name
        q = json.loads((qd / "queue.json").read_text(encoding="utf-8"))
        assert q["size"] == 30 == len(q["items"]) and q["pool_left"] == 40, (q["size"], q["pool_left"])
        assert q["size"] == min(30, q["size"] + q["pool_left"])
        sys.path.insert(0, str(SCRIPTS))
        import jobqueue
        assert jobqueue.check_queue(q) == [], jobqueue.check_queue(q)[:5]
        folders = [root / it["folder"] for it in q["items"]]
        assert len({f.name for f in folders}) == 30
        for f in folders:
            for name in ("posting.md", "posting.json", "fill_plan.json", "resume_B.pdf"):
                assert (f / name).exists(), (f.name, name)
            listed = set((f / ".runner_files").read_text(encoding="utf-8").split())
            assert {"posting.md", "posting.json", "fill_plan.json", "resume_B.pdf"} <= listed, (f.name, listed)
        uploads = [p for p in (root / "upload").iterdir() if p.is_file()]
        assert len(uploads) == 30, sorted(p.name for p in uploads)
        assert sorted(p.name for p in uploads) == sorted(Path(it["upload"]).name for it in q["items"])
        assert all(it["resume_status"] in ("built", "fallback_base", "tier_a_pending") for it in q["items"])
        assert sum(1 for it in q["items"] if it["tier"] == "A") == 4
        status = json.loads((root / "runner" / "out" / "status.json").read_text(encoding="utf-8"))
        assert status["last_daily"]["ok"] and status["last_daily"]["date"] == DAY.isoformat(), status
        assert status["version"] == "1.3.1" and status["mode"] == "L" and "tracker_locked_since" in status
        with open(root / "reports" / "funnel.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1 and rows[0]["date"] == DAY.isoformat() and rows[0]["queued"] == "30", rows
        assert rows[0]["pool"] == "70", rows[0]
        # The leads tried once yesterday are offered to today's web search, never discarded yet.
        assert "resolve" not in rows[0]["discarded_by_gate"], rows[0]
        needs = json.loads((root / "runner" / "out" / "needs_search.json").read_text(encoding="utf-8"))
        assert needs["date"] == DAY.isoformat() and len(needs["companies"]) == 10, needs
        assert sum(c["leads"] for c in needs["companies"]) == 92, needs["companies"]
        lead_state = json.loads((root / "state" / "leads_state.json").read_text(encoding="utf-8"))["leads"]
        assert all(ld["status"] == "pending" and ld.get("offered") == [DAY.isoformat()]
                   for ld in lead_state.values()), "a lead was given up before a web search had it"
        with open(qd / "discards.csv", encoding="utf-8", newline="") as f:
            assert sum(1 for _ in csv.DictReader(f)) == 52
        today = (qd / "today.md").read_text(encoding="utf-8")
        assert len(today.splitlines()) <= 80 and "Ready: 30." in today, today[:400]
        html = (qd / "review.html").read_bytes()
        assert html.isascii() and b"http" not in html.lower().replace(b"http-equiv", b""), "review.html"
        assert not (root / "state" / "run.lock").exists()
        assert not any(p.name == "run.lock" for p in (root.parent / "runner_home").glob("*")) \
            if (root.parent / "runner_home").exists() else True
        print(f"ok daily ({took:.0f} s)")

        before = tree(root)
        code, lines, err = run_daily(root)
        assert code == 0 and lines[-1] == "DAILY ok queue=30 unchanged", (lines[-5:], err[-800:])
        after = tree(root)
        assert before == after, sorted(set(before.items()) ^ set(after.items()))[:10]
        print("ok second daily adds nothing")

        r = subprocess.run([sys.executable, str(TODAY_VIEW), "card", "--root", str(root), "--date", DAY.isoformat()],
                           capture_output=True, text=True, encoding="utf-8",
                           env=dict(os.environ, JOB_SKILL_NOW="2026-11-06T17:00:00Z"))
        assert r.returncode == 0, (r.stdout, r.stderr)
        card = r.stdout.splitlines()
        assert card[0] == "Friday, November 6", card
        assert any(ln.startswith("Ready: 30. No account needed for ") for ln in card), card
        assert card[-1].startswith("Starting #1 now."), card
        import report
        assert report.check_wording(r.stdout) == [], report.check_wording(r.stdout)
        print("ok card")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if "--make" in sys.argv[1:]:
        make_fixture()
        return
    if not FIXTURE.exists():
        print(f"FAIL {FIXTURE} is missing: run python tests/test_daily_e2e.py --make")
        sys.exit(1)
    test_day()
    print("PASS")


if __name__ == "__main__":
    main()
