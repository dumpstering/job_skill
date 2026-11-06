"""Test job/scripts/answers.py: fill plans, computed years, the daily question
batch, the bank (add, migrate) and unparking.

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


def test_parse_questions():
    qs = answers.parse_questions((FIXTURE / "gh_questions_posting.md").read_text(encoding="utf-8"))
    assert len(qs) == 19 and sum(q.voluntary for q in qs) == 2
    q = qs[7]
    assert q.label == "Are you legally authorized to work in the United States?" and q.required
    assert q.qtype == "multi_value_single_select" and q.options == ["Yes", "No"], q
    assert answers.letter_required((FIXTURE / "posting_qpcr.md").read_text(encoding="utf-8"))
    assert not answers.letter_required((FIXTURE / "gh_questions_posting.md").read_text(encoding="utf-8"))
    print("ok posting.md questions parse")


def main():
    root = test_plan()
    test_plan_page_form(root)
    test_years(root)
    test_questions()
    test_bank_add_and_migrate()
    test_unpark()
    test_parse_questions()
    print("PASS")


if __name__ == "__main__":
    main()
