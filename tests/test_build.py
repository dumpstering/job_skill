"""Build the sample resume and letter and check the outputs.

Usage: python tests/test_build.py [out_dir]
Needs python-docx, reportlab and pypdf. Exits non-zero on the first failure.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "job" / "scripts" / "build_resume.py"


def build(json_name, out, base):
    r = subprocess.run([sys.executable, str(SCRIPT), str(HERE / json_name), "--out", str(out), "--base", base],
                       capture_output=True, text=True)
    print(r.stdout, end="")
    assert r.returncode == 0, f"build exited {r.returncode}: {r.stderr}"
    for ext in (".docx", ".pdf", ".txt"):
        assert (out / (base + ext)).exists(), f"missing {base}{ext}"
    return r.stdout


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

    log = build("sample_letter.json", out, "Jordan Avery Cover Letter")
    assert "WARN" not in log, "sample letter should lint clean"

    print(f"PASS  outputs in {out}")


if __name__ == "__main__":
    main()
