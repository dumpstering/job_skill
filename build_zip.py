"""Package the job skill for Claude Desktop.

Usage: python build_zip.py

Writes to dist/:
  job.zip                 the skill (upload in Customize > Skills)
  job-search-plugin.zip   the same skill as a plugin (Customize > Plugins), the
                          fallback if /job does not show up in Cowork
  SETUP_GUIDE.md          the install and use guide for the person receiving it

Checks first: the folder name matches the frontmatter name, the description
is present and at most 1,024 characters, SKILL.md is under 500 lines, and
every file is ASCII. Exits 1 on a failed check.
"""
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "job"
DIST = ROOT / "dist"


def skill_files():
    return sorted(p for p in SKILL.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def check():
    problems = []
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["SKILL.md has no frontmatter"]
    front = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    name = front.get("name", "").strip()
    desc = front.get("description", "").strip()
    if name != SKILL.name:
        problems.append(f'frontmatter name "{name}" does not match folder "{SKILL.name}"')
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        problems.append("name must be 1-64 lowercase letters, numbers or hyphens")
    if not desc or len(desc) > 1024:
        problems.append(f"description must be 1-1024 characters (is {len(desc)})")
    if len(text.splitlines()) >= 500:
        problems.append("SKILL.md should stay under 500 lines")
    for p in skill_files() + [ROOT / "SETUP_GUIDE.md"]:
        try:
            p.read_bytes().decode("ascii")
        except UnicodeDecodeError:
            problems.append(f"non-ASCII characters in {p.relative_to(ROOT)}")
    return problems


def main():
    problems = check()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    DIST.mkdir(exist_ok=True)

    with zipfile.ZipFile(DIST / "job.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in skill_files():
            z.write(p, Path("job") / p.relative_to(SKILL))

    manifest = {
        "name": "job-search",
        "version": VERSION,
        "description": "Tailor a resume to a job posting, grow an achievement library, and fill the application in the browser.",
    }
    with zipfile.ZipFile(DIST / "job-search-plugin.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".claude-plugin/plugin.json", json.dumps(manifest, indent=2) + "\n")
        for p in skill_files():
            z.write(p, Path("skills/job") / p.relative_to(SKILL))

    shutil.copy2(ROOT / "SETUP_GUIDE.md", DIST / "SETUP_GUIDE.md")
    for f in sorted(DIST.iterdir()):
        print(f"wrote: {f} ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
