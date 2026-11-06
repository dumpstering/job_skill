"""Alert-card leads: validate the cards Claude read from alert emails and write
them as lead lines for the runner (spec 2.5, 3.5; R2). Runs in the sandbox.

Usage:
    python leads.py validate --source linkedin_alert --in scratch.json --out runner/in/leads/<date>_<HHMM>_<source>.jsonl
                             [--root R] [--seen YYYY-MM-DD]

scratch.json holds the cards of each message, and nothing else:
    {"messages": [{"id": "<message id>", "cards": [{"company": "...", "title": "...", "location": "..."}]}]}
(a bare list of messages works too). Only company, title and location are
kept; every other key (links, job ids, work mode, salary, labels) is dropped.

Validation fails closed:
- company and location at most 80 characters, title at most 120, company and
  title required;
- a field holding http, www., @, a URL-like token or instruction-like text
  ("ignore", "you are", "assistant", "system") drops that whole card;
- an input file containing linkedin.com anywhere is refused and nothing is
  written;
- a message that yields zero cards prints PARSE_FAILURE <msg_ref> and the
  command exits 9 (the other messages' leads are still written).

Output lines:
    DROP <reason> msg=<msg_ref> card=<i>      (reason: url-in-field, email-in-field,
                                              instruction-text, too-long, missing-field)
    LEADS kept=<n> dropped=<n>
    WROTE <out>
    PARSE_FAILURE <msg_ref>
    REFUSED linkedin.com in input

Each lead line is {"v","lead_id","source","company","title","location","seen","msg_ref"}:
lead_id is sha256(salt|source|company|title|city)[:16] and msg_ref is
sha256(message id)[:16]. The salt is a random local value kept in
config/lead_salt.txt (created on first use), so lead ids mean nothing outside
this folder. --out must lie inside the JobSearch folder's runner/in/leads and
is created only once (create-only). Stdlib only.

Exit codes: 0 ok, 1 bad input (ERROR ...) or REFUSED, 9 PARSE_FAILURE.
"""
import argparse
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402

LIMITS = {"company": 80, "title": 120, "location": 80}
SOURCE_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
URLISH = re.compile(r"https?:|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|co|gov|edu|ly|me|jobs|ai|us)\b|[a-z]+://"
                    r"|\blnkd\.in\b|\blicdn\b", re.I)
EMAILISH = re.compile(r"@")
# "assistant" alone is a job title word (Research Assistant, Assistant Professor),
# so it counts only as a role marker ("assistant:") or after an AI word.
INSTRUCTION = re.compile(r"\b(ignore|you are|system|instructions?|prompt)\b|\bassistant\s*:|"
                         r"\b(ai|virtual|language model|an|the|helpful) assistant\b|^assistant\b(?!\s+\w)", re.I)
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
EXIT_PARSE_FAILURE = 9


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


class Refused(Exception):
    pass


def sha16(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def salt(root):
    """The folder's local lead salt (config/lead_salt.txt), made on first use."""
    if root is None:
        return ""
    path = jobfs.Folder(root).config / "lead_salt.txt"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_hex(16)
    try:
        jobfs.create_only(path, value + "\n")
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    return value


def clean(value):
    """One line of plain text; NFKC-ish ASCII spaces only."""
    s = CONTROL.sub(" ", str(value if value is not None else ""))
    return re.sub(r"\s+", " ", s).strip()


def check_card(card):
    """(fields, None) or (None, reason) for one card."""
    if not isinstance(card, dict):
        return None, "missing-field"
    out = {}
    for key, limit in LIMITS.items():
        v = clean(card.get(key))
        if URLISH.search(v):
            return None, "url-in-field"
        if EMAILISH.search(v):
            return None, "email-in-field"
        if INSTRUCTION.search(v):
            return None, "instruction-text"
        if len(v) > limit:
            return None, "too-long"
        out[key] = v
    if not out["company"] or not out["title"]:
        return None, "missing-field"
    return out, None


def lead_line(source, fields, seen, msg_ref, salt_value=""):
    city = fields["location"].split(",")[0].strip()
    lead_id = sha16(f"{salt_value}|{source}|{fields['company']}|{fields['title']}|{city}")
    return {"v": 1, "lead_id": lead_id, "source": source, "company": fields["company"],
            "title": fields["title"], "location": fields["location"], "seen": seen, "msg_ref": msg_ref}


def messages_of(data):
    msgs = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(msgs, list):
        raise ValueError("expected {\"messages\": [...]} or a list of messages")
    return msgs


def validate(raw_text, source, seen, salt_value="", emit=print):
    """(leads, dropped, failures): leads to write, dropped count, and msg_refs
    of messages with zero cards. Raises Refused for linkedin.com input."""
    if "linkedin.com" in raw_text.lower():
        raise Refused("REFUSED linkedin.com in input")
    data = json.loads(raw_text)
    leads, dropped, failures, ids = [], 0, [], set()
    for m in messages_of(data):
        m = m if isinstance(m, dict) else {}
        msg_ref = sha16(str(m.get("id") or m.get("message_id") or ""))
        cards = m.get("cards") if isinstance(m.get("cards"), list) else []
        if not cards:
            failures.append(msg_ref)
            continue
        for i, card in enumerate(cards, 1):
            fields, why = check_card(card)
            if why:
                dropped += 1
                emit(f"DROP {why} msg={msg_ref} card={i}")
                continue
            lead = lead_line(source, fields, seen, msg_ref, salt_value)
            if lead["lead_id"] not in ids:
                ids.add(lead["lead_id"])
                leads.append(lead)
    return leads, dropped, failures


def infer_root(out):
    """The JobSearch folder when --out is <root>/runner/in/leads/<file>, else None
    (then no salt is used and nothing else in the folder is touched)."""
    p = Path(out).resolve()
    if len(p.parents) > 3 and [x.name for x in p.parents[:3]] == ["leads", "in", "runner"]:
        return p.parents[3]
    return None


def out_path_ok(root, out):
    folder = jobfs.Folder(root)
    try:
        Path(out).resolve().relative_to(folder.leads.resolve())
        return True
    except ValueError:
        return False


def cmd_validate(a):
    if not SOURCE_RE.match(a.source or ""):
        print(f"ERROR --source must be lowercase letters, digits and _ (got {a.source!r})")
        return 1
    root = Path(a.root) if a.root else infer_root(a.out)
    if a.root and not out_path_ok(root, a.out):
        print(f"ERROR --out must be inside {jobfs.Folder(root).leads}")
        return 1
    try:
        raw = Path(a.input).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR cannot read --in: {e}")
        return 1
    if a.seen and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.seen):
        print(f"ERROR --seen must be YYYY-MM-DD, got '{a.seen}'")
        return 1
    seen = a.seen or jobfs.today(root).isoformat()
    try:
        leads, dropped, failures = validate(raw, a.source, seen, salt(root))
    except Refused as e:
        print(str(e))
        return 1
    except (ValueError, RecursionError) as e:  # RecursionError: JSON nested thousands deep
        print(f"ERROR --in is not a cards file: {type(e).__name__}: {str(e)[:120]}")
        return 1
    print(f"LEADS kept={len(leads)} dropped={dropped}")
    if leads:
        body = "".join(json.dumps(ld, ensure_ascii=True, separators=(",", ":")) + "\n" for ld in leads)
        try:
            jobfs.create_only(a.out, body)
        except FileExistsError:
            print(f"ERROR {a.out} exists; leads files are create-only (use a new HHMM)")
            return 1
        print(f"WROTE {a.out}")
    for ref in failures:
        print(f"PARSE_FAILURE {ref}")
    return EXIT_PARSE_FAILURE if failures else 0


def build_parser():
    ap = Parser(prog="leads.py", description="Validate alert cards into lead lines (spec 2.5).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("validate", help="Validate cards and write runner/in/leads/<file>.jsonl.",
                       allow_abbrev=False)
    p.add_argument("--source", required=True, help="linkedin_alert, or <board>_alert for a confirmed board sender")
    p.add_argument("--in", dest="input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--root", help="the JobSearch folder (default: four levels above --out)")
    p.add_argument("--seen", help="the day the cards were read (default today)")
    p.set_defaults(func=cmd_validate)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
