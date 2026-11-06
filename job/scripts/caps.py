"""Application caps (spec 3.9; R7): whether one more application to a company
is allowed today, or must wait (DEFER, never a discard).

Usage:
    python caps.py check --root R --company C [--family F] [--job-key K | --url U]
        [--req-id ID ...] [--host H] [--posting posting.md] [--title T] [--date YYYY-MM-DD]
        [--transport auto|events|direct]

Prints one line:
    CAP ok
    CAP DEFER rejected-block until 2027-01-30
    CAP DEFER ashby-open <company>
    CAP DEFER google 3/3 until 2026-12-02        ([[caps.employer]] rules)
    CAP DEFER google same-job until 2027-02-01
    CAP DEFER company 5/5 until 2026-12-01
    CAP DEFER family qc_micro 2/2 until 2026-12-01
    CAP DEFER unit zhou 2/2 until 2026-12-01       (academic and the like, below)
    CAP DEFER institution 15/15 until 2026-12-01
    CAP DEFER company-day 2/2 until 2026-11-21
"until" is the first day it is allowed. Rules are checked in that order.

What counts (3.9): tracker rows at Filled - awaiting submit and beyond (the
event overlay included), plus today's queued items, which the queue builder
reserves. A reservation holds its slot for that day only: a DEFER that only
today's reservations cause says "until" tomorrow, when it is checked again.
A row at Filled - awaiting submit whose job the user then skipped or put off
(the job's last queue event, state/queue_state.json, is skipped or later)
was never sent and does not count. Employers sharing a `parent` in
config/watchlist.csv, state/watchlist_auto.csv or the Employers sheet count
as one company.

The numbers are [caps] settings (defaults: 5 a company and 2 a role family a
company per 30 days; Google 3, its published rule). An employer whose
watch-list segment is academic, institute, hospital or government
(`unit_segments`) counts per hiring unit instead: the lab or department the
posting names (a lab in the title, else the lab, division, department or
center its text opens with), 2 per unit (`per_unit_30d`, a role family
included) and 15 for the whole institution (`per_institution_30d`). A posting
that names no unit counts against the institution at the company caps.
--title (or the Role line of --posting) gives the unit.

The library is caps_verdict(index, company_norm, family, job_key, req_ids, host,
today, rules, unit=...) (the queue builder calls it per item), hiring_unit(title,
text, company_norm), load_index(root, today) and load_rules(root).

Exit codes: 0 ok (either verdict), 1 bad input (ERROR ...), 2 openpyxl missing
(direct transport only).
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import tracker  # noqa: E402

WINDOW_DAYS = 30
REPOST_BITS = 3
# Rows that are applications (handover and beyond), and those still open.
COUNTED = {"Filled - awaiting submit", "Answers ready - fill by hand", "Applied", "No response", "Assessment",
           "Screen", "Interview", "Offer", "Rejected", "Withdrawn"}
OPEN = {"Filled - awaiting submit", "Answers ready - fill by hand", "Applied", "No response", "Assessment",
        "Screen", "Interview", "Offer", "Queued"}
HANDOVER = "Filled - awaiting submit"
NOT_SENT_STATES = {"skipped", "later"}   # the user's reply after a handover: not sent
# The [caps] keys and their defaults (jobfs.SETTINGS_DEFAULTS when it holds them).
RULE_DEFAULTS = {"per_company_30d": 5, "per_family_per_company_30d": 2, "reject_reapply_days": 90,
                 "ashby_one_open": True, "per_company_per_day": 2, "per_unit_30d": 2, "per_institution_30d": 15,
                 "unit_segments": ["academic", "institute", "hospital", "government"]}
# Words that name a kind of lab or a field, not one lab: "Clinical Laboratory
# Scientist" and "QC Microbiology Lab" name no hiring unit.
GENERIC_UNIT_WORDS = set("""
a an the and of for at in on with our your their its this new
dr prof professor pi md phd
research clinical core central main shared public health quality control qc qa analytical diagnostic diagnostics
testing test reference medical medicine hospital laboratory laboratories lab labs group groups team unit program
programme center centre institute school department division office services service facility facilities
molecular cell cellular tissue animal wet dry teaching sequencing genomics genetics biology biochemistry
chemistry microbiology immunology pathology pharmacology physiology neuroscience bioinformatics data science
sciences scientific translational biomedical life university college national state county city
us u s usa american federal
""".split())
LAB_WORD_RE = re.compile(r"\b(?:lab|labs|laboratory|laboratories|group)\b", re.I)
ORG_KIND = r"(?:Division|Department|Dept\.?|School|Center|Centre|Institute|Program|Programme)"
ORG_OF_RE = re.compile(rf"\b({ORG_KIND})[ \t]+(?:of|for)[ \t]+"
                       rf"((?:(?!{ORG_KIND}\b)[A-Z][A-Za-z&'\-]*,?[ \t]*(?:and[ \t]+|&[ \t]+)?){{1,6}})")
ORG_NAMED_RE = re.compile(r"((?:[A-Z][A-Za-z&'\-]*[ \t]+){1,4})(Institute|Center|Centre|School|Program)\b")
STOP_TOKEN_RE = re.compile(r"[.:;,()!?|]$|^[-|]+$|^\W+$")
UNIT_TEXT_CHARS = 1500


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- the verdict (pure) ----------

def group(rules, company_norm):
    """The company a cap counts against: its parent when the watch list sets one."""
    return (rules.get("parents") or {}).get(company_norm, company_norm)


def in_window(d, today, days):
    return d is not None and today < d + timedelta(days=days)


def count_rule(entries, cap, today, label, days=WINDOW_DAYS):
    """DEFER when the entries inside the window reach cap; until is the day the
    oldest one that matters leaves the window."""
    dates = sorted(e["date"] for e in entries if in_window(e["date"], today, days))
    if cap and len(dates) >= cap:
        return ("DEFER", f"{label} {len(dates)}/{cap}", dates[len(dates) - cap] + timedelta(days=days))
    return None


def is_ashby(job_key, host, ats=""):
    return (job_key or "").startswith("ashby:") or "ashbyhq.com" in (host or "") or (ats or "").lower() == "ashby"


def same_req_or_repost(e, g, job_key, req_ids, simhash, rules):
    if job_key and e["job_key"] == job_key:
        return True
    if req_ids and e["req_ids"] and group(rules, e["company_norm"]) == g and set(req_ids) & e["req_ids"]:
        return True
    return bool(simhash and e["simhash"] and e["simhash"] != "0" * 16
                and jobkey.hamming(simhash, e["simhash"]) <= REPOST_BITS)


def rejected_block(index, g, job_key, req_ids, simhash, today, rules):
    days = int(rules.get("reject_reapply_days", 90))
    until = None
    for e in index:
        if e["status"] == "Rejected" and in_window(e["rejected_on"], today, days) \
                and same_req_or_repost(e, g, job_key, req_ids, simhash, rules):
            end = e["rejected_on"] + timedelta(days=days)
            until = max(until, end) if until else end
    return ("DEFER", "rejected-block", until) if until else None


def ashby_open(index, g, company_norm, job_key, host, rules):
    if not rules.get("ashby_one_open", True) or not is_ashby(job_key, host):
        return None
    for e in index:
        if group(rules, e["company_norm"]) == g and e["job_key"] != job_key and e["status"] in OPEN \
                and is_ashby(e["job_key"], e["host"], e.get("ats", "")):
            return ("DEFER", f"ashby-open {company_norm}", None)
    return None


def employer_rule(rules, company_norm):
    for emp in rules.get("employer") or []:
        for m in emp.get("match") or []:
            if re.search(rf"\b{re.escape(str(m).lower())}\b", company_norm):
                return emp
    return None


def employer_caps(emp, index, rules, company_norm, job_key, today):
    name = str(emp.get("name", "employer")).lower()
    mine = [e for e in index if employer_rule(rules, e["company_norm"]) is emp]
    v = count_rule([e for e in mine if e["job_key"] != job_key], int(emp.get("per_30d", 0)), today, name)
    if v:
        return v
    days = int(emp.get("same_job_reapply_days", 0))
    if days and job_key:
        ends = [e["date"] + timedelta(days=days) for e in mine
                if e["job_key"] == job_key and e["status"] != "Queued" and in_window(e["date"], today, days)]
        if ends:
            return ("DEFER", f"{name} same-job", max(ends))
    return None


# ---------- hiring units (academic, institute, hospital, government) ----------

def unit_words(text, company_norm=""):
    """The words of a unit name that tell one unit from another: lower case,
    with generic lab and field words and the employer's own name taken out."""
    drop = GENERIC_UNIT_WORDS | set((company_norm or "").split())
    words = re.findall(r"[a-z0-9]+", re.sub(r"'s\b", "", str(text or "").lower().replace(chr(0x2019), "'")))
    return [w for w in words if w not in drop and len(w) > 1]


def lab_name(text, company_norm=""):
    """'zhou' for "Zhou Lab", "Dr. Lisa Zhou's laboratory" or "The Zhou Research
    Group"; 'gao zhou' for "Zhou & Gao Labs"; '' when no lab is named."""
    for m in LAB_WORD_RE.finditer(text or ""):
        toks = text[:m.start()].rsplit("\n", 1)[-1].split()[-6:]  # the same line only
        names, joined = [], False
        for tok in reversed(toks):
            if tok in ("&", "and", "/"):
                joined = True
                continue
            if not tok[:1].isupper() or len(names) >= 4 or (STOP_TOKEN_RE.search(tok) and tok != "Dr."):
                break
            names.insert(0, tok)
        words = unit_words(" ".join(names), company_norm)
        if words:
            return " ".join(sorted(set(words))) if joined else words[-1]
    return ""


def hiring_unit(title, text="", company_norm=""):
    """The hiring unit a posting names: a lab in the title, else the lab,
    division, department, center or school the first part of its text names;
    '' when it names none (the institution is the unit then)."""
    unit = lab_name(title, company_norm)
    if unit:
        return unit
    head = str(text or "")[:UNIT_TEXT_CHARS]
    unit = lab_name(head, company_norm)
    if unit:
        return unit
    for kind in ("Div", "Dep", ""):  # a division, then a department, then any "<kind> of <name>"
        for m in ORG_OF_RE.finditer(head):
            if m.group(1).startswith(kind):
                words = unit_words(m.group(2), company_norm)
                if words:
                    return " ".join(words[:5])
    for m in ORG_NAMED_RE.finditer(head):
        words = unit_words(m.group(1), company_norm)
        if words:
            return " ".join(words[:5]) + " " + m.group(2).lower()
    return ""


def posting_unit(text, company_norm="", role=""):
    """hiring_unit() of a posting.md: its Role line (or role) and its description."""
    head, _, body = str(text or "").partition("## Description")
    role = role or next((ln.split(":", 1)[1].strip() for ln in head.splitlines() if ln.startswith("Role:")), "")
    return hiring_unit(role, body or text, company_norm)


def unit_mode(rules, company_norm):
    """True when this employer's caps count per hiring unit (its watch-list segment)."""
    seg = (rules.get("segments") or {}).get(company_norm, "")
    return bool(seg) and seg in {str(s).lower() for s in rules.get("unit_segments") or []}


def unit_of(e):
    """An index entry's hiring unit, read once (lazily) from its role and its
    job folder's posting.md; only unit_segments employers ever need it."""
    if e.get("unit") is None:
        text = tracker.read_posting(e["root"], e["folder"]) if e.get("root") and e.get("folder") else ""
        e["unit"] = posting_unit(text, e["company_norm"], e.get("title") or "")
    return e["unit"]


def unit_caps(others, family, unit, today, rules):
    """Per hiring unit (a role family within it too), then the whole
    institution; a posting with no unit counts against the institution at the
    company caps."""
    if unit:
        mine = [e for e in others if unit_of(e) == unit]
        v = count_rule(mine, int(rules.get("per_unit_30d", 0)), today, f"unit {unit}")
        if v:
            return v
        if family:
            fam = [e for e in mine if e["family"] == family.lower()]
            v = count_rule(fam, int(rules.get("per_family_per_company_30d", 0)), today, f"family {family}")
            if v:
                return v
        v = count_rule(others, int(rules.get("per_institution_30d", 0)), today, "institution")
        if v:
            return v
        day_scope = mine
    else:
        v = count_rule(others, int(rules.get("per_company_30d", 0)), today, "company") \
            or count_rule(others, int(rules.get("per_institution_30d", 0)), today, "institution")
        if v:
            return v
        if family:
            fam = [e for e in others if e["family"] == family.lower()]
            v = count_rule(fam, int(rules.get("per_family_per_company_30d", 0)), today, f"family {family}")
            if v:
                return v
        day_scope = others
    return day_rule(day_scope, today, rules)


def day_rule(entries, today, rules):
    day_cap = int(rules.get("per_company_per_day", 0))
    todays = [e for e in entries if e["date"] == today]
    if day_cap and len(todays) >= day_cap:
        return ("DEFER", f"company-day {len(todays)}/{day_cap}", today + timedelta(days=1))
    return None


def verdict_of(index, company_norm, family, job_key, req_ids, host, today, rules, simhash, unit):
    g = group(rules, company_norm)
    same = [e for e in index if group(rules, e["company_norm"]) == g]
    others = [e for e in same if not job_key or e["job_key"] != job_key]
    v = rejected_block(index, g, job_key, req_ids, simhash, today, rules) \
        or ashby_open(index, g, company_norm, job_key, host, rules)
    if v:
        return v
    emp = employer_rule(rules, company_norm)
    if emp:
        v = employer_caps(emp, index, rules, company_norm, job_key, today)
    elif unit_mode(rules, company_norm):
        return unit_caps(others, family, unit or "", today, rules) or ("ALLOW", None)
    else:
        v = count_rule(others, int(rules.get("per_company_30d", 0)), today, "company")
    if v:
        return v
    if family:
        fam = [e for e in others if e["family"] == family.lower()]
        v = count_rule(fam, int(rules.get("per_family_per_company_30d", 0)), today, f"family {family}")
        if v:
            return v
    return day_rule(others, today, rules) or ("ALLOW", None)


def caps_verdict(index, company_norm, family, job_key, req_ids, host, today, rules, simhash=None, unit=None):
    """("ALLOW", None) or ("DEFER", rule, until) for one more application.

    index: entries from load_index(); company_norm: jobkey.norm_company() form;
    req_ids: the posting's requisition IDs; simhash: its 16-hex SimHash, for the
    90-day block on a rejected job's repost; unit: its hiring_unit() (used for
    the unit_segments employers); until is a date or None. A DEFER that only
    today's queue reservations cause is until tomorrow: an untaken item frees
    its slot."""
    v = verdict_of(index, company_norm, family, job_key, req_ids, host, today, rules, simhash, unit)
    if v[0] == "DEFER" and any(e["status"] == "Queued" for e in index):
        sent = [e for e in index if e["status"] != "Queued"]
        if verdict_of(sent, company_norm, family, job_key, req_ids, host, today, rules, simhash, unit)[0] == "ALLOW":
            return ("DEFER", v[1], today + timedelta(days=1))
    return v


def verdict_line(v):
    if v[0] == "ALLOW":
        return "CAP ok"
    return f"CAP DEFER {v[1]}" + (f" until {v[2].isoformat()}" if v[2] else "")


# ---------- the index and the rules ----------

def entry(root, row):
    """An index entry for one tracker row, or None when the row does not count."""
    status = tracker.canon_status(row.get("Status"))
    applied = tracker.to_date(row.get("Applied"))
    if status not in COUNTED and not (status == "Closed" and applied):
        return None
    url = tracker.text(row.get("URL"))
    e = {"company_norm": jobkey.norm_company(tracker.text(row.get("Company"))),
         "family": tracker.text(row.get("Family")).lower(),
         "job_key": tracker.text(row.get("Job key")) or jobkey.job_key(url),
         "host": tracker.url_host(url), "ats": tracker.text(row.get("ATS")).lower(), "status": status,
         "date": applied or tracker.to_date(row.get("Date")), "rejected_on": None,
         "req_ids": set(), "simhash": "", "title": tracker.text(row.get("Role")),
         "folder": tracker.text(row.get("Folder")), "root": str(root), "unit": None}
    if status == "Rejected":
        e["rejected_on"] = tracker.to_date(row.get("Last update")) or e["date"]
        e["req_ids"], e["simhash"] = tracker.row_fingerprint(root, row)
    elif tracker.text(row.get("Req ID")):
        e["req_ids"] = {tracker.text(row["Req ID"]).upper()}
    return e


def queue_entry(item, today, root=None, unit=None):
    """A queue reservation: counts for its day only."""
    return {"company_norm": jobkey.norm_company(str(item.get("company", ""))),
            "family": str(item.get("family", "")).lower(), "job_key": str(item.get("job_key", "")),
            "host": str(item.get("host", "")), "ats": str(item.get("ats", "")).lower(), "status": "Queued",
            "date": today, "rejected_on": None, "req_ids": set(), "simhash": "",
            "title": str(item.get("role") or item.get("title") or ""), "folder": str(item.get("folder") or ""),
            "root": str(root) if root else "", "unit": unit}


def open_store(root, transport="auto"):
    ns = argparse.Namespace(root=str(root), transport=transport, store=None)
    ns.view = "overlay" if tracker.resolve_transport(ns, write=False) == "events" else "xlsx"
    return tracker.open_tracker(ns, quiet=True)


def queue_states(root):
    """job_key -> the folded queue state record (state/queue_state.json)."""
    try:
        data = json.loads(jobfs.Folder(root).queue_state_json.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, dict) else {}
    except (OSError, ValueError):
        return {}


def not_sent(row, qstate):
    """A handover row the user then skipped or put off: never sent."""
    st = qstate.get(tracker.text(row.get("Job key"))) or {}
    return tracker.canon_status(row.get("Status")) == HANDOVER and st.get("state") in NOT_SENT_STATES


def load_index(root, today, transport="auto", store=None):
    """Entries for every counted tracker row (archive included) plus today's
    queued items. A queued job that already has a row counts once."""
    store = store if store is not None else open_store(root, transport)
    qstate = queue_states(root)
    index = []
    if store is not None:
        for row in store.app_rows() + store.archived_rows():
            e = None if not_sent(row, qstate) else entry(root, row)
            if e:
                index.append(e)
    keys = {e["job_key"] for e in index if e["job_key"]}
    for item in tracker.queue_items(root, today):
        if item.get("job_key") and item["job_key"] not in keys:
            index.append(queue_entry(item, today, root))
            keys.add(item["job_key"])
    return index


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def load_parents(root, store=None):
    """employer_norm -> parent_norm from the watch lists and the Employers sheet."""
    folder = jobfs.Folder(root)
    pairs = []
    for path in (folder.watchlist_csv, folder.watchlist_auto_csv):
        for r in read_csv(path):
            pairs.append((r.get("employer_norm") or r.get("employer") or "", r.get("parent") or ""))
    if store is not None and store.employers is not None:
        for r in store.employers.rows():
            pairs.append((tracker.text(r.get("Normalized")) or tracker.text(r.get("Employer")),
                          tracker.text(r.get("Parent"))))
    parents = {}
    for name, parent in pairs:
        n, p = jobkey.norm_company(name), jobkey.norm_company(parent)
        if n and p:
            parents[n] = p
            parents.setdefault(p, p)
    return parents


def load_segments(root):
    """employer_norm -> segment (academic, biotech, ...) from the watch lists."""
    folder = jobfs.Folder(root)
    out = {}
    for path in (folder.watchlist_csv, folder.watchlist_auto_csv):
        for r in read_csv(path):
            n = jobkey.norm_company(r.get("employer_norm") or r.get("employer") or "")
            if n and n not in out:
                out[n] = (r.get("segment") or "").strip().lower()
    return out


def load_rules(root, store=None):
    caps = jobfs.settings(root).get("caps", {})
    defaults = dict(RULE_DEFAULTS, **{k: v for k, v in jobfs.SETTINGS_DEFAULTS.get("caps", {}).items()
                                      if k in RULE_DEFAULTS})
    rules = {k: caps.get(k, defaults[k]) for k in RULE_DEFAULTS}
    rules["employer"] = caps.get("employer", jobfs.SETTINGS_DEFAULTS["caps"]["employer"])
    rules["parents"] = load_parents(root, store)
    rules["segments"] = load_segments(root)
    return rules


# ---------- CLI ----------

def cmd_check(a):
    try:
        today = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else jobfs.today(a.root)
    except ValueError:
        raise tracker.TrackerError(f"--date must be YYYY-MM-DD, got '{a.date}'")
    company = jobkey.norm_company(a.company)
    if not company:
        raise tracker.TrackerError("--company cannot be empty")
    job_key = a.job_key or jobkey.job_key(a.url)
    host = a.host or tracker.url_host(a.url)
    req_ids, simhash, text = {r.upper() for r in a.req_id or []}, "", ""
    if a.posting:
        text = Path(a.posting).read_text(encoding="utf-8", errors="replace")
        reqs, simhash = tracker.folder_fingerprint(Path(a.posting).resolve().parent)
        req_ids |= reqs | jobkey.req_ids(text)
    store = open_store(a.root, a.transport)
    index = load_index(a.root, today, store=store)
    rules = load_rules(a.root, store)
    unit = posting_unit(text, company, a.title or "") if unit_mode(rules, company) else ""
    print(verdict_line(caps_verdict(index, company, a.family or "", job_key, req_ids, host, today, rules,
                                    simhash=simhash, unit=unit)))
    return 0


def build_parser():
    ap = Parser(prog="caps.py", description="Application caps (spec 3.9).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("check", help="CAP ok or CAP DEFER <rule> [until <date>] for one more application.",
                       allow_abbrev=False)
    p.add_argument("--root", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--family", default="")
    p.add_argument("--job-key", default="")
    p.add_argument("--url", default="")
    p.add_argument("--req-id", nargs="+")
    p.add_argument("--host", default="")
    p.add_argument("--posting", help="the posting.md (for requisition IDs, the repost SimHash and the hiring unit)")
    p.add_argument("--title", default="", help="the job title (for the hiring unit; default the posting's Role line)")
    p.add_argument("--date", help="the day to judge (default today)")
    p.add_argument("--transport", default="auto", choices=("auto", "events", "direct"))
    p.set_defaults(func=cmd_check)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except tracker.TrackerError as e:
        print(f"ERROR {e}")
        return e.code
    except (jobfs.ConfigError, OSError) as e:
        print(f"ERROR {e}")
        return 1
    except tracker.MissingPackage:
        print("MISSING openpyxl: run  pip install openpyxl  and run again")
        return 2


if __name__ == "__main__":
    sys.exit(main())
