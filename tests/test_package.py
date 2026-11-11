"""Test the skill text and the packaging: SKILL.md, the references, build_zip.py
and its PII guard, and SETUP_GUIDE.md.

Usage: python tests/test_package.py
Prints "ok <name>" per test and PASS last. Exits non-zero on the first failure.

The doc checks hold the text to the scripts: every `<script>.py <subcommand>
--flag` named in SKILL.md and the references must exist in that script's
argparse, and every output line any doc names must appear in a script.
build_zip.py runs on temp copies of the checkout (a clean checkout,
then one copy per planted problem), never on the repository itself.
"""
import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "job"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))
import build_zip  # noqa: E402

VERSION = build_zip.VERSION
DESCRIPTION = ("Daily job search: finds fresh postings, writes truthful tailored resumes, fills applications for "
               "you to submit, and tracks replies. Use for /job, a job link, or job emails.")
# Spec 1.7, plus the two scheduled modes.
ENTRY_POINTS = ["/job", "/job short", "/job <link>", "/job add <link>", "/job paste", "/job linkedin", "/job inbox",
                "/job log", "/job status",
                "/job prep", "/job week", "/job worksearch", "/job usage", "/job settings", "/job variants",
                "/job watch <company or careers URL>", "/job network", "/job setup", "morning", "inbox-watch"]
# Capitalized words in code spans that are not script output lines.
NOT_OUTPUT = {"UNVERIFIED", "ASCII", "PDF", "LF", "CRLF", "JSON", "URL", "SSN", "TOML", "CSV", "HTML", "API",
              "UTC", "ISO", "EEO", "CV", "AI", "ID", "IANA", "TODO", "NN", "NNc", "NNw", "L", "S", "M", "W", "CA",
              "US", "JOB", "ANS", "P", "N", "E", "Q", "JOB_SKILL_PSSA"}
FAILURE_CODES = ["closed", "login_required", "account_needed", "account_locked", "captcha_failed",
                 "email_code_timeout", "sso_required", "spam_rejected", "site_error", "upload_failed", "form_error",
                 "host_mismatch", "unknown_required", "knockout_at_form", "stored_resume_mismatch",
                 "unsafe_verification", "page_injection", "browser_unavailable", "usage_limit", "deferred_cap",
                 "prep_failed"]
# Planted values are built at runtime, so this file never trips the guard itself.
GMAIL = "someone" + "@gmail.com"
TOKEN_URL = "https://www.linkedin.com/comm/jobs/view/1?" + "otp" + "Token=abc"
BOARD_HOST = re.compile(r"linkedin\.com|indeed\.(com|\*)|\bindeed\b", re.I)
INDEED_HOST = re.compile(r"indeed\.(com|\*)|\bindeed\b", re.I)
REFUSAL = re.compile(r"\bnever\b|\bnot\b|refus|\bdeny|denied|do-yourself|\bdrop|unopened|without opening|"
                     r"left out|leave out|"
                     r"\bno link\b|\bnor\b|\bban", re.I)
# The one reference that tells Claude how to work on LinkedIn (the opt-in browser mode).
LINKEDIN_DOC = "references/linkedin_browser.md"
# A paragraph that sends Claude there names the setting or the gate with it.
LINKEDIN_MODE = re.compile(r"/job\s+linkedin\b|linkedin_browser\.md")
LINKEDIN_GATE = re.compile(r"\[linkedin\]\s+browser|browser\s+=\s+true|LINKEDIN GO")
RISK = r"LinkedIn's rules do not allow\s+automated tools, and it\s+can restrict an\s+account"


def docs():
    """SKILL.md and every packaged reference, as (relative name, text)."""
    out = [("SKILL.md", (SKILL / "SKILL.md").read_text(encoding="utf-8"))]
    for p in sorted((SKILL / "references").rglob("*.md")):
        out.append((p.relative_to(SKILL).as_posix(), p.read_text(encoding="utf-8")))
    return out


# ---------- SKILL.md ----------

def test_skill_md():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) < 500, f"SKILL.md has {len(lines)} lines"
    front = build_zip.frontmatter(text)
    assert front and front["name"] == "job", front
    desc = front["description"]
    assert desc == DESCRIPTION, desc
    assert len(desc) <= 200 and all(ord(c) < 128 for c in desc), len(desc)
    raw = next(l for l in lines if l.startswith("description:"))
    # "Daily job search: finds" holds ": ", which plain YAML reads as a mapping: it must be quoted.
    assert raw.split(":", 1)[1].strip().startswith('"'), raw
    text.encode("ascii")
    print("ok SKILL.md under 500 lines, the spec 7.3 description (quoted, ASCII, at most 200)")


def test_links_resolve():
    t = build_zip.Tree(ROOT)
    problems = build_zip.check_links(t)
    assert not problems, problems
    linked = set()
    for name, text in docs():
        base = (SKILL / name).parent
        for target in build_zip.LINK_RE.findall(text):
            if not re.match(r"^[a-z]+:", target):
                linked.add((base / target.split("#")[0]).resolve())
    for p in (SKILL / "references").rglob("*.md"):
        assert p.resolve() in linked, f"{p.relative_to(SKILL)} is linked from nowhere"
    print("ok every relative link resolves to a packaged file, and every reference is linked")


def routing_rows():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## Routing", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in section.splitlines() if l.startswith("|") and not re.match(r"^\|[-| ]+\|$", l)]
    return rows[1:]  # drop the header


def test_routing_table():
    rows = routing_rows()
    for entry in ENTRY_POINTS:
        hits = []
        for row in rows:
            first = row.split("|")[1]
            m = re.search(r"`([^`]+)`", first)
            if m and m.group(1) == entry:
                hits.append(row)
        assert len(hits) == 1, f"{entry}: {len(hits)} routing rows"
        refs = re.findall(r"\]\((references/[^)]+\.md)\)", hits[0])
        assert len(refs) == 1, f"{entry} routes to {refs}"
        assert (SKILL / refs[0]).is_file(), refs[0]
    print(f"ok all {len(ENTRY_POINTS)} entry points route to exactly one reference")


# ---------- the docs against the scripts ----------

class _Stop(Exception):
    def __init__(self, parser):
        self.parser = parser


def surface(parser):
    """{None: top-level flags, sub: (flags, positional choices)} for one parser."""
    out = {"flags": set(), "subs": {}, "choices": set()}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                s = surface(sp)
                out["subs"][name] = s
        elif a.option_strings:
            out["flags"].update(a.option_strings)
        elif a.choices:
            out["choices"].update(a.choices)
    return out


def cli_surfaces():
    """Each script's argparse tree, read by stopping parse_args (nothing runs)."""
    found = {}
    orig = argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args

    def stop(self, args=None, namespace=None):
        raise _Stop(self)
    argparse.ArgumentParser.parse_args = stop
    argparse.ArgumentParser.parse_known_args = stop
    saved = sys.argv
    try:
        for path in sorted(SCRIPTS.glob("*.py")):
            mod = importlib.import_module(path.stem)
            if not hasattr(mod, "main"):
                continue
            sys.argv = [path.name]
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    mod.main()
            except _Stop as s:
                found[path.stem] = surface(s.parser)
            except SystemExit:
                pass
    finally:
        argparse.ArgumentParser.parse_args, argparse.ArgumentParser.parse_known_args = orig
        sys.argv = saved
    return found


def code_spans(text):
    """Inline code spans (they may wrap lines) and the lines of fenced blocks."""
    spans = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        spans += block.splitlines()
    rest = re.sub(r"```[a-z]*\n.*?```", "", text, flags=re.S)
    spans += [s.replace("\n", " ") for s in re.findall(r"`([^`]+)`", rest)]
    return spans


def command_problems(name, text, cli):
    problems = []
    for span in code_spans(text):
        for m in re.finditer(r"\b([a-z_]+)\.py\b", span):
            script = m.group(1)
            if script not in cli:
                continue
            tree = cli[script]
            tail = re.split(r"\b[a-z_]+\.py\b", span[m.end():])[0]
            tokens = tail.split()
            node = tree
            if tree["subs"] and tokens and re.fullmatch(r"[a-z][a-z-]*", tokens[0]):
                if tokens[0] not in tree["subs"]:
                    problems.append(f"{name}: {script}.py has no subcommand {tokens[0]!r} ({span.strip()[:60]})")
                    continue
                node = tree["subs"][tokens[0]]
                if node["choices"] and len(tokens) > 1 and re.fullmatch(r"[a-z_]+", tokens[1]) \
                        and tokens[1] not in node["choices"]:
                    problems.append(f"{name}: {script}.py {tokens[0]} has no choice {tokens[1]!r}")
            allowed = set(tree["flags"]) | set(node["flags"])
            if node is tree and tree["subs"]:
                for s in tree["subs"].values():
                    allowed |= s["flags"]
            for tok in tokens:
                f = re.match(r"^\[?(--[a-z][a-z0-9-]*)", tok)
                if f and f.group(1) not in allowed:
                    problems.append(f"{name}: {script}.py {'' if node is tree else tokens[0] + ' '}"
                                    f"has no flag {f.group(1)} ({span.strip()[:70]})")
    return problems


def test_commands_match_argparse():
    cli = cli_surfaces()
    assert {"tracker", "events", "caps", "posting", "today_view", "jobrunner", "answers"} <= set(cli), sorted(cli)
    problems = []
    for name, text in docs():
        problems += command_problems(name, text, cli)
    for extra in ("README.md", "SETUP_GUIDE.md"):
        problems += command_problems(extra, (ROOT / extra).read_text(encoding="utf-8"), cli)
    assert not problems, "\n".join(problems)
    # Every script is named in SKILL.md's script list.
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for path in SCRIPTS.glob("*.py"):
        assert re.search(r"`" + re.escape(path.name) + r"[` ]", skill), f"SKILL.md does not list {path.name}"
    print(f"ok every script, subcommand and flag the docs name exists in argparse ({len(cli)} scripts)")


def test_output_lines_exist():
    code = list(SCRIPTS.glob("*.py")) + list((ROOT / "runner").glob("*.ps1")) + [ROOT / "runner" / "install_runner.cmd"]
    source = "\n".join(p.read_text(encoding="utf-8") for p in code)
    problems = []
    # Every packaged doc (other packages' references too), the README and the guides.
    texts = docs() + [(n, (ROOT / n).read_text(encoding="utf-8")) for n in ("README.md", "SETUP_GUIDE.md",
                                                                            "runner/README.md")]
    for rel, text in texts:
        for span in code_spans(text):
            words = []
            for tok in span.strip().split():
                if re.fullmatch(r"[A-Z][A-Z_-]*[A-Z]", tok):
                    words.append(tok)
                else:
                    break
            if not words or words[0] in NOT_OUTPUT:
                continue
            literal = " ".join(words)
            # A line built in pieces (f"SELFTEST {'PASS' ...}") counts when each word is there.
            if literal not in source and not all(re.search(r"\b" + re.escape(w) + r"\b", source) for w in words):
                problems.append(f"{rel}: output line {literal!r} is in no script")
    assert not problems, "\n".join(problems)
    print("ok every output line the docs name appears in a script")


def test_no_job_board_fetch():
    """No reference tells Claude to open a job board or keep an alert link.
    The rule since the LinkedIn browser mode: Indeed and the other boards
    appear only in refusal or do-yourself wording, in every file; so does
    linkedin.com, in every file but linkedin_browser.md, the opt-in mode's
    own reference (test_linkedin_rule holds that file to its gate and its
    limits). A paragraph of any other file that sends Claude to that mode
    names the setting or the LINKEDIN GO gate with it."""
    problems = [p for name, text in docs() for p in board_problems(name, text)]
    assert not problems, "\n".join(problems)
    # the lint itself: each kind of planted paragraph is caught, in the right files only
    other = "references/daily.md"
    assert board_problems(other, "Open linkedin.com/jobs and search for the title.")
    assert not board_problems(LINKEDIN_DOC, "Open linkedin.com/jobs and search for the title.")
    assert board_problems(LINKEDIN_DOC, "Then open indeed.com and search there too.")
    assert board_problems(other, "Then open indeed.com and search there too.")
    assert board_problems(other, "When the list is done, run /job linkedin for the rest.")
    assert board_problems(other, "Follow [linkedin_browser.md](linkedin_browser.md) for the rest.")
    assert not board_problems(other, "With `[linkedin] browser = true`, offer /job linkedin.")
    assert not board_problems(other, "After `LINKEDIN GO`, follow linkedin_browser.md.")
    assert board_problems("references/inbox.md", "Keep the alert link for later.")
    assert not board_problems(other, "The resume lists linkedin.com/in/name as written.")
    print("ok job-board hosts appear only in refusal or do-yourself wording; the LinkedIn mode only with its "
          "setting or gate")


def board_problems(name, text):
    """The job-board problems of one doc (test_no_job_board_fetch)."""
    problems = []
    for para in re.split(r"\n\s*\n", text):
        if para.lstrip().startswith("#"):
            continue  # a heading names the site; the paragraphs under it are checked
        # A resume's own profile link (linkedin.com/in/<name>) is content, never fetched.
        body = para.replace("linkedin.com/in/", "")
        host = INDEED_HOST if name == LINKEDIN_DOC else BOARD_HOST
        if host.search(body) and not REFUSAL.search(para):
            problems.append(f"{name}: {para.strip()[:90]!r}")
        if name != LINKEDIN_DOC and LINKEDIN_MODE.search(para) and not LINKEDIN_GATE.search(para):
            problems.append(f"{name}: names the LinkedIn mode without its setting or gate: {para.strip()[:90]!r}")
        if re.search(r"\b(store|save|keep)\b[^.]{0,40}\balert link", para, re.I) and \
                not re.search(r"\bnever\b", para, re.I):
            problems.append(f"{name}: stores an alert link: {para.strip()[:90]!r}")
    return problems


def test_linkedin_rule():
    """The LinkedIn rule (v1.2.0: never automated). SKILL.md says what is and
    is not automated under each [linkedin] browser setting; the mode is off
    in the shipped settings; linkedin_browser.md states its gate and every
    limit before its first step; setup asks once, with the risk in one plain
    sentence, and only a clear yes turns it on; the README and the setup
    guide carry that sentence; scripts and the runner refuse LinkedIn under
    every setting, and the docs say so."""
    import tomllib
    import events
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    rules = skill.split("## Rules that hold everywhere", 1)[1].split("\n## ", 1)[0]
    assert "**Never LinkedIn or Indeed.**" not in skill, "the v1.2.0 wording is replaced, not left beside the new rule"
    need = {
        "no script or fetch, ever": r"No script or fetch ever opens linkedin\.com",
        "under every setting": r"refuse them under every setting",
        "the default": r"`\[linkedin\] browser = false` \(the default\): never automated\. No browser\s+action "
                       r"opens LinkedIn either",
        "the opt-in": r"`\[linkedin\] browser = true` \(the user's own yes at setup\)",
        "its reference": r"only as\s+\[references/linkedin_browser\.md\]\(references/linkedin_browser\.md\) says",
        "its gate": r"only after `today_view\.py linkedin` prints `LINKEDIN GO`",
        "its limits": r"never a profile, a message, a connection\s+request or anything about a person",
        "a notice ends the day": r"a check or notice from LinkedIn\s+ends it for the day",
        "Indeed never": r"Indeed and the other boards are never opened by any tool, under any\s+setting",
        "the one link": r"plain\s+`linkedin\.com/jobs/view/<id>`, as its job key, never fetched",
        "alert links": r"a link in one is never opened, stored, echoed or\s+hyperlinked",
    }
    for what, pat in need.items():
        assert re.search(pat, rules), f"SKILL.md rules: {what}"
    assert rules.index("browser = false") < rules.index("browser = true"), "the default comes first"

    template = tomllib.loads((SKILL / "templates" / "settings.toml").read_text(encoding="utf-8"))
    assert template["linkedin"] == {"browser": False, "per_day": 25, "min_seconds_between_actions": 4,
                                    "stop_on_warning": True}, template.get("linkedin")
    assert events.LINKEDIN_DEFAULTS == template["linkedin"], "the script's defaults are the template's"

    doc = (SKILL / LINKEDIN_DOC).read_text(encoding="utf-8")
    head, loop = doc.split("## The loop", 1)
    need = {
        "the opt-in setting": r"`\[linkedin\] browser = true`",
        "off means not this file": r"With `browser =\s+false` \(the default\) nothing in this file is done",
        "the gate": r"\*\*Only after `LINKEDIN GO`\.\*\*",
        "any other line": r"Any other line:\s+say it in one plain sentence, open no LinkedIn page",
        "the browser tool only": r"\*\*The browser tool only, in the user's own Chrome\.\*\*",
        "never a script": r"Never the built-in\s+browser, a fetch tool, a script or the runner",
        "scripts still refuse": r"refuse every LinkedIn link under every setting",
        "never Submit": r"\*\*Never Submit\.\*\*",
        "one tab": r"One LinkedIn tab, never two",
        "the pace": r"wait at least the `pace=` seconds",
        "per day": r"\*\*At most `per_day` a day\.\*\*",
        "jobs only": r"Never a person's profile",
        "no messages or connections": r"a message, InMail, connection request",
        "nothing about people": r"Never read or keep anything about a person",
        "the stop": r"`events\.py linkedin-stop --reason <code>`",
        "what stops it": r"On a CAPTCHA or puzzle,\s+a sign-in or verification page, an \"unusual activity\"",
        "never again today": r"open no LinkedIn page again today",
        "never retry": r"Never retry, never work around it",
        "the one link": r"`https://www\.linkedin\.com/jobs/view/<id>`, with nothing after the\s+number",
        "no cookies or tokens": r"never store or print a\s+cookie, a token",
        "sessions only": r"Never in job-morning or job-inbox",
    }
    for what, pat in need.items():
        assert re.search(pat, head), f"{LINKEDIN_DOC} before its first step: {what}"
    for code in events.LINKEDIN_STOPS:
        assert f"`{code}`" in head or f"--reason {code}" in head, f"{LINKEDIN_DOC}: stop reason {code}"
    need = {
        "step 1 is the gate": r"1\. \*\*May I\?\*\* `today_view\.py linkedin`",
        "the text file and the gates": r"`posting\.py --out\s.*--text-file\s.*--gate --args-file claude/args\.json`",
        "the plain link as the key": r"\"_\":\s+\"https://www\.linkedin\.com/jobs/view/<id>\"",
        "duplicates": r"`tracker\.py\s+check --posting",
        "caps": r"`caps\.py check --family <family>",
        "the employer's site": r"follow daily\.md \"/job add <link>\"",
        "the row": r"--source LinkedIn\s+--found-via linkedin:browser",
        "sensitive answers": r"Sensitive answers exactly as apply_batch\.md \"Sensitive confirmations\"",
        "handed over, then Submit is the user's": r"status=\"Filled - awaiting\s+submit\".*click Submit\s+application, "
                                                  r"then type s",
        "a stop mid-job touches nothing more": r"touch\s+nothing more on LinkedIn",
    }
    for what, pat in need.items():
        assert re.search(pat, loop, re.S), f"{LINKEDIN_DOC} loop: {what}"

    setup = (SKILL / "references" / "setup.md").read_text(encoding="utf-8")
    assert re.search(RISK + r"; this mode works slowly\s+in your own browser, and you click Submit", setup), \
        "setup.md: the one plain sentence on the risk"
    assert re.search(r"asked once", setup) and re.search(r"Only a clear yes turns it on", setup), "setup.md: once, a clear yes"
    assert re.search(r"leave `browser =\s+false`, never guess", setup), "setup.md: anything else leaves it off"
    settings = (SKILL / "references" / "settings.md").read_text(encoding="utf-8")
    for key in ("`[linkedin] browser`", "`[linkedin] per_day`", "`[linkedin] min_seconds_between_actions`",
                "`[linkedin] stop_on_warning`"):
        assert key in settings, f"settings.md: {key}"
    assert re.search(RISK, settings) and re.search(r"Only a clear\s+yes turns it on", settings), "settings.md: the notice"
    for name in ("README.md", "SETUP_GUIDE.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(RISK, text), f"{name}: the risk sentence"
        assert re.search(r"/job paste", text) and re.search(r"/job linkedin", text), f"{name}: both commands"
    board = (SKILL / "references" / "ats" / "linkedin.md").read_text(encoding="utf-8")
    assert re.search(r"`false` \(the default\): never automated", board), "ats/linkedin.md: the default, as v1.2.0"
    assert re.search(r"No script and no fetch ever opens `linkedin\.com`", board) and \
        re.search(r"under any setting", board), "ats/linkedin.md: scripts never, whatever the setting"
    paste = (SKILL / "references" / "paste.md").read_text(encoding="utf-8")
    assert re.search(r"Always available, whatever `\[linkedin\] browser` says", paste) and \
        re.search(r"You open no page", paste), "paste.md: always available, nothing opened"
    print("ok the LinkedIn rule: what is automated under each setting, off by default, the gate and limits before "
          "any step, the risk sentence at setup and in the guides")


def test_apply_batch_rules():
    text = (SKILL / "references" / "apply_batch.md").read_text(encoding="utf-8")
    for rule in (r"\*\*Never Submit\.\*\*", r"\*\*Never a CAPTCHA\.\*\*", r"\*\*Never create an account\.\*\*",
                 r"\*\*Never store or type a password\*\*"):
        assert re.search(rule, text), rule
    table = text.split("## Failure taxonomy", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in table.splitlines() if l.startswith("| ")]
    cells = {c.strip() for row in rows for c in row.split("|")[1].split(",")}
    missing = [c for c in FAILURE_CODES if c not in cells]
    assert not missing, missing
    print(f"ok apply_batch.md has the never rules and all {len(FAILURE_CODES)} failure codes")


# ---------- SETUP_GUIDE.md ----------

def test_setup_guide():
    raw = (ROOT / "SETUP_GUIDE.md").read_bytes()
    raw.decode("ascii")
    text = raw.decode("ascii")
    assert "Customize > Skills" not in text
    need = {
        "Plugins upload path": r"Add > Upload\s+plugin",
        "marked UNVERIFIED": r"UNVERIFIED",
        "runner install": r"install_runner\.cmd",
        "Trusted folders": r"Trusted\s+folders",
        "built-in browser sign-in": r"built-in browser, sign in",
        "Firefox cookies": r"Firefox",
        "LinkedIn alerts": r"job alerts on LinkedIn",
        "data export": r"Download your data",
        "usage check": r"Settings > Usage",
        "short days": r"/job short",
        "rejections weekly": r"Rejections come weekly",
        "Submit is the user's": r"Submit is always yours",
        "network egress": r"Allow network\s+egress",
        "never a Project": r"Project",
        "paste": r"`/job paste`",
        "LinkedIn mode is a choice, off unless yes": r"optional, off unless you say yes",
        "LinkedIn risk sentence": RISK + r"; this mode works slowly in your own browser, and\s+you\s+click Submit",
        "LinkedIn stops for the day": r"stops using LinkedIn for the rest of that day",
        "LinkedIn never messages": r"never messages anyone, never sends a connection request",
    }
    for what, pat in need.items():
        assert re.search(pat, text), what
    print("ok SETUP_GUIDE.md: plugin path (UNVERIFIED), runner, Trusted folders, browser, alerts, export, "
          "usage, short days, weekly rejections, Submit is the user's, paste, the LinkedIn choice and its risk; ASCII")


# ---------- build_zip.py ----------

def git_files():
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
                       capture_output=True)
    if r.returncode != 0:
        return None
    return [n for n in r.stdout.decode("utf-8").split("\0") if n]


def checkout_copy(dest):
    """A clean-checkout stand-in: git's view of the repository, copied to dest."""
    names = git_files()
    if names is None:  # no git: copy the packaged tree and the docs
        names = [p.relative_to(ROOT).as_posix() for p in build_zip.packaged(build_zip.Tree(ROOT))]
        names += ["build_zip.py", "README.md", ".gitignore"]
    for n in names:
        src = ROOT / n
        if src.is_file():
            out = dest / n
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
    return dest


def run_build(src):
    r = subprocess.run([sys.executable, str(src / "build_zip.py")], cwd=str(src), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def tree_state(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_build_clean_checkout(tmp):
    src = checkout_copy(tmp / "clean")
    old = src / "dist" / "v1.1.0"
    old.mkdir(parents=True)
    (old / "job.zip").write_bytes(b"v1.1.0 build")
    before = tree_state(src)
    code, out = run_build(src)
    assert code == 0, out
    dist = src / "dist" / f"v{VERSION}"
    names = ["job-search-plugin.zip", "job.zip", f"jobrunner-{VERSION}.zip", "install_runner.cmd", "SETUP_GUIDE.md",
             "job-skill-full.zip"]
    assert sorted(p.name for p in dist.iterdir()) == sorted(names), list(dist.iterdir())
    after = tree_state(src)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert all(k.startswith(f"dist/v{VERSION}/") for k in changed), changed
    assert (old / "job.zip").read_bytes() == b"v1.1.0 build"

    with zipfile.ZipFile(dist / "job-search-plugin.zip") as z:
        plugin = z.namelist()
        manifest = json.loads(z.read(".claude-plugin/plugin.json"))
    assert plugin.count(".claude-plugin/plugin.json") == 1
    assert sum(1 for n in plugin if n.endswith("plugin.json")) == 1, plugin
    for need in ("skills/job/SKILL.md", "agents/job-tailor.md", "agents/job-auditor.md",
                 "skills/job/templates/settings.toml", "skills/job/templates/presets/lifesci_bayarea/watchlist.csv",
                 "skills/job/scripts/jobrunner.py", "skills/job/references/ats/dayforce.md"):
        assert need in plugin, need
    assert all(n.startswith(("skills/job/", "agents/", ".claude-plugin/")) for n in plugin), plugin
    assert manifest == {"name": "job-search", "version": VERSION, "description": DESCRIPTION}, manifest

    with zipfile.ZipFile(dist / "job.zip") as z:
        skill = z.namelist()
    assert skill and all(n.startswith("job/") for n in skill), skill[:5]
    assert not any("agents/" in n for n in skill)
    assert sorted(n[len("job/"):] for n in skill) == sorted(n[len("skills/job/"):] for n in plugin
                                                              if n.startswith("skills/job/"))

    runner = dist / f"jobrunner-{VERSION}.zip"
    with zipfile.ZipFile(runner) as z:
        app = sorted(z.namelist())
    want = sorted(["app/" + p.name for p in (src / "job" / "scripts").glob("*.py")] +
                  ["install_runner.ps1", "uninstall_runner.ps1", "requirements.lock"])
    assert app == want, (app, want)

    sha = hashlib.sha256(runner.read_bytes()).hexdigest()
    cmd = (dist / "install_runner.cmd").read_bytes()
    assert b"\r\n" in cmd and b"\n" not in cmd.replace(b"\r\n", b""), "install_runner.cmd must be CRLF"
    text = cmd.decode("ascii")
    assert "@@" not in text, "a placeholder was left"
    assert f'set "SHA={sha}"' in text, "the embedded SHA-256 is not the runner zip's"
    assert f'set "VER={VERSION}"' in text
    assert f"SHA256 jobrunner-{VERSION}.zip {sha}" in out
    assert (dist / "SETUP_GUIDE.md").read_bytes() == (src / "SETUP_GUIDE.md").read_bytes()

    # The one download for a new user: every part, byte for byte, plus the two guides as .txt.
    with zipfile.ZipFile(dist / "job-skill-full.zip") as z:
        full = {n: z.read(n) for n in z.namelist()}
    parts = ["job-search-plugin.zip", "job.zip", f"jobrunner-{VERSION}.zip", "install_runner.cmd"]
    assert sorted(full) == sorted("job-skill/" + n for n in parts + ["START_HERE.txt", "SETUP_GUIDE.txt"]), sorted(full)
    for n in parts:
        assert full["job-skill/" + n] == (dist / n).read_bytes(), n
    start = full["job-skill/START_HERE.txt"].replace(b"\r\n", b"\n")
    assert start == (src / "GETTING_STARTED.md").read_bytes().replace(b"\r\n", b"\n")
    assert b"releases/latest/download/job-skill-full.zip" in start, "the guide links the full download"
    for word in (b"install_runner.cmd", b"job-search-plugin.zip", b"/job setup", b"Extract All"):
        assert word in start, word

    code, out2 = run_build(src)  # a rebuild gives the same bytes
    assert code == 0, out2
    assert hashlib.sha256(runner.read_bytes()).hexdigest() == sha, "the runner zip is not deterministic"
    print(f"ok build_zip.py on a clean checkout: five artifacts, plugin and skill zips, runner zip, "
          f"install_runner.cmd hash {sha[:12]}..., dist/v1.1.0 untouched")
    return src


def expect_fail(base, tmp, label, plant, needle):
    src = tmp / label
    shutil.copytree(base, src, ignore=shutil.ignore_patterns("dist"))
    plant(src)
    code, out = run_build(src)
    fails = [l for l in out.splitlines() if l.startswith("FAIL")]
    assert code == 1, f"{label}: exit {code}\n{out}"
    assert any(needle in l for l in fails), f"{label}: no FAIL line naming {needle}\n{out}"
    assert not (src / "dist" / f"v{VERSION}").exists(), f"{label}: wrote artifacts despite FAIL"
    shutil.rmtree(src, ignore_errors=True)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def test_guard(base, tmp):
    expect_fail(base, tmp, "connections", lambda s: write(s / "job/templates/Connections.csv", "First Name\n"),
                "job/templates/Connections.csv")
    expect_fail(base, tmp, "xlsx", lambda s: write(s / "job/templates/applications.xlsx", b"PK\x03\x04"),
                "job/templates/applications.xlsx")
    expect_fail(base, tmp, "email", lambda s: write(s / "tests/notes.md", f"Write to {GMAIL}\n"),
                "tests/notes.md")
    expect_fail(base, tmp, "email_pkg", lambda s: write(s / "job/references/contact.md",
                                                        f"# Contact\n\n{GMAIL}\n"),
                "job/references/contact.md")
    expect_fail(base, tmp, "nonascii", lambda s: write(s / "job/references/note.md", "# Caf" + chr(0xe9) + "\n"),
                "job/references/note.md")
    expect_fail(base, tmp, "token", lambda s: write(s / "tests/fixtures/alert.txt",
                                                    TOKEN_URL + "\n"),
                "tests/fixtures/alert.txt")
    expect_fail(base, tmp, "jsonl", lambda s: write(s / "job/templates/leads.jsonl", "{}\n"),
                "job/templates/leads.jsonl")
    expect_fail(base, tmp, "state_dir", lambda s: write(s / "job/templates/state/pool.md", "x\n"),
                "job/templates/state/pool.md")

    def long_desc(s):
        p = s / "job" / "SKILL.md"
        t = p.read_text(encoding="utf-8")
        write(p, t.replace(DESCRIPTION, DESCRIPTION + "x" * (201 - len(DESCRIPTION)), 1))
    expect_fail(base, tmp, "desc201", long_desc, "description must be 1-200 characters (is 201)")

    # Fixtures may hold synthetic data, and LinkedIn's alert senders are allowed.
    src = tmp / "fixture_ok"
    shutil.copytree(base, src, ignore=shutil.ignore_patterns("dist"))
    write(src / "tests/fixtures/notes.md", f"Write to {GMAIL}\n")
    code, out = run_build(src)
    assert code == 0, out
    shutil.rmtree(src, ignore_errors=True)
    print("ok the PII guard: Connections.csv, .xlsx, a gmail address outside fixtures, non-ASCII, a sign-in "
          "token, .jsonl, a private folder and a 201-character description each fail with the file named")


def test_walkthrough():
    text = (HERE / "walkthrough_v12.md").read_text(encoding="utf-8")
    text.encode("ascii")
    for need in ("tracker.py check", "IN QUEUE", "caps.py check", "ADDED P-", "build_resume.py", "audit",
                 "HANDOVER", "today_view.py card", "Starting #1 now", "Gaps"):
        assert need in text, need
    print("ok walkthrough_v12.md covers the single-job flow, the card and the gaps")


def main():
    test_skill_md()
    test_links_resolve()
    test_routing_table()
    test_commands_match_argparse()
    test_output_lines_exist()
    test_no_job_board_fetch()
    test_linkedin_rule()
    test_apply_batch_rules()
    test_setup_guide()
    test_walkthrough()
    tmp = Path(tempfile.mkdtemp(prefix="job_skill_package_"))
    try:
        base = test_build_clean_checkout(tmp)
        test_guard(base, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS")


if __name__ == "__main__":
    main()
