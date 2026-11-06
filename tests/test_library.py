"""Test job/scripts/library.py: ID assignment and the dangling-ID check.

Usage: python tests/test_library.py
Prints "ok <name>" per test and PASS last. Exits non-zero on the first failure.
"""
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "job" / "scripts"))
import library  # noqa: E402

LIB = """# Achievement library

Format: one `### Job Title | Company | Mon YYYY - Mon YYYY` heading per role.

### Scientist II | Northwind Biologics | Jan 2016 - Apr 2023

- Validated 3 real-time PCR assays.
- Ran environmental monitoring for 4 cleanrooms,
  trended monthly.
- Led 6 deviation investigations.

### Postdoctoral Fellow | Contoso Research Institute | Sep 2012 - Dec 2015

- Built a panel for 8 gut anaerobes.

### Skills, Certifications & Education

- Not held: California CLS license.
"""


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = library.main([str(a) for a in argv])
    return code, buf.getvalue()


def make_root():
    root = Path(tempfile.mkdtemp(prefix="job_skill_library_"))
    (root / "achievement_library.md").write_text(LIB, encoding="utf-8", newline="\n")
    return root


def test_ids_in_file_order():
    root = make_root()
    code, out = run("ids", "--root", root)
    assert code == 0 and "IDS assigned 4: L-0001..L-0004" in out, out
    text = (root / "achievement_library.md").read_text(encoding="utf-8")
    lib = library.parse(text)
    got = [(e.id, e.text[:20]) for e in lib.entries if e.held]
    assert got == [("L-0001", "Validated 3 real-tim"), ("L-0002", "Ran environmental mo"),
                   ("L-0003", "Led 6 deviation inve"), ("L-0004", "Built a panel for 8 ")], got
    assert "- Not held: California CLS license." in text, "Not held lines get no ID"
    assert "trended monthly" in lib.by_id["L-0002"].text, "continuation lines join the entry"
    assert text.rstrip().endswith("<!-- last-id: L-0004 -->"), text[-60:]
    assert lib.by_id["L-0004"].heading.startswith("Postdoctoral Fellow"), lib.by_id["L-0004"].heading
    print("ok ids in file order")
    return root


def test_backup_first(root):
    backups = list((root / "backups").glob("achievement_library_*.md"))
    assert len(backups) == 1, backups
    assert backups[0].read_text(encoding="utf-8") == LIB, "the backup is the file before the change"
    print("ok backup written first")


def test_second_run_changes_nothing(root):
    path = root / "achievement_library.md"
    before = path.read_bytes()
    code, out = run("ids", "--root", root)
    assert code == 0 and out.startswith("IDS unchanged"), out
    assert path.read_bytes() == before, "a second run must not change the file"
    assert len(list((root / "backups").glob("*.md"))) == 1, "no backup when nothing changes"
    print("ok second run changes nothing")


def test_new_entry_next_free_id(root):
    path = root / "achievement_library.md"
    text = path.read_text(encoding="utf-8")
    # A new entry in the middle of the first role, and one retired entry (L-0003 removed).
    text = text.replace("- [L-0003] Led 6 deviation investigations.\n",
                        "- Trained 5 analysts on aseptic technique.\n")
    path.write_text(text, encoding="utf-8", newline="\n")
    code, out = run("ids", "--root", root)
    assert code == 0 and "IDS assigned 1: L-0005..L-0005" in out, out
    lib = library.parse(path.read_text(encoding="utf-8"))
    ids = [e.id for e in lib.entries if e.held]
    assert ids == ["L-0001", "L-0002", "L-0005", "L-0004"], ids
    assert "L-0003" not in lib.by_id, "a retired ID is never reused"
    print("ok new entry gets the next free ID without renumbering")


def test_crlf_and_bom_kept():
    root = Path(tempfile.mkdtemp(prefix="job_skill_library_"))
    path = root / "achievement_library.md"
    path.write_bytes(b"\xef\xbb\xbf" + LIB.replace("\n", "\r\n").encode("utf-8"))
    code, out = run("ids", "--file", path)
    assert code == 0, out
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n- [L-0001] Validated" in raw and b"\n\n" not in raw.replace(b"\r\n", b"")
    print("ok line endings and BOM kept")


def test_check(root):
    code, out = run("check", "--root", root)
    assert code == 0 and out.startswith("LIBRARY ok"), out
    v = root / "variants" / "qc_micro" / "v1"
    v.mkdir(parents=True)
    (v / "resume.json").write_text(json.dumps({"sections": [{"bullets": [{"text": "x", "ev": ["L-0001", "L-0042"]}]}]}),
                                   encoding="utf-8")
    (root / "techniques.md").write_text("- qPCR | level: hands-on | years_ok: yes | evidence: L-0002, L-0003\n",
                                        encoding="utf-8")
    code, out = run("check", "--root", root)
    assert code == 1, out
    assert "DANGLING L-0042 in variants/qc_micro/v1/resume.json" in out, out
    assert "DANGLING L-0003 in techniques.md" in out, out
    assert out.strip().endswith("LIBRARY fail (2 problems)"), out
    # Duplicates and entries without IDs.
    path = root / "achievement_library.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n### Extra | X | 2020 - 2021\n\n- [L-0001] again\n- no id yet\n",
                    encoding="utf-8")
    code, out = run("check", "--root", root, "--cites", v / "resume.json")
    assert "DUPLICATE L-0001" in out and 'NO-ID line' in out and "DANGLING L-0042" in out, out
    shutil.rmtree(root, ignore_errors=True)
    print("ok check: dangling, duplicate and missing IDs")


def test_techniques_and_roles():
    root = Path(tempfile.mkdtemp(prefix="job_skill_library_"))
    (root / "techniques.md").write_text(
        "Format: `- <technique> | aliases: <a>; <b> | level: hands-on|familiar | years_ok: yes|no | evidence: L-0012`\n\n"
        "- real-time PCR | aliases: qPCR; quantitative PCR | level: hands-on | years_ok: yes | evidence: L-0004, L-0010\n"
        "- ELISA | level: familiar | years_ok: no | evidence: L-0017\n", encoding="utf-8")
    techs = library.load_techniques(root / "techniques.md")
    assert [t.name for t in techs] == ["real-time PCR", "ELISA"], techs
    t = library.find_technique(techs, "QPCR")
    assert t.name == "real-time PCR" and t.years_ok and t.evidence == ["L-0004", "L-0010"], t
    assert library.find_technique(techs, "ELISA").years_ok is False
    assert library.role_of("Scientist II | Northwind | Jan 2016 - Apr 2023") == ("Scientist II", "Northwind", "Jan 2016 - Apr 2023")
    assert library.role_of("Skills, Certifications & Education") is None
    print("ok techniques and role headings")


def main():
    root = test_ids_in_file_order()
    test_backup_first(root)
    test_second_run_changes_nothing(root)
    test_new_entry_next_free_id(root)
    test_crlf_and_bom_kept()
    test_check(root)
    test_techniques_and_roles()
    print("PASS")


if __name__ == "__main__":
    main()
