"""The unemployment work-search record (spec 6.6, R9).

Usage:
    python worksearch.py export --root R [--state ST] [--from YYYY-MM-DD --to YYYY-MM-DD] [--mark]
    python worksearch.py check --root R [--state ST] --week YYYY-MM-DD
    python worksearch.py employers --root R [--from YYYY-MM-DD --to YYYY-MM-DD]
    python worksearch.py states

Rows are built at export time from the tracker (the runner's snapshot plus
pending events in mode L, applications.xlsx in mode M):
    - Applications with an Applied date (by-hand logs included),
    - Referrals asks he sent (activity_type "Referral contact"),
    - Activities (job fairs, workshops, RESEA, interviews),
joined to the Employers sheet for addresses, websites and phones.

export writes worksearch/<ST>_<from>_<to>.csv, .xlsx and .md. The CSV header is
exactly COLUMNS. The .xlsx adds a Claimant sheet whose name, SSN/claimant ID and
signature cells are blank and flagged; the skill never handles them. The .md is
a ready-to-type block of the strongest rows per week. Files are create-only:
an earlier export is never rewritten or deleted; a second export of the same
period gets _v2, _v3. Default period: the state's certification period ending
yesterday (California: two weeks). --mark records today's date in each exported
application row's Work search column (through the tracker transport).

check prints one WEEK line, then a WARN line per rule the week breaks, or
CHECK ok:
    WEEK GA 2026-11-01 to 2026-11-07 | contacts 6 | days 1 | new employers 6
    WARN GA needs contacts on 3 or more days
The rules (weekly minimum, days, new employers, same employer and location 3
weeks running, repeat application to one job) come from
templates/worksearch_states.toml; unlisted states use its generic preset.

employers lists employers in the period's rows that lack details:
    MISSING Acme Bio | street, city, zip, phone
    EMPLOYERS ok

Exit codes: 0 ok, 1 bad input (ERROR ...), 2 openpyxl missing.
"""
import argparse
import csv
import io
import sys
import tomllib
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobfs  # noqa: E402
import jobkey  # noqa: E402

COLUMNS = ["week_start", "week_end", "activity_date", "activity_type", "contact_type", "employer_name",
           "employer_location", "job_title", "job_req_id", "method", "job_site", "posting_url",
           "employer_main_website", "employer_street", "employer_city", "employer_state", "employer_zip",
           "employer_phone", "employer_email", "employer_fax", "contact_person", "type_of_work_sought", "result",
           "proof_ref", "counts_toward_minimum", "reported_to_state_on"]
PRESETS = Path(__file__).resolve().parent.parent / "templates" / "worksearch_states.toml"
GENERIC = {"name": "Other state", "weekly_minimum": 0, "min_days": 0, "week_start": "sunday",
           "entry_mode": "record", "period_weeks": 1, "ready_rows": 5, "website": "posting", "checks": [],
           "registration": "", "retention": "at least 2 years", "note": "Check your state's work-search rules."}
NOT_APPLIED = {"started", "resume ready", "filled - awaiting submit", "answers ready - fill by hand",
               "blocked - login", "blocked - captcha", "blocked - site", "not pursued"}
RESULTS = {"applied": "Application filed", "no response": "Application filed", "assessment": "Assessment",
           "screen": "Interview", "interview": "Interview", "offer": "Job offer", "rejected": "Not hired",
           "withdrawn": "Withdrawn", "closed": "Position closed"}
SENT = {"sent": "Asked for a referral", "replied": "Contact replied", "referred": "Referred",
        "no reply": "No reply", "declined": "Declined"}
CHANNEL_METHOD = {"linkedin message": "Online", "email": "Email", "text": "Phone", "in person": "In person"}
ATS_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
             "workable": "Workable", "bamboohr": "BambooHR", "smartrecruiters": "SmartRecruiters",
             "icims": "iCIMS", "usajobs": "USAJOBS", "linkedin": "LinkedIn", "indeed": "Indeed"}
DETAIL_COLS = [("employer_street", "street"), ("employer_city", "city"), ("employer_state", "state"),
               ("employer_zip", "zip"), ("employer_phone", "phone"), ("employer_main_website", "main website")]
WEEKDAY = {"monday": 0, "sunday": 6}


class WsError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- presets ----------

def unwrap(v):
    return v["value"] if isinstance(v, dict) and "value" in v else v


def load_presets(path=PRESETS):
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def preset(state, presets=None):
    """The rules for a state code: its preset over the generic one, values unwrapped."""
    presets = load_presets() if presets is None else presets
    rules = dict(GENERIC)
    rules.update({k: unwrap(v) for k, v in (presets.get("generic") or {}).items()})
    found = (presets.get("states") or {}).get(state.upper())
    if found:
        rules.update({k: unwrap(v) for k, v in found.items()})
    rules["code"], rules["listed"] = state.upper(), bool(found)
    return rules


def week_of(d, rules):
    """(start, end) of the claim week holding d."""
    start_wd = WEEKDAY.get(str(rules.get("week_start", "sunday")).lower(), 6)
    start = d - timedelta(days=(d.weekday() - start_wd) % 7)
    return start, start + timedelta(days=6)


# ---------- reading the tracker ----------

def text(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v).strip()


def to_date(v):
    s = text(v)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_day(label, s):
    d = to_date(s)
    if d is None:
        raise WsError(f"{label} must be YYYY-MM-DD, got '{s}'")
    return d


def open_store(root):
    import caps
    return caps.open_store(root)


def family_labels(root):
    try:
        fams = jobfs.search_profile(root).get("family") or []
    except jobfs.ConfigError:
        fams = []
    return {str(f.get("id", "")).lower(): str(f.get("label") or f.get("id") or "") for f in fams
            if isinstance(f, dict)}


def employers_index(store):
    out = {}
    if store is not None and store.employers is not None:
        for r in store.employers.rows():
            n = text(r.get("Normalized")) or jobkey.norm_company(text(r.get("Employer")))
            if n:
                out[n] = r
    return out


def job_site(row):
    via = text(row.get("Found via")).lower()
    ats = text(row.get("ATS")).lower()
    if ats:
        return ATS_NAMES.get(ats, ats.title())
    if via.startswith("alert:"):
        return ATS_NAMES.get(via[6:], via[6:].title())
    host = jobkey.parse_url(text(row.get("URL")))[0] if text(row.get("URL")) else ""
    for k, v in ATS_NAMES.items():
        if k in host:
            return v
    return host


def employer_fields(emp, row=None):
    e = emp or {}
    site = text(e.get("Main website")) or (text(row.get("Employer site")) if row else "")
    return {"employer_main_website": site, "employer_street": text(e.get("Street")),
            "employer_city": text(e.get("City")), "employer_state": text(e.get("State")),
            "employer_zip": text(e.get("ZIP")), "employer_phone": text(e.get("Phone")),
            "employer_email": text(e.get("Email")), "employer_fax": ""}


def app_activity(row, emps, labels):
    status = text(row.get("Status")).lower()
    d = to_date(row.get("Applied"))
    if d is None or status in NOT_APPLIED:
        return None
    company = text(row.get("Company"))
    fam = text(row.get("Family")).lower()
    rec = {"activity_date": d.isoformat(), "activity_type": "Applied", "employer_name": company,
           "employer_location": text(row.get("Location")), "job_title": text(row.get("Role")),
           "job_req_id": text(row.get("Req ID")), "method": "Online", "job_site": job_site(row),
           "posting_url": text(row.get("URL")), "contact_person": "online application",
           "type_of_work_sought": labels.get(fam) or text(row.get("Role")),
           "result": RESULTS.get(status, "Application filed"), "proof_ref": text(row.get("Proof")),
           "_norm": jobkey.norm_company(company), "_id": text(row.get("ID")), "_kind": "application",
           "_key": text(row.get("Job key")) or jobkey.job_key(text(row.get("URL"))),
           "_source": text(row.get("Source"))}
    rec.update(employer_fields(emps.get(rec["_norm"]), row))
    return rec


def referral_activity(row, emps, roles):
    status = text(row.get("Status")).lower()
    d = to_date(row.get("Date"))
    if d is None or status not in SENT:
        return None
    company = text(row.get("Company"))
    key = text(row.get("Job key"))
    rec = {"activity_date": d.isoformat(), "activity_type": "Referral contact", "employer_name": company,
           "employer_location": "", "job_title": roles.get(key, ""), "job_req_id": "",
           "method": CHANNEL_METHOD.get(text(row.get("Channel")).lower(), "Online"), "job_site": "",
           "posting_url": "", "contact_person": text(row.get("Contact")), "type_of_work_sought": roles.get(key, ""),
           "result": SENT[status], "proof_ref": "", "_norm": jobkey.norm_company(company), "_id": "",
           "_kind": "referral", "_key": key, "_source": ""}
    rec.update(employer_fields(emps.get(rec["_norm"])))
    return rec


def other_activity(row):
    d = to_date(row.get("Date"))
    if d is None:
        return None
    desc = text(row.get("Description"))
    rec = {"activity_date": d.isoformat(), "activity_type": text(row.get("Type")) or "Other",
           "employer_name": desc, "employer_location": "", "job_title": "", "job_req_id": "", "method": "In person",
           "job_site": "", "posting_url": "", "contact_person": text(row.get("Contact")),
           "type_of_work_sought": "", "result": text(row.get("Result")), "proof_ref": "",
           "_norm": "", "_id": "", "_kind": "activity", "_key": "", "_source": ""}
    rec.update(employer_fields(None))
    return rec


def all_activities(root, store):
    """Every activity the tracker holds, oldest first (history decides New or Follow-up)."""
    if store is None:
        return []
    emps, labels = employers_index(store), family_labels(root)
    apps = store.app_rows() + store.archived_rows()
    roles = {text(r.get("Job key")): text(r.get("Role")) for r in apps if text(r.get("Job key"))}
    out = [a for a in (app_activity(r, emps, labels) for r in apps) if a]
    if store.referrals is not None:
        out += [a for a in (referral_activity(r, emps, roles) for r in store.referrals.rows()) if a]
    if store.activities is not None:
        out += [a for a in (other_activity(r) for r in store.activities.rows()) if a]
    order = {"application": 0, "referral": 1, "activity": 2}
    out.sort(key=lambda a: (a["activity_date"], order[a["_kind"]], a["employer_name"].lower()))
    seen = set()
    for a in out:
        a["contact_type"] = "Follow-up" if a["_norm"] and a["_norm"] in seen else "New"
        if a["_norm"]:
            seen.add(a["_norm"])
    return out


def repeat_jobs(acts):
    """Indexes of applications to a job applied for earlier (PA does not count them)."""
    seen, out = set(), set()
    for i, a in enumerate(acts):
        if a["_kind"] != "application":
            continue
        k = a["_key"] or f"{a['_norm']}|{jobkey.norm_title(a['job_title'])}"
        if k in seen:
            out.add(i)
        seen.add(k)
    return out


def rows_for(acts, start, end, rules):
    """The period's rows with week columns and the counting flag for this state."""
    repeats = repeat_jobs(acts) if "repeat_job" in rules["checks"] else set()
    out = []
    for i, a in enumerate(acts):
        d = date.fromisoformat(a["activity_date"])
        if not start <= d <= end:
            continue
        r = dict(a)
        ws, we = week_of(d, rules)
        r["week_start"], r["week_end"] = ws.isoformat(), we.isoformat()
        counts = not (i in repeats or ("new_employers" in rules["checks"] and r["contact_type"] == "Follow-up"))
        r["counts_toward_minimum"] = "yes" if counts else "no"
        r["reported_to_state_on"] = ""
        out.append(r)
    out.sort(key=lambda r: (r["week_start"], r["activity_date"]))
    return out


def load_rows(root, start, end, rules):
    store = open_store(root)
    if store is None:
        return None, []
    acts = all_activities(root, store)
    return store, rows_for(acts, start, end, rules)


# ---------- export ----------

def csv_cell(v):
    """CSV opens in Excel: a value starting with = + - @ gets an apostrophe."""
    s = text(v).replace("\r", " ").replace("\n", " ")
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def csv_text(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([csv_cell(r.get(c, "")) for c in COLUMNS])
    return buf.getvalue()


def missing_details(r):
    return [label for col, label in DETAIL_COLS if not r.get(col)]


def strength(r):
    """Sort key for the ready-to-type block: applications with full details first."""
    kind = {"application": 0, "referral": 1, "activity": 2}[r["_kind"]]
    return (r["counts_toward_minimum"] != "yes", len(missing_details(r)), r["contact_type"] != "New", kind)


def pick_ready(rows, n):
    """The n strongest rows, spread over as many days as possible."""
    ranked = sorted(rows, key=strength)
    out, days = [], set()
    for r in ranked:
        if len(out) < n and r["activity_date"] not in days:
            out.append(r)
            days.add(r["activity_date"])
    for r in ranked:
        if len(out) < n and r not in out:
            out.append(r)
    return sorted(out, key=lambda r: r["activity_date"])


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def weeks(rows):
    out = OrderedDict()
    for r in rows:
        out.setdefault((r["week_start"], r["week_end"]), []).append(r)
    return out


def ready_line(i, r, rules):
    url = r["employer_main_website"] if rules["website"] == "main" else (r["posting_url"] or r["employer_main_website"])
    parts = [r["activity_date"], r["activity_type"], r["contact_type"], r["employer_name"],
             r["job_title"], ("req " + r["job_req_id"]) if r["job_req_id"] else "", r["method"], r["contact_person"],
             url, r["employer_phone"], r["result"]]
    return f"{i}. " + " | ".join(p for p in parts if p)


def md_text(rows, rules, start, end, flagged):
    lines = [f"# Work-search record: {rules['code']}, {start.isoformat()} to {end.isoformat()}", "",
             "FLAG fill SSN/ID yourself (left blank; this skill never handles it).",
             "FLAG fill name and signature yourself.", "",
             f"{rules['name']}: {rules['note']}"]
    if rules.get("weekly_minimum"):
        lines.append(f"Weekly minimum: {rules['weekly_minimum']} contacts"
                     + (f" on {rules['min_days']} or more days" if rules.get("min_days") else "") + ".")
    for (ws, we), group in weeks(rows).items():
        days = len({r["activity_date"] for r in group})
        lines += ["", f"## Week {ws} to {we}: {plural(len(group), 'contact')} on {plural(days, 'day')}", "",
                  f"Ready to type ({min(len(group), rules['ready_rows'])} strongest):"]
        for i, r in enumerate(pick_ready(group, rules["ready_rows"]), 1):
            lines.append(ready_line(i, r, rules))
    if flagged:
        lines += ["", "Employer details still missing (run worksearch.py employers): " + ", ".join(flagged) + "."]
    lines += ["", "All rows are in the .csv and .xlsx beside this file. Keep them at least 2 years."]
    return "\n".join(lines) + "\n"


def xlsx_bytes(rows, rules, start, end):
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        raise MissingPackage()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Work search"

    def put(sheet, r, c, v, bold=False):
        cell = sheet.cell(r, c)
        s = text(v)
        if s:
            cell.value = s
            cell.data_type = "s"  # untrusted text never becomes a formula
        if bold:
            cell.font = Font(bold=True)

    for c, name in enumerate(COLUMNS, 1):
        put(ws, 1, c, name, bold=True)
    for i, r in enumerate(rows, 2):
        for c, name in enumerate(COLUMNS, 1):
            put(ws, i, c, r.get(name, ""))
    ws.freeze_panes = "A2"
    cl = wb.create_sheet("Claimant")
    block = [("Field", "Value", "Note"),
             ("Claimant name", "", "FLAG fill yourself"),
             ("SSN or claimant ID", "", "FLAG fill SSN/ID yourself; this skill never handles it"),
             ("Signature", "", "FLAG sign yourself"),
             ("Date signed", "", "FLAG fill yourself"),
             ("State", rules["code"], rules["name"]),
             ("Period", f"{start.isoformat()} to {end.isoformat()}", ""),
             ("Weekly minimum", str(rules.get("weekly_minimum") or "none fixed"), rules.get("note", ""))]
    for i, row in enumerate(block, 1):
        for c, v in enumerate(row, 1):
            put(cl, i, c, v, bold=(i == 1))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class MissingPackage(Exception):
    pass


def free_paths(root, state, start, end):
    """The three export paths; an earlier export of the period keeps its files."""
    folder = jobfs.Folder(root)
    base = {ext: folder.worksearch_file(state, start, end, ext) for ext in ("csv", "xlsx", "md")}
    kept = [p for p in base.values() if p.exists()]
    k = 1
    paths = base
    while any(p.exists() for p in paths.values()):
        k += 1
        paths = {ext: p.with_name(f"{p.stem}_v{k}{p.suffix}") for ext, p in base.items()}
    return paths, kept


def default_period(root, rules, start, end):
    if start and end:
        return start, end
    if start or end:
        raise WsError("give both --from and --to, or neither")
    yesterday = jobfs.today(root) - timedelta(days=1)
    end = week_of(yesterday, rules)[1]
    if end > yesterday:
        end -= timedelta(days=7)
    return end - timedelta(days=7 * int(rules["period_weeks"]) - 1), end


def state_of(root, a):
    return (a.state or jobfs.settings(root)["worksearch"].get("state") or "CA").upper()


def rel(root, p):
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(p)


def mark_rows(root, rows):
    """Record today's date in the Work search column of each exported application row."""
    import contextlib
    import tracker
    today = jobfs.today(root).isoformat()
    n = 0
    for r in rows:
        if r["_kind"] == "application" and r["_id"]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = tracker.main(["update", r["_id"], f"work_search={today}", "--root", str(root)])
            n += code == 0
    print(f"MARKED {n} application rows (Work search {today})")


def cmd_export(a):
    state = state_of(a.root, a)
    rules = preset(state)
    start, end = default_period(a.root, rules, opt_day("--from", a.start), opt_day("--to", a.end))
    if end < start:
        raise WsError("--to is before --from")
    xlsx_bytes([], rules, start, end)  # fail on a missing openpyxl before writing anything
    store, rows = load_rows(a.root, start, end, rules)
    if store is None:
        print("NO TRACKER")
        return 0
    paths, kept = free_paths(a.root, state, start, end)
    for p in kept:
        print(f"KEPT {rel(a.root, p)} (earlier exports are never rewritten)")
    flagged = sorted({r["employer_name"] for r in rows if r["_kind"] == "application" and missing_details(r)})
    jobfs.create_only(paths["csv"], csv_text(rows))
    jobfs.create_only(paths["xlsx"], xlsx_bytes(rows, rules, start, end))
    jobfs.create_only(paths["md"], md_text(rows, rules, start, end, flagged))
    kinds = Counter(r["_kind"] for r in rows)
    print(f"ROWS {len(rows)} | applications {kinds['application']} | referrals {kinds['referral']} | "
          f"activities {kinds['activity']} | by hand {sum(1 for r in rows if r['_source'].lower() == 'by hand')}")
    for (ws, we), group in weeks(rows).items():
        print(f"WEEK {ws} to {we} | {plural(len(group), 'row')} | "
              f"{plural(len({r['activity_date'] for r in group}), 'day')}")
    for ext in ("csv", "xlsx", "md"):
        print(f"WROTE {rel(a.root, paths[ext])}")
    print("FLAG fill SSN/ID yourself")
    print("FLAG fill name and signature yourself")
    if flagged:
        print(f"MISSING employer details for {len(flagged)} employers (worksearch.py employers)")
    if not rules["listed"]:
        print(f"NOTE {state} has no preset: {rules['note']}")
    if a.mark:
        mark_rows(a.root, rows)
    return 0


def opt_day(label, s):
    return parse_day(label, s) if s else None


# ---------- check ----------

def week_warnings(code, rows, rules, history):
    """WARN lines for one week's rows against the state rules."""
    out = []
    counted = [r for r in rows if r["counts_toward_minimum"] == "yes"]
    days = {r["activity_date"] for r in counted}
    new_emps = {r["_norm"] for r in counted if r["contact_type"] == "New" and r["_norm"]}
    checks = rules["checks"]
    minimum, min_days = int(rules.get("weekly_minimum") or 0), int(rules.get("min_days") or 0)
    if "applications_plus_activity" in checks:
        apps = sum(1 for r in counted if r["_kind"] == "application")
        if apps < 2:
            out.append(f"WARN {code} needs 2 applications a week (this week has {apps})")
        if len(counted) < 3:
            out.append(f"WARN {code} needs 1 more work-search activity or a 3rd application")
    elif "new_employers" in checks:
        if len(new_emps) < minimum:
            out.append(f"WARN {code} needs {minimum} new employers (this week has {len(new_emps)})")
    elif minimum and len(counted) < minimum:
        out.append(f"WARN {code} needs {minimum} contacts a week (this week has {len(counted)})")
    if min_days and len(days) < min_days:
        out.append(f"WARN {code} needs contacts on {min_days} or more days")
    if "same_employer_location" in checks:
        for name in sorted(fl_repeats(rows, history)):
            out.append(f"WARN {code} {name} at the same location 3 weeks in a row does not count")
    if "repeat_job" in checks:
        for r in rows:
            if r["_kind"] == "application" and r["counts_toward_minimum"] == "no":
                out.append(f"WARN {code} repeat application to the same job does not count: "
                           f"{r['employer_name']} - {r['job_title']}")
    return out


def fl_place(r):
    return (r["_norm"], (r["employer_location"] or f"{r['employer_city']} {r['employer_state']}").strip().lower())


def fl_repeats(rows, prior_weeks):
    """Employers at one location in this week and both weeks before it."""
    this = {fl_place(r) for r in rows if r["_norm"]}
    names = {fl_place(r): r["employer_name"] for r in rows}
    if len(prior_weeks) < 2:
        return set()
    hit = this & prior_weeks[0] & prior_weeks[1]
    return {names[k] for k in hit}


def cmd_check(a):
    state = state_of(a.root, a)
    rules = preset(state)
    start, end = week_of(parse_day("--week", a.week), rules)
    store = open_store(a.root)
    if store is None:
        print("NO TRACKER")
        return 0
    acts = all_activities(a.root, store)
    rows = rows_for(acts, start, end, rules)
    history = []
    for k in (1, 2):
        ps, pe = start - timedelta(days=7 * k), end - timedelta(days=7 * k)
        history.append({fl_place(r) for r in rows_for(acts, ps, pe, rules) if r["_norm"]})
    counted = [r for r in rows if r["counts_toward_minimum"] == "yes"]
    new_emps = {r["_norm"] for r in counted if r["contact_type"] == "New" and r["_norm"]}
    print(f"WEEK {state} {start.isoformat()} to {end.isoformat()} | contacts {len(counted)} | "
          f"days {len({r['activity_date'] for r in counted})} | new employers {len(new_emps)}")
    warns = week_warnings(state, rows, rules, history)
    for w in warns:
        print(w)
    if not rules["listed"]:
        print(f"NOTE {state} has no preset: {rules['note']}")
    if not warns:
        print("CHECK ok")
    return 0


# ---------- employers ----------

def cmd_employers(a):
    rules = preset(state_of(a.root, a))
    if a.start or a.end:
        start, end = default_period(a.root, rules, opt_day("--from", a.start), opt_day("--to", a.end))
    else:
        start, end = date(1900, 1, 1), jobfs.today(a.root)
    store, rows = load_rows(a.root, start, end, rules)
    if store is None:
        print("NO TRACKER")
        return 0
    seen, n = set(), 0
    for r in rows:
        if r["_kind"] not in ("application", "referral") or r["_norm"] in seen:
            continue
        seen.add(r["_norm"])
        gaps = missing_details(r)
        if gaps:
            n += 1
            print(f"MISSING {r['employer_name']} | {', '.join(gaps)}")
    if n:
        print('NEXT ask him once per employer, then: tracker.py employer-set --employer "<name>" '
              '--street ... --city ... --state ... --zip ... --phone ... --main-website ... --source user')
    else:
        print("EMPLOYERS ok")
    return 0


def cmd_states(a):
    presets = load_presets()
    for code in sorted((presets.get("states") or {})):
        r = preset(code, presets)
        print(f"STATE {code} | {r['name']} | minimum {r['weekly_minimum'] or 'none fixed'} | "
              f"week starts {r['week_start']} | {r['entry_mode']} | every {r['period_weeks']} week(s)")
    return 0


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="worksearch.py", description="Work-search record export and checks.", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("export", help="Write worksearch/<ST>_<from>_<to>.csv, .xlsx and .md (create-only).")
    p.add_argument("--root", required=True)
    p.add_argument("--state")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--mark", action="store_true", help="set Work search = today on exported application rows")
    p.set_defaults(func=cmd_export)
    p = sub.add_parser("check", help="Check one week against the state's rules.")
    p.add_argument("--root", required=True)
    p.add_argument("--state")
    p.add_argument("--week", required=True, help="any day of the week to check")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("employers", help="List employers that lack address, phone or website.")
    p.add_argument("--root", required=True)
    p.add_argument("--state")
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.set_defaults(func=cmd_employers)
    p = sub.add_parser("states", help="List the state presets.")
    p.set_defaults(func=cmd_states)
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
    except (WsError, jobfs.ConfigError) as e:
        print(f"ERROR {e}")
        return 1
    except FileExistsError as e:
        print(f"ERROR an export appeared while writing, nothing overwritten: {e}")
        return 1
    except MissingPackage:
        print("MISSING openpyxl: run  pip install openpyxl  and run again")
        return 2
    except Exception as e:
        if type(e).__name__ == "MissingPackage":  # tracker's, for a mode M tracker
            print("MISSING openpyxl: run  pip install openpyxl  and run again")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
