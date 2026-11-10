"""Options from a JSON file, so outside text never passes through a shell.

A value that came from an email, a posting, a web page, a form or the
user's own words reaches a script only through a JSON file that Claude
writes with its file tool under <folder>/claude/ and passes as
--args-file <path>; no shell ever parses those values:

    python tracker.py log-email --root "<folder>" --args-file claude/args.json

    claude/args.json:
    {"message-id": "18c2f0a1b2c3d4e5", "email-date": "2026-11-05",
     "from": "recruiting@example.com", "subject": "Interview: Scientist II",
     "class": "Interview", "quote": "Can you talk on Tuesday?", "kind": "reply"}

Each key is an option name without its dashes ("job-key" or "job_key"),
spelled out in full: "--<key>" (with '_' read as '-') must be exactly one of
the long options of the command that reads it (the subcommand's own parser),
so no abbreviation or unknown name gets through, and every parser expanded
here parses with allow_abbrev=False. A string or a number gives
--key=<value>, true gives the flag --key (false or null leaves it out), and a
list gives --key v1 v2 ... (an option that takes several values). The key
"_" holds the positional arguments, a string or a list (tracker.py update's
ID and field=value pairs, posting.py's link). Nothing in a value is ever
read as another option.

The file lies inside <root>/claude/, where <root> is the --root given on the
command line (a relative --args-file path is read from the root), ends in
.json and holds at most 64 KB. It is removed once read, so a value is never
used twice and the next command starts from a fresh file. Every script that
takes free text calls expand() (or parse()) at the top of main(); with no
--args-file the command line comes back as it was. jobrunner.py request
--json-file reads its request arguments through load(), with the same rules.

A refusal prints `ERROR --args-file ...` and exits 1. Stdlib (and jobfs) only.
"""
import argparse
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402  the safe JSON loader (shared decision R3)

OPTION = "--args-file"
MAX_BYTES = 64 * 1024
FOLDER = "claude"
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Keys a file may not set: the root confines the file, and a file never names another.
REFUSED_KEYS = ("root", "args-file", "json-file", "help", "h")


class ArgfileError(Exception):
    pass


def _fail(msg):
    # ASCII only: this runs before a script sets its output to UTF-8 (a path may hold any letter)
    print(f"ERROR {OPTION} {msg}".encode("ascii", "backslashreplace").decode("ascii"))
    raise SystemExit(1)


def root_of(argv):
    """The last --root value on the command line, or None."""
    root = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--root" and i + 1 < len(argv):
            root, i = argv[i + 1], i + 2
            continue
        if tok.startswith("--root="):
            root = tok.split("=", 1)[1]
        i += 1
    return root


def checked_path(path, root):
    """The resolved file, inside <root>/claude/, ending .json, at most 64 KB."""
    if not root:
        raise ArgfileError("needs --root \"<folder>\" on the command line")
    base = Path(root)
    if not base.is_dir():
        raise ArgfileError(f"needs --root to be the JobSearch folder, got {root}")
    base = base.resolve()
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ArgfileError(f"{path} not found: write it under {FOLDER}/ in the JobSearch folder, then run again")
    allowed = base / FOLDER
    if not real.is_relative_to(allowed) or real == allowed:
        raise ArgfileError(f"{path} is outside {FOLDER}/ in the JobSearch folder (refused)")
    if real.suffix.lower() != ".json":
        raise ArgfileError(f"{path} is not a .json file (refused)")
    if not real.is_file():
        raise ArgfileError(f"{path} is not a file (refused)")
    if real.stat().st_size > MAX_BYTES:
        raise ArgfileError(f"{path} is over 64 KB (refused)")
    return real


def load(path, root):
    """The JSON object in path, after the checks above; the file is removed once read."""
    real = checked_path(path, root)
    try:
        with open(real, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise ArgfileError(f"{path} cannot be read: {e}")
    try:
        os.remove(real)  # used once: the next command writes a fresh file
    except OSError:
        pass
    if len(raw) > MAX_BYTES:
        raise ArgfileError(f"{path} is over 64 KB (refused)")
    try:
        obj = jobfs.loads_json(raw, allow_nan=False)
    except jobfs.BadJson as e:  # not UTF-8, not JSON, NaN, or nested deeper than 64
        raise ArgfileError(f"{path} is not valid UTF-8 JSON ({e}); write it again")
    if not isinstance(obj, dict):
        raise ArgfileError(f"{path} must hold one JSON object")
    return obj


def _scalar(key, v):
    """One value as text: a string as it is, a number in decimal."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ArgfileError(f"key {key!r}: a value here is a string or a number")
    if isinstance(v, float) and not math.isfinite(v):
        raise ArgfileError(f"key {key!r}: not a finite number")
    return str(v)


def _bare(key, s):
    """A list item or positional stands alone on the command line: it must not read as an option."""
    if s.startswith("-"):
        raise ArgfileError(f"key {key!r}: the value {s[:20]!r} starts with '-' (give it as a string option)")
    return s


def _subparsers(parser):
    return [a for a in getattr(parser, "_actions", []) if isinstance(a, argparse._SubParsersAction)]


def no_abbrev(parser):
    """allow_abbrev=False on the parser and every subcommand parser under it,
    so '--roo' never stands for '--root' (argparse reads the flag at parse time)."""
    seen, todo = set(), [parser]
    while todo:
        p = todo.pop()
        if p is None or id(p) in seen:
            continue
        seen.add(id(p))
        p.allow_abbrev = False
        for sub in _subparsers(p):
            todo += list(sub.choices.values())


def _takes_value(parser, tok):
    """True when tok is one of parser's options that reads the next token as its value."""
    if "=" in tok:
        return False
    act = getattr(parser, "_option_string_actions", {}).get(tok)
    return act is not None and act.nargs != 0


def target_parser(parser, argv):
    """The parser that will read the options a file adds at the end of argv:
    the (nested) subcommand argv names, else the parser itself."""
    p = parser
    tokens = list(argv)
    while True:
        subs = _subparsers(p)
        if not subs:
            return p
        i, pick = 0, None
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("-"):
                i += 2 if _takes_value(p, tok) else 1
                continue
            pick = i
            break
        if pick is None or tokens[pick] not in subs[0].choices:
            raise ArgfileError("needs the command name before --args-file")
        p, tokens = subs[0].choices[tokens[pick]], tokens[pick + 1:]


def long_options(parser):
    """Every long option string of one parser ('--job-key', '--root', ...)."""
    return {s for a in getattr(parser, "_actions", []) for s in a.option_strings if s.startswith("--")}


def options(obj, allowed=None):
    """The command-line tokens for one args object: positionals first, then
    options. allowed is the target parser's long options: a key is taken only
    when '--<key>' is exactly one of them (shared decision R4)."""
    positional, opts = [], []
    for key, v in obj.items():
        if key == "_":
            items = v if isinstance(v, list) else [v]
            positional += [_bare(key, _scalar(key, x)) for x in items]
            continue
        name = key.lstrip("-")
        if not KEY_RE.match(name):
            raise ArgfileError(f"key {key!r} is not an option name")
        name = name.replace("_", "-")
        if name in REFUSED_KEYS:
            raise ArgfileError(f"key {key!r} cannot come from the file (refused)")
        flag = "--" + name
        if allowed is None or flag not in allowed:
            raise ArgfileError(f"key {key!r} is not an option of this command (spell the option out in full)")
        if v is None or v is False:
            continue
        if v is True:
            opts.append(flag)
        elif isinstance(v, list):
            if not v:
                raise ArgfileError(f"key {key!r}: an empty list")
            opts += [flag] + [_bare(key, _scalar(key, x)) for x in v]
        elif isinstance(v, dict):
            raise ArgfileError(f"key {key!r}: an object is not an option value")
        else:
            opts.append(f"{flag}={_scalar(key, v)}")
    return positional, opts


def add_option(parser):
    """List --args-file in the script's --help (expand() removes it before parsing)."""
    if parser is not None and OPTION not in getattr(parser, "_option_string_actions", {}):
        parser.add_argument(OPTION, metavar="PATH",
                            help="options from a JSON file under <folder>/claude/ (argfile.py): "
                                 "text from an email, a posting or a form goes there, never inline")


def expand(argv=None, parser=None):
    """argv with --args-file <path> replaced by the options in that file.
    parser is the script's parser: it names the options a file may set, and
    it (with every subcommand parser) is set to allow_abbrev=False."""
    argv = list(sys.argv[1:] if argv is None else argv)
    add_option(parser)
    no_abbrev(parser)
    at = [i for i, tok in enumerate(argv) if tok == OPTION or tok.startswith(OPTION + "=")]
    if not at:
        return argv
    try:
        if len(at) > 1:
            raise ArgfileError("given twice")
        i = at[0]
        if argv[i] == OPTION:
            if i + 1 >= len(argv):
                raise ArgfileError("needs a path")
            path, rest = argv[i + 1], argv[:i] + argv[i + 2:]
        else:
            path, rest = argv[i].split("=", 1)[1], argv[:i] + argv[i + 1:]
        obj = load(path, root_of(rest))  # read once and removed, even when a key is refused below
        if parser is None:
            raise ArgfileError("needs the script's parser (argfile.parse(parser) or expand(argv, parser))")
        positional, opts = options(obj, long_options(target_parser(parser, rest)))
        inline = {tok.split("=", 1)[0] for tok in rest if tok.startswith("--")}
        twice = sorted({t.split("=", 1)[0] for t in opts if t.startswith("--")} & inline)
        if twice:
            raise ArgfileError(f"{', '.join(twice)} given both inline and in the file")
    except ArgfileError as e:
        _fail(str(e))
    return rest + positional + opts


def parse(parser, argv=None):
    """parser.parse_args on the expanded command line (the one-line hook)."""
    return parser.parse_args(expand(argv, parser))
