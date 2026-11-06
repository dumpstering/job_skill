"""Check job/scripts/argfile.py and the --args-file / --json-file forms.

Outside text (an email subject, a company name, a form question, a link)
reaches a script only through a JSON file under <folder>/claude/, never on a
command line. This checks the expansion rules and refusals, that a hostile
value (quotes, $( ), backticks, a newline, a semicolon, a non-ASCII letter)
reaches every script that takes free text unchanged, end to end for
tracker.py (mode M and through a mode L event), answers.py add and
jobrunner.py request --json-file, and that no reference puts outside text on
a command line.

Usage: python tests/test_argfile.py
Prints ok <name> per test and PASS last.
"""
import argparse
import contextlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "job"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ["JOB_SKILL_NOW"] = "2026-11-20T18:00:00Z"
import argfile  # noqa: E402
import jobfs  # noqa: E402

# Built from parts so this file stays ASCII; never run through a shell.
HOSTILE = ("Children's \"Lab\" $(echo x) `echo y` ; echo z & | > <\n"
           "second line caf\u00e9 \u00c5ngstr\u00f6m -- --root=/ %PATH% \\")


def new_root():
    root = Path(tempfile.mkdtemp(prefix="job_argfile_test_"))
    (root / "claude").mkdir()
    return root


def write_args(root, obj, name="args.json"):
    p = root / "claude" / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def refused(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            argfile.expand(argv)
        except SystemExit as e:
            assert e.code == 1, e.code
            return out.getvalue()
    raise AssertionError(f"not refused: {argv}")


def test_expand_rules():
    root = new_root()
    assert argfile.expand(["add", "--root", str(root), "--company", "Acme"]) == \
        ["add", "--root", str(root), "--company", "Acme"], "no --args-file: unchanged"
    p = write_args(root, {"company": HOSTILE, "job_key": "greenhouse:1", "--role": "-Scientist", "used": True,
                          "force": False, "notes": None, "req-id": ["R1", "R2"], "n": 7, "_": ["P-1a2b3c4d", "x=y"]})
    out = argfile.expand(["update", "--root", str(root), "--args-file", "claude/args.json"])
    assert out == ["update", "--root", str(root), "P-1a2b3c4d", "x=y", "--company=" + HOSTILE,
                   "--job-key=greenhouse:1", "--role=-Scientist", "--used", "--req-id", "R1", "R2", "--n=7"], out
    assert not p.exists(), "the file is removed once read"
    # an absolute path and the --x=y forms work too; a lone positional string
    write_args(root, {"_": "https://example.com/jobs/1"})
    out = argfile.expand([f"--root={root}", f"--args-file={root / 'claude' / 'args.json'}"])
    assert out == [f"--root={root}", "https://example.com/jobs/1"], out
    # parse() adds --args-file to the parser's help and parses the expanded line
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--company")
    write_args(root, {"company": HOSTILE})
    ns = argfile.parse(ap, ["--root", str(root), "--args-file", "claude/args.json"])
    assert ns.company == HOSTILE and "--args-file" in ap.format_help()
    print("ok expand: strings as --key=value, flags, lists, positionals first, keys with _ or dashes, read once")


def test_refusals():
    root = new_root()
    outside = Path(tempfile.mkdtemp(prefix="job_argfile_outside_"))
    (outside / "a.json").write_text('{"company": "x"}', encoding="utf-8")
    (root / "loose.json").write_text('{"company": "x"}', encoding="utf-8")
    base = ["add", "--root", str(root), "--args-file"]
    cases = {
        "outside the folder": base + [str(outside / "a.json")],
        "in the root, not claude/": base + ["loose.json"],
        "climbs out": base + ["claude/../loose.json"],
        "missing": base + ["claude/none.json"],
        "no --root": ["add", "--args-file", str(outside / "a.json")],
        "no path": base,
    }
    for what, argv in cases.items():
        assert "ERROR --args-file" in refused(argv), what
    assert (outside / "a.json").exists() and (root / "loose.json").exists(), "a refused file is never removed"
    (root / "claude" / "a.txt").write_text('{"company": "x"}', encoding="utf-8")
    assert "not a .json" in refused(base + ["claude/a.txt"])
    (root / "claude" / "big.json").write_text('{"notes": "' + "x" * (64 * 1024) + '"}', encoding="utf-8")
    assert "64 KB" in refused(base + ["claude/big.json"])
    for what, body in {"not an object": "[1, 2]", "bad JSON": "{'a': 1}", "NaN": '{"n": NaN}',
                       "root key": '{"root": "C:/"}', "args-file key": '{"args-file": "claude/b.json"}',
                       "object value": '{"company": {"a": 1}}', "list item as an option": '{"status": ["--force"]}',
                       "positional as an option": '{"_": ["--transport=direct"]}', "empty list": '{"status": []}',
                       "not a name": '{"comp any": "x"}', "bool in a list": '{"status": [true]}'}.items():
        (root / "claude" / "args.json").write_text(body, encoding="utf-8")
        assert "ERROR --args-file" in refused(base + ["claude/args.json"]), what
        assert not (root / "claude" / "args.json").exists(), f"{what}: read once, even when refused"
    write_args(root, {"company": "x"})
    assert "given both" in refused(base + ["claude/args.json", "--company", "y"])
    write_args(root, {"company": "x"})
    assert "twice" in refused(base + ["claude/args.json", "--args-file", "claude/args.json"])
    shutil.rmtree(outside, ignore_errors=True)
    print("ok refusals: outside claude/, not .json, over 64 KB, not an object, root or args-file keys, "
          "values that would read as options, no --root, given twice")


class _Got(Exception):
    def __init__(self, ns):
        self.ns = ns


def parsed(module, argv):
    """The namespace main() parses for argv (nothing runs after the parse)."""
    orig = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        raise _Got(orig(self, args, namespace))
    argparse.ArgumentParser.parse_args = grab
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            importlib.import_module(module).main(argv)
    except _Got as g:
        return g.ns
    finally:
        argparse.ArgumentParser.parse_args = orig
    raise AssertionError(f"{module}.main never parsed")


# script -> (command line before --args-file, the args object, {namespace attribute: value})
PER_SCRIPT = {
    "tracker": (["log-email"], {"message-id": "m1", "email-date": "2026-11-05", "from": HOSTILE, "subject": HOSTILE,
                                "class": "Other", "quote": HOSTILE, "change": HOSTILE},
                {"sender": HOSTILE, "subject": HOSTILE, "quote": HOSTILE, "change": HOSTILE}),
    "answers": (["add"], {"question": HOSTILE, "answer": HOSTILE, "kind": "text"},
                {"question": HOSTILE, "answer": HOSTILE}),
    "caps": (["check"], {"company": HOSTILE, "url": "https://example.com/j?a=1&b=$(x)"},
             {"company": HOSTILE, "url": "https://example.com/j?a=1&b=$(x)"}),
    "worksearch": (["export"], {"state": HOSTILE}, {"state": HOSTILE}),
    "posting": ([], {"_": "https://example.com/jobs/1?x=1&y='2'", "out": "x.md", "text-file": "p.txt",
                     "company": HOSTILE, "role": HOSTILE, "location": HOSTILE},
                {"url": "https://example.com/jobs/1?x=1&y='2'", "company": HOSTILE, "role": HOSTILE,
                 "location": HOSTILE}),
    "events": (["emit", "--type", "skipped"], {"json": HOSTILE}, {"json": HOSTILE}),
    "network": (["match"], {"company": HOSTILE}, {"company": HOSTILE}),
    "report": (["usage"], {"plan": HOSTILE}, {"plan": HOSTILE}),
    "jobrunner": (["request", "probe_url"], {"json": HOSTILE}, {"json": HOSTILE}),
    "netfetch": (["check"], {"_": "https://example.com/j?a=1&b=$(x)"}, {"url": "https://example.com/j?a=1&b=$(x)"}),
    "gates": (["explain"], {"job-key": "url:example.com/j?a=1&b=$(x)"}, {"job_key": "url:example.com/j?a=1&b=$(x)"}),
    "build_resume": (["resume.json", "--out", "out"], {"base": HOSTILE}, {"base": HOSTILE}),
}


def test_every_script_gets_the_value():
    for script, (head, obj, want) in PER_SCRIPT.items():
        root = new_root()
        write_args(root, obj)
        ns = parsed(script, head + ["--root", str(root), "--args-file", "claude/args.json"])
        for attr, value in want.items():
            assert getattr(ns, attr) == value, (script, attr, getattr(ns, attr))
        assert not (root / "claude" / "args.json").exists(), script
        shutil.rmtree(root, ignore_errors=True)
    print(f"ok a value with quotes, $( ), backticks, a newline, ; and a non-ASCII letter reaches "
          f"{len(PER_SCRIPT)} scripts unchanged: {', '.join(PER_SCRIPT)}")


def run_main(module, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = importlib.import_module(module).main(argv)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


def test_end_to_end():
    import openpyxl
    # mode M: the tracker writes the workbook directly
    root = new_root()
    (root / "config").mkdir()
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "M"\n', encoding="utf-8")
    write_args(root, {"message-id": "m-1", "email-date": "2026-11-05", "from": HOSTILE, "subject": HOSTILE,
                      "class": "Other", "quote": HOSTILE, "kind": "reply"})
    code, out = run_main("tracker", ["log-email", "--root", str(root), "--args-file", "claude/args.json"])
    assert code == 0 and "LOGGED m-1" in out, out
    ws = openpyxl.load_workbook(root / "applications.xlsx")["Email log"]
    head = [c.value for c in ws[1]]
    row = dict(zip(head, [c.value for c in ws[2]]))
    assert row["Subject"] == HOSTILE and row["From"] == HOSTILE and row["Quote"] == HOSTILE, row
    # update's positionals: the ID and field=value pairs
    write_args(root, {"company": HOSTILE, "role": "Scientist", "url": "https://example.com/jobs/9?a=1&b=2"})
    code, out = run_main("tracker", ["add", "--root", str(root), "--args-file", "claude/args.json"])
    assert code == 0 and out.strip() == "ADDED 1", out
    write_args(root, {"_": ["1", "next_step=" + HOSTILE.replace("\n", " ")]})
    code, out = run_main("tracker", ["update", "--root", str(root), "--args-file", "claude/args.json"])
    assert code == 0, out
    ws = openpyxl.load_workbook(root / "applications.xlsx")["Applications"]
    head = [c.value for c in ws[1]]
    row = dict(zip(head, [c.value for c in ws[2]]))
    assert row["Company"] == HOSTILE and row["Next step"] == HOSTILE.replace("\n", " "), (row, app)
    # mode L: the event carries the expanded command line, and the runner's replay reads the same value
    root = new_root()
    (root / "config").mkdir()
    (root / "config" / "settings.toml").write_text('[runner]\nmode = "L"\n', encoding="utf-8")
    write_args(root, {"company": HOSTILE, "role": "Scientist"})
    code, out = run_main("tracker", ["add", "--root", str(root), "--args-file", "claude/args.json"])
    assert code == 0 and out.strip().startswith("ADDED P-"), out
    evs = list((root / "runner" / "in" / "events").glob("*_tracker.json"))
    argv = json.loads(evs[0].read_text(encoding="utf-8"))["payload"]["argv"]
    assert "--company=" + HOSTILE in argv and not any("args-file" in t for t in argv), argv
    code, out = run_main("tracker", ["apply-events", "--root", str(root)])
    assert code == 0 and "APPLIED 1" in out, out
    ws = openpyxl.load_workbook(root / "applications.xlsx")["Applications"]
    head = [c.value for c in ws[1]]
    assert dict(zip(head, [c.value for c in ws[2]]))["Company"] == HOSTILE
    # a real process (sys.argv), as Claude runs it: only the root and the file's path are on the command line
    write_args(root, {"_": [HOSTILE.split("\n")[0][:30]]})
    r = subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "seen", "--root", str(root), "--args-file",
                        "claude/args.json"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == "NEW " + HOSTILE.split("\n")[0][:30], r.stdout + r.stderr
    print("ok end to end: tracker log-email, add and update in mode M, an add through a mode L event, and "
          "seen in its own process keep the value exactly")


def test_answers_and_request():
    root = new_root()
    for name in ("profile.md", "achievement_library.md", "master_resume.md"):
        (root / name).write_text("# x\n", encoding="utf-8")
    shutil.copy(SKILL / "templates" / "answers.md", root / "answers.md")
    q = "Why \"this\" lab; $(really)? `now` caf\u00e9"
    write_args(root, {"question": q, "answer": "Because it's the work I do.", "kind": "text", "scope": "any",
                      "sensitive": "no", "source": "user:2026-11-20"})
    code, out = run_main("answers", ["add", "--root", str(root), "--args-file", "claude/args.json"])
    assert code == 0, out
    assert q in (root / "answers.md").read_text(encoding="utf-8"), out
    # jobrunner request --json-file: the request file holds the values as written
    root = new_root()
    for sub in ("runner/in/requests", "config"):
        (root / sub).mkdir(parents=True)
    company = "Children's Hospital $(x) `y`; caf\u00e9"
    write_args(root, {"url": "https://careers.example.com/jobs/1?a=1&b=2", "company": company,
                      "title": "Scientist"}, name="req.json")
    code, out = run_main("jobrunner", ["request", "probe_url", "--root", str(root), "--json-file", "claude/req.json"])
    assert code == 0 and out.startswith("REQUEST "), out
    req = json.loads(next((root / "runner" / "in" / "requests").glob("*.json")).read_text(encoding="utf-8"))
    args = req.get("args", req)
    assert args["company"] == company and args["url"].endswith("a=1&b=2"), req
    assert not (root / "claude" / "req.json").exists()
    (root / "elsewhere.json").write_text('{"url": "https://example.com/"}', encoding="utf-8")
    code, out = run_main("jobrunner", ["request", "probe_url", "--root", str(root), "--json-file", "elsewhere.json"])
    assert code == 1 and out.startswith("ERROR --json-file"), out
    print("ok answers.py add and jobrunner.py request --json-file keep the value exactly; a request file "
          "outside claude/ is refused")


def test_folder_names_are_inert():
    name = jobfs.job_folder_name("2026-11-20", "Acme $(echo x) `id` Bio", "Scientist $HOME")
    assert "$" not in name and "`" not in name and '"' not in name and "\\" not in name, name
    print(f"ok a job folder name holds no $, backtick, quote or backslash: {name!r}")


# ---------- the references ----------

FREE = ("company", "title", "role", "location", "subject", "from", "quote", "change", "question", "answer",
        "contact", "employer", "description", "text", "notes", "email", "skill", "url", "referral-url", "street",
        "city", "zip", "phone", "main-website", "also", "base")


def code_spans(text):
    spans = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        spans += block.splitlines()
    rest = re.sub(r"```[a-z]*\n.*?```", "", text, flags=re.S)
    spans += [s.replace("\n", " ") for s in re.findall(r"`([^`]+)`", rest)]
    return spans


def doc_problems(name, text):
    problems = []
    for span in code_spans(text):
        if not re.search(r"\b[a-z_]+\.py\b", span):
            continue
        for m in re.finditer(r"--([a-z][a-z-]*)(?:\s+|=)(\S)", span):
            if m.group(1) in FREE and m.group(2) in "\"'<.":
                problems.append(f"{name}: --{m.group(1)} takes outside text inline: {span.strip()[:80]}")
        if re.search(r"--json\s+'", span):
            problems.append(f"{name}: --json '...' inline: {span.strip()[:80]}")
        if re.search(r"\b(posting\.py|tracker\.py seen)\s+<", span):
            problems.append(f"{name}: a link or message id inline: {span.strip()[:80]}")
        if re.search(r"tracker\.py update <ID>[^`]*(notes\+=|next_step=\"<|referral_url=<)", span):
            problems.append(f"{name}: update with outside text inline: {span.strip()[:80]}")
    return problems


def test_references_use_the_file():
    docs = [(SKILL / "SKILL.md")] + sorted((SKILL / "references").rglob("*.md")) \
        + sorted((ROOT / "plugin" / "agents").glob("*.md"))
    problems = []
    for p in docs:
        problems += doc_problems(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
    for extra in ("README.md", "SETUP_GUIDE.md"):
        problems += doc_problems(extra, (ROOT / extra).read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    scripts = skill.split("## Scripts", 1)[1].split("\n## ", 1)[0]
    assert "--args-file claude/args.json" in scripts and "never goes on a command line" in scripts, \
        "SKILL.md Scripts states the rule once, with an example"
    assert "FOLDER" in skill.split("3. **Get the posting", 1)[1].split("4. **Assess", 1)[0], \
        "SKILL.md step 3 builds the job folder from posting.py's FOLDER line"
    print(f"ok no reference puts outside text on a command line ({len(docs)} files); SKILL.md states the rule "
          "and step 3 uses the FOLDER line")


def main():
    test_expand_rules()
    test_refusals()
    test_every_script_gets_the_value()
    test_end_to_end()
    test_answers_and_request()
    test_folder_names_are_inert()
    test_references_use_the_file()
    print("PASS")


if __name__ == "__main__":
    main()
