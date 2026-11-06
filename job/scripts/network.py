"""The referral network: the user's own contact exports, matched to jobs (spec 6.3, R11).

Usage:
    python network.py import --root R [--raw DIR]
    python network.py match --root R --date YYYY-MM-DD [--n N]
    python network.py match --root R --company C
    python network.py asks --root R --date YYYY-MM-DD

import reads the files the user dropped in network/raw (loose, or inside the LinkedIn
export zip) and writes network/network.csv:
    id, first, last, company_raw, company_norm, position, connected_on, email,
    source (linkedin|google|rec), strength (rec|emailed|connection)
Only an allowlist of files and columns is read: Connections.csv (the 7 columns,
any layout), Recommendations_Received.csv (names, Company, Job Title, Creation
Date), Jobs/Job Applications.csv (Application Date, Company Name, Job Title,
Job Url kept as text in network/past_applications.csv, never fetched) and a
Google Contacts CSV (names, organization, e-mail values). Every other file,
Profile.csv, PhoneNumbers.csv, saved answers, logins, registration and
messages among them, is never opened. A zip is opened only to list its
members; allowlisted members are read straight from the zip (nothing is
written to disk), one per base name, with path checks. Limits: 50 MB a file
and 200 MB for the whole import (counted as the data unpacks; the import stops
at the first file that would pass it). One unreadable file (corrupt, encrypted,
an odd compression) is one ERROR line naming it and the error type, and the
import goes on. No profile URL is kept. A value starting with = + - @ is
written with a leading apostrophe.

match pairs people with today's queue items (queue/<date>/queue.json and its
addenda), or with one company (--company, the single-job flow):
    tier 1   same company after legal-suffix and punctuation strip, or
             "@ <Company>" in a staffing-agency Position
    maybe    tier 2 alias (one name holds the other as whole words) or tier 3
             parent/subsidiary (watchlist parent column, Employers sheet);
             shown for the user's confirmation only
    never    Self-employed, Stealth, Freelance, Retired, Student, Open to work,
             Confidential, blank
At most 2 names per job, ranked rec > emailed > connection. --date writes
network/matches_<date>.csv and prints IDs only (MATCHES, REFERRAL lines);
names print only for one job (--n) or one company (--company):
    MATCH N-0004 | Jane Doe | Scientist | tier 1 company | rec
    MAYBE N-0010 | Lee Park | Analyst | tier 3 parent | connection

asks drafts at most [referrals] asks_per_day referral asks for the day's tier-1
matches: never a person who has an open ask (Referrals row Drafted or Sent)
for another job, never one person twice, never the same text to two people at
one company. Each draft (50-80 words: exact title, link, req ID, a two-line
pitch from the job's fact-checked resume JSON, a yes/no ask) runs through
checks.fact_check; one that cites a number missing from the sources is dropped
with its FACT line. The user sends every ask.
    ASK 1 #3 Acme Bio | Jane Doe (N-0004) | LinkedIn message | 61 words
    TEXT Hi Jane, ...
    DROP #5 Globex | Sam Lee (N-0007): a number is not in your sources
    SKIP #6 Initech | Pat Kim (N-0009): open ask for another job
    ASKS 3 of 3

Library (for the runner's queue builder): load_people(root),
match_company(people, employer, parents), referrals_for_items(root, items,
day) -> {job_key: {"referral": {state, contact_ref, hold_until}, "tier1": bool,
"matches": [...]}}.

Exit codes: 0 ok, 1 bad input (ERROR ...), 2 openpyxl missing (a tracker in
mode M only).
"""
import argparse
import csv
import io
import os
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import argfile  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402

NET_COLS = ["id", "first", "last", "company_raw", "company_norm", "position", "connected_on", "email",
            "source", "strength"]
PAST_COLS = ["date", "company", "company_norm", "title", "job_url"]
MATCH_COLS = ["date", "n", "job_key", "company", "contact_ref", "first", "last", "position", "tier", "label",
              "via", "strength", "state", "hold_until"]

# The only files ever opened (by base name, any folder depth); everything else is listed and skipped.
ALLOW = {"connections.csv": "connections", "recommendations_received.csv": "recs",
         "job applications.csv": "applications", "contacts.csv": "google", "google contacts.csv": "google",
         "google.csv": "google", "other contacts.csv": "google"}
NEVER_HINT = ("profile", "phone", "saved answers", "login", "registration", "message", "email addresses")
MAX_FILE = 50 * 1024 * 1024       # per zip member and per CSV read
MAX_TOTAL = 200 * 1024 * 1024     # every file one import reads, zips included (counted as it unpacks)
MAX_MEMBERS = 2000

STRENGTH_RANK = {"rec": 0, "emailed": 1, "connection": 2}
SOURCE_STRENGTH = {"rec": "rec", "google": "emailed", "linkedin": "connection"}
EXCLUDED = {"self employed", "selfemployed", "stealth", "stealth startup", "stealth mode", "freelance",
            "freelancer", "retired", "student", "open to work", "opentowork", "confidential", "unemployed",
            "none", "n a", "na", "various"}
OPEN_ASK = {"drafted", "sent"}
WORDS_MIN, WORDS_MAX = 50, 80
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


class NetError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- small helpers ----------

def hkey(h):
    """A header name compared without case, spaces or punctuation (older layouts)."""
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def safe(v, n=200):
    """Plain one-line text; a leading = + - @ gets an apostrophe so no
    spreadsheet ever reads it as a formula."""
    s = re.sub(r"[\r\n\t]+", " ", str(v or "")).strip()[:n]
    if s and s[0] in "=+-@":
        s = "'" + s
    return s


def plain(v):
    """The value as stored, without the formula guard."""
    s = str(v or "")
    return s[1:] if s[:2] in ("'=", "'+", "'-", "'@") else s


def norm(name):
    return jobkey.norm_company(plain(name))


def to_iso(s):
    """YYYY-MM-DD from the export date forms ('27 Mar 2026', '4-Apr-21',
    '03/27/2026', '2026-03-27'); the text itself when none fits."""
    s = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[AaPp][Mm])?$", "", str(s or "").strip())
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"^(\d{1,2})[\s-]([A-Za-z]{3})[A-Za-z]*[\s-](\d{2,4})$", s)
    if m and m.group(2).lower() in MONTHS:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
        except ValueError:
            return s
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return s
    return s


def parse_day(label, s):
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise NetError(f"{label} must be YYYY-MM-DD, got '{s}'")


def rel(root, path):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------- reading the exports ----------

def kind_of(e):
    """The exception type for an ERROR line: zlib.error, csv.Error, BadZipFile."""
    t = type(e)
    return t.__name__ if t.__module__ in ("builtins", "zipfile") else f"{t.__module__.lstrip('_')}.{t.__name__}"


class OverBudget(NetError):
    """The import read MAX_TOTAL bytes: nothing more is read this time."""


class Budget:
    """Bytes the whole import may still read (MAX_TOTAL), counted as they arrive."""

    def __init__(self, total=None):
        self.left = MAX_TOTAL if total is None else total


def read_stream(f, label, budget):
    """The bytes of an open file or zip member, refusing past MAX_FILE for this
    file and past what the budget has left (counted as the data unpacks, so a
    member that claims to be small cannot unpack to more)."""
    chunks, n = [], 0
    while chunk := f.read(1 << 16):
        n += len(chunk)
        if n > MAX_FILE:
            raise NetError(f"{label} is over {MAX_FILE // (1024 * 1024)} MB")
        if n > budget.left:
            raise OverBudget(f"{label}: this import reached its {MAX_TOTAL // (1024 * 1024)} MB total; "
                             "the files after it are not read")
        chunks.append(chunk)
    budget.left -= n
    return b"".join(chunks).decode("utf-8-sig", errors="replace")


def read_text(path, budget=None):
    if os.path.getsize(path) > MAX_FILE:
        raise NetError(f"{Path(path).name} is over {MAX_FILE // (1024 * 1024)} MB")
    with open(path, "rb") as f:
        return read_stream(f, Path(path).name, budget or Budget())


def find_table(text, required):
    """Rows as {hkey: value} below the first line whose cells hold every
    required key; skips a BOM and a 'Notes:' preamble of any length."""
    rows = list(csv.reader(io.StringIO(text)))
    for i, row in enumerate(rows):
        keys = [hkey(c) for c in row]
        if all(any(k in keys for k in alts) for alts in required):
            out = []
            for r in rows[i + 1:]:
                if not any(c.strip() for c in r):
                    continue
                out.append({keys[j]: r[j] for j in range(min(len(keys), len(r))) if keys[j]})
            return out
    return None


def first(rec, *keys):
    for k in keys:
        if rec.get(k, "").strip():
            return rec[k].strip()
    return ""


def person(first_name, last_name, company, position, connected_on, email, source):
    return {"first": safe(first_name, 80), "last": safe(last_name, 80), "company_raw": safe(company, 120),
            "company_norm": jobkey.norm_company(company), "position": safe(position, 160),
            "connected_on": safe(to_iso(connected_on), 20), "email": safe(email, 120) if "@" in email else "",
            "source": source, "strength": SOURCE_STRENGTH[source]}


def read_connections(text):
    rows = find_table(text, [("firstname",), ("lastname",), ("company",)])
    if rows is None:
        return None
    return [person(first(r, "firstname"), first(r, "lastname"), first(r, "company"), first(r, "position"),
                   first(r, "connectedon"), first(r, "emailaddress", "email"), "linkedin") for r in rows]


def read_recs(text):
    rows = find_table(text, [("firstname",), ("lastname",), ("company",)])
    if rows is None:
        return None
    return [person(first(r, "firstname"), first(r, "lastname"), first(r, "company"), first(r, "jobtitle"),
                   first(r, "creationdate"), "", "rec")
            for r in rows if first(r, "status").lower() not in ("hidden", "rejected", "declined")]


def read_google(text):
    rows = find_table(text, [("firstname", "givenname", "name"), ("organizationname", "organization1name")])
    if rows is None:
        return None
    out = []
    for r in rows:
        given, family = first(r, "firstname", "givenname"), first(r, "lastname", "familyname")
        if not (given or family) and first(r, "name"):
            parts = first(r, "name").split()
            given, family = parts[0], " ".join(parts[1:])
        emails = [v for k, v in sorted(r.items()) if re.fullmatch(r"email\d+value", k) and "@" in v]
        email = emails[0].split(":::")[0].strip() if emails else ""
        out.append(person(given, family, first(r, "organizationname", "organization1name"),
                          first(r, "organizationtitle", "organization1title"), "", email, "google"))
    return out


def read_applications(text):
    rows = find_table(text, [("applicationdate",), ("companyname",), ("jobtitle",)])
    if rows is None:
        return None
    return [{"date": safe(to_iso(first(r, "applicationdate")), 20), "company": safe(first(r, "companyname"), 120),
             "company_norm": jobkey.norm_company(first(r, "companyname")), "title": safe(first(r, "jobtitle"), 160),
             "job_url": safe(first(r, "joburl"), 500)} for r in rows]


READERS = {"connections": read_connections, "recs": read_recs, "google": read_google,
           "applications": read_applications}


def member_ok(name):
    """A zip member name with no absolute path, drive or parent step."""
    n = name.replace("\\", "/")
    return not (n.startswith("/") or re.match(r"^[A-Za-z]:", n) or any(p == ".." for p in n.split("/")))


def zip_sources(zip_path, report):
    """(base name, label, read(budget)) for each allowlisted member of one zip,
    read straight from the zip while it is open; nothing is written to disk.
    One member per base name (a zip holding Connections.csv in 2,000 folders is
    read once). Other members are listed by name only, never opened. A zip
    that cannot be opened is one ERROR line."""
    where = Path(zip_path).name
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as e:  # noqa: BLE001  a corrupt or locked zip is named; the other files are read
        report.append(f"ERROR {where}: not a readable zip ({kind_of(e)})")
        return
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            report.append(f"ERROR {where}: over {MAX_MEMBERS} files, not read")
            return
        taken = set()
        for info in infos:
            if info.is_dir():
                continue
            base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if base.lower() not in ALLOW:
                report.append(skip_line(base, where))
                continue
            if not member_ok(info.filename) or info.file_size > MAX_FILE:
                report.append(f"WARN {info.filename} in {where}: refused (path or size)")
                continue
            if base.lower() in taken:
                report.append(f"SKIP {base} in {where} (another copy in this zip was read)")
                continue
            taken.add(base.lower())

            def read(budget, info=info, base=base):
                with zf.open(info) as src:
                    return read_stream(src, f"{base} in {where}", budget)
            yield base, f"{base} (from {where})", read


def skip_line(base, where=""):
    why = "never opened" if any(h in base.lower() for h in NEVER_HINT) else "not on the allowlist"
    return f"SKIP {base}{' in ' + where if where else ''} ({why})"


def collect(raw, report):
    """(base name, label, read(budget)) for every allowlisted file in raw, zip
    members included, in folder order."""
    for dirpath, dirnames, filenames in os.walk(raw):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            low = fn.lower()
            if low.endswith(".zip"):
                yield from zip_sources(p, report)
            elif low in ALLOW:
                yield fn, fn, (lambda budget, p=p: read_text(p, budget))
            elif low.endswith(".csv"):
                report.append(skip_line(fn))


def merge(people):
    """One row per name: the strongest tie, the LinkedIn company when known,
    an email from any source."""
    by = {}
    for p in people:
        if not (p["first"] or p["last"]):
            continue
        k = (p["first"].lower(), p["last"].lower())
        old = by.get(k)
        if old is None:
            by[k] = dict(p)
            continue
        if STRENGTH_RANK[p["strength"]] < STRENGTH_RANK[old["strength"]]:
            old["strength"] = p["strength"]
        if p["source"] == "linkedin" and old["source"] != "linkedin":
            for f in ("company_raw", "company_norm", "position", "connected_on"):
                old[f] = p[f] or old[f]
            old["source"] = "linkedin"
        for f in ("company_raw", "company_norm", "position", "connected_on", "email"):
            old[f] = old[f] or p[f]
    return list(by.values())


def assign_ids(people, old_rows):
    """Keep each person's N- ID from the previous network.csv; new people get the next."""
    old = {(r.get("first", "").lower(), r.get("last", "").lower()): r.get("id", "") for r in old_rows}
    nums = [int(i[2:]) for i in old.values() if re.fullmatch(r"N-\d+", i or "")]
    nxt = max(nums + [0]) + 1
    for p in sorted(people, key=lambda p: (p["last"].lower(), p["first"].lower())):
        pid = old.get((p["first"].lower(), p["last"].lower()))
        if not pid:
            pid, nxt = f"N-{nxt:04d}", nxt + 1
        p["id"] = pid
    return sorted(people, key=lambda p: p["id"])


def write_csv(path, cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    jobfs.atomic_write_text(path, buf.getvalue())


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def cmd_import(a):
    folder = jobfs.Folder(a.root)
    raw = Path(a.raw) if a.raw else folder.network_raw
    if not raw.is_dir():
        print(f"NONE no export folder at {rel(a.root, raw)}")
        return 0
    report, people, past, counts = [], [], [], {"connections": 0, "recs": 0, "google": 0}
    budget = Budget()
    sources = collect(raw, report)
    try:
        for base, label, read in sources:
            kind = ALLOW[base.lower()]
            try:
                rows = READERS[kind](read(budget))
            except OverBudget as e:
                report.append(f"WARN {e}")
                break
            except Exception as e:  # noqa: BLE001  one unreadable file (corrupt, encrypted, too big) is named
                report.append(f"ERROR {label}: not read ({kind_of(e)})")
                continue
            if rows is None:
                report.append(f"WARN {label}: no header row found")
                continue
            report.append(f"READ {label}: {len(rows)} row{'' if len(rows) == 1 else 's'}")
            if kind == "applications":
                past += rows
            else:
                people += rows
                counts[kind] += len(rows)
    finally:
        sources.close()
    for line in report:
        print(line)
    if not people and not past:
        if any(line.startswith("ERROR ") for line in report):
            print(f"NONE nothing readable in {rel(a.root, raw)} (the ERROR lines above name each file)")
        else:
            print(f"NONE no allowlisted export file in {rel(a.root, raw)}")
        return 0
    merged = assign_ids(merge(people), read_csv(folder.network_csv))
    write_csv(folder.network_csv, NET_COLS, merged)
    print(f"IMPORTED {len(merged)} people: {counts['connections']} connections, {counts['recs']} recommendations, "
          f"{counts['google']} contacts")
    print(f"WROTE {rel(a.root, folder.network_csv)}")
    if past:
        out = folder.network / "past_applications.csv"
        write_csv(out, PAST_COLS, past)
        print(f"WROTE {rel(a.root, out)} ({len(past)} earlier application{'' if len(past) == 1 else 's'}, "
              "links kept as text, never fetched)")
    return 0


# ---------- matching ----------

def load_people(root):
    rows = read_csv(jobfs.Folder(root).network_csv)
    for r in rows:
        r["company_norm"] = r.get("company_norm") or norm(r.get("company_raw"))
        r["strength"] = r.get("strength") if r.get("strength") in STRENGTH_RANK else "connection"
    return rows


def excluded(n):
    return not n or n in EXCLUDED or n.startswith("stealth") or n.startswith("self employed")


def at_company(position):
    """The employer named after the last '@' in a Position ('Magnit / Scientist @ Acme')."""
    hits = re.findall(r"@\s*([^@/|,;()]+)", plain(position))
    return jobkey.norm_company(hits[-1]) if hits else ""


def alias_of(a, b):
    """One normalized name holds the other as leading whole words ('genentech'
    and 'genentech a member of the roche'); short names never count."""
    short_, long_ = sorted((a.split(), b.split()), key=len)
    return (len(" ".join(short_)) >= 4 and short_ != long_ and long_[:len(short_)] == short_)


def tier_of(p, emp, parents):
    """(tier, via) for one person against one employer norm, or (None, '')."""
    pn = p["company_norm"]
    at = at_company(p.get("position"))
    if at and not excluded(at) and at == emp:
        return 1, "position"
    if excluded(pn):
        return None, ""
    if pn == emp:
        return 1, "company"
    if alias_of(pn, emp):
        return 2, "alias"
    pe, pp = parents.get(emp), parents.get(pn)
    if (pe and pp and pe == pp) or pe == pn or pp == emp:
        return 3, "parent"
    return None, ""


def match_company(people, employer, parents=None, limit=2):
    """At most `limit` matches for one employer, ranked tier 1 first, then
    rec > emailed > connection. Each is {person, tier, via, label}."""
    emp = jobkey.norm_company(employer)
    if excluded(emp):
        return []
    hits = []
    for p in people:
        tier, via = tier_of(p, emp, parents or {})
        if tier:
            hits.append({"person": p, "tier": tier, "via": via, "label": "match" if tier == 1 else "maybe"})
    hits.sort(key=lambda h: (h["tier"] > 1, STRENGTH_RANK[h["person"]["strength"]],
                             h["person"]["last"].lower(), h["person"]["first"].lower()))
    return hits[:limit]


def open_store(root):
    """The tracker as the transport reads it (snapshot plus overlay, or the xlsx)."""
    import caps
    return caps.open_store(root)


def load_parents(root, store):
    import caps
    return caps.load_parents(root, store)


def referral_rows(store):
    if store is None or store.referrals is None:
        return []
    return store.referrals.rows()


def text(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return "" if v is None else str(v).strip()


def add_business_days(d, n):
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def referral_state(item_key, ref_rows, hold_days, day):
    """('asked'|'held'|'', hold_until) from the Referrals sheet for one job."""
    for r in ref_rows:
        if text(r.get("Job key")) == item_key and text(r.get("Status")).lower() in ("sent", "replied", "referred"):
            sent = text(r.get("Date"))[:10]
            if hold_days and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sent):
                until = add_business_days(date.fromisoformat(sent), int(hold_days))
                if day < until:
                    return "held", until.isoformat()
            return "asked", ""
    return "", ""


def referrals_for_items(root, items, day, people=None, store=None):
    """{job_key: {"referral": {...}, "tier1": bool, "matches": [...]}} for queue
    items, the shape queue.json carries (spec 2.5)."""
    people = load_people(root) if people is None else people
    store = open_store(root) if store is None else store
    parents = load_parents(root, store)
    refs = referral_rows(store)
    hold = jobfs.settings(root)["referrals"].get("hold_days", 0) or 0
    out = {}
    for it in items:
        key = str(it.get("job_key", ""))
        hits = match_company(people, str(it.get("company", "")), parents)
        tier1 = [h for h in hits if h["tier"] == 1]
        state, until = referral_state(key, refs, hold, day)
        if not state:
            state = "candidate" if tier1 else "none"
        ref = {"state": state, "contact_ref": tier1[0]["person"]["id"] if tier1 else "", "hold_until": until}
        out[key] = {"referral": ref, "tier1": bool(tier1), "matches": hits}
    return out


def name_line(h):
    p = h["person"]
    tag = "MATCH" if h["tier"] == 1 else "MAYBE"
    return (f"{tag} {p['id']} | {plain(p['first'])} {plain(p['last'])} | {plain(p['position']) or '-'} | "
            f"tier {h['tier']} {h['via']} | {p['strength']}")


def queue_items(root, day):
    import tracker
    return tracker.queue_items(root, day)


def cmd_match(a):
    people = load_people(a.root)
    if not people:
        print("NONE no network/network.csv yet (drop the export in network/raw, then network.py import)")
        return 0
    if a.company:
        store = open_store(a.root)
        hits = match_company(people, a.company, load_parents(a.root, store))
        for h in hits:
            print(name_line(h))
        if not hits:
            print(f"NO MATCH {a.company}")
        return 0
    if not a.date:
        raise NetError("give --date YYYY-MM-DD (today's queue) or --company C")
    day = parse_day("--date", a.date)
    items = queue_items(a.root, day)
    if not items:
        print(f"NO QUEUE {day.isoformat()}")
        return 0
    if a.n is not None:
        items = [i for i in items if i.get("n") == a.n]
        if not items:
            raise NetError(f"no item #{a.n} in the {day.isoformat()} queue")
    found = referrals_for_items(a.root, items, day, people)
    rows, tier1, maybe = [], 0, 0
    for it in items:
        res = found[str(it.get("job_key", ""))]
        tier1 += res["tier1"]
        maybe += any(h["tier"] > 1 for h in res["matches"])
        for h in res["matches"]:
            p = h["person"]
            rows.append({"date": day.isoformat(), "n": it.get("n", ""), "job_key": it.get("job_key", ""),
                         "company": safe(it.get("company", ""), 120), "contact_ref": p["id"], "first": p["first"],
                         "last": p["last"], "position": p["position"], "tier": h["tier"], "label": h["label"],
                         "via": h["via"], "strength": p["strength"], "state": res["referral"]["state"],
                         "hold_until": res["referral"]["hold_until"]})
            if a.n is not None:
                print(name_line(h))
        ref = res["referral"]
        if ref["state"] != "none" and a.n is None:
            print(f"REFERRAL #{it.get('n')} {it.get('job_key')} {ref['state']} {ref['contact_ref']}"
                  + (f" until {ref['hold_until']}" if ref["hold_until"] else ""))
    if a.n is not None:
        if not rows:
            print(f"NO MATCH #{a.n}")
        return 0
    print(f"MATCHES {day.isoformat()} | {len(items)} jobs | {tier1} with a tier-1 contact | {maybe} maybe")
    out = jobfs.Folder(a.root).matches_csv(day)
    write_csv(out, MATCH_COLS, rows)
    print(f"WROTE {rel(a.root, out)}")
    return 0


# ---------- asks ----------

def sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(s or "").strip()) if x.strip()]


def resume_summary(folder):
    """The Summary paragraph of the job's fact-checked resume JSON (Tier A
    resume.json first, then the Tier B resume_B.json)."""
    import json
    for name in ("resume.json", "resume_B.json"):
        p = Path(folder) / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for s in data.get("sections", []) if isinstance(data, dict) else []:
            if isinstance(s, dict) and str(s.get("heading", "")).lower() in ("summary", "profile") and s.get("paragraph"):
                return str(s["paragraph"]), name
    return "", ""


def posting_facts(root, item):
    """(link, req ID, posting text) for a queue item's job folder."""
    import json
    folder = Path(root) / str(item.get("folder", ""))
    link, req, md = str(item.get("apply_url", "")), "", ""
    try:
        pj = json.loads((folder / "posting.json").read_text(encoding="utf-8"))
        link = pj.get("url") or link
        req = (pj.get("req_ids") or [""])[0]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        md = (folder / "posting.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        md = ""
    if not req and md:
        reqs = sorted(jobkey.req_ids(md))
        req = reqs[0] if reqs else ""
    return link, str(req), md


def my_first_name(root):
    try:
        prof = (Path(root) / "profile.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for key in ("Preferred first name", "Legal first name"):
        m = re.search(rf"^-\s*{key}:[ \t]*(\S[^\r\n]*)$", prof, re.M)
        if m:
            return m.group(1).strip()
    return ""


def applied_on(store, job_key):
    """The date the user applied to this job, when the tracker says so."""
    if store is None or not job_key:
        return ""
    for r in store.app_rows():
        if text(r.get("Job key")) == job_key and text(r.get("Applied")):
            return text(r.get("Applied"))[:10]
    return ""


def draft(contact_first, item, link, req, pitch, me, applied=""):
    """The ask: 50-80 words, exact title, link and req ID, the pitch, one yes/no question."""
    role = f"the {item.get('role', '')} role at {item.get('company', '')}"
    if req:
        role += f" (req {req})"
    if applied:
        opener = f"I applied for {role} on {applied}: {link}."
        ask = "Would you be willing to refer me or put in a word with the hiring team? A yes or no is fine."
    else:
        opener = f"I am about to apply for {role}: {link}."
        ask = "Would you be willing to refer me before I apply? A yes or no is completely fine."
    body = f"{opener} {pitch} {ask}"
    sign = f" Thanks, {me}" if me else " Thanks"
    return f"Hi {contact_first}, I hope you are well. {body}{sign}", body


def fact_lines(text_, sources):
    import checks
    data = {"sections": [{"heading": "Ask", "paragraph": text_}]}
    return [ln for ln in checks.fact_check(data, checks.strip_ids(sources), v12=True) if not ln.startswith("FACT ok")]


def source_text(root, md, extra):
    chunks = [md, "\n".join(str(x) for x in extra)]
    for name in ("master_resume.md", "achievement_library.md", "profile.md"):
        try:
            chunks.append((Path(root) / name).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(chunks)


def open_asks(ref_rows):
    """name (lowercase) -> (job key, status, date) for every Drafted or Sent ask."""
    out = {}
    for r in ref_rows:
        if text(r.get("Status")).lower() in OPEN_ASK:
            out.setdefault(plain(text(r.get("Contact"))).lower(), (text(r.get("Job key")), text(r.get("Status")),
                                                                    text(r.get("Date"))[:10]))
    return out


def plan_asks(root, day, items, people, store):
    """The day's asks: (lines to print, asks). Pure apart from reading files."""
    cfg = jobfs.settings(root)["referrals"]
    cap = int(cfg.get("asks_per_day", 3))
    found = referrals_for_items(root, items, day, people, store)
    refs = referral_rows(store)
    busy = open_asks(refs)
    me = my_first_name(root)
    lines, asks, used, company_texts = [], [], set(), {}
    ordered = sorted(items, key=lambda i: (-(i.get("score") or 0), i.get("n") or 0))
    for it in ordered:
        if len(asks) >= cap:
            break
        key = str(it.get("job_key", ""))
        res = found.get(key)
        if not res or not res["tier1"] or res["referral"]["state"] in ("asked", "held"):
            continue
        tag = f"#{it.get('n')} {it.get('company', '')}"
        for h in [h for h in res["matches"] if h["tier"] == 1]:
            p = h["person"]
            name = f"{plain(p['first'])} {plain(p['last'])}".strip()
            who = f"{name} ({p['id']})"
            if name.lower() in used:
                lines.append(f"SKIP {tag} | {who}: already asked about another job today")
                continue
            if name.lower() in busy and busy[name.lower()][0] != key:
                lines.append(f"SKIP {tag} | {who}: open ask for another job ({busy[name.lower()][1]} "
                             f"{busy[name.lower()][2]})")
                continue
            pitch, src = resume_summary(Path(root) / str(it.get("folder", "")))
            if not pitch:
                lines.append(f"SKIP {tag}: no resume JSON in the job folder for the pitch")
                break
            link, req, md = posting_facts(root, it)
            applied = applied_on(store, key)
            parts = sentences(pitch)
            for k in (2, 1):
                full, body = draft(plain(p["first"]), it, link, req, " ".join(parts[:k]), me, applied)
                if len(full.split()) <= WORDS_MAX:
                    break
            sources = source_text(root, md, [it.get("company"), it.get("role"), link, req, applied, me])
            facts = fact_lines(full, sources)
            if facts:
                lines.append(f"DROP {tag} | {who}: a number is not in your sources")
                lines += facts
                break
            cn = jobkey.norm_company(str(it.get("company", "")))
            if body in company_texts.get(cn, set()):
                lines.append(f"SKIP {tag} | {who}: same text as another ask at {it.get('company', '')} today")
                continue
            company_texts.setdefault(cn, set()).add(body)
            used.add(name.lower())
            channel = "Email" if p.get("email") else "LinkedIn message"
            asks.append({"n": it.get("n"), "job_key": key, "company": it.get("company", ""), "contact": name,
                         "contact_ref": p["id"], "channel": channel, "text": full, "words": len(full.split()),
                         "pitch_from": src})
            break
    return lines, asks, cap


def cmd_asks(a):
    day = parse_day("--date", a.date)
    if not jobfs.settings(a.root)["referrals"].get("enabled", True):
        print("ASKS off (referrals.enabled = false)")
        return 0
    people = load_people(a.root)
    if not people:
        print("NONE no network/network.csv yet")
        return 0
    items = queue_items(a.root, day)
    if not items:
        print(f"NO QUEUE {day.isoformat()}")
        return 0
    lines, asks, cap = plan_asks(a.root, day, items, people, open_store(a.root))
    for i, ask in enumerate(asks, 1):
        print(f"ASK {i} #{ask['n']} {ask['company']} | {ask['contact']} ({ask['contact_ref']}) | "
              f"{ask['channel']} | {ask['words']} words")
        print(f"TEXT {ask['text']}")
        if not WORDS_MIN <= ask["words"] <= WORDS_MAX:
            print(f"WARN ask {i} is {ask['words']} words (aim for {WORDS_MIN}-{WORDS_MAX})")
    for line in lines:
        print(line)
    print(f"ASKS {len(asks)} of {cap}")
    return 0


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="network.py", description="Referral network: import, match, asks.", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("import", help="Read network/raw into network/network.csv.")
    p.add_argument("--root", required=True)
    p.add_argument("--raw", help="the folder holding the exports (default network/raw)")
    p.set_defaults(func=cmd_import)
    p = sub.add_parser("match", help="Match people to the day's queue (or one company).")
    p.add_argument("--root", required=True)
    p.add_argument("--date")
    p.add_argument("--n", type=int, help="print the names for this one item")
    p.add_argument("--company")
    p.set_defaults(func=cmd_match)
    p = sub.add_parser("asks", help="Draft the day's referral asks (the user sends them).")
    p.add_argument("--root", required=True)
    p.add_argument("--date", required=True)
    p.set_defaults(func=cmd_asks)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = argfile.parse(build_parser(), argv)  # outside text arrives in a JSON file, never inline
    try:
        return args.func(args)
    except (NetError, jobfs.ConfigError, zipfile.BadZipFile) as e:
        print(f"ERROR {e}")
        return 1
    except Exception as e:  # noqa: BLE001  a tracker in mode M needs openpyxl; anything else is one ERROR line
        if type(e).__name__ == "MissingPackage":
            print("MISSING openpyxl: run  pip install openpyxl  and run again")
            return 2
        print(f"ERROR network {args.cmd if hasattr(args, 'cmd') else 'command'} stopped: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
