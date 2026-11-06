"""Test job/scripts/answers.py: fill plans, computed years, the daily question
batch, the bank (add, migrate, list, the any/US/Canada scope) and unparking.

Usage: python tests/test_answers.py
Prints "ok <name>" per test and PASS last. Exits non-zero on the first failure.
Uses the synthetic JobSearch root in tests/fixtures/variant_example (copied).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
SCRIPT = SCRIPTS / "answers.py"
FIXTURE = HERE / "fixtures" / "variant_example"
NOW = "2026-11-06T14:30:00Z"
DAY = "2026-11-06"
os.environ["JOB_SKILL_NOW"] = NOW
sys.path.insert(0, str(SCRIPTS))
import answers  # noqa: E402
import jobfs  # noqa: E402

GH = "2026-11-06 Acme Bio - QC Microbiologist II"


def fresh_root():
    root = Path(tempfile.mkdtemp(prefix="job_skill_answers_")) / "root"
    shutil.copytree(FIXTURE, root)
    return root


def cli(*argv):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, argv)], capture_output=True, text=True,
                       env={**os.environ, "JOB_SKILL_NOW": NOW})
    return r.returncode, r.stdout + r.stderr


def job(root, name, posting="gh_questions_posting.md"):
    folder = root / "applications" / name
    folder.mkdir(parents=True, exist_ok=True)
    text = posting if "\n" in posting else (FIXTURE / posting).read_text(encoding="utf-8")
    (folder / "posting.md").write_text(text, encoding="utf-8")
    return folder


def field(plan, start):
    hits = [f for f in plan["fields"] if f["label"].startswith(start)]
    assert len(hits) == 1, (start, [f["label"] for f in plan["fields"]])
    return hits[0]


def test_plan():
    root = fresh_root()
    folder = job(root, GH)
    code, out = cli("plan", "--root", root, "--job", folder, "--family", "qc_micro")
    assert code == 0, out
    assert out.startswith("PLAN fields=19 ok=13 needs_you=1 sensitive_confirm=2 check=3 (greenhouse_api)"), out
    plan = jobfs.read_json_verified(folder / "fill_plan.json", retries=0)
    assert plan["form_source"] == "greenhouse_api" and "_sha256" in plan
    for f in plan["fields"]:
        assert set(f) >= {"label", "name", "type", "required", "answer", "answer_source", "sensitive", "user_part", "status"}, f
        if f["answer"]:
            assert f["answer_source"], f"filled with no source: {f}"
            assert f["answer_source"].split(":")[0] in ("profile", "answers", "library", "computed", "user"), f
    assert field(plan, "First Name")["answer"] == "Jordan" and field(plan, "First Name")["answer_source"] == "profile:first_name"
    assert field(plan, "Are you legally authorized")["answer"] == "Yes"
    spons = field(plan, "Will you now or in the future require visa sponsorship")
    assert spons["status"] == "sensitive_confirm" and spons["answer"] == "No" and spons["sensitive"], spons
    salary = field(plan, "What are your salary expectations")
    assert salary["status"] == "sensitive_confirm" and salary["answer"] == "$95,000-$110,000", salary
    assert salary["answer_source"] == "profile:salary"
    crim = field(plan, "Have you ever been convicted")
    assert crim["answer"] == "" and crim["answer_source"] == "" and crim["user_part"] and crim["status"] == "check", crim
    cert = field(plan, "I certify")
    assert cert["answer"] == "" and cert["user_part"], cert
    raw = (folder / "fill_plan.json").read_text(encoding="utf-8")
    assert "convicted" not in (root / "answers.md").read_text(encoding="utf-8"), "never stored in the bank"
    years = field(plan, "How many years of qPCR")
    assert years["answer"] == "16" and years["answer_source"] == "computed:years(real-time PCR)" and years["status"] == "check"
    bsl = field(plan, "Have you worked in a BSL-2 lab?")
    assert bsl["answer"] == "Yes" and bsl["answer_source"] == "answers:ANS-002", bsl
    unknown = field(plan, "Describe your experience with endotoxin")
    assert unknown["status"] == "needs_you" and unknown["answer"] == "" and unknown["qid"].startswith("Q-"), unknown
    assert field(plan, "How did you hear")["answer_source"] == "answers:ANS-001"
    why = field(plan, "Why Acme Bio?")
    assert why["answer"] == "I want to bring my GMP QC microbiology work to the QC Microbiologist II role at Acme Bio.", why
    assert why["answer_source"] == "answers:ANS-003"
    vet = field(plan, "VeteranStatus")
    assert vet["answer"] == "I don't wish to answer" and vet["answer_source"] == "profile:veteran", vet
    assert field(plan, "What is your gender identity?")["answer"] == "", "no decline option: left blank"
    assert field(plan, "Resume/CV")["user_part"] and field(plan, "Resume/CV")["type"] == "file"
    assert "never filled or stored" in raw, "the log says why"
    # Without the family the template is not used, and the optional question stays blank.
    p2 = answers.plan(root, folder, family="other_family", write=False)
    assert field(p2, "Why Acme Bio?")["answer"] == ""
    print("ok plan: a source on every value; criminal history never filled; salary sensitive_confirm; unknown needs_you")
    return root


def test_plan_page_form(root):
    posting = (FIXTURE / "posting_16s.md").read_text(encoding="utf-8")
    folder = job(root, "2026-11-06 Lakeside Microbiome - Scientist", posting)
    plan = answers.plan(root, folder, write=False)
    assert plan["form_source"] == "page", plan["form_source"]
    labels = {f["label"]: f for f in plan["fields"]}
    assert labels["Email"]["answer"] == "jordan.q.avery@example.com"
    assert labels["Location"]["answer"] == "Springfield, IL" and labels["Location"]["answer_source"] == "profile:location"
    assert labels["Desired salary"]["status"] == "sensitive_confirm"
    assert all(f["answer"] and f["answer_source"] for f in plan["fields"]), "page plans list only known values"
    print("ok plan for a page form: profile contact and eligibility fields")


def test_years(root):
    code, out = cli("years", "--root", root, "--skill", "qPCR")
    assert code == 0, out
    assert out.strip() == "YEARS real-time PCR 16 (192 months: May 2007 - Apr 2023; from L-0004, L-0010, L-0014)", out
    y = answers.years(root, "real-time PCR")
    assert y["years"] == 16 and y["months"] == 192
    tech = root / "techniques.md"
    tech.write_text(tech.read_text(encoding="utf-8").replace(
        "real-time PCR | aliases: qPCR; quantitative PCR | level: hands-on | years_ok: yes",
        "real-time PCR | aliases: qPCR; quantitative PCR | level: hands-on | years_ok: no"), encoding="utf-8")
    code, out = cli("years", "--root", root, "--skill", "qPCR")
    assert code == 1 and out.strip() == "REFUSED years qPCR: techniques.md marks years_ok: no", out
    plan = answers.plan(root, root / "applications" / GH, family="qc_micro", write=False)
    assert field(plan, "How many years of qPCR")["status"] == "needs_you", "no computed value: a question, never a guess"
    code, out = cli("years", "--root", root, "--skill", "Western blot")
    assert code == 1 and "not in techniques.md" in out, out
    # The counting rule: both end months, overlaps once, gaps out, rounded down; year-only dates taken shortest.
    today = jobfs.today(root)
    assert answers.span_of("May 2007 - Apr 2023", today) == (2007 * 12 + 4, 2023 * 12 + 3)
    assert answers.span_of("2016 - 2019", today) == (2016 * 12 + 11, 2019 * 12)
    assert answers.span_of("Jun 2023 - Present", today) == (2023 * 12 + 5, 2026 * 12 + 10)
    print("ok years: qPCR for May 2007 - Apr 2023 is 16; refused when years_ok is no")


def write_plan(folder, needs=(), sensitive=()):
    fields = [{"label": q, "name": answers.slug(q), "type": "text", "required": True, "answer": "",
               "answer_source": "", "sensitive": False, "user_part": False, "status": "needs_you",
               "qid": answers.question_id(q)} for q in needs]
    fields += [{"label": "What are your salary expectations?", "name": "salary", "type": "text", "required": True,
                "answer": v, "answer_source": "profile:salary", "sensitive": True, "user_part": False,
                "status": "sensitive_confirm"} for v in sensitive]
    folder.mkdir(parents=True, exist_ok=True)
    jobfs.atomic_write_json(folder / "fill_plan.json", {"form_source": "page", "fields": fields, "log": []})


def test_questions():
    root = fresh_root()
    bank = root / "answers.md"
    bank.write_text("\n".join(ln for ln in bank.read_text(encoding="utf-8").splitlines() if "ANS-002" not in ln) + "\n",
                    encoding="utf-8")
    apps = root / "applications"
    salary = "$95,000-$110,000"
    plans = {
        1: (["Describe your experience with endotoxin testing in a GMP lab."], [salary]),
        2: (["Do you have BSL-3 experience?"], []),
        4: (["Do you have BSL-2 experience?"], [salary]),
        5: (["Do you have  BSL-2 experience?", "How did you hear about us?"], [salary]),
        6: (["Describe your experience with endotoxin testing in a GMP lab"], []),
        7: (["Are you comfortable working weekends?"], []),
        8: (["Can you lift 25 pounds unassisted?"], []),
        9: (["Which LIMS platforms have you used?"], []),
        10: (["Have you worked in an ISO 5 cleanroom?"], []),
    }
    items = []
    for n in range(1, 11):
        folder = apps / f"{DAY} Example {n} - Analyst"
        items.append({"n": n, "job_key": f"greenhouse:90000{n:02d}", "folder": f"applications/{folder.name}",
                      "family": "qc_micro", "tier": "A" if n == 3 else "B"})
        if n in plans:
            write_plan(folder, *plans[n])
        else:
            folder.mkdir(parents=True, exist_ok=True)
    (apps / f"{DAY} Example 3 - Analyst" / "questions.json").write_text(json.dumps([
        {"q": "Have you worked in a BSL-2 lab? Where, and about how long?", "also": ["Do you have BSL-2 experience?"]},
        {"q": "Which qPCR instruments have you run?"},
        {"q": "A third Tier A question that is never asked?"}]), encoding="utf-8")
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), {"v": 1, "date": DAY, "items": items})
    v1 = root / "variants" / "qc_micro" / "v1"
    (root / "variants" / "qc_micro" / "current.txt").write_text("v1\n", encoding="utf-8")
    (v1 / "questions.json").write_text(json.dumps([{"q": "Any GMP audit experience to add to the variant?"}]), encoding="utf-8")

    code, out = cli("questions", "--root", root, "--date", DAY)
    assert code == 0, out
    lines = out.strip().splitlines()
    assert lines[0] == "QUESTIONS 6 (10 jobs wait)", lines
    assert lines[1].startswith("1. Have you worked in a BSL-2 lab? Where, and about how long? (3 jobs: #3, #4, #5) [Q-"), lines
    assert lines[2].startswith("2. Describe your experience with endotoxin testing in a GMP lab. (2 jobs: #1, #6)"), lines
    assert lines[3].startswith("3. Which qPCR instruments have you run? (1 job: #3)"), "Tier A first among equal counts"
    assert any("Do you have BSL-3 experience? (1 job: #2)" in ln for ln in lines), "a different question stays apart"
    assert not any("How did you hear" in ln for ln in lines), "answered in the bank: not asked"
    assert not any("third Tier A" in ln for ln in lines), "at most 2 per Tier A job"
    assert not any("GMP audit" in ln for ln in lines), "a variant gap unblocks no job and ranks last"
    assert lines[-1] == f'SENSITIVE salary "{salary}" on #1, #4, #5', lines[-1]
    numbered = [ln for ln in lines if ln[:1].isdigit()]
    assert len(numbered) == 6
    result = answers.questions(root, DAY, cap=20)
    counts = [len(q["jobs"]) for q in result["questions"]]
    assert counts == sorted(counts, reverse=True), counts
    assert result["questions"][-1]["origin"] == "variant", result["questions"][-1]
    code, out = cli("questions", "--root", root, "--date", DAY, "--json")
    data = json.loads(out)
    assert data["questions"][0]["jobs"] == [3, 4, 5] and data["questions"][0]["tier_a"] and data["waiting"] == 10, data
    assert "Do you have BSL-2 experience?" in data["questions"][0]["also"]
    # Similarity: the same question reworded slightly merges; a different meaning does not.
    assert answers.same_question("Do you have BSL-2 experience?", "Do you have BSL-2 lab experience?")
    assert not answers.same_question("Do you have BSL-2 experience?", "Do you have BSL-3 experience?")
    assert not answers.same_question("Do you have experience with qPCR?", "Do you have experience with ELISA?")
    assert not answers.same_question("Do you have at least 3 years of GMP QC microbiology experience?",
                                     "Do you have at least 5 years of GMP QC microbiology experience?")
    print("ok questions: aliases merged, meanings kept apart, at most 6 ranked by jobs unblocked, sensitive line")


def test_bank_add_and_migrate():
    root = fresh_root()
    code, out = cli("add", "--root", root, "--question", "Have you ever been convicted of a crime?", "--answer", "No")
    assert code == 1 and out.startswith("REFUSED never stored"), out
    assert "convicted" not in (root / "answers.md").read_text(encoding="utf-8")
    code, out = cli("add", "--root", root, "--question", "Are you comfortable working weekends?", "--answer", "Yes",
                    "--kind", "yesno", "--also", "Can you work weekends?")
    assert code == 0 and out.strip() == "ADDED ANS-004", out
    line = [ln for ln in (root / "answers.md").read_text(encoding="utf-8").splitlines() if "ANS-004" in ln][0]
    assert line == ("- ANS-004 | Are you comfortable working weekends? | Yes | yesno | any | no | user:2026-11-06 | "
                    "2026-11-06 | also: Can you work weekends?"), line
    assert list((root / "backups").glob("answers_*.md")), "backup first"
    code, out = cli("add", "--root", root, "--question", "Can you work weekends?", "--answer", "Yes, most weekends",
                    "--kind", "text")
    assert code == 0 and out.strip() == "UPDATED ANS-004", out
    code, out = cli("add", "--root", root, "--question", "What is your expected hourly pay?", "--answer", "$48")
    assert code == 0, out
    assert "| yes | user:2026-11-06 |" in [ln for ln in (root / "answers.md").read_text(encoding="utf-8").splitlines()
                                          if "hourly pay" in ln][0], "pay questions are always sensitive"
    code, out = cli("add", "--root", root, "--question", "x", "--answer", "a | b")
    assert code == 1 and "cannot contain |" in out, out
    code, out = cli("add", "--root", root, "--question", "y", "--answer", "z", "--source", "chat")
    assert code == 1 and "is not profile:" in out, out
    # v1.1 Standard answers move into the bank.
    profile = root / "profile.md"
    profile.write_text(profile.read_text(encoding="utf-8") +
                       "- Are you willing to work night shifts? | No | any | no\n- How did you hear about us? |  | any | no\n",
                       encoding="utf-8")
    code, out = cli("migrate", "--root", root)
    assert code == 0 and "ADDED ANS-006 Are you willing to work night shifts?" in out and "SKIPPED 1 blank" in out, out
    print("ok bank: add, update by alias, sensitive pay, refusals, v1.1 migration")


def test_unpark():
    root = fresh_root()
    folder = job(root, GH)
    answers.plan(root, folder, family="qc_micro")
    item = {"n": 7, "job_key": "greenhouse:4800002", "folder": f"applications/{GH}", "family": "qc_micro", "tier": "B"}
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), {"v": 1, "date": DAY, "items": [item]})
    code, out = cli("unpark", "--root", root, "--date", DAY)
    assert code == 0 and out.strip() == "UNPARK n=0", out
    code, out = cli("add", "--root", root, "--question", "Describe your experience with endotoxin testing in a GMP lab.",
                    "--answer", "LAL kinetic chromogenic testing on about 300 samples a month at Northwind Biologics.")
    assert code == 0, out
    code, out = cli("unpark", "--root", root, "--date", DAY)
    assert code == 0 and out.splitlines()[0].startswith("UNPARKED #7 greenhouse:4800002 (Q-"), out
    events = list((root / "runner" / "in" / "events").glob("*_unparked.json"))
    assert len(events) == 1, events
    ev = json.loads(events[0].read_text(encoding="utf-8"))
    assert ev["type"] == "unparked" and ev["n"] == 7 and ev["date"] == DAY and ev["v"] == jobfs.EVENT_SCHEMA, ev
    assert ev["id"] == events[0].name[:-len("_unparked.json")] and ev["at"].startswith("2026-11-06T06:30:00")
    assert ev["payload"]["question_ids"] == [answers.question_id("Describe your experience with endotoxin testing in a GMP lab.")]
    code, out = cli("unpark", "--root", root, "--date", DAY)
    assert out.strip() == "UNPARK n=0", "an unparked job is not unparked twice"
    print("ok unpark: an answered job gets one unparked event")


EVENTS = SCRIPTS / "events.py"


def emit(root, typ, n, key, payload=None):
    body = {"payload": payload or {"confirmation": "page"} if typ == "applied" else payload or {},
            "job_key": key, "n": n, "date": DAY}
    r = subprocess.run([sys.executable, str(EVENTS), "emit", "--root", str(root), "--type", typ, "--json", json.dumps(body)],
                       capture_output=True, text=True, env={**os.environ, "JOB_SKILL_NOW": NOW})
    assert r.returncode == 0 and r.stdout.startswith("EVENT "), r.stdout + r.stderr


def test_unpark_final_states():
    """contracts-02: an applied, later, skipped, blocked or handed-over job is never unparked."""
    root = fresh_root()
    items = []
    for n, state in enumerate(("applied", "later", "skipped", "handed_over", "parked", None), 1):
        name = f"{GH} {n}"
        folder = job(root, name)
        answers.plan(root, folder, family="qc_micro")
        key = f"greenhouse:48000{n}"
        items.append({"n": n, "job_key": key, "folder": f"applications/{name}", "family": "qc_micro", "tier": "B"})
        if state == "parked":
            emit(root, "parked", n, key, {"reason": "unknown_required", "confirmation": "none"})
        elif state:
            emit(root, "parked", n, key, {"reason": "unknown_required", "confirmation": "none"})
            emit(root, state, n, key, {"confirmation": "page"} if state == "applied" else {"confirmation": "none"})
    jobfs.atomic_write_json(jobfs.Folder(root).queue_json(DAY), {"v": 1, "date": DAY, "items": items})
    code, out = cli("add", "--root", root, "--question", "Describe your experience with endotoxin testing in a GMP lab.",
                    "--answer", "LAL kinetic chromogenic testing on about 300 samples a month at Northwind Biologics.")
    assert code == 0, out
    code, out = cli("unpark", "--root", root, "--date", DAY)
    got = sorted(ln.split()[1] for ln in out.splitlines() if ln.startswith("UNPARKED"))
    assert got == ["#5", "#6"], out
    print("ok unpark: only parked jobs, or jobs with no state yet, come back")


def test_profile_rules_and_years():
    """TR-01 / TR-02: an experience question never takes an eligibility value, and
    computed years answer only a question about that technique alone."""
    root = fresh_root()
    posting = ("Company: Acme Bio\nRole: QC Microbiologist II\nLocation: Springfield, IL\nSource: greenhouse-api\n\n"
               "## Application questions\n\n"
               "1. Do you have at least 18 months of experience in a GMP QC laboratory? (required) - multi_value_single_select: Yes; No\n"
               "2. Do you have hands-on experience with drug testing by LC-MS/MS? (required) - multi_value_single_select: Yes; No\n"
               "3. Have you worked directly with clinical trial sponsors? (required) - multi_value_single_select: Yes; No\n"
               "4. Do you have at least 10 years of qPCR experience in a GMP-regulated laboratory? (required) - multi_value_single_select: Yes; No\n"
               "5. Do you have at least 5 years of experience with qPCR and flow cytometry? (required) - multi_value_single_select: Yes; No\n"
               "6. Do you have at least 10 years of qPCR experience? (required) - multi_value_single_select: Yes; No\n"
               "7. Are you 18 years of age or older? (required) - multi_value_single_select: Yes; No\n"
               "8. Will you now or in the future require visa sponsorship? (required) - multi_value_single_select: Yes; No\n"
               "9. Are you willing to undergo a drug screen? (required) - multi_value_single_select: Yes; No\n")
    folder = job(root, "2026-11-06 Acme Bio - QC Microbiologist II", posting)
    p = answers.plan(root, folder, "qc_micro", write=False)
    st = [(f["status"], f["answer"], f["answer_source"]) for f in p["fields"]]
    for i in range(5):
        assert st[i][0] == "needs_you" and not st[i][1], (p["fields"][i]["label"], st[i])
    assert st[5] == ("check", "Yes", "computed:years(real-time PCR)"), st[5]
    assert p["fields"][5]["note"].startswith("YEARS real-time PCR 16 (192 months"), p["fields"][5]
    assert st[6] == ("ok", "Yes", "profile:adult"), st[6]
    assert st[7][0] == "sensitive_confirm" and st[7][2] == "profile:sponsorship", st[7]
    assert st[8][2] == "profile:background", st[8]
    assert any("not \"flow cytometry\"" in ln for ln in p["log"]), p["log"]
    code, out = cli("plan", "--root", root, "--job", folder, "--family", "qc_micro", "--dry-run")
    assert 'CHECK "Do you have at least 10 years of qPCR experience?" = "Yes" (computed:years(real-time PCR)) YEARS' in out, out
    print("ok experience questions park; computed years only for the technique alone, with the arithmetic")


def test_bank_strict_and_sensitive():
    """TR-03 / TR-08: a yes/no answer settles only the same question, an add never
    overwrites a different one, and every pay or visa answer needs the day's yes."""
    root = fresh_root()
    prof = root / "profile.md"
    prof.write_text(prof.read_text(encoding="utf-8").replace(
        "## Standard answers\n", "## Standard answers\n- What is your expected hourly rate? | $55 per hour | any | yes\n", 1),
        encoding="utf-8")
    posting = ("Company: Acme Bio\nRole: QC Microbiology Supervisor\nLocation: Springfield, IL\nSource: greenhouse-api\n\n"
               "## Application questions\n\n"
               "1. Have you worked in a BSL-2 lab as the lab supervisor? (required) - multi_value_single_select: Yes; No\n"
               "2. Have you worked in BSL-2 labs? (required) - multi_value_single_select: Yes; No\n"
               "3. What is your expected hourly rate? (required) - input_text\n"
               "4. What is your expected annual remuneration? (required) - input_text\n")
    folder = job(root, "2026-11-06 Acme Bio - QC Microbiology Supervisor", posting)
    (folder / "notes.md").write_text("## Answers\n\n- JOB-1 | Expected hourly rate for this contract | $60 | text | any | yes | "
                                     "user:2026-11-05 | 2026-11-05\n", encoding="utf-8")
    p = answers.plan(root, folder, "qc_micro", write=False)
    assert field(p, "Have you worked in a BSL-2 lab as")["status"] == "needs_you", p
    assert field(p, "Have you worked in BSL-2 labs?")["answer"] == "Yes", "a plural is the same question"
    hourly = field(p, "What is your expected hourly rate?")
    assert hourly["status"] == "sensitive_confirm" and hourly["answer"] == "$55 per hour", hourly
    assert field(p, "What is your expected annual")["sensitive"], "remuneration is pay"
    code, out = cli("add", "--root", root, "--question", "Have you worked in a BSL-2 lab as the lab supervisor?",
                    "--answer", "No", "--kind", "yesno")
    assert code == 0 and out.strip() == "ADDED ANS-004", out
    bank = (root / "answers.md").read_text(encoding="utf-8")
    assert "- ANS-002 | Do you have BSL-2 experience? | Yes |" in bank, "ANS-002 kept its answer"
    code, out = cli("add", "--root", root, "--question", "What is your expected annual remuneration?", "--answer", "$105,000")
    assert code == 0 and "| $105,000 | text | any | yes |" in (root / "answers.md").read_text(encoding="utf-8")
    p = answers.plan(root, folder, "qc_micro", write=False)
    assert field(p, "What is your expected annual")["status"] == "sensitive_confirm", p
    assert field(p, "Have you worked in a BSL-2 lab as")["answer"] == "No", p
    # A job's own JOB-n line keeps its sensitive flag.
    q = answers.Planner(root, folder, "qc_micro")
    got = q.field("Expected hourly rate for this contract", "input_text", True)
    assert got["status"] == "sensitive_confirm" and got["answer"] == "$60", got
    print("ok yes/no answers match exactly; pay answers always ask the day's yes")


def test_letter_missing():
    """SJ-06: a required cover letter with none built is named by plan."""
    root = fresh_root()
    folder = job(root, "2026-11-06 Acme Bio - QC Microbiologist II", "posting_qpcr.md")
    code, out = cli("plan", "--root", root, "--job", folder, "--dry-run")
    assert 'LETTER "Cover Letter" is required and no cover letter is built in this job folder' in out, out
    (folder / "cover_B.pdf").write_bytes(b"%PDF-1.4\n")
    code, out = cli("plan", "--root", root, "--job", folder, "--dry-run")
    assert "LETTER" not in out, out
    code, out = cli("plan", "--root", root)
    assert code == 1 and out.startswith("ERROR "), out
    print("ok plan names a required cover letter that is not built; bad arguments exit 1")


def test_parse_questions():
    qs = answers.parse_questions((FIXTURE / "gh_questions_posting.md").read_text(encoding="utf-8"))
    assert len(qs) == 19 and sum(q.voluntary for q in qs) == 2
    q = qs[7]
    assert q.label == "Are you legally authorized to work in the United States?" and q.required
    assert q.qtype == "multi_value_single_select" and q.options == ["Yes", "No"], q
    assert answers.letter_required((FIXTURE / "posting_qpcr.md").read_text(encoding="utf-8"))
    assert not answers.letter_required((FIXTURE / "gh_questions_posting.md").read_text(encoding="utf-8"))
    print("ok posting.md questions parse")


def test_scope():
    """Scope is any, US or Canada (any case). CA is refused for a new answer (a
    California user reads it as the state); a CA already in answers.md still
    means Canada, and list names that line in a WARN."""
    root = fresh_root()
    bank = root / "answers.md"
    code, out = cli("add", "--root", root, "--question", "Are you legally able to work here?", "--answer", "Yes",
                    "--scope", "CA")
    assert code == 1 and out.strip() == ('REFUSED scope "CA" could mean California or Canada: '
                                         "write --scope US or --scope Canada"), out
    assert "legally able" not in bank.read_text(encoding="utf-8")
    code, out = cli("add", "--root", root, "--question", "Do you have a work permit?", "--answer", "No",
                    "--scope", "GB")
    assert code == 1 and out.strip() == 'REFUSED scope "GB": write any, US or Canada', out
    for scope, stored in (("canada", "Canada"), ("us", "US"), ("ANY", "any")):
        q = f"Question for {stored}?"
        code, out = cli("add", "--root", root, "--question", q, "--answer", "Yes", "--scope", scope)
        assert code == 0 and out.startswith("ADDED"), out
        line = next(ln for ln in bank.read_text(encoding="utf-8").splitlines() if q in ln)
        assert f"| Yes | text | {stored} | no |" in line, line
    text = bank.read_text(encoding="utf-8")
    legacy = "- ANS-090 | Are you eligible to work in Canada? | Yes | yesno | CA | no | user:2026-10-01 | 2026-10-01"
    bank.write_text(text + legacy + "\n", encoding="utf-8")
    line_no = len((text + legacy).splitlines())
    parsed = answers.load_bank(bank)
    assert answers.find_answer("Are you eligible to work in Canada?", parsed, "CA").id == "ANS-090"
    assert answers.find_answer("Are you eligible to work in Canada?", parsed, "US") is None
    assert answers.find_answer("Question for Canada?", parsed, "CA") is not None
    code, out = cli("list", "--root", root)
    lines = out.splitlines()
    assert code == 0 and lines[-1] == f"ANSWERS n={len(parsed)}", out
    assert "ANS-090 | CA | sensitive no | Are you eligible to work in Canada? = Yes" in lines, out
    warns = [ln for ln in lines if ln.startswith("WARN")]
    assert warns == [f'WARN answers.md line {line_no}: scope "CA" is read as Canada; write US or Canada'], warns
    # v1.1 Standard answers with CA (Canada then) migrate as Canada
    profile = root / "profile.md"
    profile.write_text(profile.read_text(encoding="utf-8") + "- Can you work in Toronto? | Yes | CA | no\n",
                       encoding="utf-8")
    code, out = cli("migrate", "--root", root)
    assert code == 0, out
    line = next(ln for ln in bank.read_text(encoding="utf-8").splitlines() if "Toronto" in ln)
    assert "| Canada |" in line, line
    print("ok scope: any, US, Canada; CA refused, a legacy CA read as Canada and named by list")


def main():
    root = test_plan()
    test_plan_page_form(root)
    test_years(root)
    test_questions()
    test_bank_add_and_migrate()
    test_scope()
    test_unpark()
    test_unpark_final_states()
    test_profile_rules_and_years()
    test_bank_strict_and_sensitive()
    test_letter_missing()
    test_parse_questions()
    print("PASS")


if __name__ == "__main__":
    main()
