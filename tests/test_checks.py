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


# ---------- round 2: claims, not just tokens ----------

VX_SOURCES = [VX / "master_resume.md", VX / "achievement_library.md", VX / "profile.md"]


def vx_text():
    text, _ = checks.read_sources(VX_SOURCES)
    return checks.strip_ids(text)


def role(title, org, dates, bullets=("Did a thing.",)):
    return {"title": title, "org": org, "dates": dates, "bullets": [{"text": b, "ev": []} for b in bullets]}


def test_word_numbers():
    """SJ-03 / TR-11: words count like digits under v1.2 flags; "one" is not a count."""
    got = [(t, k) for t, k in checks.numbers("Led twelve audits, twenty-four reviews and a dozen hires over a decade; one of 8.",
                                             words=True)]
    values = sorted(int(k[1]) for _, k in got)
    assert values == [8, 10, 12, 12, 24], got
    assert checks.numbers("Led twelve audits.") == [], "v1.1 reads digits only on the resume side"
    entries = {"L-0006": ("Deviations: led 6 microbial data deviation investigations to closure, each within 30 days.",
                          "Scientist II, QC Microbiology | Northwind Biologics | Jan 2016 - Apr 2023")}
    data = {"name": "A", "contact": [], "sections": [{"heading": "Experience", "entries": [
        {"title": "Scientist II, QC Microbiology", "org": "Northwind Biologics", "dates": "Jan 2016 - Apr 2023",
         "bullets": [{"text": "Led twelve microbial data deviation investigations to closure, each within thirty days.",
                      "ev": ["L-0006"]},
                     {"text": "Led six investigations, each within thirty days.", "ev": ["L-0006"]}]}]}]}
    got = checks.evidence_check(data, entries, v12=True)
    assert got == ['EVIDENCE number "twelve" in Experience / Scientist II, QC Microbiology bullet 1 is not in L-0006'], got
    assert checks.evidence_check(data, {k: v[0] for k, v in entries.items()}) == \
        ["EVIDENCE ok (2 bullets, 1 entries cited)"], "v1.1 never read the words"
    fact = checks.fact_check({"name": "A", "contact": [], "sections": [
        {"heading": "Summary", "paragraph": "Microbiologist with thirteen years of GMP work and a decade of qPCR."}]},
        vx_text(), v12=True)
    assert 'FACT number "thirteen" in Summary is not in your sources (if derived, list it under Verify)' in fact, fact
    assert 'FACT number "a decade" in Summary is not in your sources (if derived, list it under Verify)' in fact, fact
    print("ok numbers written as words: FACT and EVIDENCE read them under v1.2 flags")


def test_roles_bind():
    """TR-12 / TR-05: title, employer and dates are one role; Present only where a source says so."""
    text = vx_text()
    ok = role("Scientist II, QC Microbiology", "Northwind Biologics", "Jan 2016 - Apr 2023")
    assert checks.role_problems(ok, text) == []
    merged = role("Scientist II, QC Microbiology", "Fabrikam Labs", "Jan 2016 - Present")
    assert checks.role_problems(merged, text) == [
        'FACT "Scientist II, QC Microbiology | Fabrikam Labs" is not one role in your sources '
        "(no line names this title with this employer)"]
    stretched = role("Scientist II, QC Microbiology", "Northwind Biologics", "Jan 2016 - Present")
    got = checks.role_problems(stretched, text)
    assert len(got) == 1 and got[0].startswith('FACT "Present" for Scientist II, QC Microbiology | Northwind Biologics'), got
    current = role("Microbiology Consultant", "Fabrikam Labs", "Jun 2023 - Present")
    assert checks.role_problems(current, text) == []
    # The role ended: the master resume (and library) give an end date.
    ended = text.replace("06/2023 - Present", "06/2023 - 10/2026").replace("Jun 2023 - Present", "Jun 2023 - Oct 2026")
    got = checks.role_problems(current, ended)
    assert len(got) == 1 and "your sources do not show this role as current" in got[0], got
    # Only the master resume changed: the sources disagree, which fails too.
    half = text.replace("06/2023 - Present", "06/2023 - 10/2026")
    got = checks.role_problems(current, half)
    assert len(got) == 1 and '06/2023 - 10/2026' in got[0], got
    wrong_dates = role("Postdoctoral Fellow", "Contoso Research Institute", "Sep 2011 - Dec 2015")
    assert checks.role_problems(wrong_dates, text) == [
        'FACT dates "Sep 2011 - Dec 2015" of Postdoctoral Fellow | Contoso Research Institute are not the dates '
        "your sources give for that role"]
    # A bullet-less entry (education) is held only to the Present rule.
    degree = {"title": "Ph.D., Microbiology", "org": "Springfield State University", "dates": "Aug 2012"}
    assert checks.role_problems(degree, text) == []
    print("ok roles: title, employer and dates bound; Present needs a source")


def test_evidence_binds_roles():
    """TR-06: a cited entry filed under another role (or stint) fails EVIDENCE."""
    lib = checks.library_entries([VX / "achievement_library.md"], headings=True)
    data = {"name": "A", "contact": [], "sections": [{"heading": "Experience", "entries": [
        {"title": "Scientist II, QC Microbiology", "org": "Northwind Biologics", "dates": "Sep 2010 - Aug 2011",
         "bullets": [{"text": "Performed bioburden and sterility testing under GMP for 2 commercial products.",
                      "ev": ["L-0008"]}]},
        {"title": "Postdoctoral Fellow", "org": "Contoso Research Institute", "dates": "Sep 2012 - Dec 2015",
         "bullets": [{"text": "Ran sandwich ELISAs for cytokine panels.", "ev": ["L-0017"]},
                     {"text": "Built a real-time PCR panel for 8 gut anaerobes, used in 2 published studies.",
                      "ev": ["L-0010"]}]}]}]}
    got = checks.evidence_check(data, lib, v12=True)
    assert got == ['EVIDENCE L-0008 in Experience / Scientist II, QC Microbiology bullet 1 is filed under '
                   '"### Scientist II, QC Microbiology | Northwind Biologics | Jan 2016 - Apr 2023" in your library, '
                   "not this role"], got
    assert checks.same_dates("Jan 2016 - Apr 2023", "01/2016 - 2023-04") and checks.same_dates("2016 - 2023", "Jan 2016 - Apr 2023")
    assert not checks.same_dates("2015", "Jan 2016") and not checks.same_dates("Jan 2016 - Present", "Jan 2016 - Apr 2023")
    print("ok EVIDENCE binds each cited entry to its role and stint")


def test_techniques_and_credentials():
    """SJ-04 / TR-12: Skills items are hands-on and held; certification names come from the sources."""
    import library
    techs = library.load_techniques(VX / "techniques.md")
    text = vx_text()
    data = {"name": "A", "contact": [], "sections": [
        {"heading": "Techniques", "lines": [
            {"label": "Molecular", "text": "qPCR, endotoxin testing (LAL), confocal microscopy, flow cytometry"},
            {"label": "Imaging", "text": "confocal imaging (familiar)"},
            {"label": "Clinical", "text": "California CLS license"},
            {"label": "Tools", "text": "ELISA, MALDI-TOF"}]},
        {"heading": "Certifications", "bullets": ["BSL-2 biosafety training (earned 2016)",
                                                  "California Clinical Laboratory Scientist license (earned 2016)",
                                                  "California CLS license (2019)"]}]}
    got = checks.technique_check(data, techs, text)
    assert got == [
        'TECHNIQUE "confocal microscopy" in Techniques line "Molecular": techniques.md marks it level: familiar; '
        'list it as "confocal microscopy (familiar)" or leave it out',
        'TECHNIQUE "flow cytometry" in Techniques line "Molecular" is not in techniques.md or your sources',
        'TECHNIQUE "California CLS license" in Techniques line "Clinical": your library says "Not held: California CLS license"',
    ], got
    lines, n = checks.credential_problems(data, text)
    assert n == 3 and lines == [
        'FACT certification "California Clinical Laboratory Scientist license" in Certifications bullet 2 is not in '
        "your sources as written (copy the name from master_resume.md, or ask)",
        'FACT "California CLS license" in Certifications bullet 3: your library says "Not held: California CLS license"',
    ], lines
    assert checks.split_not_held("- Not held: QIIME 2, shotgun metagenomics.\n- x\n")[1] == "QIIME 2\nshotgun metagenomics"
    print("ok TECHNIQUE and certification names: hands-on, held, as written")


def test_notes_answers_only(out):
    """SJ-02 / TR-09: under v1.2 flags notes.md counts only for ## Answers, and
    EVIDENCE reads the library alone."""
    job = out / "notes_job"
    job.mkdir(exist_ok=True)
    notes = job / "notes.md"
    notes.write_text("# Notes\n\n**Job Fit Assessment**\n| Requirement | Status |\n|---|---|\n"
                     "| Senior QC Microbiologist, 10+ years of GMP | Partial |\n\n"
                     "### Scientist II, QC Microbiology | Northwind Biologics | Jan 2016 - Apr 2023\n"
                     "- [L-0099] Led 4 qPCR method transfers to 2 partner sites.\n\n"
                     "## Answers\n\n- One method transfer in 2021, with the QC lead's protocol.\n", encoding="utf-8")
    data = json.loads((VX / "variants" / "qc_micro" / "v1" / "resume.json").read_text(encoding="utf-8"))
    data["sections"] = [s for s in data["sections"] if "citations" not in s]
    data["sections"][0]["paragraph"] = "Senior QC Microbiologist with 10+ years of GMP work since 2021."
    path = out / "notes_resume.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code, log = strict([path, "--sources", *VX_SOURCES, notes, "--strict"], out, "notes")
    assert code == 6, log
    assert "SOURCES notes.md: only its ## Answers section is a source (1 lines)" in log, log
    assert 'STRICT-FAIL FACT number "10+" in Summary is not in your sources (if derived, list it under Verify)' in log, log
    assert "2021" not in log, "2021 is in the Answers section, so it is a source"
    data["sections"][1]["entries"][1]["bullets"][0] = {"text": "Led 4 qPCR method transfers to 2 partner sites.",
                                                       "ev": ["L-0099"]}
    data["sections"][0]["paragraph"] = "Microbiologist (Ph.D.) with 7 years of GMP QC microbiology."
    path.write_text(json.dumps(data), encoding="utf-8")
    code, log = strict([path, "--sources", *VX_SOURCES, notes, "--strict"], out, "notes")
    assert code == 6 and "STRICT-FAIL EVIDENCE unknown L-0099 in Experience / Scientist II, QC Microbiology bullet 1" in log, log
    print("ok notes.md: only ## Answers is a source; EVIDENCE reads the library alone")


def test_keywords_v12():
    """SJ-08 / SJ-15: -ed/-ing forms count; Not held lines are no source and get their own line."""
    terms = [("mentor", []), ("QIIME 2", []), ("validate", []), ("gnotobiotic", []), ("anaerobic", [])]
    src = "Mentored 3 students.\n- Not held: QIIME 2, gnotobiotic mouse models.\n- validating assays\n"
    got = checks.keyword_check("Mentored 3 graduate students.", src, terms, True, v12=True)
    assert got == ["KEYWORD on resume: mentor", "KEYWORD in your sources, not on this resume: validate",
                   "KEYWORD not evidenced: anaerobic",
                   "KEYWORD not held (you said you lack these): QIIME 2, gnotobiotic"], got
    old = checks.keyword_check("Mentored 3 graduate students.", src, terms, True)
    assert len(old) == 3 and old[0] == "KEYWORD on resume: (none)", old
    print("ok KEYWORD under v1.2 flags: stems, and Not held terms named apart")


def test_final_json(out):
    """TR-07 / shared decision 1: a strict build keeps files under the final
    names and writes final.json only when every check passes."""
    job = out / "final_job"
    job.mkdir(exist_ok=True)
    good = json.loads((HERE / "sample_resume_ev.json").read_text(encoding="utf-8"))
    path = job / "resume.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    args = [path, "--sources", *IT_SOURCES, "--strict", "--mode", "industry"]
    code, log = strict(args, job, "Jordan Avery Resume")
    assert code == 0 and log.rstrip().endswith("VERDICT ok"), log
    final = json.loads((job / "final.json").read_text(encoding="utf-8"))
    assert final["v"] == 1 and final["resume"] == "Jordan Avery Resume.pdf" and final["letter"] is None, final
    assert final["verdict"] == "ok" and final["built_at"], final
    assert (job / "Jordan Avery Resume.pdf").exists() and not list(job.glob(".build_*")), list(job.iterdir())
    assert 'FINAL final.json: resume "Jordan Avery Resume.pdf", letter none' in log, log
    # Shared decision 5: final.json records the hash of every library entry the resume cites.
    import library
    lib = library.load(next(p for p in IT_SOURCES if p.name == "achievement_library.md")).by_id
    cited = library.cited_ids(good)
    assert cited and final["entries"] == {i: library.entry_hash(lib[i]) for i in cited}, final.get("entries")
    # A letter beside it.
    letter = json.loads((HERE / "sample_letter.json").read_text(encoding="utf-8"))
    lpath = job / "cover_letter.json"
    lpath.write_text(json.dumps(letter), encoding="utf-8")
    code, log = strict([lpath, "--sources", *IT_SOURCES, "--strict"], job, "Jordan Avery Cover Letter")
    assert code == 0, log
    after = json.loads((job / "final.json").read_text(encoding="utf-8"))
    assert after["letter"] == "Jordan Avery Cover Letter.pdf" and after["entries"] == final["entries"], after
    # A failing rebuild: nothing under the final names, final.json gone.
    bad = copy.deepcopy(good)
    bad["sections"][0]["paragraph"] = "IT lead with 15 years of service desk work."
    path.write_text(json.dumps(bad), encoding="utf-8")
    code, log = strict(args, job, "Jordan Avery Resume")
    assert code == 6 and log.rstrip().endswith("VERDICT fail"), log
    assert not any((job / ("Jordan Avery Resume" + e)).exists() for e in (".pdf", ".docx", ".txt"))
    assert not (job / "final.json").exists() and not list(job.glob(".build_*"))
    assert 'FINAL none: nothing kept under "Jordan Avery Resume" (removed Jordan Avery Resume.pdf' in log, log
    assert any(ln.startswith("built: ") for ln in log.splitlines()) and "wrote: " not in log, log
    # A letter with no passing resume beside it is built but not named in final.json.
    code, log = strict([lpath, "--sources", *IT_SOURCES, "--strict"], job, "Jordan Avery Cover Letter")
    assert code == 0 and "FINAL final.json not written: build the resume with --strict here first" in log, log
    assert not (job / "final.json").exists()
    # A long job folder path builds in the system temp folder instead (Windows path limit).
    import build_resume
    near = build_resume.strict_dir(job, "Jordan Avery Resume")
    far = build_resume.strict_dir(job, "x" * 230)
    assert near.parent == job and job not in far.parents, (near, far)
    near.rmdir()
    far.rmdir()
    print("ok strict build: final names and final.json only after VERDICT ok")


def test_build_input_rules(out):
    """TR-10: typed citations share their section with nothing; contracts-15: a
    bad argument exits 1, never 2 (a missing package)."""
    data = {"name": "Jordan Q. Avery", "contact": ["jordan.q.avery@example.com"], "sections": [
        {"heading": "Publications and Patents", "paragraph": "Inventor on 4 issued patents.",
         "lines": [{"label": "Licenses", "text": "California CLS license"}],
         "bullets": ["Avery JQ, Patel S, Rivera M. Biofilm formation by *Pseudomonas aeruginosa* on three surface types. "
                     "*Springfield J Microbiol*. 2011;12(3):45-52."]}]}
    path = out / "typed_cites.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code, log = strict([path, "--publications", VX / "publications.md", "--strict"], out, "typed")
    assert code == 1 and 'ERROR section "Publications and Patents" holds typed citations and lines, paragraph' in log, log
    code, log = strict([path, "--bogus"], out, "typed")
    assert code == 1 and log.startswith("ERROR "), log
    print("ok typed citations stand alone; a bad argument is exit 1")


def test_short_last_page(out):
    """SJ-13: a capped build whose last page holds a few lines gets a WARN."""
    data = json.loads((HERE / "sample_resume_ev.json").read_text(encoding="utf-8"))
    import build_resume
    for k in range(1, 40):
        trial = copy.deepcopy(data)
        trial["sections"][-1]["bullets"] = data["sections"][-1]["bullets"] + ["ITIL 4 Foundation"] * k
        if build_resume.count_pages(build_resume.prepare(copy.deepcopy(trial))) == 2:
            break
    path = out / "short_last.json"
    path.write_text(json.dumps(trial), encoding="utf-8")
    code, log = strict([path, "--mode", "industry"], out, "short")
    assert code == 0 and "WARN page 2 holds only" in log and "tighten to 1 page" in log, log
    code, log = strict([path], out, "short_v11")
    assert "WARN page" not in log, "v1.1 builds unchanged"
    print("ok a near-empty last page gets a WARN")


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
    test_word_numbers()
    test_roles_bind()
    test_evidence_binds_roles()
    test_techniques_and_credentials()
    test_notes_answers_only(out)
    test_keywords_v12()
    test_final_json(out)
    test_build_input_rules(out)
    test_short_last_page(out)
    print(f"PASS  outputs in {out}")


if __name__ == "__main__":
    main()
