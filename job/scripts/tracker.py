"""The job-application tracker: <JobSearch folder>/applications.xlsx.

Usage (every subcommand except `statuses` takes --root <JobSearch folder>):
    python tracker.py init --root R
    python tracker.py check --root R --company C --title T [--url U] [--job-key K] [--posting P]
    python tracker.py add --root R --company C --role ROLE [--location L] [--fit F]
        [--status S] [--url U] [--folder F] [--source Skill|"By hand"|Runner]
        [--applied YYYY-MM-DD] [--notes N] [--job-key K] [--tier A|B|"By hand"] [--family F]
        [--variant V] [--ats A] [--found-via W] [--posted D] [--req-id R] [--queue-date D]
        [--account none|exists|new] [--referral NAME] [--referral-url U] [--confirmed D]
        [--proof P] [--block-reason CODE] [--employer-site U] [--force]
    python tracker.py update --root R ID field=value [field=value ...] [--force]
    python tracker.py show --root R [--id ID] [--status S [S ...]] [--active] [--company C]
    python tracker.py due --root R
    python tracker.py account --root R --site HOST [--email E] [--notes N] [--employer E]
        [--ats A] [--used] [--reuse]
    python tracker.py inbox-plan --root R
    python tracker.py seen --root R MESSAGE_ID [MESSAGE_ID ...]
    python tracker.py log-email --root R --message-id M --email-date D --from X
        --subject S --class C [--job-id ID] [--quote Q] [--change T] [--kind reply|alert]
    python tracker.py inbox-done --root R
    python tracker.py referral-add --root R --contact NAME --company C [--job-key K]
        [--channel CH] [--status S] [--nudge-on D] [--referral-url U] [--notes N] [--date D]
    python tracker.py employer-set --root R --employer NAME [--parent P] [--main-website U]
        [--street S] [--city C] [--state S] [--zip Z] [--phone P] [--email E] [--source S]
        [--confirmed-on D] [--notes N]
    python tracker.py activity-add --root R --type T --description D [--date D]
        [--contact C] [--result R]
    python tracker.py archive --root R --before YYYY-MM-DD
    python tracker.py stats --root R
    python tracker.py statuses
  Runner side (it is the only writer of applications.xlsx in mode L):
    python tracker.py apply-events --root R
    python tracker.py snapshot --root R
    python tracker.py upgrade --root R
  Any command also takes --args-file <R>/claude/<name>.json (argfile.py): the
  options as one JSON object, so text from an email, a posting or a form never
  passes through a shell. An event in mode L carries the expanded command line.

Columns are found by header name, so the user may sort rows, add columns or
retype values in Excel. Every write saves to a temp file and renames it over
applications.xlsx, after copying the old file to backups/applications_<date>.xlsx
the first time that day. The workbook never holds a password. A v1.1 workbook is
upgraded in place on its first write (a copy goes to
backups/applications_<date>_before_upgrade.xlsx; new columns and sheets are
appended, nothing moves).

Transports (spec 2.6). Reads and writes take --transport auto|events|direct.
    direct  reads and writes applications.xlsx (mode M, and the runner).
    events  reads runner/out/tracker.json plus pending events, and turns a write
            into one create-only runner/in/events/<id>_tracker.json that the runner
            applies with this same code. It prints the lines direct mode prints;
            `add` prints ADDED P-<8hex> (pending). Every ID argument also takes a
            P- ID. No openpyxl needed.
    auto    events when config/settings.toml sets [runner] mode L or S; direct
            when it sets M; with no settings file, events when the runner's
            snapshot (or a pending tracker event) is there, else direct.

One job, one row: `add` for a job key that already has a row updates that row
instead when it was never sent (Started through Filled - awaiting submit,
Blocked, Not pursued; the status moves only forward unless the row was Not
pursued or Closed) and prints `ADDED <ID> (existing row)` (`ADDED P-<8hex>
(pending, existing row)`); when the row records a sent application it prints
`REFUSED <ID> already <status> for job key <key>: ...` and exits 5 (--force
adds a new row anyway, for a real second application).

`check` labels, strongest first:
    SAME POSTING   same job key or URL
    LIKELY REPOST  same company and requisition ID, or posting text at least
                   50% the same (word 5-grams); another company's posting with
                   a SimHash at most 3 bits away
    SIMILAR ROLE   same company, title at least 85% alike
    SAME COMPANY   same company, another role, started in the last 90 days
It also prints IN QUEUE <date> #<n> when the job is in today's queue.

Exit codes: 0 ok, 1 bad input (ERROR ...), 2 openpyxl missing (pip install
openpyxl), 4 applications.xlsx is open in Excel (LOCKED ...), 5 a backward
status move, or a second row for a job already sent, was refused (REFUSED
...; add --force to correct a mistake).

apply-events treats the workbook as locked only when a program holds it
(applications.xlsx, or Excel's hidden ~$applications.xlsx owner file, cannot
be opened for writing); an owner file Excel left behind after a crash or a
forced restart is ignored.
"""
import argparse
import contextlib
import io
import os
import re
import secrets
import shutil
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import argfile  # noqa: E402
import events  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    openpyxl = None

FILENAME = "applications.xlsx"
LOCK_NAME = "~$" + FILENAME
ARCHIVE_GLOB = "applications_archive_*.xlsx"
DATE_FMT = "yyyy-mm-dd"
MAX_ROWS = 20000
SIMHASH_BITS = 3  # spec 2.6: cross-company repost at Hamming distance 3 or less

V11_APP_COLS = ["ID", "Date", "Company", "Role", "Location", "Fit", "Status", "Applied", "Follow-up",
                "Last update", "Next step", "Source", "Job key", "Resume sent", "Contact", "Notes",
                "Folder", "URL"]
APP_COLS = V11_APP_COLS + ["Tier", "Family", "Variant", "ATS", "Found via", "Posted", "Req ID",
                           "Queue date", "Account", "Referral", "Referral URL", "Confirmed", "Proof",
                           "Block reason", "Employer site", "Work search"]
ACC_COLS = ["Site", "Login email", "Created", "Notes", "Employer", "ATS", "Last used"]
LOG_COLS = ["Checked", "Message ID", "Email date", "From", "Subject", "Job ID", "Classified as",
            "Quote", "Change", "Kind"]
SET_COLS = ["Key", "Value"]
REF_COLS = ["ID", "Date", "Contact", "Company", "Job key", "Channel", "Status", "Nudge on",
            "Referral URL", "Notes"]
EMP_COLS = ["Employer", "Normalized", "Parent", "Main website", "Street", "City", "State", "ZIP",
            "Phone", "Email", "Source", "Confirmed on", "Notes"]
ACT_COLS = ["Date", "Type", "Description", "Contact", "Result"]
# (attribute, sheet title, columns) in workbook order; Summary comes last.
SHEETS = [("apps", "Applications", APP_COLS), ("accounts", "Accounts", ACC_COLS),
          ("log", "Email log", LOG_COLS), ("settings", "Settings", SET_COLS),
          ("referrals", "Referrals", REF_COLS), ("employers", "Employers", EMP_COLS),
          ("activities", "Activities", ACT_COLS)]
DATE_FIELDS = {"Date", "Applied", "Follow-up", "Last update", "Posted", "Queue date", "Confirmed",
               "Work search"}
LINK_COLS = {"URL", "Referral URL"}

STATUSES = ["Started", "Not pursued", "Resume ready", "Filled - awaiting submit",
            "Answers ready - fill by hand", "Blocked - login", "Blocked - CAPTCHA", "Blocked - site",
            "Applied", "No response", "Assessment", "Screen", "Interview", "Offer", "Rejected",
            "Withdrawn", "Closed"]
FITS = ["Strong Fit", "Good Fit", "Stretch", "Long Shot"]
SOURCES = ["Skill", "By hand", "Runner"]
TIERS = ["A", "B", "By hand"]
ACCOUNT_STATES = ["none", "exists", "new"]
KINDS = ["reply", "alert"]
REF_CHANNELS = ["LinkedIn message", "Email", "Text", "In person"]
REF_STATUSES = ["Drafted", "Sent", "Replied", "Referred", "No reply", "Declined"]
ACT_TYPES = ["Job fair", "Workshop", "RESEA", "Interview", "Other"]
# "Not pursued" is final like Rejected, so dropping a job is never a backward move.
RANK = {"Started": 0, "Not pursued": 7, "Resume ready": 1, "Filled - awaiting submit": 2,
        "Answers ready - fill by hand": 2, "Blocked - login": 2, "Blocked - CAPTCHA": 2,
        "Blocked - site": 2, "Applied": 3, "No response": 3, "Assessment": 4, "Screen": 4,
        "Interview": 5, "Offer": 6, "Rejected": 7, "Withdrawn": 7, "Closed": 7}
CLEAR_FOLLOWUP = {"Assessment", "Screen", "Interview", "Offer", "Rejected", "Withdrawn", "Closed",
                  "Not pursued"}
INACTIVE = {"Not pursued", "Rejected", "Withdrawn", "Closed"}
RESPONDED = {"Assessment", "Screen", "Interview", "Offer", "Rejected"}
INTERVIEWED = {"Interview", "Offer"}
SHOW_COLS = ["ID", "Date", "Company", "Role", "Fit", "Status", "Applied", "Follow-up", "Next step",
             "Job key", "Folder", "URL"]
WIDTHS = {
    "Applications": {"ID": 6, "Date": 11, "Company": 22, "Role": 30, "Location": 18, "Fit": 12,
                     "Status": 26, "Applied": 11, "Follow-up": 11, "Last update": 11,
                     "Next step": 30, "Source": 9, "Job key": 28, "Resume sent": 22,
                     "Contact": 22, "Notes": 50, "Folder": 28, "URL": 40, "Tier": 7, "Family": 14,
                     "Variant": 14, "ATS": 14, "Found via": 16, "Posted": 11, "Req ID": 14,
                     "Queue date": 11, "Account": 9, "Referral": 20, "Referral URL": 30,
                     "Confirmed": 11, "Proof": 24, "Block reason": 16, "Employer site": 28,
                     "Work search": 11},
    "Accounts": {"Site": 28, "Login email": 30, "Created": 11, "Notes": 50, "Employer": 22,
                 "ATS": 14, "Last used": 11},
    "Email log": {"Checked": 11, "Message ID": 22, "Email date": 11, "From": 30, "Subject": 40,
                  "Job ID": 7, "Classified as": 16, "Quote": 50, "Change": 30, "Kind": 8},
    "Settings": {"Key": 22, "Value": 14},
    "Referrals": {"ID": 6, "Date": 11, "Contact": 22, "Company": 22, "Job key": 28, "Channel": 16,
                  "Status": 11, "Nudge on": 11, "Referral URL": 30, "Notes": 40},
    "Employers": {"Employer": 24, "Normalized": 20, "Parent": 18, "Main website": 28, "Street": 24,
                  "City": 16, "State": 7, "ZIP": 8, "Phone": 14, "Email": 24, "Source": 12,
                  "Confirmed on": 11, "Notes": 40},
    "Activities": {"Date": 11, "Type": 12, "Description": 40, "Contact": 22, "Result": 30},
}
PASSWORD_RE = re.compile(r"pass(word)?|pwd|passwd", re.I)
TEXT_STRIP = chr(9) + chr(13)  # leading tab/CR are stripped from stored text
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PID_RE = re.compile(r"P-[0-9a-f]{8}")
WRITES = {"init", "add", "update", "account", "log-email", "inbox-done", "referral-add",
          "employer-set", "activity-add", "archive"}
READS = {"check", "show", "due", "inbox-plan", "seen", "stats"}


class TrackerError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


class MissingPackage(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- values ----------

_ACTING_ON = None  # while an event applies: the day the user acted, not the day it reached the PC


def today(root=None):
    """Today in the [time] timezone (spec 2.8), or the acting day of an event."""
    return _ACTING_ON if _ACTING_ON is not None else jobfs.today(root)


@contextlib.contextmanager
def acting_on(day):
    global _ACTING_ON
    old, _ACTING_ON = _ACTING_ON, day
    try:
        yield
    finally:
        _ACTING_ON = old


def text(v):
    """A cell value as clean text (dates as YYYY-MM-DD)."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def to_date(v):
    """A real date, or YYYY-MM-DD text (a time after it is ignored); else None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = DATE_RE.match(str(v).strip()) if v is not None else None
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def to_int(v):
    try:
        return int(float(text(v)))
    except ValueError:
        return None


def row_id(v):
    """A row's ID: its number, or its P- ID while the row is still pending."""
    s = text(v)
    return s if PID_RE.fullmatch(s) else to_int(v)


def parse_date_arg(label, s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise TrackerError(f"{label} must be YYYY-MM-DD, got '{s}'")


def opt_date(label, s):
    return parse_date_arg(label, s) if s and s.strip() else None


def pick(label, value, choices):
    """The canonical spelling of value from choices (case-insensitive), or an error."""
    want = re.sub(r"\s+", " ", value.strip()).lower()
    for c in choices:
        if c.lower() == want:
            return c
    raise TrackerError(f"{label} '{value}' is not one of: {', '.join(choices)}")


def opt_pick(label, value, choices):
    return pick(label, value, choices) if value and value.strip() else ""


def rank_of(status):
    s = text(status).lower()
    for name, r in RANK.items():
        if name.lower() == s:
            return r
    return None


def canon_status(status):
    s = text(status).lower()
    for name in STATUSES:
        if name.lower() == s:
            return name
    return text(status)


def header_key(v):
    return re.sub(r"\s+", " ", str(v)).strip().lower() if v is not None else ""


def field_key(name):
    return re.sub(r"[\s_\-]", "", name.lower())


def short(s, n=60):
    s = re.sub(r"\s*\n\s*", " / ", text(s))
    return s if len(s) <= n else s[:n - 3] + "..."


def url_host(url):
    return jobkey.parse_url(url)[0]


def clean_value(name, val):
    """What a cell stores for val: None for blank, a date, or text without a
    leading tab or CR (so untrusted text can never start a formula)."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        val = val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str) or name == "Message ID":
        return str(val).lstrip(TEXT_STRIP)
    return val


def jsonable(v):
    if isinstance(v, (datetime, date)):
        return text(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)


def check_folder(root, folder):
    """A job folder relative to --root, with forward slashes; outside it is an error."""
    folder = (folder or "").strip().replace("\\", "/")
    if folder and not jobfs.Folder(root).inside(folder):
        raise TrackerError(f"folder must be inside the JobSearch folder: {folder}")
    return folder


CHECKED_LINKS = {"URL": "url", "Referral URL": "referral-url", "Employer site": "employer-site"}


def check_link(label, value, board_ok=False):
    """A link the tracker stores passes netfetch.web_refusal (R1), or the write
    is refused, writing nothing. A blank value stays blank. Only a job's own
    URL may be a job board's link (linkedin.md records LinkedIn postings by
    their link); the board link is stored, never opened."""
    v = (value or "").strip() if isinstance(value, str) else value
    if not v:
        return v
    import netfetch  # stdlib and jobfs only; loaded only when a link is stored
    why = netfetch.web_refusal(v)
    if why and not (board_ok and why.startswith("job-board ")):
        raise TrackerError(f"--{label} refused ({why}): leave it out, or use the employer's own "
                           "plain https:// link")
    return v


def clickable(url):
    """A stored link becomes a hyperlink only when netfetch.web_refusal passes
    it (R1): a job board's link (linkedin.md: never hyperlinked) and an older
    row's file:, javascript: or user-name link stay plain text."""
    import netfetch
    return netfetch.web_refusal(url) is None


def followup_rule(root):
    cfg = jobfs.settings(root).get("followups", {})
    return [str(t).lower() for t in cfg.get("tiers", ["A", "referral"])], int(cfg.get("days", 7))


def wants_followup(root, tier, referral):
    """Follow-ups only for Tier A and referral rows (spec 2.6); a row with no
    Tier (v1.1, or the single-job flow) keeps the v1.1 rule."""
    tiers, _ = followup_rule(root)
    t = text(tier).lower()
    return not t or t in tiers or ("referral" in tiers and bool(text(referral)))


# ---------- sheets ----------

class Sheet:
    """One worksheet whose columns are found by header name."""

    def __init__(self, ws, columns):
        self.ws = ws
        self.title = ws.title
        self.columns = columns
        self.col = {}
        self.reindex()

    def reindex(self):
        found = {}
        for c in range(1, self.ws.max_column + 1):
            h = header_key(self.ws.cell(1, c).value)
            if h and h not in found:
                found[h] = c
        self.col = {n: found[header_key(n)] for n in self.columns if header_key(n) in found}

    def missing(self):
        return [n for n in self.columns if n not in self.col]

    def ensure_columns(self):
        last = 0
        for c in range(1, self.ws.max_column + 1):
            if header_key(self.ws.cell(1, c).value):
                last = c
        for n in self.columns:
            if n not in self.col:
                last += 1
                cell = self.ws.cell(1, last, n)
                style_header(cell)
                width = WIDTHS.get(self.ws.title, {}).get(n)
                if width:
                    self.ws.column_dimensions[get_column_letter(last)].width = width
                self.col[n] = last

    def rows(self):
        """Non-blank rows as dicts keyed by column name, plus _row."""
        out = []
        idx = {n: c - 1 for n, c in self.col.items()}
        for r, vals in enumerate(self.ws.iter_rows(min_row=2, values_only=True), start=2):
            rec = {n: (vals[i] if i < len(vals) else None) for n, i in idx.items()}
            if all(text(v) == "" for v in rec.values()):
                continue
            for n in self.columns:
                rec.setdefault(n, None)
            rec["_row"] = r
            out.append(rec)
        return out

    def last_row(self):
        for r in range(self.ws.max_row, 1, -1):
            if any(c.value not in (None, "") for c in self.ws[r]):
                return r
        return 1

    def put(self, row, name, val):
        cell = self.ws.cell(row, self.col[name])
        if name in LINK_COLS:
            cell.hyperlink = None
        val = clean_value(name, val)
        if val is None:
            cell.value = None
            return
        if isinstance(val, date):
            cell.value = val
            cell.number_format = DATE_FMT
            return
        if isinstance(val, str):
            # Untrusted text (email subjects, company names): never let it be a formula.
            cell.value = val
            cell.data_type = "s"
        else:
            cell.value = val
        if name in LINK_COLS and isinstance(val, str) and clickable(val):
            cell.hyperlink = str(val)
            cell.font = Font(color="0563C1", underline="single")
        elif name == "Notes":
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    def append(self, values):
        row = self.last_row() + 1
        for n, v in values.items():
            self.put(row, n, v)
        return row


class MemSheet:
    """A sheet held in memory (the snapshot view): the same rows/put/append."""

    def __init__(self, title, columns, records=()):
        self.title = title
        self.columns = list(columns)
        self.col = {n: i + 1 for i, n in enumerate(self.columns)}
        self.data = [dict(r) for r in records]

    def rows(self):
        out = []
        for i, rec in enumerate(self.data):
            if rec.get("_gone") or all(text(rec.get(n)) == "" for n in self.columns):
                continue
            r = dict(rec)
            for n in self.columns:
                r.setdefault(n, None)
            r["_row"] = i + 2
            out.append(r)
        return out

    def put(self, row, name, val):
        self.data[row - 2][name] = clean_value(name, val)

    def append(self, values):
        self.data.append({})
        row = len(self.data) + 1
        for n, v in values.items():
            self.put(row, n, v)
        return row

    def ensure_columns(self):
        pass


def style_header(cell):
    cell.font = Font(bold=True)
    cell.fill = PatternFill("solid", fgColor="D9E1F2")


def make_sheet(ws, columns):
    for i, n in enumerate(columns, 1):
        style_header(ws.cell(1, i, n))
        width = WIDTHS.get(ws.title, {}).get(n)
        if width:
            ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"


def list_validation(wb, ws, target, values, list_col, label, inline_limit=254):
    """A dropdown. Inline when the list fits Excel's 255-character limit, else a
    range on the Settings sheet (columns D onward)."""
    joined = ",".join(values)
    if len(joined) <= inline_limit and not any("," in v for v in values):
        formula = f'"{joined}"'
    else:
        st = wb["Settings"]
        st.cell(1, list_col, label).font = Font(bold=True)
        for i, v in enumerate(values, 2):
            st.cell(i, list_col, v)
        letter = get_column_letter(list_col)
        formula = f"Settings!${letter}$2:${letter}${len(values) + 1}"
    dv = DataValidation(type="list", formula1=formula, allow_blank=True, errorStyle="warning",
                        showErrorMessage=True, errorTitle=label,
                        error=f"Pick a {label.lower()} from the list.")
    dv.add(target)
    ws.add_data_validation(dv)
    return formula


# (column, values, Settings list column, label) for the Applications dropdowns.
DROPDOWNS = [("Status", STATUSES, 4, "Status"), ("Fit", FITS, 5, "Fit"), ("Tier", TIERS, 6, "Tier"),
             ("Account", ACCOUNT_STATES, 7, "Account")]


def add_validations(wb, apps_sheet):
    for col, values, list_col, label in DROPDOWNS:
        if col in apps_sheet.col:
            L = get_column_letter(apps_sheet.col[col])
            list_validation(wb, apps_sheet.ws, f"{L}2:{L}{MAX_ROWS}", values, list_col, label)


def build_workbook():
    wb = openpyxl.Workbook()
    apps = wb.active
    apps.title = "Applications"
    make_sheet(apps, APP_COLS)
    for _, title, cols in SHEETS[1:]:
        make_sheet(wb.create_sheet(title), cols)
    wb["Settings"].append(["Last inbox check", None])
    wb["Settings"]["B2"].number_format = DATE_FMT
    apps.auto_filter.ref = f"A1:{get_column_letter(len(APP_COLS))}1"
    for c in DATE_FIELDS:
        apps.column_dimensions[get_column_letter(APP_COLS.index(c) + 1)].number_format = DATE_FMT
    add_validations(wb, Sheet(apps, APP_COLS))
    add_summary(wb, get_column_letter(APP_COLS.index("Status") + 1))
    return wb


def add_summary(wb, status_L="G", index=None):
    sm = wb.create_sheet("Summary", index)
    for i, n in enumerate(("Status", "Count"), 1):
        style_header(sm.cell(1, i, n))
    for r, s in enumerate(STATUSES, 2):
        sm.cell(r, 1, s)
        sm.cell(r, 2, f"=COUNTIF(Applications!${status_L}:${status_L},A{r})")
    total = len(STATUSES) + 2
    sm.cell(total, 1, "Total").font = Font(bold=True)
    sm.cell(total, 2, f"=SUM(B2:B{total - 1})").font = Font(bold=True)
    sm.column_dimensions["A"].width = 30
    sm.column_dimensions["B"].width = 8


# ---------- the two stores ----------

class Book:
    """What every command reads and writes: the sheets, plus the P- ID map."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / FILENAME
        self.refs = {}
        self.dirty = False
        for attr, _, _ in SHEETS:
            setattr(self, attr, None)

    def sheet(self, attr):
        s = getattr(self, attr)
        if s is None:
            raise TrackerError(f"{self.path} has no {dict((a, t) for a, t, _ in SHEETS)[attr]} sheet")
        return s

    def get_setting(self, key):
        if not self.settings or "Key" not in self.settings.col:
            return None
        for r in self.settings.rows():
            if header_key(r["Key"]) == header_key(key):
                return r["Value"]
        return None

    def set_setting(self, key, value):
        for r in self.settings.rows():
            if header_key(r["Key"]) == header_key(key):
                self.settings.put(r["_row"], "Value", value)
                return
        self.settings.append({"Key": key, "Value": value})

    def app_rows(self):
        rows = self.apps.rows()
        for r in rows:
            r["_id"] = row_id(r["ID"])
        return rows

    def find(self, rid):
        for r in self.app_rows():
            if r["_id"] == rid:
                return r
        return None

    def resolve_id(self, raw):
        """An ID argument: a row number, or a P- ID (its real number once applied)."""
        s = text(raw)
        if re.fullmatch(r"\d+", s):
            return int(s)
        if PID_RE.fullmatch(s):
            return self.refs.get(s, s)
        raise TrackerError(f"ID must be a number or P-<8 hex>, got '{s}'")

    def last_archived_id(self):
        return to_int(self.get_setting("Last archived ID")) or 0


class Tracker(Book):
    """applications.xlsx through openpyxl."""

    def __init__(self, root):
        super().__init__(root)
        self.wb = None
        self.defer = False        # apply-events saves once, after the batch
        self.fresh = False        # built in memory, not on disk yet
        self.upgraded_backup = None
        self._archived = None

    def exists(self):
        return self.path.exists() or self.fresh

    def create(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.wb = build_workbook()
        self.bind()
        self.save()

    def open_or_new(self):
        """For apply-events: the workbook loaded for writing, or a new one in memory."""
        if self.path.exists():
            self.load(for_write=True)
        else:
            self.wb = build_workbook()
            self.bind()
            self.fresh = True

    def load(self, for_write=False):
        try:
            self.wb = openpyxl.load_workbook(self.path)
        except PermissionError:
            raise
        except Exception as e:
            raise TrackerError(f"cannot read {self.path}: {e}")
        if "Applications" not in self.wb.sheetnames:
            raise TrackerError(f"{self.path} has no Applications sheet")
        self.bind()
        if for_write:
            self.upgrade()

    def bind(self):
        for attr, title, cols in SHEETS:
            setattr(self, attr, Sheet(self.wb[title], cols) if title in self.wb.sheetnames else None)

    def upgrade_needed(self):
        if any(title not in self.wb.sheetnames for _, title, _ in SHEETS):
            return True
        if any(getattr(self, attr).missing() for attr, _, _ in SHEETS):
            return True
        return "Summary" not in self.wb.sheetnames or summary_statuses(self.wb) != STATUSES

    def upgrade(self):
        """Bring an older workbook up to v1.2 in place: missing sheets go before
        Summary, missing columns are appended, and the Summary and dropdowns are
        rebuilt for the new statuses. Backs the file up first."""
        if not self.upgrade_needed():
            return False
        self.upgraded_backup = self.backup_copy("before_upgrade")
        for attr, title, cols in SHEETS:
            if title not in self.wb.sheetnames:
                at = self.wb.sheetnames.index("Summary") if "Summary" in self.wb.sheetnames else None
                make_sheet(self.wb.create_sheet(title, at), cols)
        self.bind()
        for attr, _, _ in SHEETS:
            getattr(self, attr).ensure_columns()
        if "Summary" not in self.wb.sheetnames or summary_statuses(self.wb) != STATUSES:
            at = None
            if "Summary" in self.wb.sheetnames:
                at = self.wb.sheetnames.index("Summary")
                del self.wb["Summary"]
            add_summary(self.wb, get_column_letter(self.apps.col["Status"]), at)
            self.refresh_validations()
        return True

    def refresh_validations(self):
        """Replace the Applications dropdowns (the status list grew; rows go to MAX_ROWS)."""
        ws = self.apps.ws
        starts = {f"{get_column_letter(self.apps.col[c])}2" for c, _, _, _ in DROPDOWNS if c in self.apps.col}
        for dv in list(ws.data_validations.dataValidation):
            if str(dv.sqref).split(":")[0] in starts:
                ws.data_validations.dataValidation.remove(dv)
        add_validations(self.wb, self.apps)

    def backup(self):
        if not self.path.exists():
            return
        folder = self.root / "backups"
        target = folder / f"applications_{today(self.root).isoformat()}.xlsx"
        if target.exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, target)

    def backup_copy(self, tag):
        """backups/applications_<date>_<tag>.xlsx, before an upgrade or an archive."""
        if not self.path.exists():
            return None
        folder = self.root / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"applications_{today(self.root).isoformat()}_{tag}.xlsx"
        n = 2
        while target.exists():
            target = folder / f"applications_{today(self.root).isoformat()}_{tag}_{n}.xlsx"
            n += 1
        shutil.copy2(self.path, target)
        return target

    def save(self):
        self.dirty = True
        if not self.defer:
            self.flush()

    def flush(self):
        ws = self.apps.ws
        if ws.auto_filter.ref and self.apps.col:
            last_col = get_column_letter(max(self.apps.col.values()))
            ws.auto_filter.ref = f"A1:{last_col}{max(self.apps.last_row(), 2)}"
        self.root.mkdir(parents=True, exist_ok=True)
        write_book(self.wb, self.path, before_replace=self.backup)
        self.fresh = False

    def new_app_id(self, pid=None):
        ids = [r["_id"] for r in self.app_rows() if isinstance(r["_id"], int)]
        new_id = max(ids + [self.last_archived_id()], default=0) + 1
        if pid:
            self.refs[pid] = new_id
        return new_id

    def archived_rows(self):
        if self._archived is None:
            self._archived = []
            for p in sorted(self.root.glob(ARCHIVE_GLOB)):
                try:
                    wb = openpyxl.load_workbook(p)
                except Exception:
                    continue
                if "Applications" in wb.sheetnames:
                    self._archived += Sheet(wb["Applications"], APP_COLS).rows()
        return self._archived

    def archive_rows(self, rows):
        """Copy rows into applications_archive_<year>.xlsx (year of Date), then
        take them out of this workbook. Every column, the user's too, goes along."""
        self.backup_copy("before_archive")
        ws = self.apps.ws
        heads = [text(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
        heads = [h for h in heads if h]
        full = {r["_row"]: r for r in Sheet(ws, heads).rows()}
        counts = Counter()
        for year, group in group_by_year(self.root, rows):
            path = self.root / f"applications_archive_{year}.xlsx"
            wb = openpyxl.load_workbook(path) if path.exists() else archive_workbook(heads)
            sheet = Sheet(wb["Applications"], heads)
            sheet.ensure_columns()
            have = {to_int(r["ID"]) for r in sheet.rows()}
            for r in group:
                if to_int(r["ID"]) not in have:
                    sheet.append({h: full[r["_row"]].get(h) for h in heads})
                counts[year] += 1
            write_book(wb, path)
        for r in sorted(rows, key=lambda r: r["_row"], reverse=True):
            ws.delete_rows(r["_row"])
        self._archived = None
        return counts


class View(Book):
    """The tracker as the runner's snapshot plus pending events (mode L)."""

    def __init__(self, root, snap):
        super().__init__(root)
        snap = snap or {}
        self.found = bool(snap)
        self.apps = MemSheet("Applications", APP_COLS, snap.get("applications", []))
        self.accounts = MemSheet("Accounts", ACC_COLS, snap.get("accounts", []))
        self.log = MemSheet("Email log", LOG_COLS, [{"Message ID": m} for m in snap.get("email_log_ids", [])])
        self.settings = MemSheet("Settings", SET_COLS,
                                 [{"Key": k, "Value": v} for k, v in (snap.get("settings") or {}).items()])
        self.referrals = MemSheet("Referrals", REF_COLS, snap.get("referrals", []))
        self.employers = MemSheet("Employers", EMP_COLS, snap.get("employers", []))
        self.activities = MemSheet("Activities", ACT_COLS, snap.get("activities", []))
        self.archived = [dict(r) for r in snap.get("archived", [])]
        self.refs = dict(snap.get("refs") or {})

    def exists(self):
        return self.found

    def create(self):
        self.found = True
        self.dirty = True

    def save(self):
        self.found = True
        self.dirty = True

    def new_app_id(self, pid=None):
        return pid or f"P-{secrets.token_hex(4)}"

    def archived_rows(self):
        return [dict(r) for r in self.archived]

    def archive_rows(self, rows):
        counts = Counter()
        for year, group in group_by_year(self.root, rows):
            for r in group:
                self.archived.append({k: v for k, v in r.items() if not k.startswith("_")})
                self.apps.data[r["_row"] - 2]["_gone"] = True
                counts[year] += 1
        return counts


def write_book(wb, path, before_replace=None):
    """Save to a temp file beside path, then rename over it."""
    fd, tmp = tempfile.mkstemp(prefix="." + path.stem + "_", suffix=".xlsx", dir=str(path.parent))
    os.close(fd)
    try:
        wb.save(tmp)
        if before_replace:
            before_replace()
        # A scanner or the indexer may hold the file for a moment after a write; Excel
        # holding it open still ends in PermissionError (LOCKED) after the short waits.
        jobfs._rename(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def archive_workbook(heads):
    wb = openpyxl.Workbook()
    wb.active.title = "Applications"
    make_sheet(wb.active, heads)
    return wb


def group_by_year(root, rows):
    groups = {}
    for r in rows:
        d = to_date(r["Date"]) or to_date(r["Applied"]) or today(root)
        groups.setdefault(d.year, []).append(r)
    return sorted(groups.items())


def summary_statuses(wb):
    sm = wb["Summary"]
    out = []
    for r in range(2, sm.max_row + 1):
        v = text(sm.cell(r, 1).value)
        if v == "Total":
            break
        out.append(v)
    return out


# ---------- choosing the store ----------

def need_openpyxl():
    if openpyxl is None:
        raise MissingPackage()


def runner_mode(root):
    """[runner] mode from config/settings.toml, or None when there is no file."""
    if not jobfs.Folder(root).settings_toml.exists():
        return None
    return str(jobfs.settings(root).get("runner", {}).get("mode", "")).upper()


def has_pending_tracker_events(root):
    return any(p.name.endswith("_tracker.json") for p in events.pending_files(root))


def resolve_transport(a, write):
    """events or direct for this command (see the module docstring)."""
    t = getattr(a, "transport", "auto") or "auto"
    if t != "auto":
        return t
    mode = runner_mode(a.root)
    if mode in ("L", "S"):
        return "events"
    if mode == "M":
        return "direct"
    if jobfs.Folder(a.root).tracker_json.exists():
        return "events"
    return "events" if not write and has_pending_tracker_events(a.root) else "direct"


def read_snapshot(root):
    """runner/out/tracker.json verified (retry, then .prev), or None."""
    path = jobfs.Folder(root).tracker_json
    if not path.exists() and not jobfs.prev_path(path).exists():
        return None
    try:
        return jobfs.read_json_verified(path)
    except jobfs.NotReady:
        return None


def load_view(root):
    """The snapshot (or, with none yet, the xlsx read once) plus every tracker
    event it does not hold yet, replayed in name order with this module's code."""
    snap = read_snapshot(root)
    if snap is None and jobfs.Folder(root).tracker.exists() and openpyxl is not None:
        tr = Tracker(root)
        tr.load()
        snap = snapshot_dict(tr, root, fingerprints=False)
    view = View(root, snap)
    applied = set((snap or {}).get("applied_ids", []))
    for ev in events.recent(root):
        if ev["type"] == "tracker" and ev["id"] not in applied:
            run_event(view, ev, root)
    view.dirty = False
    return view


def open_tracker(a, create=False, for_write=False, quiet=False):
    """The store this command works on; None (after NO TRACKER unless quiet)
    when there is none and create is False."""
    if getattr(a, "store", None) is not None:
        return a.store
    if getattr(a, "view", "xlsx") == "overlay":
        tr = load_view(a.root)
        if not tr.exists():
            if not create:
                if not quiet:
                    print("NO TRACKER")
                return None
            tr.create()
    else:
        need_openpyxl()
        tr = Tracker(a.root)
        if not tr.exists():
            if not create:
                if not quiet:
                    print("NO TRACKER")
                return None
            tr.create()
            tr.load(for_write=True)
        else:
            tr.load(for_write=for_write)
    a.store = tr
    return tr


# ---------- job folders and the queue ----------

def read_posting(root, folder):
    if not folder:
        return ""
    p = Path(root) / folder / "posting.md"
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        return ""


def folder_fingerprint(folder):
    """(req_ids, simhash) of a job folder: posting.json when it holds them
    (the runner's Posting record), else computed from posting.md."""
    folder = Path(folder)
    try:
        data = jobfs.read_json(folder / "posting.json", jobfs.MAX_FILE)
        if isinstance(data, dict) and re.fullmatch(r"[0-9a-f]{16}", str(data.get("simhash", ""))):
            return {str(x).upper() for x in data.get("req_ids") or []}, data["simhash"]
    except (OSError, ValueError):
        pass
    try:
        md = (folder / "posting.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), ""
    return (jobkey.req_ids(md), jobkey.simhash64(md)) if md.strip() else (set(), "")


def row_fingerprint(root, row):
    """(req_ids, simhash) for a tracker row: the snapshot's values when present,
    else read from its job folder. The Req ID column always counts."""
    if "simhash" in row:
        reqs, sh = {str(x).upper() for x in row.get("req_ids") or []}, row.get("simhash") or ""
    else:
        folder = text(row.get("Folder"))
        reqs, sh = folder_fingerprint(Path(root) / folder) if folder else (set(), "")
    if text(row.get("Req ID")):
        reqs = reqs | {text(row["Req ID"]).upper()}
    return reqs, sh


def referral_urls(store):
    """job_key -> the referral link stored for that job (`ref <n> <url>`: the
    Referrals sheet's or the row's Referral URL), only links
    netfetch.web_refusal passes (R1); the Applications row wins over the
    Referrals sheet."""
    out = {}
    if store is None:
        return out
    import netfetch  # stdlib and jobfs only
    pairs = []
    if getattr(store, "referrals", None) is not None:
        pairs += [(r.get("Job key"), r.get("Referral URL")) for r in store.referrals.rows()]
    if getattr(store, "apps", None) is not None:
        pairs += [(r.get("Job key"), r.get("Referral URL")) for r in store.app_rows()]
    for key, url in pairs:
        key, url = text(key), text(url)
        if key and url and netfetch.web_refusal(url) is None:
            out[jobkey.safe_key(key)] = url  # R2: an earlier version's key finds today's item
    return out


def queue_items(root, day):
    """Items of queue/<day>/queue.json and its addenda, plus jobs from links
    whose added event is not in addendum_user.json yet."""
    qd = jobfs.Folder(root).queue_dir(day)
    if not qd.exists():
        return []
    items = []
    for p in [qd / "queue.json"] + sorted(x for x in qd.glob("addendum_*.json") if not x.name.endswith(".prev.json")):
        if not p.exists():
            continue
        try:
            data = jobfs.read_json_verified(p, retries=0)
        except jobfs.NotReady:
            continue
        items += [i for i in data.get("items", []) if isinstance(i, dict)]
    keys = {i.get("job_key") for i in items}
    return items + [i for i in events.user_items(root, day) if i["job_key"] not in keys]


# ---------- commands ----------

def cmd_init(a):
    tr = open_tracker(a, quiet=True)
    path = Path(a.root) / FILENAME
    if tr is not None and tr.exists():
        print(f"EXISTS {path}")
        return 0
    if tr is None:
        open_tracker(a, create=True)
    else:
        tr.create()  # a view with nothing in it yet
    print(f"CREATED {path}")
    return 0


def match_row(a, row, ctx):
    """(rank, why) of one tracked row against the new posting, or (None, '')."""
    folder = text(row["Folder"])
    root = Path(a.root)
    row_folder = os.path.normcase(str((root / folder).resolve())) if folder else None
    if ctx["folder"] and row_folder == ctx["folder"]:
        return None, ""
    key = ctx["key"]
    # A v1.1 row stored a url: fallback key for hosts v1.2 now keys by ID
    # (bamboohr, workable, adp, ...), so its URL is keyed again as well.
    # R2: both sides through jobkey.safe_key, so a key an earlier version stored still matches.
    row_keys = {jobkey.safe_key(text(row["Job key"])),
                jobkey.job_key(text(row["URL"])) if text(row["URL"]) else ""} - {"", None}
    same_company = bool(ctx["company"]) and jobkey.norm_company(text(row["Company"])) == ctx["company"]
    if key and jobkey.safe_key(key) in row_keys:
        return 0, f"job key {key}"
    if same_company:
        # The v1.1 rule: read this company's posting text.
        old_text = read_posting(root, folder)
        reqs = jobkey.req_ids(old_text) | ({text(row["Req ID"]).upper()} if text(row.get("Req ID")) else set())
        if ctx["reqs"] & reqs:
            return 1, f"same requisition {', '.join(sorted(ctx['reqs'] & reqs))}"
        if (sim := jobkey.jaccard(ctx["shingles"], jobkey.shingles(old_text))) >= 0.5:
            return 1, f"posting text {int(sim * 100)}% the same"
        if (t := SequenceMatcher(None, ctx["title"], jobkey.norm_title(text(row["Role"]))).ratio()) >= 0.85:
            return 2, f"title {int(t * 100)}% alike"
        started = to_date(row["Date"])
        if started and (ctx["now"] - started).days <= 90:
            return 3, f"other role, {(ctx['now'] - started).days} days ago"
        return None, ""
    if ctx["simhash"]:
        _, sh = row_fingerprint(root, row)
        if sh and sh != "0" * 16 and (d := jobkey.hamming(ctx["simhash"], sh)) <= SIMHASH_BITS:
            return 1, f"posting text near-identical (SimHash distance {d})"
    return None, ""


def cmd_check(a):
    # R2: the key in today's safe form (A-Z a-z 0-9 . _ : / -), so the printed
    # line can go inline; a key an earlier version stored maps to the same form.
    key = jobkey.safe_key(a.job_key) or jobkey.job_key(a.url)
    print(f"JOB KEY {key or '(none: no URL)'}")
    now = today(a.root)
    if key:
        for item in queue_items(a.root, now):
            if jobkey.same_key(item.get("job_key"), key):
                print(f"IN QUEUE {now.isoformat()} #{item.get('n')}")
    tr = open_tracker(a, quiet=True)
    if tr is None:
        print("NO MATCH (no tracker yet)")
        return 0
    new_text = Path(a.posting).read_text(encoding="utf-8", errors="replace") if a.posting else ""
    new_sh = folder_fingerprint(Path(a.posting).resolve().parent)[1] if a.posting and new_text.strip() else ""
    ctx = {"key": key, "now": now, "reqs": jobkey.req_ids(new_text), "shingles": jobkey.shingles(new_text),
           "company": jobkey.norm_company(a.company), "title": jobkey.norm_title(a.title),
           "simhash": new_sh if new_sh != "0" * 16 else "",
           "folder": os.path.normcase(str(Path(a.posting).resolve().parent)) if a.posting else None}
    matches = []
    for row in tr.app_rows() + tr.archived_rows():
        rank, why = match_row(a, row, ctx)
        if rank is not None:
            matches.append((rank, row, why))
    if not matches:
        print("NO MATCH")
    labels = ["SAME POSTING", "LIKELY REPOST", "SIMILAR ROLE", "SAME COMPANY"]
    for rank, row, why in sorted(matches, key=lambda m: m[0]):
        cols = " | ".join(text(row[k]) for k in ("Date", "Company", "Role", "Status", "Folder"))
        print(f"{labels[rank]:<13} ID {text(row['ID'])} | {cols} ({why})")
    return 0


def add_values(a):
    """Checked column values from add's options (everything but ID and dates of today)."""
    status = pick("status", a.status, STATUSES)
    vals = {"Fit": opt_pick("fit", a.fit, FITS), "Status": status,
            "Source": pick("source", a.source, SOURCES), "Applied": opt_date("applied", a.applied),
            "Tier": opt_pick("tier", a.tier, TIERS), "Account": opt_pick("account", a.account, ACCOUNT_STATES),
            "Posted": opt_date("posted", a.posted), "Queue date": opt_date("queue-date", a.queue_date),
            "Confirmed": opt_date("confirmed", a.confirmed)}
    if not a.company.strip() or not a.role.strip():
        raise TrackerError("company and role cannot be empty")
    vals.update({"Company": a.company.strip(), "Role": a.role.strip(), "Location": a.location,
                 "Notes": a.notes, "Folder": check_folder(a.root, a.folder),
                 "URL": check_link("url", a.url, board_ok=True),
                 "Job key": a.job_key if a.job_key is not None else jobkey.job_key(a.url),
                 "Family": a.family, "Variant": a.variant, "ATS": a.ats, "Found via": a.found_via,
                 "Req ID": a.req_id, "Referral": a.referral,
                 "Referral URL": check_link("referral-url", a.referral_url),
                 "Proof": a.proof, "Block reason": a.block_reason,
                 "Employer site": check_link("employer-site", a.employer_site)})
    return vals


def same_job_rows(tr, key):
    """Applications rows with this job key, in sheet order."""
    return [r for r in tr.app_rows() if key and text(r["Job key"]) == key] if key else []


def sent(row):
    """True when a row records a sent application (Applied and beyond, or
    Closed with an Applied date)."""
    st = canon_status(row["Status"])
    return st in RESPONDED | {"Applied", "No response", "Withdrawn"} or (st == "Closed" and bool(to_date(row["Applied"])))


def reuse_row(a, tr, row, vals):
    """add for a job that already has a row it never sent: that row is updated
    (one job, one row), the status only forward unless it was dropped."""
    now = today(a.root)
    old = canon_status(row["Status"])
    new = vals["Status"]
    if not (old in ("Not pursued", "Closed") or (rank_of(new) or 0) >= (rank_of(old) or 0)):
        new = old
    changes = {c: v for c, v in vals.items() if c not in ("ID", "Date") and v not in (None, "")}
    changes.update({"Status": new, "Last update": now})
    applied = to_date(changes.get("Applied") or row["Applied"])
    if applied and RANK.get(new, 0) <= 3 and not text(row["Follow-up"]) and \
            wants_followup(a.root, changes.get("Tier") or row["Tier"], changes.get("Referral") or row["Referral"]):
        changes["Follow-up"] = applied + timedelta(days=followup_rule(a.root)[1])
    for col, v in changes.items():
        if col in APP_COLS and text(row.get(col)) != text(v):
            tr.apps.put(row["_row"], col, v)
    tr.save()
    rid = text(row["ID"])
    print(f"ADDED {rid} (" + ("pending, " if PID_RE.fullmatch(rid) else "") + "existing row)")
    return 0


def cmd_add(a):
    vals = add_values(a)
    tr = open_tracker(a, create=True, for_write=True)
    rows = [] if getattr(a, "force", False) else same_job_rows(tr, text(vals["Job key"]))
    done = [r for r in rows if sent(r)]
    if done:
        r = done[-1]
        print(f"REFUSED {text(r['ID'])} already {canon_status(r['Status'])} for job key {text(r['Job key'])}: "
              "a second row would count a second application (add --force for a new application)")
        return 5
    if rows:
        return reuse_row(a, tr, rows[-1], vals)
    new_id = tr.new_app_id(getattr(a, "pid", None))
    pending = isinstance(new_id, str)
    if pending:
        a.pid = new_id
    now = today(a.root)
    applied = vals["Applied"]
    _, days = followup_rule(a.root)
    follow = None
    if applied and RANK[vals["Status"]] <= 3 and wants_followup(a.root, vals["Tier"], vals["Referral"]):
        follow = applied + timedelta(days=days)
    vals.update({"ID": new_id, "Date": now, "Follow-up": follow, "Last update": now})
    tr.apps.append({c: vals.get(c) for c in APP_COLS if c in vals})
    tr.save()
    print(f"ADDED {new_id}" + (" (pending)" if pending else ""))
    return 0


def resolve_field(name):
    k = field_key(name)
    for col in APP_COLS:
        if field_key(col) == k:
            return col
    raise TrackerError(f"unknown field '{name}'; fields are: {', '.join(APP_COLS)}")


CHOICES = {"Status": STATUSES, "Fit": FITS, "Source": SOURCES, "Tier": TIERS, "Account": ACCOUNT_STATES}


def parse_assign(root, item):
    if "=" not in item:
        raise TrackerError(f"'{item}' is not field=value")
    name, value = item.split("=", 1)
    append = name.endswith("+")
    col = resolve_field(name[:-1] if append else name)
    if col == "ID":
        raise TrackerError("ID cannot be changed")
    if append and col != "Notes":
        raise TrackerError("only notes+= appends")
    if col in DATE_FIELDS and value.strip():
        value = parse_date_arg(col, value)
    elif col in CHOICES and value.strip():
        value = pick(col.lower(), value, CHOICES[col])
    elif col == "Folder":
        value = check_folder(root, value)
    elif col in CHECKED_LINKS:
        value = check_link(CHECKED_LINKS[col], value, board_ok=col == "URL")
    elif isinstance(value, str):
        value = value.strip() if col != "Notes" else value
    return col, append, value


def check_id_arg(raw):
    if not (re.fullmatch(r"\d+", raw.strip()) or PID_RE.fullmatch(raw.strip())):
        raise TrackerError(f"ID must be a number or P-<8 hex>, got '{raw}'")
    return raw.strip()


def cmd_update(a):
    a.id = check_id_arg(a.id)
    assigns = [parse_assign(a.root, item) for item in a.fields]
    tr = open_tracker(a, for_write=True, quiet=True)
    if tr is None:
        raise TrackerError(f"no row with ID {a.id} (no tracker yet)")
    row = tr.find(tr.resolve_id(a.id))
    if row is None:
        raise TrackerError(f"no row with ID {a.id}")

    now = today(a.root)
    old = {c: row[c] for c in APP_COLS}
    new = dict(old)
    order, explicit = [], set()
    notes_added = None
    for col, append, value in assigns:
        explicit.add(col)
        if col not in order:
            order.append(col)
        if append:
            line = f"{now.isoformat()}: {value.strip()}"
            new["Notes"] = f"{text(new['Notes'])}\n{line}" if text(new["Notes"]) else line
            notes_added = line
        elif value == "":
            new[col] = None
        else:
            new[col] = value

    old_status, new_status = canon_status(old["Status"]), canon_status(new["Status"])
    if "Status" in explicit and old_status != new_status and not a.force:
        ro, rn = rank_of(old_status), rank_of(new_status)
        if ro is not None and rn is not None and rn < ro:
            print(f"REFUSED {a.id} backward move {old_status} -> {new_status} "
                  "(use --force to correct a mistake)")
            return 5

    # Applied without a date means today, so stats and follow-ups count the row.
    if ("Status" in explicit and new_status == "Applied" and "Applied" not in explicit
            and not to_date(new["Applied"])):
        new["Applied"] = now
        explicit.add("Applied")
        order.append("Applied")

    applied = to_date(new["Applied"])
    if "Follow-up" not in explicit:
        if (applied and "Applied" in explicit and text(old["Follow-up"]) == ""
                and (rank_of(new_status) or 0) <= 3 and wants_followup(a.root, new["Tier"], new["Referral"])):
            new["Follow-up"] = applied + timedelta(days=followup_rule(a.root)[1])
        if "Status" in explicit and new_status in CLEAR_FOLLOWUP and old_status != new_status:
            new["Follow-up"] = None
    # A finished job has no next step: a rejected interview must not show as upcoming.
    if ("Status" in explicit and "Next step" not in explicit and old_status != new_status
            and rank_of(new_status) == 7 and text(old["Next step"])):
        new["Next step"] = None
        order.append("Next step")
    if "Last update" not in explicit:
        new["Last update"] = now
    if "Follow-up" not in order and text(old["Follow-up"]) != text(new["Follow-up"]):
        order.append("Follow-up")
    if "Last update" not in order and text(old["Last update"]) != text(new["Last update"]):
        order.append("Last update")

    parts = []
    for col in order:
        if col == "Notes" and notes_added:
            parts.append(f"Notes: +{short(notes_added)}")
        elif text(old[col]) != text(new[col]):
            parts.append(f"{col}: {short(old[col])} -> {short(new[col])}")
    for col in order:
        if text(old[col]) != text(new[col]):
            tr.apps.put(row["_row"], col, new[col])
    tr.save()
    print(f"UPDATED {a.id} " + ("; ".join(parts) if parts else "(no change)"))
    return 0


def table_line(row, cols):
    return " | ".join(text(row[c]).replace("|", "/").replace("\n", " ") for c in cols)


def cmd_show(a):
    if a.id is not None:
        a.id = check_id_arg(a.id)
    tr = open_tracker(a)
    if tr is None:
        return 0
    want_id = tr.resolve_id(a.id) if a.id is not None else None
    want_status = {pick("status", s, STATUSES) for s in a.status} if a.status else None
    want_company = jobkey.norm_company(a.company) if a.company else None
    out = []
    for r in tr.app_rows():
        st = canon_status(r["Status"])
        if want_id is not None and r["_id"] != want_id:
            continue
        if want_status is not None and st not in want_status:
            continue
        if a.active and st in INACTIVE:
            continue
        if want_company is not None and jobkey.norm_company(text(r["Company"])) != want_company:
            continue
        out.append(r)
    if not out:
        print("NONE")
        return 0
    print(" | ".join(SHOW_COLS))
    for r in out:
        print(table_line(r, SHOW_COLS))
    return 0


def cmd_due(a):
    tr = open_tracker(a)
    if tr is None:
        return 0
    now = today(a.root)
    rows = tr.app_rows()
    lines = []
    for r in rows:
        if canon_status(r["Status"]) in ("Applied", "No response"):
            fu = to_date(r["Follow-up"])
            if fu and fu <= now:
                lines.append((0, f"FOLLOW-UP ID {text(r['ID'])} | {text(r['Company'])} | {text(r['Role'])}"
                                 f" | applied {text(r['Applied'])} | due {fu.isoformat()}"))
    for kind, label, status_set, days in ((1, "UNSUBMITTED", {"Filled - awaiting submit"}, 2),
                                          (2, "STALE", {"Started", "Resume ready"}, 14)):
        for r in rows:
            if canon_status(r["Status"]) in status_set:
                lu = to_date(r["Last update"]) or to_date(r["Date"])
                if lu and (now - lu).days > days:
                    lines.append((kind, f"{label} ID {text(r['ID'])} | {text(r['Company'])} | {text(r['Role'])}"
                                        f" | {canon_status(r['Status'])} | last update {lu.isoformat()}"))
    for r in rows:
        step = text(r["Next step"])
        for m in DATE_RE.finditer(step):
            d = to_date(m.group(0))
            if d and now <= d <= now + timedelta(days=7):
                lines.append((3, f"UPCOMING ID {text(r['ID'])} | {text(r['Company'])} | {text(r['Role'])}"
                                 f" | {step}"))
                break
    if not lines:
        print("NONE")
    for _, line in sorted(lines, key=lambda x: x[0]):
        print(line)
    return 0


def normalize_site(site):
    s = site.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc or s
    return s.removeprefix("www.")


def account_line(r):
    line = (f"ACCOUNT {text(r['Site'])} | {text(r['Login email'])} | {text(r['Created'])} | "
            f"{text(r['Notes']).replace(chr(10), ' ')}")
    extra = [text(r.get(c)) for c in ("Employer", "ATS", "Last used")]
    return line + (" | " + " | ".join(extra) if any(extra) else "")


def reuse_email(rows):
    """The login email the user uses most (latest use breaks a tie)."""
    counts = Counter(text(r["Login email"]).lower() for r in rows if text(r["Login email"]))
    if not counts:
        return ""
    latest = {}
    for r in rows:
        d = to_date(r.get("Last used")) or to_date(r["Created"]) or date.min
        e = text(r["Login email"]).lower()
        latest[e] = max(latest.get(e, date.min), d)
    return max(counts, key=lambda e: (counts[e], latest.get(e, date.min)))


def account_lookup(a, site):
    tr = open_tracker(a)
    if tr is None:
        return 0
    rows = tr.accounts.rows() if tr.accounts else []
    hits = [r for r in rows if site in text(r["Site"]).lower()]
    if not hits:
        print("NONE")
    for r in hits:
        print(account_line(r))
    if a.reuse and not hits and reuse_email(rows):
        print(f"REUSE {reuse_email(rows)}")
    return 0


def cmd_account(a):
    for v in (a.email, a.notes, a.employer, a.ats):
        if v and PASSWORD_RE.search(v):
            raise TrackerError("passwords are never stored")
    site = normalize_site(a.site)
    if not site:
        raise TrackerError("--site cannot be empty")
    if not a.email:
        return account_lookup(a, site)
    tr = open_tracker(a, create=True, for_write=True)
    now = today(a.root)
    acc = tr.sheet("accounts")
    for r in acc.rows():
        if text(r["Site"]).lower() == site:
            acc.put(r["_row"], "Login email", a.email.strip())
            for col, v in (("Notes", a.notes), ("Employer", a.employer), ("ATS", a.ats)):
                if v is not None:
                    acc.put(r["_row"], col, v.strip() if col != "Notes" else v)
            if a.used:
                acc.put(r["_row"], "Last used", now)
            tr.save()
            print(f"ACCOUNT UPDATED {site}")
            return 0
    acc.append({"Site": site, "Login email": a.email.strip(), "Created": now, "Notes": a.notes,
                "Employer": (a.employer or "").strip(), "ATS": (a.ats or "").strip(),
                "Last used": now if a.used else None})
    tr.save()
    print(f"ACCOUNT ADDED {site}")
    return 0


def logged_ids(tr):
    if not tr.log or "Message ID" not in tr.log.col:
        return set()
    return {text(r["Message ID"]) for r in tr.log.rows() if text(r["Message ID"])}


def cmd_inbox_plan(a):
    tr = open_tracker(a)
    if tr is None:
        return 0
    now = today(a.root)
    rows = tr.app_rows()
    last = to_date(tr.get_setting("Last inbox check"))
    if last:
        since = last - timedelta(days=2)
    else:
        applied = [to_date(r["Applied"]) for r in rows if rank_of(r["Status"]) != 7]
        applied = [d for d in applied if d]
        since = min(applied) if applied else now - timedelta(days=30)
    print(f"SINCE {since.isoformat()}")
    for r in rows:
        rk = rank_of(r["Status"])
        if rk is not None and 2 <= rk <= 6:
            key = text(r["Job key"]) or jobkey.job_key(text(r["URL"]))
            print(f"ACTIVE ID {text(r['ID'])} | {text(r['Company'])} | {text(r['Role'])} | "
                  f"{canon_status(r['Status'])} | applied {text(r['Applied']) or '-'} | {key} | "
                  f"{url_host(text(r['URL']))}")
    print(f"LOGGED {len(logged_ids(tr))} emails")
    return 0


def cmd_seen(a):
    tr = open_tracker(a)
    if tr is None:
        return 0
    known = logged_ids(tr)
    for m in a.ids:
        print(f"{'SEEN' if m.strip() in known else 'NEW'} {m}")
    return 0


def cmd_log_email(a):
    mid = a.message_id.strip()
    if not mid:
        raise TrackerError("--message-id cannot be empty")
    kind = pick("kind", a.kind, KINDS)
    if a.job_id is not None and to_int(a.job_id) is None and not PID_RE.fullmatch(a.job_id.strip()):
        raise TrackerError(f"--job-id must be a number, got '{a.job_id}'")
    tr = open_tracker(a, create=True, for_write=True)
    if mid in logged_ids(tr):
        print(f"DUPLICATE {mid}")
        return 0
    email_date = to_date(a.email_date) if DATE_RE.match(a.email_date.strip()) else None
    job_id = None
    if a.job_id is not None:
        job_id = tr.resolve_id(a.job_id.strip()) if PID_RE.fullmatch(a.job_id.strip()) else to_int(a.job_id)
    tr.sheet("log").append({"Checked": today(a.root), "Message ID": mid, "Email date": email_date or a.email_date,
                            "From": a.sender, "Subject": a.subject, "Job ID": job_id,
                            "Classified as": a.cls, "Quote": a.quote, "Change": a.change, "Kind": kind})
    tr.save()
    print(f"LOGGED {mid}")
    return 0


def cmd_inbox_done(a):
    tr = open_tracker(a, create=True, for_write=True)
    now = today(a.root)
    tr.set_setting("Last inbox check", now)
    tr.save()
    print(f"INBOX CHECKED {now.isoformat()}")
    return 0


def name_only(label, value):
    """Contact names hold a name and nothing else (no email, link or phone)."""
    v = (value or "").strip()
    if not v:
        raise TrackerError(f"{label} cannot be empty")
    if "@" in v or "://" in v or "www." in v.lower() or re.search(r"\d{3}", v):
        raise TrackerError(f"{label} holds a name only")
    return v


def changes(sheet, row, values):
    """Put the changed values; returns 'Col: old -> new' parts."""
    parts = []
    for col, v in values.items():
        if text(row[col]) != text(v):
            parts.append(f"{col}: {short(row[col])} -> {short(v)}")
            sheet.put(row["_row"], col, v)
    return parts


def cmd_referral_add(a):
    contact = name_only("--contact", a.contact)
    if not a.company.strip():
        raise TrackerError("--company cannot be empty")
    vals = {"Channel": opt_pick("channel", a.channel, REF_CHANNELS),
            "Status": opt_pick("status", a.status, REF_STATUSES),
            "Nudge on": opt_date("nudge-on", a.nudge_on),
            "Referral URL": check_link("referral-url", a.referral_url or ""),
            "Job key": (a.job_key or "").strip(), "Notes": a.notes}
    when = opt_date("date", a.date) or today(a.root)
    if vals["Status"] == "Sent" and not vals["Nudge on"]:
        vals["Nudge on"] = when + timedelta(days=int(jobfs.settings(a.root)["referrals"]["nudge_after_days"]))
    tr = open_tracker(a, create=True, for_write=True)
    ref = tr.sheet("referrals")
    company_n = jobkey.norm_company(a.company)
    for r in ref.rows():
        if (text(r["Contact"]).lower() == contact.lower() and jobkey.norm_company(text(r["Company"])) == company_n
                and text(r["Job key"]) == vals["Job key"]):
            parts = changes(ref, r, {k: v for k, v in vals.items() if v not in (None, "")})
            tr.save()
            print(f"REFERRAL UPDATED {contact} | {a.company.strip()} | " + ("; ".join(parts) or "(no change)"))
            return 0
    ids = [to_int(r["ID"]) for r in ref.rows()]
    vals["Status"] = vals["Status"] or "Drafted"
    ref.append(dict(vals, **{"ID": max([i for i in ids if i] + [0]) + 1, "Date": when, "Contact": contact,
                             "Company": a.company.strip()}))
    tr.save()
    print(f"REFERRAL ADDED {contact} | {a.company.strip()} | {vals['Status']}")
    return 0


EMPLOYER_OPTS = [("Parent", "parent"), ("Main website", "main_website"), ("Street", "street"),
                 ("City", "city"), ("State", "state"), ("ZIP", "zip"), ("Phone", "phone"),
                 ("Email", "email"), ("Source", "source"), ("Confirmed on", "confirmed_on"),
                 ("Notes", "notes")]


def cmd_employer_set(a):
    name = (a.employer or "").strip()
    norm = jobkey.norm_company(name)
    if not norm:
        raise TrackerError("--employer cannot be empty")
    vals = {}
    for col, attr in EMPLOYER_OPTS:
        v = getattr(a, attr)
        if v is not None:
            vals[col] = opt_date("confirmed-on", v) if col == "Confirmed on" else v.strip()
    tr = open_tracker(a, create=True, for_write=True)
    emp = tr.sheet("employers")
    for r in emp.rows():
        if text(r["Normalized"]) == norm or jobkey.norm_company(text(r["Employer"])) == norm:
            parts = changes(emp, r, vals)
            tr.save()
            print(f"EMPLOYER UPDATED {name}: " + ("; ".join(parts) if parts else "(no change)"))
            return 0
    emp.append(dict(vals, Employer=name, Normalized=norm))
    tr.save()
    print(f"EMPLOYER ADDED {name}")
    return 0


def cmd_activity_add(a):
    kind = pick("type", a.type, ACT_TYPES)
    if not a.description.strip():
        raise TrackerError("--description cannot be empty")
    when = opt_date("date", a.date) or today(a.root)
    tr = open_tracker(a, create=True, for_write=True)
    tr.sheet("activities").append({"Date": when, "Type": kind, "Description": a.description.strip(),
                                   "Contact": a.contact, "Result": a.result})
    tr.save()
    print(f"ACTIVITY ADDED {when.isoformat()} | {kind} | {short(a.description)}")
    return 0


def cmd_archive(a):
    before = parse_date_arg("--before", a.before)
    tr = open_tracker(a, for_write=True)
    if tr is None:
        return 0
    rows = [r for r in tr.app_rows() if rank_of(r["Status"]) == 7
            and (to_date(r["Last update"]) or to_date(r["Date"]) or before) < before]
    if not rows:
        print(f"ARCHIVED 0 rows (none final before {before.isoformat()})")
        return 0
    counts = tr.archive_rows(rows)
    ids = [r["_id"] for r in rows if isinstance(r["_id"], int)]
    if ids and max(ids) > tr.last_archived_id():
        tr.set_setting("Last archived ID", max(ids))
    tr.save()
    for year in sorted(counts):
        print(f"ARCHIVED {counts[year]} rows to applications_archive_{year}.xlsx")
    print(f"KEPT {len(tr.app_rows())} rows")
    return 0


def rate(x, y):
    return f"{x} of {y} ({int(100 * x / y + 0.5)}%)" if y else f"{x} of {y} (n/a)"


def rates(rows):
    applied = [r for r in rows if to_date(r["Applied"])]
    resp = sum(1 for r in applied if canon_status(r["Status"]) in RESPONDED)
    intv = sum(1 for r in applied if canon_status(r["Status"]) in INTERVIEWED)
    return len(applied), resp, intv


def cmd_stats(a):
    tr = open_tracker(a)
    if tr is None:
        return 0
    rows = tr.app_rows()
    counts = Counter(canon_status(r["Status"]) or "(none)" for r in rows)
    for s in STATUSES + sorted(set(counts) - set(STATUSES)):
        if counts.get(s):
            print(f"STATUS {s}: {counts[s]}")
    print(f"TOTAL {len(rows)}")
    n, resp, intv = rates(rows)
    print(f"APPLIED {n}")
    print(f"RESPONSE RATE {rate(resp, n)}")
    print(f"INTERVIEW RATE {rate(intv, n)}")
    fits = [text(r["Fit"]) or "(none)" for r in rows]
    for f in FITS + sorted(set(fits) - set(FITS)):
        if f in fits:
            sub = [r for r in rows if (text(r["Fit"]) or "(none)") == f]
            n, resp, intv = rates(sub)
            print(f"FIT {f} | applied {n} | response rate {rate(resp, n)} | interview rate {rate(intv, n)}")
    months = Counter(to_date(r["Applied"]).strftime("%Y-%m") for r in rows if to_date(r["Applied"]))
    for m in sorted(months):
        print(f"MONTH {m}: {months[m]}")
    return 0


def cmd_statuses(a):
    for s in STATUSES:
        print(s)
    return 0


# ---------- the runner side: events, journal, snapshot ----------

class Journal:
    """state/tracker_events.jsonl: one line per applied event, so a file sent
    twice has no second effect and P- IDs keep their real row numbers."""

    def __init__(self, root):
        self.root = root
        self.path = jobfs.Folder(root).state / "tracker_events.jsonl"
        self.recs = jobfs.read_jsonl(self.path)

    def ids(self):
        return {r["id"] for r in self.recs if isinstance(r.get("id"), str)}

    def refs(self):
        return {r["pid"]: r["row"] for r in self.recs if r.get("pid") and isinstance(r.get("row"), int)}

    def recent_ids(self, day, days=events.RECENT_DAYS + 1):
        first = (day - timedelta(days=days)).isoformat()
        return sorted(r["id"] for r in self.recs if isinstance(r.get("id"), str) and str(r.get("on", "")) >= first)

    def add(self, ev, row=None):
        rec = {"id": ev["id"], "type": ev["type"], "on": jobfs.today(self.root).isoformat()}
        pid = ev["payload"].get("pid") if ev["type"] == "tracker" else None
        if pid:
            rec.update(pid=pid, row=row)
        jobfs.append_jsonl(self.path, rec)
        self.recs.append(rec)


def event_day(ev):
    try:
        return datetime.fromisoformat(ev["at"]).date()
    except (KeyError, TypeError, ValueError):
        return None


def run_event(store, ev, root):
    """Run one tracker event's command line against store; (exit code, last line)."""
    out = io.StringIO()
    code = 1
    with contextlib.redirect_stdout(out), acting_on(event_day(ev)):
        try:
            ns = build_parser().parse_args(list(ev["payload"]["argv"]) + ["--root", str(root)])
            ns.store, ns.pid = store, ev["payload"].get("pid")
            ns.view = "overlay" if isinstance(store, View) else "xlsx"
            code = ns.func(ns)
        except TrackerError as e:
            print(f"ERROR {e}")
            code = e.code
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except PermissionError:
            print("LOCKED")
            code = 4
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    return code, (lines[-1] if lines else f"exit {code}")


def update_status(root, **values):
    """Merge keys into runner/out/status.json (the runner's health file)."""
    path = jobfs.Folder(root).status_json
    try:
        data = jobfs.read_json_verified(path, retries=0) if path.exists() else {}
    except jobfs.NotReady:
        data = {}
    if path.exists() and all(data.get(k) == v for k, v in values.items()):
        return
    data.update(values)
    jobfs.atomic_write_json(path, data)


def held_open(path):
    """True when another program holds path so it cannot be opened for writing
    (Excel holds an open workbook and its ~$ owner file that way). Opening for
    r+b changes nothing; a missing file is not held."""
    try:
        with open(path, "r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def workbook_locked(root):
    """Excel has applications.xlsx open: the workbook or its owner file is held.
    An owner file nothing holds is a leftover (Excel crashed, a forced restart)."""
    root = Path(root)
    return held_open(root / FILENAME) or held_open(root / LOCK_NAME)


def report_locked(root):
    print("LOCKED applications.xlsx is open in another program (Excel?). Events stay pending.")
    path = jobfs.Folder(root).status_json
    try:
        since = jobfs.read_json_verified(path, retries=0).get("tracker_locked_since") if path.exists() else None
    except jobfs.NotReady:
        since = None
    update_status(root, tracker_locked_since=since or jobfs.now_iso(root))
    return 4


def take_event(a, tr, path, applied_ids):
    """'applied', 'pending', 'dup' or ('rejected', reason) for one event file."""
    ev, err, permanent = events.read_file(path)
    if err is None:
        err, permanent = events.validate(ev, a.root, path.name), True
    if err:
        if not permanent and (events.is_young(path) or events.note_failure(a.root, path.name) < events.MAX_TRIES):
            return "pending", None
        if not permanent:
            err = f"unreadable after {events.MAX_TRIES} tries: {err}"
        return ("rejected", err), None
    if ev["id"] in applied_ids:
        return "dup", ev
    if ev["type"] == "tracker":
        code, last = run_event(tr, ev, a.root)
        if code == 4:
            return "locked", ev
        if code != 0:
            return ("rejected", last), ev
    return "applied", ev


def snapshot_current(root):
    """True when tracker.json is newer than applications.xlsx and every archive."""
    folder = jobfs.Folder(root)
    try:
        snap_t = folder.tracker_json.stat().st_mtime
    except OSError:
        return not folder.tracker.exists()
    books = [folder.tracker] + sorted(Path(root).glob(ARCHIVE_GLOB))
    return all(not b.exists() or b.stat().st_mtime <= snap_t for b in books)


def screen_events(root, files):
    """(readable files, files still arriving, files set aside): an event file
    that cannot be read as JSON (over 64 KB, nested deeper than 64, or not
    JSON after 3 tries) moves to runner/in/bad/ with one ERROR line, before the
    workbook is opened, so a bad file never stops a tick (shared decision R3)."""
    keep, waiting, bad = [], 0, 0
    for path in files:
        ev, err, permanent = events.read_file(path)
        if err is None:
            keep.append(path)
            continue
        if not permanent and (events.is_young(path) or events.note_failure(root, path.name) < events.MAX_TRIES):
            waiting += 1
            continue
        why = err if permanent else f"unreadable after {events.MAX_TRIES} tries: {err}"
        try:
            events.set_aside(root, path, why)
        except OSError as e:  # held open by another program: the next tick moves it
            print(f"ERROR bad event file {path.name}: {why}; not moved yet ({type(e).__name__})")
            continue
        print(f"ERROR bad event file {path.name}: {why}; moved to runner/in/bad/")
        bad += 1
    return keep, waiting, bad


def cmd_apply_events(a):
    need_openpyxl()
    files, waiting, bad = screen_events(a.root, events.pending_files(a.root))
    if not files:
        # An idle tick: refresh the snapshot only when the user edited (and saved) the workbook.
        if not snapshot_current(a.root) and not workbook_locked(a.root):
            tr = Tracker(a.root)
            tr.load()
            write_snapshot(tr, a.root)
        update_status(a.root, tracker_locked_since=None)
        print(f"APPLIED 0 PENDING {waiting} REJECTED {bad}")
        return 0
    if workbook_locked(a.root):
        return report_locked(a.root)
    journal = Journal(a.root)
    applied_ids = journal.ids()
    tr = Tracker(a.root)
    try:
        tr.open_or_new()
    except PermissionError:
        return report_locked(a.root)
    tr.defer = True
    tr.refs.update(journal.refs())
    done, dups, pending, rejected = [], [], waiting, bad
    for path in files:
        outcome, ev = take_event(a, tr, path, applied_ids)
        if outcome == "locked":
            return report_locked(a.root)
        if outcome == "pending":
            pending += 1
        elif outcome == "dup":
            dups.append(path)
        elif outcome == "applied":
            done.append((path, ev))
            applied_ids.add(ev["id"])
        else:
            events.reject(a.root, path, outcome[1])
            print(f"REJECTED {path.name}: {outcome[1]}")
            rejected += 1
    if any(ev["type"] == "tracker" for _, ev in done) and tr.dirty:
        try:
            tr.flush()
        except PermissionError:
            return report_locked(a.root)
    day = jobfs.today(a.root)
    for path, ev in done:
        journal.add(ev, tr.refs.get(ev["payload"].get("pid")) if ev["type"] == "tracker" else None)
        events.move_done(a.root, path, day)
    for path in dups:  # a copy of an applied event: no second effect
        events.move_done(a.root, path, day)
    events.clear_failure(a.root, [p.name for p, _ in done])
    if tr.path.exists():
        write_snapshot(tr, a.root, journal)
    update_status(a.root, tracker_locked_since=None)
    print(f"APPLIED {len(done)} PENDING {pending} REJECTED {rejected}")
    return 0


def plain(sheet, cols):
    return [{c: jsonable(r[c]) for c in cols} for r in sheet.rows()] if sheet else []


def snapshot_row(root, row, cache, fingerprints=True):
    out = {c: jsonable(row.get(c)) for c in APP_COLS}
    if not fingerprints:
        return out
    old = cache.get((text(row.get("ID")), text(row.get("Folder"))))
    if old is not None and "simhash" in old:
        reqs, sh = set(old.get("req_ids") or []), old["simhash"]
    else:
        folder = text(row.get("Folder"))
        reqs, sh = folder_fingerprint(Path(root) / folder) if folder else (set(), "")
    if text(row.get("Req ID")):
        reqs = reqs | {text(row["Req ID"]).upper()}
    out.update(req_ids=sorted(reqs), simhash=sh)
    return out


def snapshot_dict(tr, root, prev=None, journal=None, fingerprints=True, archived=None):
    """The runner/out/tracker.json body (spec 2.5): every sheet's rows, plus
    req_ids and simhash per application, refs for P- IDs, and recent rejections."""
    prev = prev or {}
    cache = {(text(r.get("ID")), text(r.get("Folder"))): r
             for r in (prev.get("applications") or []) + (prev.get("archived") or [])}
    journal = journal or Journal(root)
    settings = {}
    for r in tr.settings.rows() if tr.settings else []:
        if text(r["Key"]):
            settings[text(r["Key"])] = jsonable(r["Value"])
    return {"v": 1, "generated_at": jobfs.now_iso(root),
            "applications": [snapshot_row(root, r, cache, fingerprints) for r in tr.apps.rows()],
            "archived": archived if archived is not None else
                        [snapshot_row(root, r, cache, fingerprints) for r in tr.archived_rows()],
            "accounts": plain(tr.accounts, ACC_COLS), "email_log_ids": sorted(logged_ids(tr)),
            "referrals": plain(tr.referrals, REF_COLS), "employers": plain(tr.employers, EMP_COLS),
            "activities": plain(tr.activities, ACT_COLS), "settings": settings,
            "refs": journal.refs(), "applied_ids": journal.recent_ids(jobfs.today(root)),
            "pending_rejected": events.rejected_list(root)}


def write_snapshot(tr, root, journal=None):
    folder = jobfs.Folder(root)
    prev, archived = {}, None
    try:
        if folder.tracker_json.exists():
            prev = jobfs.read_json_verified(folder.tracker_json, retries=0, fallback=False)
            snap_t = folder.tracker_json.stat().st_mtime
            if "archived" in prev and all(p.stat().st_mtime <= snap_t for p in Path(root).glob(ARCHIVE_GLOB)):
                archived = prev["archived"]  # no archive file changed since the last snapshot
    except (jobfs.NotReady, OSError):
        pass
    snap = snapshot_dict(tr, root, prev, journal, archived=archived)
    jobfs.atomic_write_json(folder.tracker_json, snap)
    return snap


def cmd_snapshot(a):
    need_openpyxl()
    tr = Tracker(a.root)
    if not tr.exists():
        print("NO TRACKER")
        return 0
    tr.load()
    snap = write_snapshot(tr, a.root)
    print(f"SNAPSHOT {jobfs.Folder(a.root).tracker_json} | {len(snap['applications'])} rows | "
          f"{len(snap['archived'])} archived")
    return 0


def cmd_upgrade(a):
    need_openpyxl()
    tr = Tracker(a.root)
    if not tr.exists():
        print("NO TRACKER")
        return 0
    tr.load()
    if not tr.upgrade_needed():
        print(f"CURRENT {tr.path}")
        return 0
    tr.upgrade()
    tr.save()
    print(f"UPGRADED {tr.path} (backup {tr.upgraded_backup})")
    return 0


# ---------- CLI ----------

def strip_root_args(argv):
    """The command line without --root and --transport (an event carries the rest)."""
    out, skip = [], False
    for tok in argv:
        if skip:
            skip = False
            continue
        name = tok.split("=", 1)[0]
        if name in ("--root", "--transport"):
            skip = "=" not in tok
            continue
        out.append(tok)
    return out


def event_job_key(a):
    if a.cmd == "add":
        return a.job_key if a.job_key is not None else jobkey.job_key(a.url)
    return getattr(a, "job_key", "") or ""


def add_add_options(p):
    p.add_argument("--company", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--location", default="")
    p.add_argument("--fit", default="", help=" | ".join(FITS))
    p.add_argument("--status", default="Started", help="default Started; see `statuses`")
    p.add_argument("--url", default="")
    p.add_argument("--folder", default="", help="job folder relative to --root")
    p.add_argument("--source", default="Skill", help='"Skill" (default), "By hand" or "Runner"')
    p.add_argument("--applied", help="YYYY-MM-DD; sets Follow-up to +7 days (Tier A and referral rows)")
    p.add_argument("--notes", default="")
    p.add_argument("--job-key", default=None, help="default: derived from --url")
    p.add_argument("--tier", default="", help=" | ".join(TIERS))
    for opt in ("--family", "--variant", "--ats", "--found-via", "--req-id", "--referral",
                "--referral-url", "--proof", "--block-reason", "--employer-site"):
        p.add_argument(opt, default="")
    p.add_argument("--posted", help="YYYY-MM-DD")
    p.add_argument("--queue-date", help="YYYY-MM-DD")
    p.add_argument("--account", default="", help=" | ".join(ACCOUNT_STATES))
    p.add_argument("--confirmed", help="YYYY-MM-DD")
    p.add_argument("--force", action="store_true",
                   help="a new row even when the job key already has one (a real second application)")


def add_subcommands(add):
    add("init", cmd_init, "Create applications.xlsx if missing (CREATED or EXISTS).")
    p = add("check", cmd_check, "Duplicate check of a new posting against every tracked job.")
    p.add_argument("--company", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--job-key", default="", help="default: derived from --url")
    p.add_argument("--posting", help="the new posting.md, for requisition IDs and text similarity")
    add_add_options(add("add", cmd_add, "Add a row; prints ADDED <ID> (ADDED P-<8hex> (pending) via events)."))
    p = add("update", cmd_update, "Change fields of a row: update ID field=value ... "
            "(notes+=text appends a dated line; field= clears it). ID may be a P- ID.")
    p.add_argument("id")
    p.add_argument("fields", nargs="+", metavar="field=value")
    p.add_argument("--force", action="store_true", help="allow a backward status move")
    p = add("show", cmd_show, "Print matching rows as a pipe-separated table.")
    p.add_argument("--id")
    p.add_argument("--status", nargs="+")
    p.add_argument("--active", action="store_true", help="exclude Not pursued, Rejected, Withdrawn, Closed")
    p.add_argument("--company")
    add("due", cmd_due, "FOLLOW-UP, UNSUBMITTED, STALE and UPCOMING items, or NONE.")
    p = add("account", cmd_account, "Find (no --email) or add/update a job-site account. "
            "Passwords are never stored.")
    p.add_argument("--site", required=True)
    p.add_argument("--email")
    p.add_argument("--notes")
    p.add_argument("--employer")
    p.add_argument("--ats")
    p.add_argument("--used", action="store_true", help="set Last used to today")
    p.add_argument("--reuse", action="store_true", help="on no match, print REUSE <the email the user uses most>")
    add("inbox-plan", cmd_inbox_plan, "SINCE date, ACTIVE rows to match mail against, LOGGED count.")
    p = add("seen", cmd_seen, "SEEN or NEW for each message ID, against the Email log.")
    p.add_argument("ids", nargs="+", metavar="message-id")
    p = add("log-email", cmd_log_email, "Append one read email to the Email log (DUPLICATE if logged).")
    p.add_argument("--message-id", required=True)
    p.add_argument("--email-date", required=True)
    p.add_argument("--from", dest="sender", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--class", dest="cls", required=True)
    p.add_argument("--job-id")
    p.add_argument("--quote", default="")
    p.add_argument("--change", default="")
    p.add_argument("--kind", default="reply", help="reply (default) | alert")
    add("inbox-done", cmd_inbox_done, "Set Last inbox check to today.")
    p = add("referral-add", cmd_referral_add, "Add or update a Referrals row (contact name only).")
    p.add_argument("--contact", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--job-key", default="")
    p.add_argument("--channel", default="", help=" | ".join(REF_CHANNELS))
    p.add_argument("--status", default="", help=" | ".join(REF_STATUSES) + " (new rows: Drafted)")
    p.add_argument("--nudge-on")
    p.add_argument("--referral-url", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--date")
    p = add("employer-set", cmd_employer_set, "Add or update an Employers row (work-search records).")
    p.add_argument("--employer", required=True)
    for _, attr in EMPLOYER_OPTS:
        p.add_argument("--" + attr.replace("_", "-"), dest=attr)
    p = add("activity-add", cmd_activity_add, "Add a work-search activity that is not an application.")
    p.add_argument("--type", required=True, help=" | ".join(ACT_TYPES))
    p.add_argument("--description", required=True)
    p.add_argument("--date")
    p.add_argument("--contact", default="")
    p.add_argument("--result", default="")
    p = add("archive", cmd_archive, "Move final-status rows last updated before a date into "
            "applications_archive_<year>.xlsx (backup first; nothing deleted).")
    p.add_argument("--before", required=True)
    add("stats", cmd_stats, "Counts by status, response and interview rates, applied per month.")
    add("statuses", cmd_statuses, "Print the valid statuses, one per line.", parents=False)
    add("apply-events", cmd_apply_events, "Runner: apply runner/in/events in name order; "
        "prints APPLIED n PENDING n REJECTED n.", transport=False)
    add("snapshot", cmd_snapshot, "Runner: write runner/out/tracker.json.", transport=False)
    add("upgrade", cmd_upgrade, "Upgrade an older applications.xlsx in place (backup first).", transport=False)


def build_parser():
    ap = Parser(prog="tracker.py", description="The job-application tracker (applications.xlsx).",
                allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    common = Parser(add_help=False, allow_abbrev=False)
    common.add_argument("--root", required=True, help="the JobSearch folder holding applications.xlsx")
    common_t = Parser(add_help=False, allow_abbrev=False)
    common_t.add_argument("--transport", default="auto", choices=("auto", "events", "direct"),
                          help="auto (default): events when the runner is the writer (mode L)")

    def add(name, fn, help_text, parents=True, transport=True):
        par = ([common] + ([common_t] if transport else [])) if parents else []
        p = sub.add_parser(name, parents=par, help=help_text, description=help_text, allow_abbrev=False)
        p.set_defaults(func=fn)
        return p

    add_subcommands(add)
    return ap


def is_write(a):
    return a.cmd in WRITES and not (a.cmd == "account" and not a.email)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = build_parser()
    argv = argfile.expand(argv, ap)  # outside text arrives in a JSON file, never inline
    args = ap.parse_args(argv)
    args.store, args.pid, args.view, transport = None, None, "xlsx", "direct"
    try:
        if args.cmd in WRITES | READS:
            transport = resolve_transport(args, is_write(args))
            args.view = "overlay" if transport == "events" else "xlsx"
        code = args.func(args)
        if code == 0 and transport == "events" and is_write(args) and args.store is not None and args.store.dirty:
            payload = {"argv": strip_root_args(argv)}
            if args.pid:
                payload["pid"] = args.pid
            events.emit(args.root, "tracker", payload, job_key=event_job_key(args))
        return code
    except TrackerError as e:
        print(f"ERROR {e}")
        return e.code
    except (events.EventError, jobfs.ConfigError) as e:
        print(f"ERROR {e}")
        return 1
    except MissingPackage:
        print("MISSING openpyxl: run  pip install openpyxl  and run again")
        return 2
    except PermissionError:
        print("LOCKED applications.xlsx is open in another program (Excel?). Close it and run again.")
        return 4


if __name__ == "__main__":
    sys.exit(main())
