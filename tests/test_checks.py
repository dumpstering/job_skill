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


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="job_skill_checks_"))
    test_numbers()
    test_dates()
    test_keywords()
    test_end_to_end(out)
    test_parse_failures(out)
    print(f"PASS  outputs in {out}")


if __name__ == "__main__":
    main()
