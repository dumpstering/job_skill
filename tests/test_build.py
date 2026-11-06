"""Build the sample resume and letter and check the outputs.

Usage: python tests/test_build.py [out_dir]
Needs python-docx, reportlab and pypdf. Exits non-zero on the first failure.

The v1.1 cases come first and are unchanged. The v1.2 cases print "ok <name>":
strict builds, page caps per mode, the CV section order, citations with
*italics* and the bold name, and in-memory page counts.
"""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "job" / "scripts" / "build_resume.py"
VX = HERE / "fixtures" / "variant_example"
SCI_SOURCES = [VX / "master_resume.md", VX / "achievement_library.md", VX / "profile.md"]
IT_SOURCES = [HERE / "sample_sources" / "master_resume.md", HERE / "sample_sources" / "achievement_library.md"]
# sha256 of the v1.1 .txt outputs (newlines as \n), recorded before the v1.2 changes.
V11_TXT = {"resume": "3b94d7a71342775a8fd24f4f388c4ca693dbb940229b190058908b306c38baf8",
           "letter": "ba68cc0b55a102e7f7f56e6d220b23c5820438540b6a6f5b2f12f9c9bfe2b1f1"}
sys.path.insert(0, str(SCRIPT.parent))
import build_resume  # noqa: E402
import checks  # noqa: E402


def build(json_name, out, base):
    r = subprocess.run([sys.executable, str(SCRIPT), str(HERE / json_name), "--out", str(out), "--base", base],
                       capture_output=True, text=True)
    print(r.stdout, end="")
    assert r.returncode == 0, f"build exited {r.returncode}: {r.stderr}"
    for ext in (".docx", ".pdf", ".txt"):
        assert (out / (base + ext)).exists(), f"missing {base}{ext}"
    return r.stdout


def run(json_path, out, base, *flags):
    r = subprocess.run([sys.executable, str(SCRIPT), str(json_path), "--out", str(out), "--base", base,
                        *map(str, flags)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def txt_sha(path):
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


# ---------- v1.2 ----------

def test_v11_unchanged(out):
    assert txt_sha(out / "Jordan Avery Resume.txt") == V11_TXT["resume"], "v1.1 resume .txt changed"
    assert txt_sha(out / "Jordan Q. Avery Cover Letter.txt") == V11_TXT["letter"], "v1.1 letter .txt changed"
    code, log = run(HERE / "sample_resume.json", out, "plain")
    assert code == 0 and "VERDICT" not in log and "STRICT" not in log, "no v1.2 lines without v1.2 flags"
    print("ok v1.1 text and output unchanged")


def test_strict_ok(out):
    code, log = run(HERE / "sample_resume_ev.json", out, "Test Resume", "--sources", *IT_SOURCES,
                    "--strict", "--mode", "industry")
    assert code == 0, log
    assert log.rstrip().splitlines()[-1] == "VERDICT ok", log
    assert "EVIDENCE ok (6 bullets" in log and "STRICT-FAIL" not in log, log
    print("ok strict industry build passes with VERDICT ok")


def three_page_industry(out):
    """sample_resume_ev.json with its roles repeated until the PDF is 3 pages."""
    base = json.loads((HERE / "sample_resume_ev.json").read_text(encoding="utf-8"))
    for k in range(2, 12):
        data = copy.deepcopy(base)
        data["sections"][1]["entries"] = base["sections"][1]["entries"] * k
        if build_resume.count_pages(build_resume.prepare(copy.deepcopy(data))) == 3:
            path = out / "three_pages.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return path
    raise AssertionError("no repeat count gives exactly 3 pages")


def test_page_caps(out):
    path = three_page_industry(out)
    code, log = run(path, out, "three", "--sources", *IT_SOURCES, "--strict", "--mode", "industry")
    assert code == 6, log
    assert "STRICT-FAIL pages 3 > 2" in log.splitlines(), log
    assert "WARN pages 3 > 2 (industry cap 2)" in log, log
    # Without --strict the same build exits 0 and says VERDICT fail.
    code, log = run(path, out, "three", "--sources", *IT_SOURCES, "--mode", "industry")
    assert code == 0 and "VERDICT fail" in log and "STRICT-FAIL" not in log, log
    # --max-pages lowers the cap.
    code, log = run(HERE / "sample_resume_ev.json", out, "one", "--sources", *IT_SOURCES, "--strict",
                    "--max-pages", "1")
    assert code == 0, log
    code, log = run(path, out, "three", "--sources", *IT_SOURCES, "--strict", "--max-pages", "1")
    assert code == 6 and "STRICT-FAIL pages 3 > 1" in log, log

    fed = [*SCI_SOURCES]
    code, log = run(HERE / "sample_usajobs.json", out, "fed", "--sources", *fed, "--publications",
                    VX / "publications.md", "--strict", "--mode", "usajobs")
    assert code == 6, log
    assert "(3 pages)" in log and "STRICT-FAIL pages 3 > 2" in log, log
    fails = [ln for ln in log.splitlines() if ln.startswith("STRICT-FAIL")]
    assert fails == ["STRICT-FAIL pages 3 > 2"], fails
    code, log = run(HERE / "sample_usajobs.json", out, "fed", "--sources", *fed, "--publications",
                    VX / "publications.md", "--strict", "--mode", "usajobs", "--max-pages", "5")
    assert code == 6 and "STRICT-FAIL pages 3 > 2" in log, "usajobs stays capped at 2 pages"

    code, log = run(HERE / "sample_cv.json", out, "cv", "--sources", *SCI_SOURCES, "--publications",
                    VX / "publications.md", "--strict", "--mode", "cv")
    assert code == 0, log
    assert "(4 pages)" in log and log.rstrip().endswith("VERDICT ok"), log
    assert "CITE ok (26 citations)" in log and "WARN" not in log, log
    print("ok page caps: industry 3 > 2 and usajobs 3 > 2 exit 6; cv 4 pages exits 0")


def test_cv_warning_and_order(out):
    data = json.loads((HERE / "sample_cv.json").read_text(encoding="utf-8"))
    pubs = checks.load_publications(VX / "publications.md")
    shuffled = copy.deepcopy(data)
    shuffled["sections"] = list(reversed(shuffled["sections"]))
    warnings = []
    build_resume.prepare(shuffled, pubs, "cv", warnings)
    assert [s["heading"] for s in shuffled["sections"]] == [s["heading"] for s in data["sections"]], \
        "cv mode puts sections in the CV order"
    assert warnings and warnings[0].startswith("cv mode: sections reordered"), warnings
    keep = copy.deepcopy(data)
    warnings = []
    build_resume.prepare(keep, pubs, "cv", warnings)
    assert warnings == [], "no warning when already in order"
    # An unknown heading stays after the section before it; Experience counts as Professional Experience.
    odd = {"name": "A B", "contact": [], "sections": [
        {"heading": "Summary", "paragraph": "x"}, {"heading": "Experience", "paragraph": "x"},
        {"heading": "Projects", "paragraph": "x"}, {"heading": "Education", "paragraph": "x"}]}
    build_resume.prepare(odd, None, "cv", [])
    assert [s["heading"] for s in odd["sections"]] == ["Summary", "Education", "Experience", "Projects"], odd
    # Over 8 pages in cv mode: a warning, never a cap.
    long = copy.deepcopy(data)
    long["sections"][1]["entries"] = data["sections"][1]["entries"] * 20
    path = out / "long_cv.json"
    path.write_text(json.dumps(long), encoding="utf-8")
    code, log = run(path, out, "longcv", "--publications", VX / "publications.md", "--mode", "cv")
    assert code == 0 and "a CV over 8 pages (no cap" in log, log
    print("ok cv section order and the 8-page warning")


def test_citation_rendering(out):
    from docx import Document
    from pypdf import PdfReader
    code, log = run(HERE / "sample_cv.json", out, "cv", "--publications", VX / "publications.md", "--mode", "cv")
    assert code == 0, log
    txt = (out / "cv.txt").read_text(encoding="utf-8")
    assert "*" not in txt, "the .txt drops the italics markup"
    assert "- Avery JQ, Patel S, Rivera M. Biofilm formation by Pseudomonas aeruginosa on three surface types." in txt
    doc = Document(str(out / "cv.docx"))
    runs = [(r.text, r.bold, r.italic) for p in doc.paragraphs for r in p.runs]
    assert ("Pseudomonas aeruginosa", None, True) in runs, "*Pseudomonas aeruginosa* is an italic run"
    assert ("Avery JQ", True, None) in runs, "bold_name bolds the name"
    assert ("Springfield J Microbiol", None, True) in runs, "journal names in *...* are italic"
    pdf = PdfReader(str(out / "cv.pdf"))
    fonts = set()
    for page in pdf.pages:
        res = page.get("/Resources") or {}
        for f in (res.get("/Font") or {}).values():
            fonts.add(str(f.get_object().get("/BaseFont")))
    assert {"/Helvetica-Oblique", "/Helvetica-Bold"} <= fonts, fonts
    # Author forms bold_name recognizes, and an unpaired asterisk stays literal.
    runs = build_resume.cite_runs("Avery, J. Q., Smith AB, J. Q. Avery and Jordan Q. Avery; not Avery Jones", "Jordan Q. Avery")
    bold = [t for t, b, _ in runs if b]
    assert bold == ["Avery, J. Q.", "J. Q. Avery", "Jordan Q. Avery"], bold
    assert build_resume.cite_runs("a*b c", None) == [("a*b c", False, False)]
    print("ok citations: *italics*, bold name, plain .txt")


def test_page_count_in_memory(out):
    data = build_resume.prepare(json.loads((HERE / "sample_resume_ev.json").read_text(encoding="utf-8")))
    assert build_resume.count_pages(data) == 1
    print("ok in-memory page count")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="job_skill_test_"))
    from docx import Document
    from pypdf import PdfReader

    log = build("sample_resume.json", out, "Jordan Avery Resume")
    assert "(1 page)" in log, "sample resume should fit on one page"
    assert '"leveraged" is on the banned-word list' in log, "lint missed a banned word"
    assert "characters (about 200 at most)" in log, "lint missed a long bullet"

    text = PdfReader(str(out / "Jordan Avery Resume.pdf")).pages[0].extract_text()
    order = ["Jordan Avery", "Summary", "Experience", "IT Support Lead", "Northwind Logistics",
             "Desktop Support Technician", "Skills", "Education", "Certifications"]
    pos = [text.find(s) for s in order]
    assert all(p >= 0 for p in pos), f"PDF text missing {[s for s, p in zip(order, pos) if p < 0]}"
    assert pos == sorted(pos), "PDF text is out of reading order"
    assert "IT Support Lead Mar 2021" in text.replace("\n", " "), "title and dates not on one line"

    doc = Document(str(out / "Jordan Avery Resume.docx"))
    assert not doc.tables, "resume must not use tables"
    headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert headings == ["Summary", "Experience", "Skills", "Education", "Certifications"], headings

    # A period in the base name must not truncate the file name.
    log = build("sample_letter.json", out, "Jordan Q. Avery Cover Letter")
    assert "WARN" not in log, "sample letter should lint clean"

    test_v11_unchanged(out)
    test_strict_ok(out)
    test_page_caps(out)
    test_cv_warning_and_order(out)
    test_citation_rendering(out)
    test_page_count_in_memory(out)
    print(f"PASS  outputs in {out}")


if __name__ == "__main__":
    main()
