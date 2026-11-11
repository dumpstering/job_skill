"""Package the job skill, the plugin and the job finder (runner) for a release.

Usage: python build_zip.py [--src DIR] [--dist DIR]

Writes to dist/v<VERSION>/, so a rebuild never touches an earlier version:
  job-search-plugin.zip   the primary install: .claude-plugin/plugin.json,
                          skills/job/** and agents/*.md (Plugins > Add >
                          Upload plugin)
  job.zip                 the fallback: the job/ skill folder only, no agents
  jobrunner-<VERSION>.zip the job finder: app/ (a copy of job/scripts/*.py),
                          install_runner.ps1, uninstall_runner.ps1 and
                          requirements.lock
  install_runner.cmd      runner/install_runner.cmd with @@VERSION@@ and
                          @@ZIP_SHA256@@ filled in (CRLF line endings)
  SETUP_GUIDE.md          the install and use guide

Checks first, and exits 1 with one FAIL line per problem, writing nothing:
- SKILL.md: frontmatter name equals the folder name; the description is
  1-200 characters and ASCII; fewer than 500 lines.
- Every file in job/ matches the allowlist (SKILL.md, references/**/*.md,
  templates/**/*.md|toml|csv, scripts/*.py); every agent has a name.
- Every relative link in SKILL.md and references/ resolves to a packaged file.
- The PII guard (spec 8.7), over the packaged files and every repository file
  outside tests/fixtures: the names JobSearch/, network/, leads/, queue/,
  state/, upload/, claude/, runner/in/, runner/out/, keys.toml, *.jsonl,
  Connections*.csv, *.xlsx and *LinkedInDataExport*; any email address other
  than @example.com or @example.org (and the three LinkedIn alert senders);
  a linkedin.com URL carrying otpToken or midToken (in any file); and
  non-ASCII text.

--src points at another checkout (tests use a temp copy); --dist overrides
the output root (default <src>/dist). Zips are deterministic: sorted names,
a fixed timestamp, and LF line endings for text files, so the runner zip's
SHA-256 is the same on every checkout.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

VERSION = "1.3.0"
DESC_MAX = 200
SKILL_MAX_LINES = 500
ZIP_TIME = (2026, 11, 5, 0, 0, 0)

ALLOWED = re.compile(r"^(SKILL\.md|references/(?:[^/]+/)*[^/]+\.md|templates/(?:[^/]+/)*[^/]+\.(?:md|toml|csv)"
                     r"|scripts/[^/]+\.py)$")
TEXT_EXT = {".md", ".py", ".toml", ".csv", ".json", ".jsonl", ".txt", ".ps1", ".cmd", ".lock", ".html",
            ".xml", ".yml", ".yaml", ".cfg", ".ini", ".gitignore", ".gitattributes"}
SKIP_DIRS = {".git", ".venv", "venv", "dist", "__pycache__", ".pytest_cache", "node_modules"}

# PII guard (spec 8.7).
PRIVATE_DIRS = {"JobSearch", "network", "leads", "queue", "state", "upload", "claude"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ALLOWED_DOMAINS = ("example.com", "example.org")
RESERVED_TLDS = ("example", "test", "invalid", "localhost")
# The LinkedIn alert senders the inbox and discovery references must name
# (spec 3.5): they are the only real addresses allowed.
ALLOWED_ADDRESSES = {"jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com",
                     "jobs-listings@linkedin.com"}
TOKEN_RE = re.compile(r"linkedin\.com\S*?(otpToken|midToken)", re.I)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


class Tree:
    """Paths for one checkout."""

    def __init__(self, src, dist=None):
        self.root = Path(src).resolve()
        self.skill = self.root / "job"
        self.agents = self.root / "plugin" / "agents"
        self.runner = self.root / "runner"
        self.guide = self.root / "SETUP_GUIDE.md"
        self.dist = (Path(dist).resolve() if dist else self.root / "dist") / f"v{VERSION}"

    def rel(self, p):
        try:
            return Path(p).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p)


# ---------- file lists ----------

def walk(folder):
    """Every file under folder, skipping caches; nothing is filtered by .gitignore."""
    out = []
    if not folder.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        out += [Path(dirpath) / f for f in sorted(filenames) if not f.endswith(".pyc")]
    return out


def skill_files(t):
    return walk(t.skill)


def agent_files(t):
    return sorted(p for p in t.agents.glob("*.md") if p.is_file()) if t.agents.is_dir() else []


def scripts(t):
    return sorted(p for p in (t.skill / "scripts").glob("*.py") if p.is_file())


RUNNER_FILES = ("install_runner.ps1", "uninstall_runner.ps1", "requirements.lock")


def packaged(t):
    files = skill_files(t) + agent_files(t) + [t.runner / n for n in RUNNER_FILES]
    files += [t.runner / "install_runner.cmd", t.guide]
    return [p for p in files if p.is_file()]


def repo_files(t):
    """The checkout's files: git's view (tracked plus untracked, not ignored) when
    this is a git work tree, else every file except caches, venvs and dist."""
    try:
        r = subprocess.run(["git", "-C", str(t.root), "ls-files", "-co", "--exclude-standard", "-z"],
                           capture_output=True, timeout=60)
        top = subprocess.run(["git", "-C", str(t.root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and top.returncode == 0 and Path(top.stdout.strip()).resolve() == t.root:
            names = [n for n in r.stdout.decode("utf-8", "replace").split("\0") if n]
            return sorted({t.root / n for n in names if (t.root / n).is_file()})
    except (OSError, subprocess.SubprocessError):
        pass
    return walk(t.root)


# ---------- checks ----------

def frontmatter(text):
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return None
    front = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            front[k.strip()] = v
    return front


def check_skill(t):
    problems = []
    path = t.skill / "SKILL.md"
    if not path.is_file():
        return [f"FAIL job/SKILL.md: missing"]
    text = path.read_text(encoding="utf-8", errors="replace")
    front = frontmatter(text)
    if front is None:
        return ["FAIL job/SKILL.md: no frontmatter"]
    name, desc = front.get("name", ""), front.get("description", "")
    if name != t.skill.name:
        problems.append(f'FAIL job/SKILL.md: frontmatter name "{name}" does not match folder "{t.skill.name}"')
    if not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        problems.append("FAIL job/SKILL.md: name must be 1-64 lowercase letters, numbers or hyphens")
    if not desc or len(desc) > DESC_MAX:
        problems.append(f"FAIL job/SKILL.md: description must be 1-{DESC_MAX} characters (is {len(desc)})")
    if any(ord(c) > 127 for c in desc):
        problems.append("FAIL job/SKILL.md: description is not ASCII")
    lines = len(text.splitlines())
    if lines >= SKILL_MAX_LINES:
        problems.append(f"FAIL job/SKILL.md: {lines} lines (keep it under {SKILL_MAX_LINES})")
    for p in skill_files(t):
        rel = p.relative_to(t.skill).as_posix()
        if not ALLOWED.match(rel):
            problems.append(f"FAIL job/{rel}: unexpected file in the skill folder")
    for p in agent_files(t):
        f = frontmatter(p.read_text(encoding="utf-8", errors="replace")) or {}
        if f.get("name") != p.stem or not f.get("description"):
            problems.append(f"FAIL {t.rel(p)}: agent frontmatter needs name {p.stem} and a description")
    for need in [t.runner / n for n in RUNNER_FILES + ("install_runner.cmd",)] + [t.guide]:
        if not need.is_file():
            problems.append(f"FAIL {t.rel(need)}: missing")
    cmd = t.runner / "install_runner.cmd"
    if cmd.is_file():
        body = cmd.read_text(encoding="ascii", errors="replace")
        for ph in ("@@VERSION@@", "@@ZIP_SHA256@@"):
            if ph not in body:
                problems.append(f"FAIL {t.rel(cmd)}: template has no {ph}")
    return problems


def check_links(t):
    """Every relative link in SKILL.md and references/ resolves to a packaged file."""
    problems = []
    shipped = {p.resolve() for p in skill_files(t)}
    docs = [t.skill / "SKILL.md"] + [p for p in skill_files(t) if p.suffix == ".md"
                                     and p.relative_to(t.skill).parts[0] == "references"]
    for doc in docs:
        if not doc.is_file():
            continue
        for target in LINK_RE.findall(doc.read_text(encoding="utf-8", errors="replace")):
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue
            dest = (doc.parent / target.split("#", 1)[0]).resolve()
            if dest not in shipped:
                problems.append(f"FAIL {t.rel(doc)}: link {target} does not resolve to a packaged file")
    return problems


def in_fixtures(rel):
    return rel.startswith("tests/fixtures/")


def name_problem(rel):
    """The 8.7 name rules for one relative path (None when fine)."""
    parts = rel.split("/")
    base = parts[-1]
    low = base.lower()
    for seg in parts[:-1]:
        if seg in PRIVATE_DIRS:
            return f"private folder name {seg}/"
    if re.search(r"(^|/)runner/(in|out)/", rel):
        return "runner/in or runner/out content"
    if low == "keys.toml":
        return "keys.toml"
    if low.endswith(".jsonl"):
        return "a .jsonl file"
    if low.startswith("connections") and low.endswith(".csv"):
        return "a Connections export"
    if low.endswith(".xlsx"):
        return "an .xlsx file"
    if "linkedindataexport" in low:
        return "a LinkedIn data export"
    return None


def email_ok(addr, rel, text, start):
    a = addr.lower()
    if a in ALLOWED_ADDRESSES:
        return True
    domain = a.rsplit("@", 1)[1]
    if domain in ALLOWED_DOMAINS or any(domain.endswith("." + d) for d in ALLOWED_DOMAINS):
        return True
    if domain.rsplit(".", 1)[-1] in RESERVED_TLDS:
        return True
    # Board specs in the tests (ats:board/site@host) are not addresses.
    if rel.startswith("tests/") and start > 0 and text[start - 1] in ":/":
        return True
    return False


def content_problems(t, path, rel, fixtures_too=False):
    problems = []
    try:
        data = path.read_bytes()
    except OSError as e:
        return [f"FAIL {rel}: cannot read ({e})"]
    text = data.decode("utf-8", errors="replace")
    if TOKEN_RE.search(text):
        problems.append(f"FAIL {rel}: a linkedin.com link with a sign-in token")
    if in_fixtures(rel) and not fixtures_too:
        return problems
    is_text = path.suffix.lower() in TEXT_EXT or path.name in (".gitignore", ".gitattributes")
    if not is_text:
        return problems
    try:
        data.decode("ascii")
    except UnicodeDecodeError as e:
        line = data[:e.start].count(b"\n") + 1
        problems.append(f"FAIL {rel}: non-ASCII character on line {line}")
    for m in EMAIL_RE.finditer(text):
        if not email_ok(m.group(0), rel, text, m.start()):
            problems.append(f"FAIL {rel}: email address {m.group(0)} (only @example.com or @example.org)")
            break
    return problems


def check_pii(t):
    problems = []
    seen = set()
    for p in packaged(t) + repo_files(t):
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        rel = t.rel(p)
        if not in_fixtures(rel):
            why = name_problem(rel)
            if why:
                problems.append(f"FAIL {rel}: {why} (never in the repo or a package)")
        problems += content_problems(t, p, rel)
    return problems


def check(t):
    return check_skill(t) + check_links(t) + check_pii(t)


# ---------- build ----------

def zip_bytes(path):
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_EXT:
        data = data.replace(b"\r\n", b"\n")
    return data


def write_zip(dest, entries):
    """entries: [(arcname, bytes)]; deterministic, written to a temp name first."""
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in sorted(entries):
            info = zipfile.ZipInfo(arc, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    os.replace(tmp, dest)


def skill_description(t):
    return frontmatter((t.skill / "SKILL.md").read_text(encoding="utf-8"))["description"]


def build(t):
    t.dist.mkdir(parents=True, exist_ok=True)
    skill = [(p.relative_to(t.skill).as_posix(), zip_bytes(p)) for p in skill_files(t)]
    agents = [(p.name, zip_bytes(p)) for p in agent_files(t)]

    manifest = {"name": "job-search", "version": VERSION, "description": skill_description(t)}
    plugin = [(".claude-plugin/plugin.json", (json.dumps(manifest, indent=2) + "\n").encode("ascii"))]
    plugin += [("skills/job/" + a, d) for a, d in skill] + [("agents/" + a, d) for a, d in agents]
    write_zip(t.dist / "job-search-plugin.zip", plugin)

    write_zip(t.dist / "job.zip", [("job/" + a, d) for a, d in skill])

    runner_zip = t.dist / f"jobrunner-{VERSION}.zip"
    app = [("app/" + p.name, zip_bytes(p)) for p in scripts(t)]
    app += [(n, zip_bytes(t.runner / n)) for n in RUNNER_FILES]
    write_zip(runner_zip, app)
    sha = hashlib.sha256(runner_zip.read_bytes()).hexdigest()

    cmd = (t.runner / "install_runner.cmd").read_bytes().decode("ascii")
    cmd = cmd.replace("@@VERSION@@", VERSION).replace("@@ZIP_SHA256@@", sha)
    cmd = cmd.replace("\r\n", "\n").replace("\n", "\r\n")
    tmp = t.dist / "install_runner.cmd.tmp"
    tmp.write_bytes(cmd.encode("ascii"))
    os.replace(tmp, t.dist / "install_runner.cmd")

    tmp = t.dist / "SETUP_GUIDE.md.tmp"
    tmp.write_bytes(t.guide.read_bytes())
    os.replace(tmp, t.dist / "SETUP_GUIDE.md")

    for name in ("job-search-plugin.zip", "job.zip", runner_zip.name, "install_runner.cmd", "SETUP_GUIDE.md"):
        f = t.dist / name
        print(f"wrote: {f} ({f.stat().st_size:,} bytes)")
    print(f"SHA256 {runner_zip.name} {sha}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check and package the job skill, plugin and runner.")
    ap.add_argument("--src", default=str(Path(__file__).resolve().parent), help="the checkout to package")
    ap.add_argument("--dist", help="output root (default <src>/dist)")
    a = ap.parse_args(argv)
    t = Tree(a.src, a.dist)
    problems = check(t)
    if problems:
        for p in problems:
            print(p)
        return 1
    build(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
