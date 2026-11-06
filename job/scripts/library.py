"""Achievement library IDs: stable [L-0001] labels on every entry, and a check
that nothing cites an ID the library no longer has.

Usage:
    python library.py ids   --root <folder>  [--file achievement_library.md]
    python library.py check --root <folder>  [--file ...] [--cites FILE ...]

ids gives each entry without an ID the next free one, in file order. It never
renumbers, never reuses a retired ID (a `<!-- last-id: L-0042 -->` line keeps
the high-water mark) and copies the file to backups/ before it writes. A run
with nothing to assign changes nothing.

check prints DANGLING (an ID cited by a variant, a Tier A resume.json,
answers.md or techniques.md that the library lacks), DUPLICATE and NO-ID
lines, then `LIBRARY ok` or `LIBRARY fail`.

Entries are the `- ` lines under `### ` headings; indented lines continue the
entry above. "Not held: ..." lines and "Confirmed <Mon YYYY>: ..." lines (a
quick-check answer the user gave, such as "title unchanged") get no ID. Role
headings are `### Job Title | Company | Mon YYYY - Mon YYYY` (v1.1).

check reads techniques.md's technique lines and answers.md's answer lines
only, so the format examples in those files are never DANGLING.

Other scripts import parse(), load() and load_techniques(), and entry_hash(),
cited_ids(), changed_cited() and final_stale() for the same-day check: a
correction replaces an entry's text, so a resume built on the old text (a
variant's approval, a Tier A build's final.json "entries") no longer rests on it.
Exit codes: 0 ok, 1 bad input or a failed check.
"""
import argparse
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402

ID_RE = re.compile(r"^(\s*[-*]\s+)\[(L-(\d{4,}))\]\s*")
ENTRY_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
HEADING_RE = re.compile(r"^###\s+(.*?)\s*$")
LAST_ID_RE = re.compile(r"^<!--\s*last-id:\s*L-(\d{4,})\s*-->\s*$")
CITED_RE = re.compile(r"(?<![A-Za-z0-9-])L-\d{4,}(?![0-9])")
NOT_HELD = re.compile(r"^not held\b", re.I)
# "Confirmed Nov 2026: title unchanged ..." records a quick-check answer; it is no achievement.
CONFIRMED = re.compile(r"^confirmed\b", re.I)
BANK_LINE = re.compile(r"^[-*]\s+(?:ANS|JOB)-\d+\s*\|")  # answers.md lines (top level)


@dataclass
class Entry:
    id: str            # "L-0001", or "" before ids runs
    text: str          # the entry without its ID, continuation lines joined
    heading: str       # the ### heading above it
    line: int          # 1-based line number of the "- " line
    held: bool = True  # False for "Not held: ..." lines
    note: bool = False  # True for "Confirmed ...: ..." lines (no ID)


@dataclass
class Library:
    entries: list = field(default_factory=list)
    last_mark: int = 0          # the last-id high-water mark, 0 if absent

    @property
    def by_id(self):
        return {e.id: e for e in self.entries if e.id}

    def duplicates(self):
        seen, dups = {}, []
        for e in self.entries:
            if e.id and e.id in seen:
                dups.append((e.id, seen[e.id], e.line))
            seen.setdefault(e.id, e.line)
        return dups

    def high_water(self):
        nums = [int(e.id[2:]) for e in self.entries if e.id]
        return max(nums + [self.last_mark])


def parse(text):
    """Library entries in file order. Fenced code blocks are skipped."""
    lib, heading, fence, current = Library(), None, False, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r\n")
        if line.strip().startswith("```"):
            fence, current = not fence, None
            continue
        if fence:
            continue
        mark = LAST_ID_RE.match(line.strip())
        if mark:
            lib.last_mark = max(lib.last_mark, int(mark.group(1)))
            continue
        h = HEADING_RE.match(line)
        if h:
            heading, current = h.group(1), None
            continue
        m = ENTRY_RE.match(line)
        if m and heading is not None and len(m.group(1)) < 2:
            idm = ID_RE.match(line)
            body = line[idm.end():] if idm else m.group(2)
            current = Entry(idm.group(2) if idm else "", body.strip(), heading, n,
                            held=not NOT_HELD.match(body.strip()), note=bool(CONFIRMED.match(body.strip())))
            lib.entries.append(current)
            continue
        if current is not None and line.strip() and line[:1] in (" ", "\t"):
            current.text = f"{current.text} {line.strip()}"
            continue
        current = None
    return lib


def load(path):
    """parse() of a file; an empty Library when the file is missing."""
    try:
        return parse(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return Library()


def entry_hash(entry):
    """sha256 of one entry as a resume relies on it: its role heading and text
    (a variant's approval and a Tier A build's final.json use this form)."""
    return hashlib.sha256(f"{entry.heading}\n{entry.text}".encode("utf-8")).hexdigest()


def cited_ids(data):
    """Every library ID the "ev" lists in a resume JSON object cite, sorted."""
    ids, stack = set(), [data]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if isinstance(x.get("ev"), list):
                ids.update(i for i in x["ev"] if isinstance(i, str))
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return sorted(ids)


_LOADED = {}  # library path -> ((mtime_ns, size), {id: entry_hash}), so one run parses each library once


def hashes(path):
    """{id: entry_hash} for the library at path; {} when it is missing or unreadable."""
    p = Path(path)
    try:
        st = p.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    got = _LOADED.get(str(p))
    if got and got[0] == stamp:
        return got[1]
    try:
        by_id = {i: entry_hash(e) for i, e in load(p).by_id.items()}
    except (OSError, UnicodeDecodeError):
        by_id = {}
    _LOADED[str(p)] = (stamp, by_id)
    return by_id


def changed_cited(path, entries):
    """The IDs in entries ({id: entry_hash}) whose library entry is gone or
    whose text or role heading changed since the hash was taken."""
    now = hashes(path)
    return sorted(i for i, h in entries.items() if now.get(i) != h)


def final_stale(folder, final):
    """True when a job folder's final.json (a strict Tier A build) cites a
    library entry the user corrected after that build (shared decision 5):
    its "entries" ({id: entry_hash}) no longer match the library at
    <root>/achievement_library.md, two levels above the job folder. A
    final.json without entries (an older build) is judged as before."""
    entries = final.get("entries") if isinstance(final, dict) else None
    if not isinstance(entries, dict) or not entries:
        return False
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in entries.items()):
        return True  # an entries record that cannot be checked is not one
    return bool(changed_cited(Path(folder).resolve().parent.parent / "achievement_library.md", entries))


def role_of(heading):
    """(title, org, dates) from a role heading, or None for other headings."""
    parts = [p.strip() for p in (heading or "").split("|")]
    if len(parts) < 3 or not re.search(r"\d{4}", parts[-1]):
        return None
    return parts[0], parts[1], parts[-1]


# ---------- techniques.md ----------

@dataclass
class Technique:
    name: str
    aliases: list
    level: str          # hands-on | familiar
    years_ok: bool
    evidence: list      # L- IDs
    line: int

    @property
    def forms(self):
        return [self.name] + self.aliases


def load_techniques(path):
    """techniques.md lines `- <technique> | aliases: a; b | level: hands-on |
    years_ok: yes | evidence: L-0012, L-0031`. A missing file is []."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^[-*]\s+(.+)$", line)  # top-level bullets; indented lines are examples
        if not m or " | " not in m.group(1):
            continue
        parts = [p.strip() for p in m.group(1).split(" | ")]
        keys = {}
        for p in parts[1:]:
            k, _, v = p.partition(":")
            keys[k.strip().lower()] = v.strip()
        if "level" not in keys and "evidence" not in keys:
            continue  # a format example or note, not a technique
        aliases = [a.strip() for a in keys.get("aliases", "").split(";") if a.strip()]
        evidence = CITED_RE.findall(keys.get("evidence", ""))
        out.append(Technique(parts[0], aliases, keys.get("level", "").lower(),
                             keys.get("years_ok", "").lower() == "yes", evidence, n))
    return out


def find_technique(techniques, term):
    low = (term or "").strip().lower()
    for t in techniques:
        if low in (f.lower() for f in t.forms):
            return t
    return None


# ---------- ids ----------

def backup(path, root=None):
    """Copy path to backups/<stem>_<YYYY-MM-DD_HHMM>.md (never over an older copy)."""
    path = Path(path)
    folder = (Path(root) if root else path.parent) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = jobfs.now(root).strftime("%Y-%m-%d_%H%M")
    dest = folder / f"{path.stem}_{stamp}{path.suffix}"
    k = 2
    while dest.exists():
        dest = folder / f"{path.stem}_{stamp}_{k}{path.suffix}"
        k += 1
    shutil.copy2(path, dest)
    return dest


def assign(text):
    """(new text, [assigned IDs]). Lines keep their endings; entries that
    already hold an ID, and Not held lines, are left alone."""
    lib = parse(text)
    todo = {e.line for e in lib.entries if not e.id and e.held and not e.note and e.text}
    if not todo:
        return text, []
    nxt = lib.high_water() + 1
    out, assigned = [], []
    lines = text.splitlines(keepends=True)
    for n, raw in enumerate(lines, 1):
        if n in todo:
            m = ENTRY_RE.match(raw.rstrip("\r\n"))
            new_id = f"L-{nxt:04d}"
            nxt += 1
            assigned.append(new_id)
            ending = raw[len(raw.rstrip("\r\n")):]
            prefix = raw[:raw.index(m.group(2), len(m.group(1)))]
            raw = f"{prefix}[{new_id}] {m.group(2)}{ending}"
        elif LAST_ID_RE.match(raw.strip()):
            continue  # rewritten at the end
        out.append(raw)
    newline = "\r\n" if "\r\n" in text else "\n"
    body = "".join(out)
    if body and not body.endswith(("\n", "\r")):
        body += newline
    return body + f"<!-- last-id: {assigned[-1]} -->{newline}", assigned


def cmd_ids(path, root):
    try:
        raw = path.read_bytes()
    except OSError as e:
        print(f"ERROR cannot read {path}: {e}")
        return 1
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    new, assigned = assign(text)
    if not assigned:
        print(f"IDS unchanged ({len(parse(text).by_id)} entries already have IDs)")
        return 0
    dest = backup(path, root)
    print(f"BACKUP {dest}")
    jobfs.atomic_write_text(path, ("\ufeff" if bom else "") + new)
    print(f"IDS assigned {len(assigned)}: {assigned[0]}..{assigned[-1]}")
    return 0


# ---------- check ----------

def citing_files(root):
    """Files that may cite library IDs, relative to root."""
    root = Path(root)
    pats = ["variants/*/v*/resume.json", "variants/*/v*/pool.json", "applications/*/resume.json",
            "answers.md", "techniques.md"]
    found = []
    for pat in pats:
        found += sorted(p for p in root.glob(pat) if p.is_file())
    return found


def cited_in(path, text):
    """The IDs a file cites. techniques.md: its technique lines' evidence;
    answers.md: its answer lines; anything else (JSON): the whole text. The
    format examples in the shipped templates are not citations."""
    name = Path(path).name.lower()
    if name == "techniques.md":
        return {i for t in load_techniques(path) for i in t.evidence}
    if name == "answers.md":
        return {i for line in text.splitlines() if BANK_LINE.match(line) for i in CITED_RE.findall(line)}
    return set(CITED_RE.findall(text))


def cmd_check(path, root, cites):
    lib = load(path)
    if not path.exists():
        print(f"ERROR {path} not found")
        return 1
    ids = lib.by_id
    problems = 0
    for dup, first, again in lib.duplicates():
        print(f"DUPLICATE {dup} (lines {first} and {again})")
        problems += 1
    for e in lib.entries:
        if not e.id and e.held and not e.note and e.text:
            print(f'NO-ID line {e.line}: "{e.text[:50]}" (run library.py ids)')
            problems += 1
    files = [Path(c) for c in cites] if cites else (citing_files(root) if root else [])
    refs = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            print(f"ERROR cannot read {f}: {e}")
            return 1
        shown = f.relative_to(root) if root and f.is_relative_to(root) else f
        for cid in sorted(cited_in(f, text)):
            refs += 1
            if cid not in ids:
                print(f"DANGLING {cid} in {shown.as_posix()}")
                problems += 1
    if problems:
        print(f"LIBRARY fail ({problems} problem{'s' if problems != 1 else ''})")
        return 1
    print(f"LIBRARY ok ({len(ids)} entries, {refs} cited IDs checked)")
    return 0


class Parser(argparse.ArgumentParser):
    """A bad argument is bad input: ERROR and exit 1 (exit 2 is reserved)."""

    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def main(argv=None):
    ap = Parser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ids", "check"):
        p = sub.add_parser(name)
        p.add_argument("--root", help="the JobSearch folder")
        p.add_argument("--file", help="the library file (default <root>/achievement_library.md)")
        if name == "check":
            p.add_argument("--cites", nargs="+", metavar="FILE", help="check these files instead of the usual ones")
    args = ap.parse_args(argv)
    if not args.root and not args.file:
        print("ERROR give --root or --file")
        return 1
    root = Path(args.root) if args.root else None
    path = Path(args.file) if args.file else jobfs.Folder(root).library
    if args.cmd == "ids":
        return cmd_ids(path, root)
    return cmd_check(path, root, args.cites)


if __name__ == "__main__":
    sys.exit(main())
