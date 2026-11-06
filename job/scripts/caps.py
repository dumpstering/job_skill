"""Application caps (spec 3.9; R7): whether one more application to a company
is allowed today, or must wait (DEFER, never a discard).

Usage:
    python caps.py check --root R --company C [--family F] [--job-key K | --url U]
        [--req-id ID ...] [--host H] [--posting posting.md] [--date YYYY-MM-DD]
        [--transport auto|events|direct]

Prints one line:
    CAP ok
    CAP DEFER rejected-block until 2027-01-30
    CAP DEFER ashby-open <company>
    CAP DEFER google 3/3 until 2026-12-02        ([[caps.employer]] rules)
    CAP DEFER google same-job until 2027-02-01
    CAP DEFER company 3/3 until 2026-12-01
    CAP DEFER family qc_micro 1/1 until 2026-12-01
    CAP DEFER company-day 2/2 until 2026-11-21
"until" is the first day it is allowed. Rules are checked in that order.

What counts (3.9): tracker rows at Filled - awaiting submit and beyond (the
event overlay included), plus today's queued items, which the queue builder
reserves. Employers sharing a `parent` in config/watchlist.csv,
state/watchlist_auto.csv or the Employers sheet count as one company.

The library is caps_verdict(index, company_norm, family, job_key, req_ids, host,
today, rules) (pure; the queue builder calls it per item), load_index(root,
today) and load_rules(root).

Exit codes: 0 ok (either verdict), 1 bad input (ERROR ...), 2 openpyxl missing
(direct transport only).
"""
import argparse
import csv
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


def caps_verdict(index, company_norm, family, job_key, req_ids, host, today, rules, simhash=None):
    """("ALLOW", None) or ("DEFER", rule, until) for one more application.

    index: entries from load_index(); company_norm: jobkey.norm_company() form;
    req_ids: the posting's requisition IDs; simhash: its 16-hex SimHash, for the
    90-day block on a rejected job's repost; until is a date or None."""
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
    else:
        v = count_rule(others, int(rules.get("per_company_30d", 0)), today, "company")
    if v:
        return v
    if family:
        fam = [e for e in others if e["family"] == family.lower()]
        v = count_rule(fam, int(rules.get("per_family_per_company_30d", 0)), today, f"family {family}")
        if v:
            return v
    day_cap = int(rules.get("per_company_per_day", 0))
    todays = [e for e in others if e["date"] == today]
    if day_cap and len(todays) >= day_cap:
        return ("DEFER", f"company-day {len(todays)}/{day_cap}", today + timedelta(days=1))
    return ("ALLOW", None)


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
         "req_ids": set(), "simhash": ""}
    if status == "Rejected":
        e["rejected_on"] = tracker.to_date(row.get("Last update")) or e["date"]
        e["req_ids"], e["simhash"] = tracker.row_fingerprint(root, row)
    elif tracker.text(row.get("Req ID")):
        e["req_ids"] = {tracker.text(row["Req ID"]).upper()}
    return e


def queue_entry(item, today):
    return {"company_norm": jobkey.norm_company(str(item.get("company", ""))),
            "family": str(item.get("family", "")).lower(), "job_key": str(item.get("job_key", "")),
            "host": str(item.get("host", "")), "ats": str(item.get("ats", "")).lower(), "status": "Queued",
            "date": today, "rejected_on": None, "req_ids": set(), "simhash": ""}


def open_store(root, transport="auto"):
    ns = argparse.Namespace(root=str(root), transport=transport, store=None)
    ns.view = "overlay" if tracker.resolve_transport(ns, write=False) == "events" else "xlsx"
    return tracker.open_tracker(ns, quiet=True)


def load_index(root, today, transport="auto", store=None):
    """Entries for every counted tracker row (archive included) plus today's
    queued items. A queued job that already has a row counts once."""
    store = store if store is not None else open_store(root, transport)
    index = []
    if store is not None:
        for row in store.app_rows() + store.archived_rows():
            e = entry(root, row)
            if e:
                index.append(e)
    keys = {e["job_key"] for e in index if e["job_key"]}
    for item in tracker.queue_items(root, today):
        if item.get("job_key") and item["job_key"] not in keys:
            index.append(queue_entry(item, today))
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


def load_rules(root, store=None):
    caps = jobfs.settings(root).get("caps", {})
    rules = {k: caps.get(k, jobfs.SETTINGS_DEFAULTS["caps"][k])
             for k in ("per_company_30d", "per_family_per_company_30d", "reject_reapply_days",
                       "ashby_one_open", "per_company_per_day")}
    rules["employer"] = caps.get("employer", jobfs.SETTINGS_DEFAULTS["caps"]["employer"])
    rules["parents"] = load_parents(root, store)
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
    req_ids, simhash = {r.upper() for r in a.req_id or []}, ""
    if a.posting:
        text = Path(a.posting).read_text(encoding="utf-8", errors="replace")
        reqs, simhash = tracker.folder_fingerprint(Path(a.posting).resolve().parent)
        req_ids |= reqs | jobkey.req_ids(text)
    store = open_store(a.root, a.transport)
    index = load_index(a.root, today, store=store)
    rules = load_rules(a.root, store)
    print(verdict_line(caps_verdict(index, company, a.family or "", job_key, req_ids, host, today, rules,
                                    simhash=simhash)))
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
    p.add_argument("--posting", help="the posting.md (for requisition IDs and the repost SimHash)")
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
