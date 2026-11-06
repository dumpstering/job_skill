"""Test job/scripts/checks.py: unit cases, then an end-to-end build of the samples.

Usage: python tests/test_checks.py [out_dir]
Needs python-docx, reportlab and pypdf. Exits non-zero on the first failure.
"""
import copy
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "job" / "scripts"
SCRIPT = SCRIPTS / "build_resume.py"
SOURCES = sorted((HERE / "sample_sources").glob("*.md"))
TERMS = HERE / "sample_terms.json"
sys.path.insert(0, str(SCRIPTS))
import checks  # noqa: E402

D = Decimal


def keys(text):
    return [k for _, k in checks.numbers(text)]


def test_numbers():
    assert keys("$550K") == [("plain", D(550000))]
    assert checks.source_numbers("$550,000") & {("plain", D(550000))}
    assert keys("$550K")[0] == keys("$550,000")[0]
    assert keys("40%")[0] == keys("40 percent")[0] == keys("40-percent")[0] == ("percent", D(40))
    assert keys("$1.2M")[0] == ("plain", D(1200000)) == keys("1,200,000")[0]
    assert keys("2.5 million")[0] == ("plain", D(2500000))
    assert keys("3 billion")[0] == keys("3bn")[0] == keys("$3B")[0] == ("plain", D(3 * 10**9))
    assert keys("10 thousand")[0] == keys("10k")[0] == ("plain", D(10000))
    assert keys("200+")[0] == ("plain", D(200))
    assert checks.numbers("over 200+ users")[0][0] == "200+"
    assert keys("3.5 hours") == [("plain", D("3.5"))]
    assert keys("3x faster") == [("times", D(3))]
    # Not numbers: ordinals, product codes, units glued to digits, a trailing period.
    assert keys("the 3rd and 1st time") == []
    assert keys("S3 and EC2 and 24x7") == []
    assert keys("a 10.5pt font") == []
    assert keys("5 months") == [("plain", D(5))]
    assert keys("resolved 40.") == [("plain", D(40))]
    # Source side: words one..twenty, a dozen, ten percent.
    src = checks.source_numbers("six technicians, a dozen hires, two dozen seats, ten percent, three-year")
    for want in [("plain", D(6)), ("plain", D(12)), ("plain", D(24)), ("percent", D(10)), ("plain", D(3))]:
        assert want in src, want
    assert ("plain", D(450)) not in src
    print("PASS  numbers")


def test_dates():
    f = checks.date_in_sources
    for form in ["Mar 2021", "March 2021", "Mar. 2021", "03/2021", "3/2021", "2021-03", "March, 2021", "mar 2021"]:
        assert f("Mar 2021", f"worked here since {form} until now") is True, form
    assert f("Sep 2020", "joined Sept 2020") is True
    assert f("Mar 2021", "March 2022 and 13/2021 and 2021-031") is False
    assert f("Mar 2021", "Mar 2020") is False
    assert f("March 2021", "03/2021") is True
    assert f("2019", "the 2019 refresh") is True
    assert f("2019", "12019 and 20190") is False
    assert f("Present", "") is None
    assert f("Spring 2021", "In spring 2021 we") is True
    assert checks.date_parts("Mar 2021 - Present") == ["Mar 2021", "Present"]
    assert checks.date_parts("Jun 2016 " + checks.EN_DASH + " Feb 2021") == ["Jun 2016", "Feb 2021"]
    assert checks.date_parts("2016-2019") == ["2016", "2019"]
    assert checks.date_parts("2021-03") == ["2021-03"]
    print("PASS  dates")


def test_keywords():
    t = checks.term_found
    assert not t("JavaScript and TypeScript", "Java")
    assert t("Java, SQL", "Java")
    assert t("Java developer", "java")
    assert t("wrote C# daily", "C#")
    assert t("C#/.NET", "C#") and t("C#/.NET", ".NET")
    assert not t("ASP.NET", ".NET")
    assert t("C++ and Go", "C++")
    assert not t("ITILv4", "ITIL")
    assert t("several tickets", "ticket") and t("two boxes", "box")
    assert not t("Linux", "Lin")
    assert t("Azure AD admin", "Entra ID", ["Azure AD"])
    assert t("Entra  ID admin", "Entra ID")
    assert not t("nothing here", "Entra ID", ["Azure AD"])
    # No plural suffix after a symbol: "C#s" is not C#.
    assert not t("C#s", "C#")
    lines = checks.keyword_check("Intune", None, [("Intune", []), ("Java", [])], False)
    assert lines[1].endswith("(no --sources given)") and lines[2].startswith("KEYWORD not evidenced: Java")
    print("PASS  keywords")


def run(args, out, base):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args), "--out", str(out), "--base", base],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"build exited {r.returncode}: {r.stdout}{r.stderr}"
    return r.stdout


def lines_with(log, prefix):
    return [ln for ln in log.splitlines() if ln.startswith(prefix)]


def test_end_to_end(out):
    log = run([HERE / "sample_resume.json", "--sources", *SOURCES, "--terms", TERMS], out, "Jordan Avery Resume")
    print(log, end="")
    facts = lines_with(log, "FACT")
    assert len(facts) == 1, facts
    assert facts[0] == ('FACT number "450" in Experience / Desktop Support Technician bullet 2 is not in your '
                        "sources (if derived, list it under Verify)"), facts[0]
    assert "FACT date" not in log, "03/2021, June 2016, Feb. 2021 and 2016-05 should all be found"
    assert "FACT title" not in log and "FACT employer" not in log
    assert not [f for f in facts if '"6"' in f or '"12"' in f or '"4"' in f or '"5"' in f], "number words missed"
    assert lines_with(log, "PARSE") == ["PARSE ok (18 checks)"], lines_with(log, "PARSE")
    assert lines_with(log, "KEYWORD") == [
        "KEYWORD on resume: Entra ID, ITIL, Intune",
        "KEYWORD in your sources, not on this resume: C#, Power BI",
        "KEYWORD not evidenced: .NET, Java, ServiceNow",
    ], lines_with(log, "KEYWORD")
    # Order: WARN lines first, then PARSE, FACT, KEYWORD.
    tags = [ln.split()[0] for ln in log.splitlines() if ln.split()[0] in ("WARN", "PARSE", "FACT", "KEYWORD")]
    assert tags == sorted(tags, key=["WARN", "PARSE", "FACT", "KEYWORD"].index), tags

    # Without the options only PARSE runs; with terms alone the sources bucket says so.
    log = run([HERE / "sample_resume.json"], out, "plain")
    assert not lines_with(log, "FACT") and not lines_with(log, "KEYWORD") and lines_with(log, "PARSE")
    log = run([HERE / "sample_resume.json", "--terms", TERMS], out, "plain")
    assert "KEYWORD in your sources, not on this resume: (no --sources given)" in log

    # A letter: PARSE checks the name, email and paragraph starts; FACT checks its numbers.
    log = run([HERE / "sample_letter.json", "--sources", *SOURCES], out, "Jordan Q. Avery Cover Letter")
    print(log, end="")
    assert lines_with(log, "PARSE") == ["PARSE ok (7 checks)"], lines_with(log, "PARSE")
    assert lines_with(log, "FACT") == ["FACT ok (8 checked)"], lines_with(log, "FACT")
    print("PASS  end to end")


def test_parse_failures(out):
    pdf = out / "Jordan Avery Resume.pdf"
    data = json.loads((HERE / "sample_resume.json").read_text(encoding="utf-8"))
    data.setdefault("contact", [])
    assert checks.parse_back(data, pdf) == ["PARSE ok (18 checks)"]

    bad = copy.deepcopy(data)
    bad["name"] = "Jordan Averie"
    bad["sections"].append({"heading": "Projects", "paragraph": "x"})
    bad["sections"][1]["entries"][0]["org"] = "Northwind Logistix"
    bad["sections"][1]["entries"][1]["dates"] = "Jun 2015 - Feb 2021"
    got = checks.parse_back(bad, pdf)
    assert 'PARSE name "Jordan Averie" not found in the PDF text' in got, got
    assert 'PARSE heading "Projects" not found in the PDF text' in got, got
    assert 'PARSE entry org "Northwind Logistix" of "IT Support Lead" not found in the PDF text' in got, got
    assert 'PARSE entry dates "Jun 2015 - Feb 2021" of "Desktop Support Technician" not found in the PDF text' in got, got

    swapped = copy.deepcopy(data)
    swapped["sections"][1]["entries"].reverse()
    got = checks.parse_back(swapped, pdf)
    assert any("is out of order" in g for g in got), got

    late = copy.deepcopy(data)
    late["contact"].append("late@example.com")
    got = checks.parse_back(late, pdf)
    assert 'PARSE contact "late@example.com" not found in the PDF text' in got, got

    real = checks.read_pdf_text
    try:
        checks.read_pdf_text = lambda p: "Jordan Avery"
        assert checks.parse_back(data, pdf) == ["PARSE FAIL no text layer"]
        checks.read_pdf_text = lambda p: real(p) + " (cid:12)" * 40
        got = checks.parse_back(data, pdf)
        assert got[0].startswith("PARSE WARN") or got[0].startswith("PARSE FAIL"), got
        checks.read_pdf_text = lambda p: "(cid:1)" * 200 + " Jordan Avery " * 10
        assert checks.parse_back(data, pdf)[0].startswith("PARSE FAIL"), "cid ratio over 5%"
        checks.read_pdf_text = lambda p: None
        assert checks.parse_back(data, pdf) == [checks.PARSE_SKIPPED]
    finally:
        checks.read_pdf_text = real
    print("PASS  parse failures")


# ---------- v1.2: EVIDENCE, CITE, LETTER, strict ----------

IT_SOURCES = [HERE / "sample_sources" / "master_resume.md", HERE / "sample_sources" / "achievement_library.md"]
VX = HERE / "fixtures" / "variant_example"
EN = chr(0x2013)
BETA = chr(0x03B2)


def strict(args, out, base):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args), "--out", str(out), "--base", base],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_ids_are_not_facts():
    assert checks.strip_ids("- [L-0450] 3 hires [P-120]") == "-   3 hires  "
    lib_text = (HERE / "sample_sources" / "achievement_library.md").read_text(encoding="utf-8")
    assert ("plain", D(7)) in checks.source_numbers(lib_text), "the raw text would count L-0007 as 7"
    assert ("plain", D(7)) not in checks.source_numbers(checks.strip_ids(lib_text))
    print("ok library IDs never count as facts")


def test_evidence_units():
    entries = {"L-0001": "open tickets went from 410 down to 120 in five months", "L-0002": "a dozen hires"}
    data = {"name": "A", "contact": [], "sections": [
        {"heading": "Experience", "entries": [{"title": "Lead", "bullets": [
            {"text": "Cut backlog from 410 to 120 in 5 months.", "ev": ["L-0001"]},
            {"text": "Trained 12 new hires.", "ev": ["L-0002"]},
            {"text": "Trained 13 new hires.", "ev": ["L-0002"]},
            {"text": "Ran things.", "ev": ["L-0099"]},
            {"text": "No tag at all."},
            "A v1.1 string bullet."]}]},
        {"heading": "Certifications", "bullets": ["ITIL 4 Foundation"]}]}
    got = checks.evidence_check(data, entries)
    assert got == ['EVIDENCE number "13" in Experience / Lead bullet 3 is not in L-0002',
                   "EVIDENCE unknown L-0099 in Experience / Lead bullet 4",
                   "EVIDENCE missing ev: Experience / Lead bullet 5",
                   "EVIDENCE missing ev: Experience / Lead bullet 6"], got
    assert checks.evidence_check(data, None) == ["EVIDENCE skipped: no achievement library with [L-] IDs in --sources"]
    assert checks.has_ev(data) and not checks.has_ev({"sections": [{"heading": "X", "bullets": ["a"]}]})
    print("ok EVIDENCE: ids exist, numbers in the cited entry, credential sections exempt")


def test_evidence_build(out):
    base = json.loads((HERE / "sample_resume_ev.json").read_text(encoding="utf-8"))
    # A number that is in the sources (FACT passes) but not in the cited entry.
    moved = copy.deepcopy(base)
    moved["sections"][1]["entries"][0]["bullets"][2]["text"] = (
        "Automated new-hire laptop setup with Intune Autopilot, reducing setup time from about 3 hours to 25 minutes per device.")
    path = out / "ev_moved.json"
    path.write_text(json.dumps(moved), encoding="utf-8")
    code, log = strict([path, "--sources", *IT_SOURCES, "--strict", "--mode", "industry"], out, "ev")
    assert code == 6, log
    fails = [ln for ln in log.splitlines() if ln.startswith("STRICT-FAIL")]
    assert fails == ['STRICT-FAIL EVIDENCE number "25" in Experience / IT Support Lead bullet 3 is not in L-0002'], fails
    assert "FACT ok" in log and log.rstrip().endswith("VERDICT fail"), log
    # A number in no source at all: FACT and EVIDENCE both fail.
    bad = copy.deepcopy(base)
    bad["sections"][1]["entries"][1]["bullets"][0]["text"] = (
        "Resolved hardware and software tickets for a 300-bed hospital, averaging 35 tickets a day.")
    path = out / "ev_bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    code, log = strict([path, "--sources", *IT_SOURCES, "--strict", "--mode", "industry"], out, "ev")
    assert code == 6 and "STRICT-FAIL EVIDENCE number \"35\"" in log and "STRICT-FAIL FACT number \"35\"" in log, log
    # Strict with no sources fails closed.
    code, log = strict([HERE / "sample_resume_ev.json", "--strict"], out, "ev")
    assert code == 6 and "STRICT-FAIL FACT not run (no --sources)" in log, log
    assert "STRICT-FAIL EVIDENCE skipped" in log, log
    # A banned word fails strict; other lint does not.
    lint = copy.deepcopy(base)
    lint["sections"][1]["entries"][0]["bullets"][1]["text"] = (
        "Leveraged triage rules and a daily stand-up to cut open ticket backlog from 410 to 120 in 5 months (with the service desk manager).")
    path = out / "ev_lint.json"
    path.write_text(json.dumps(lint), encoding="utf-8")
    code, log = strict([path, "--sources", *IT_SOURCES, "--strict"], out, "ev")
    assert code == 6 and 'STRICT-FAIL lint Experience / IT Support Lead bullet 2: "leveraged" is on the banned-word list' in log, log
    print("ok EVIDENCE under --strict: exit 6 with STRICT-FAIL EVIDENCE")


def cite_root(out):
    """A publications.md with an en-dash page range and a Greek letter (built here, so fixtures stay ASCII)."""
    text = (
        "# Publications\n\n"
        f"- P-001 | journal | Avery JQ, Rivera M. Real-time PCR detection of mycoplasma. *Springfield J Microbiol*. 2020;14(3):112{EN}120.\n"
        f"- P-002 | journal | Okafor N, Avery JQ. A {BETA}-lactamase reporter for biofilm genes. *Lakeside Microb Methods*. 2011;5:41-49. "
        "| ascii: Okafor N, Avery JQ. A beta-lactamase reporter for biofilm genes. *Lakeside Microb Methods*. 2011;5:41-49.\n"
        "- P-003 | journal | Chen L, Avery JQ, Novak T. We leveraged a robust and passionate cleanroom survey of 9999 plates "
        "across many sites and many years, written long on purpose so that the bullet lint would flag it for length if it ran. "
        "*J Example Biol*. 2019;9:1-250.\n"
        f"- P-004 | preprint | Avery JQ. An {BETA}-only title with no rendering. *Example Preprint Server*. 2022.\n")
    path = out / "publications_cite.md"
    path.write_text(text, encoding="utf-8")
    return path


def cite_resume(out, typed, cited, name):
    data = {"name": "Jordan Q. Avery", "contact": ["Springfield, IL", "jordan.q.avery@example.com"],
            "sections": [{"heading": "Summary", "paragraph": "Microbiologist (Ph.D.) with 7 years of GMP QC microbiology."},
                         {"heading": "Publications", "bullets": typed},
                         {"heading": "Selected Publications", "citations": cited, "bold_name": True}]}
    path = out / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cite(out):
    pubs_path = cite_root(out)
    pubs = checks.load_publications(pubs_path)
    assert pubs["P-002"]["ascii"].startswith("Okafor N, Avery JQ. A beta-lactamase"), pubs["P-002"]
    assert checks.printable(pubs["P-002"]) == pubs["P-002"]["ascii"] and checks.printable(pubs["P-001"]) == pubs["P-001"]["text"]
    sources = [VX / "master_resume.md", VX / "achievement_library.md"]
    good = cite_resume(out, [
        "Avery JQ,  Rivera M. Real-time PCR detection of mycoplasma. *Springfield J Microbiol*. 2020;14(3):112-120.",
        "Okafor N, Avery JQ. A beta-lactamase reporter for biofilm genes. *Lakeside Microb Methods*. 2011;5:41-49.",
        pubs["P-003"]["text"]], ["P-002", "P-001", "P-003"], "cite_good.json")
    code, log = strict([good, "--sources", *sources, "--publications", pubs_path, "--strict"], out, "cite")
    assert code == 0, log
    assert "CITE ok (6 citations)" in log and log.rstrip().endswith("VERDICT ok"), log
    assert not [ln for ln in log.splitlines() if ln.startswith(("WARN", "FACT number", "FACT date"))], \
        "no lint or FACT line for citation text"
    txt = (out / "cite.txt").read_text(encoding="utf-8")
    assert "A beta-lactamase reporter" in txt and BETA not in txt, "the approved ascii rendering is printed"

    changed = cite_resume(out, [pubs["P-003"]["text"].replace("Avery JQ", "Avery JR")], [], "cite_changed.json")
    code, log = strict([changed, "--sources", *sources, "--publications", pubs_path, "--strict"], out, "cite")
    assert code == 6, log
    assert any(ln.startswith("CITE mismatch P-003") for ln in log.splitlines()), log
    assert any(ln.startswith("STRICT-FAIL CITE mismatch P-003") for ln in log.splitlines()), log
    assert '"JR," where publications.md has "JQ,"' in log, log

    greek = cite_resume(out, [], ["P-004"], "cite_greek.json")
    code, log = strict([greek, "--sources", *sources, "--publications", pubs_path, "--strict"], out, "cite")
    assert code == 6 and "CITE P-004 has characters the PDF font cannot show" in log, log
    unknown = cite_resume(out, ["Somebody Else. A paper nobody wrote at all. 1999."], [], "cite_unknown.json")
    code, log = strict([unknown, "--sources", *sources, "--publications", pubs_path, "--strict"], out, "cite")
    assert code == 6 and 'CITE not in publications.md: "Somebody Else.' in log, log
    missing = cite_resume(out, [], ["P-009"], "cite_missing.json")
    code, log = strict([missing, "--publications", pubs_path], out, "cite")
    assert code == 1 and "ERROR citation P-009 is not in publications.md" in log, log
    code, log = strict([cite_resume(out, [], ["P-001"], "cite_nopubs.json")], out, "cite")
    assert code == 1 and "give --publications" in log, log
    # Without --publications a Publications heading with bullets is a v1.1 section: no CITE.
    plain = cite_resume(out, ["Avery JQ. Some text."], ["P-001"], "cite_plain.json")
    data = json.loads(plain.read_text(encoding="utf-8"))
    data["sections"].pop()
    plain.write_text(json.dumps(data), encoding="utf-8")
    code, log = strict([plain], out, "cite")
    assert code == 0 and "CITE" not in log and "VERDICT" not in log, log
    # Dashes, spaces and * markup are ignored; letters are not.
    assert checks.cite_norm(f"a {EN} b *c*") == checks.cite_norm("a-b c")
    assert checks.cite_norm("Avery JQ") != checks.cite_norm("Avery JR")
    print("ok CITE: changed initial fails; spacing, dashes and an approved ascii rendering pass")


def test_letter(out):
    posting = (VX / "posting_qpcr.md").read_text(encoding="utf-8")
    base = json.loads((HERE / "sample_letter.json").read_text(encoding="utf-8"))
    L = base["letter"]
    L["paragraphs"][0] = "I am applying for the QC Microbiologist II role at Acme Bio. For the past five years I have led a team of 6 technicians."
    L["company"], L["role"] = "Acme Bio", "QC Microbiologist II"
    assert checks.letter_check(base, posting) == ['LETTER ok (company "Acme Bio", role "QC Microbiologist II")']
    wrong = copy.deepcopy(base)
    wrong["letter"]["company"] = "Acme"
    wrong["letter"]["paragraphs"][0] = wrong["letter"]["paragraphs"][0].replace("Acme Bio", "Acme")
    got = checks.letter_check(wrong, posting)
    assert 'LETTER company "Acme" is not posting.md\'s "Acme Bio"' in got, got
    assert 'LETTER company "Acme Bio" (posting.md) is not named in the letter' in got, got
    slot = copy.deepcopy(base)
    slot["letter"]["paragraphs"].append("Your focus on {focus} fits my work.")
    assert "LETTER unfilled slot {focus}" in checks.letter_check(slot, posting)
    assert checks.letter_check(slot) == ["LETTER unfilled slot {focus}"]
    path = out / "letter_slot.json"
    path.write_text(json.dumps(slot), encoding="utf-8")
    pfile = out / "posting.md"
    pfile.write_text(posting, encoding="utf-8")
    code, log = strict([path, "--sources", *SOURCES, "--posting", pfile, "--strict"], out, "letter")
    assert code == 6 and "STRICT-FAIL LETTER unfilled slot {focus}" in log, log
    assert "EVIDENCE" not in log, "letters have no bullets to cite"
    path.write_text(json.dumps(base), encoding="utf-8")
    code, log = strict([path, "--sources", *SOURCES, "--posting", pfile, "--strict"], out, "letter")
    assert code == 0 and "LETTER ok" in log and log.rstrip().endswith("VERDICT ok"), log
    print("ok LETTER: company and role from posting.md, no unfilled slot")


def test_verdict():
    lines = ["PARSE ok (3 checks)", "PARSE WARN 1.0% of the PDF text is unreadable (cid:) glyphs", "FACT ok (2 checked)",
             'FACT number "9" in Summary is not in your sources', "EVIDENCE ok (1 bullets, 1 entries cited)",
             "CITE mismatch P-001: x", "LETTER ok (company \"A\", role \"B\")", "KEYWORD not evidenced: Java"]
    assert checks.strict_failures(lines) == [lines[1], lines[3], lines[5]]
    assert checks.verdict([], True) == ["VERDICT ok"]
    assert checks.verdict(["pages 3 > 2"], True) == ["STRICT-FAIL pages 3 > 2", "VERDICT fail"]
    assert checks.verdict(["pages 3 > 2"], False) == ["VERDICT fail"]
    print("ok strict failures and VERDICT")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="job_skill_checks_"))
    test_numbers()
    test_dates()
    test_keywords()
    test_end_to_end(out)
    test_parse_failures(out)
    test_ids_are_not_facts()
    test_evidence_units()
    test_evidence_build(out)
    test_cite(out)
    test_letter(out)
    test_verdict()
    print(f"PASS  outputs in {out}")


if __name__ == "__main__":
    main()
