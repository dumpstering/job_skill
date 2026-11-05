"""The job-application tracker: <JobSearch folder>/applications.xlsx.

Usage (every subcommand except `statuses` takes --root <JobSearch folder>):
    python tracker.py init --root R
    python tracker.py check --root R --company C --title T [--url U] [--posting P]
    python tracker.py add --root R --company C --role ROLE [--location L] [--fit F]
        [--status S] [--url U] [--folder F] [--source Skill|"By hand"]
        [--applied YYYY-MM-DD] [--notes N] [--job-key K]
    python tracker.py update --root R ID field=value [field=value ...] [--force]
    python tracker.py show --root R [--id N] [--status S [S ...]] [--active] [--company C]
    python tracker.py due --root R
    python tracker.py account --root R --site HOST [--email E] [--notes N]
    python tracker.py inbox-plan --root R
    python tracker.py seen --root R MESSAGE_ID [MESSAGE_ID ...]
    python tracker.py log-email --root R --message-id M --email-date D --from X
        --subject S --class C [--job-id N] [--quote Q] [--change T]
    python tracker.py inbox-done --root R
    python tracker.py stats --root R
    python tracker.py statuses

Columns are found by header name, so the user may sort rows, add columns or
retype values in Excel. Every write saves to a temp file and renames it over
applications.xlsx, after copying the old file to backups/applications_<date>.xlsx
the first time that day. The workbook never holds a password.

`check` labels, strongest first:
    SAME POSTING   same job key or URL
    LIKELY REPOST  same company and requisition ID, or posting text at least
                   50% the same (word 5-grams)
    SIMILAR ROLE   same company, title at least 85% alike
    SAME COMPANY   same company, another role, started in the last 90 days

Exit codes: 0 ok, 1 bad input (ERROR ...), 2 openpyxl missing (pip install
openpyxl), 4 applications.xlsx is open in Excel (LOCKED ...), 5 a backward
status move was refused (REFUSED ...; add --force to correct a mistake).
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobkey  # noqa: E402

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    openpyxl = None

FILENAME = "applications.xlsx"
DATE_FMT = "yyyy-mm-dd"
MAX_ROWS = 2000

APP_COLS = ["ID", "Date", "Company", "Role", "Location", "Fit", "Status", "Applied", "Follow-up",
            "Last update", "Next step", "Source", "Job key", "Resume sent", "Contact", "Notes",
            "Folder", "URL"]
ACC_COLS = ["Site", "Login email", "Created", "Notes"]
LOG_COLS = ["Checked", "Message ID", "Email date", "From", "Subject", "Job ID", "Classified as",
            "Quote", "Change"]
SET_COLS = ["Key", "Value"]
DATE_FIELDS = {"Date", "Applied", "Follow-up", "Last update"}

STATUSES = ["Started", "Not pursued", "Resume ready", "Filled - awaiting submit",
            "Answers ready - fill by hand", "Blocked - login", "Blocked - CAPTCHA", "Applied",
            "No response", "Assessment", "Screen", "Interview", "Offer", "Rejected", "Withdrawn",
            "Closed"]
FITS = ["Strong Fit", "Good Fit", "Stretch", "Long Shot"]
SOURCES = ["Skill", "By hand"]
# "Not pursued" is final like Rejected, so dropping a job is never a backward move.
RANK = {"Started": 0, "Not pursued": 7, "Resume ready": 1, "Filled - awaiting submit": 2,
        "Answers ready - fill by hand": 2, "Blocked - login": 2, "Blocked - CAPTCHA": 2,
        "Applied": 3, "No response": 3, "Assessment": 4, "Screen": 4, "Interview": 5, "Offer": 6,
        "Rejected": 7, "Withdrawn": 7, "Closed": 7}
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
                     "Contact": 22, "Notes": 50, "Folder": 28, "URL": 40},
    "Accounts": {"Site": 28, "Login email": 30, "Created": 11, "Notes": 50},
    "Email log": {"Checked": 11, "Message ID": 22, "Email date": 11, "From": 30, "Subject": 40,
                  "Job ID": 7, "Classified as": 16, "Quote": 50, "Change": 30},
    "Settings": {"Key": 22, "Value": 14},
}
PASSWORD_RE = re.compile(r"pass(word)?|pwd|passwd", re.I)
TEXT_STRIP = chr(9) + chr(13)  # leading tab/CR are stripped from stored text
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class TrackerError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- values ----------

def today():
    return date.today()


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


def parse_date_arg(label, s):
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise TrackerError(f"{label} must be YYYY-MM-DD, got '{s}'")


def pick(label, value, choices):
    """The canonical spelling of value from choices (case-insensitive), or an error."""
    want = re.sub(r"\s+", " ", value.strip()).lower()
    for c in choices:
        if c.lower() == want:
            return c
    raise TrackerError(f"{label} '{value}' is not one of: {', '.join(choices)}")


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


# ---------- workbook ----------

class Sheet:
    """One worksheet whose columns are found by header name."""

    def __init__(self, ws, columns):
        self.ws = ws
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
        if name == "URL":
            cell.hyperlink = None
        if val is None or val == "":
            cell.value = None
            return
        if isinstance(val, datetime):
            val = val.date()
        if isinstance(val, date):
            cell.value = val
            cell.number_format = DATE_FMT
            return
        if isinstance(val, str) or name == "Message ID":
            # Untrusted text (email subjects, company names): never let it be a formula.
            val = str(val).lstrip(TEXT_STRIP)
            cell.value = val
            cell.data_type = "s"
        else:
            cell.value = val
        if name == "URL" and isinstance(val, str) and re.match(r"https?://", val, re.I):
            cell.hyperlink = str(val)
            cell.font = Font(color="0563C1", underline="single")
        elif name == "Notes":
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    def append(self, values):
        row = self.last_row() + 1
        for n, v in values.items():
            self.put(row, n, v)
        return row


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


def build_workbook():
    wb = openpyxl.Workbook()
    apps = wb.active
    apps.title = "Applications"
    make_sheet(apps, APP_COLS)
    for n in ("Accounts", "Email log", "Settings"):
        wb.create_sheet(n)
    make_sheet(wb["Accounts"], ACC_COLS)
    make_sheet(wb["Email log"], LOG_COLS)
    make_sheet(wb["Settings"], SET_COLS)
    wb["Settings"].append(["Last inbox check", None])
    wb["Settings"]["B2"].number_format = DATE_FMT
    apps.auto_filter.ref = f"A1:{get_column_letter(len(APP_COLS))}1"
    for c in ("Date", "Applied", "Follow-up", "Last update"):
        apps.column_dimensions[get_column_letter(APP_COLS.index(c) + 1)].number_format = DATE_FMT
    status_L = get_column_letter(APP_COLS.index("Status") + 1)
    fit_L = get_column_letter(APP_COLS.index("Fit") + 1)
    list_validation(wb, apps, f"{status_L}2:{status_L}{MAX_ROWS}", STATUSES, 4, "Status")
    list_validation(wb, apps, f"{fit_L}2:{fit_L}{MAX_ROWS}", FITS, 5, "Fit")
    add_summary(wb, status_L)
    return wb


def add_summary(wb, status_L="G"):
    sm = wb.create_sheet("Summary")
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


class Tracker:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / FILENAME
        self.wb = None

    def exists(self):
        return self.path.exists()

    def create(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.wb = build_workbook()
        self.bind()
        self.save()

    def load(self, for_write=False):
        try:
            self.wb = openpyxl.load_workbook(self.path)
        except PermissionError:
            raise
        except Exception as e:
            raise TrackerError(f"cannot read {self.path}: {e}")
        if "Applications" not in self.wb.sheetnames:
            raise TrackerError(f"{self.path} has no Applications sheet")
        if for_write:
            for name, cols in (("Accounts", ACC_COLS), ("Email log", LOG_COLS), ("Settings", SET_COLS)):
                if name not in self.wb.sheetnames:
                    make_sheet(self.wb.create_sheet(name), cols)
            if "Summary" not in self.wb.sheetnames:
                status_col = Sheet(self.wb["Applications"], APP_COLS).col.get("Status", 7)
                add_summary(self.wb, get_column_letter(status_col))
        self.bind()
        if for_write:
            for s in (self.apps, self.accounts, self.log, self.settings):
                if s:
                    s.ensure_columns()

    def bind(self):
        def get(name, cols):
            return Sheet(self.wb[name], cols) if name in self.wb.sheetnames else None
        self.apps = get("Applications", APP_COLS)
        self.accounts = get("Accounts", ACC_COLS)
        self.log = get("Email log", LOG_COLS)
        self.settings = get("Settings", SET_COLS)

    def backup(self):
        if not self.path.exists():
            return
        folder = self.root / "backups"
        target = folder / f"applications_{today().isoformat()}.xlsx"
        if target.exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, target)

    def save(self):
        ws = self.apps.ws
        if ws.auto_filter.ref and self.apps.col:
            last_col = get_column_letter(max(self.apps.col.values()))
            ws.auto_filter.ref = f"A1:{last_col}{max(self.apps.last_row(), 2)}"
        self.root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".applications_", suffix=".xlsx", dir=str(self.root))
        os.close(fd)
        try:
            self.wb.save(tmp)
            self.backup()
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # Settings
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

    # Applications
    def app_rows(self):
        rows = self.apps.rows()
        for r in rows:
            r["_id"] = to_int(r["ID"])
        return rows

    def find(self, row_id):
        for r in self.app_rows():
            if r["_id"] == row_id:
                return r
        return None


def open_tracker(root, create=False, for_write=False):
    """A loaded Tracker; None (after printing NO TRACKER) when missing and not creating."""
    tr = Tracker(root)
    if not tr.exists():
        if create:
            tr.create()
            tr.load(for_write=True)
            return tr
        print("NO TRACKER")
        return None
    tr.load(for_write=for_write)
    return tr


# ---------- commands ----------

def cmd_init(a):
    tr = Tracker(a.root)
    if tr.exists():
        print(f"EXISTS {tr.path}")
    else:
        tr.create()
        print(f"CREATED {tr.path}")
    return 0


def read_posting(root, folder):
    if not folder:
        return ""
    p = Path(root) / folder / "posting.md"
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        return ""


def cmd_check(a):
    key = jobkey.job_key(a.url)
    print(f"JOB KEY {key or '(none: no URL)'}")
    tr = Tracker(a.root)
    if not tr.exists():
        print("NO MATCH (no tracker yet)")
        return 0
    tr.load()
    root = Path(a.root)
    new_text = Path(a.posting).read_text(encoding="utf-8", errors="replace") if a.posting else ""
    new_reqs, new_sh = jobkey.req_ids(new_text), jobkey.shingles(new_text)
    company, title = jobkey.norm_company(a.company), jobkey.norm_title(a.title)
    new_folder = os.path.normcase(str(Path(a.posting).resolve().parent)) if a.posting else None
    now = today()

    matches = []
    for row in tr.app_rows():
        folder = text(row["Folder"])
        row_folder = os.path.normcase(str((root / folder).resolve())) if folder else None
        if new_folder and row_folder == new_folder:
            continue
        row_key = text(row["Job key"]) or jobkey.job_key(text(row["URL"]))
        same_company = bool(company) and jobkey.norm_company(text(row["Company"])) == company
        old_text = read_posting(root, folder)

        rank, why = None, ""
        if key and row_key and key == row_key:
            rank, why = 0, f"job key {key}"
        elif same_company and new_reqs & jobkey.req_ids(old_text):
            rank, why = 1, f"same requisition {', '.join(sorted(new_reqs & jobkey.req_ids(old_text)))}"
        elif (sim := jobkey.jaccard(new_sh, jobkey.shingles(old_text))) >= 0.5 and (same_company or sim >= 0.8):
            rank, why = 1, f"posting text {int(sim * 100)}% the same"
        elif same_company and (t := SequenceMatcher(None, title, jobkey.norm_title(text(row["Role"]))).ratio()) >= 0.85:
            rank, why = 2, f"title {int(t * 100)}% alike"
        elif same_company:
            started = to_date(row["Date"])
            if started and (now - started).days <= 90:
                rank, why = 3, f"other role, {(now - started).days} days ago"
        if rank is not None:
            matches.append((rank, row, why))

    if not matches:
        print("NO MATCH")
    labels = ["SAME POSTING", "LIKELY REPOST", "SIMILAR ROLE", "SAME COMPANY"]
    for rank, row, why in sorted(matches, key=lambda m: m[0]):
        cols = " | ".join(text(row[k]) for k in ("Date", "Company", "Role", "Status", "Folder"))
        print(f"{labels[rank]:<13} ID {text(row['ID'])} | {cols} ({why})")
    return 0


def cmd_add(a):
    status = pick("status", a.status, STATUSES)
    fit = pick("fit", a.fit, FITS) if a.fit else ""
    source = pick("source", a.source, SOURCES)
    applied = parse_date_arg("applied", a.applied) if a.applied else None
    if not a.company.strip() or not a.role.strip():
        raise TrackerError("company and role cannot be empty")
    tr = open_tracker(a.root, create=True, for_write=True)
    ids = [r["_id"] for r in tr.app_rows() if r["_id"] is not None]
    new_id = max(ids, default=0) + 1
    now = today()
    follow = applied + timedelta(days=7) if applied and RANK[status] <= 3 else None
    tr.apps.append({
        "ID": new_id, "Date": now, "Company": a.company.strip(), "Role": a.role.strip(),
        "Location": a.location, "Fit": fit, "Status": status, "Applied": applied,
        "Follow-up": follow, "Last update": now, "Source": source,
        "Job key": a.job_key if a.job_key is not None else jobkey.job_key(a.url),
        "Notes": a.notes, "Folder": a.folder.replace("\\", "/"), "URL": a.url.strip(),
    })
    tr.save()
    print(f"ADDED {new_id}")
    return 0


def resolve_field(name):
    k = field_key(name)
    for col in APP_COLS:
        if field_key(col) == k:
            return col
    raise TrackerError(f"unknown field '{name}'; fields are: {', '.join(APP_COLS)}")


def cmd_update(a):
    assigns = []
    for item in a.fields:
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
        elif col == "Status" and value.strip():
            value = pick("status", value, STATUSES)
        elif col == "Fit" and value.strip():
            value = pick("fit", value, FITS)
        elif col == "Source" and value.strip():
            value = pick("source", value, SOURCES)
        elif isinstance(value, str):
            value = value.strip() if col != "Notes" else value
        assigns.append((col, append, value))

    tr = open_tracker(a.root, for_write=True) if Tracker(a.root).exists() else None
    if tr is None:
        raise TrackerError(f"no row with ID {a.id} (no tracker yet)")
    row = tr.find(a.id)
    if row is None:
        raise TrackerError(f"no row with ID {a.id}")

    now = today()
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
                and (rank_of(new_status) or 0) <= 3):
            new["Follow-up"] = applied + timedelta(days=7)
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
    tr = open_tracker(a.root)
    if tr is None:
        return 0
    want_status = {pick("status", s, STATUSES) for s in a.status} if a.status else None
    want_company = jobkey.norm_company(a.company) if a.company else None
    out = []
    for r in tr.app_rows():
        st = canon_status(r["Status"])
        if a.id is not None and r["_id"] != a.id:
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
    tr = open_tracker(a.root)
    if tr is None:
        return 0
    now = today()
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


def cmd_account(a):
    for label, v in (("--email", a.email), ("--notes", a.notes)):
        if v and PASSWORD_RE.search(v):
            raise TrackerError("passwords are never stored")
    site = normalize_site(a.site)
    if not site:
        raise TrackerError("--site cannot be empty")
    if not a.email:
        tr = open_tracker(a.root)
        if tr is None:
            return 0
        hits = [r for r in tr.accounts.rows()] if tr.accounts else []
        hits = [r for r in hits if site in text(r["Site"]).lower()]
        if not hits:
            print("NONE")
        for r in hits:
            print(f"ACCOUNT {text(r['Site'])} | {text(r['Login email'])} | {text(r['Created'])} | "
                  f"{text(r['Notes']).replace(chr(10), ' ')}")
        return 0
    tr = open_tracker(a.root, create=True, for_write=True)
    for r in tr.accounts.rows():
        if text(r["Site"]).lower() == site:
            if a.email is not None:
                tr.accounts.put(r["_row"], "Login email", a.email.strip())
            if a.notes is not None:
                tr.accounts.put(r["_row"], "Notes", a.notes)
            tr.save()
            print(f"ACCOUNT UPDATED {site}")
            return 0
    tr.accounts.append({"Site": site, "Login email": (a.email or "").strip(),
                        "Created": today(), "Notes": a.notes})
    tr.save()
    print(f"ACCOUNT ADDED {site}")
    return 0


def logged_ids(tr):
    if not tr.log or "Message ID" not in tr.log.col:
        return set()
    return {text(r["Message ID"]) for r in tr.log.rows() if text(r["Message ID"])}


def cmd_inbox_plan(a):
    tr = open_tracker(a.root)
    if tr is None:
        return 0
    now = today()
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
    tr = open_tracker(a.root)
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
    tr = open_tracker(a.root, create=True, for_write=True)
    if mid in logged_ids(tr):
        print(f"DUPLICATE {mid}")
        return 0
    email_date = to_date(a.email_date) if DATE_RE.match(a.email_date.strip()) else None
    job_id = to_int(a.job_id) if a.job_id is not None else None
    if a.job_id is not None and job_id is None:
        raise TrackerError(f"--job-id must be a number, got '{a.job_id}'")
    tr.log.append({"Checked": today(), "Message ID": mid, "Email date": email_date or a.email_date,
                   "From": a.sender, "Subject": a.subject, "Job ID": job_id,
                   "Classified as": a.cls, "Quote": a.quote, "Change": a.change})
    tr.save()
    print(f"LOGGED {mid}")
    return 0


def cmd_inbox_done(a):
    tr = open_tracker(a.root, create=True, for_write=True)
    now = today()
    tr.set_setting("Last inbox check", now)
    tr.save()
    print(f"INBOX CHECKED {now.isoformat()}")
    return 0


def rate(x, y):
    return f"{x} of {y} ({int(100 * x / y + 0.5)}%)" if y else f"{x} of {y} (n/a)"


def rates(rows):
    applied = [r for r in rows if to_date(r["Applied"])]
    resp = sum(1 for r in applied if canon_status(r["Status"]) in RESPONDED)
    intv = sum(1 for r in applied if canon_status(r["Status"]) in INTERVIEWED)
    return len(applied), resp, intv


def cmd_stats(a):
    tr = open_tracker(a.root)
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


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="tracker.py", description="The job-application tracker (applications.xlsx).")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    common = Parser(add_help=False)
    common.add_argument("--root", required=True, help="the JobSearch folder holding applications.xlsx")

    def add(name, fn, help_text, parents=True):
        p = sub.add_parser(name, parents=[common] if parents else [], help=help_text,
                           description=help_text)
        p.set_defaults(func=fn)
        return p

    add("init", cmd_init, "Create applications.xlsx if missing (CREATED or EXISTS).")

    p = add("check", cmd_check, "Duplicate check of a new posting against every tracked job.")
    p.add_argument("--company", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--posting", help="the new posting.md, for requisition IDs and text similarity")

    p = add("add", cmd_add, "Add a row; prints ADDED <ID>.")
    p.add_argument("--company", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--location", default="")
    p.add_argument("--fit", default="", help=" | ".join(FITS))
    p.add_argument("--status", default="Started", help="default Started; see `statuses`")
    p.add_argument("--url", default="")
    p.add_argument("--folder", default="", help="job folder relative to --root")
    p.add_argument("--source", default="Skill", help='"Skill" (default) or "By hand"')
    p.add_argument("--applied", help="YYYY-MM-DD; sets Follow-up to +7 days")
    p.add_argument("--notes", default="")
    p.add_argument("--job-key", default=None, help="default: derived from --url")

    p = add("update", cmd_update, "Change fields of a row: update ID field=value ... "
            "(notes+=text appends a dated line; field= clears it).")
    p.add_argument("id", type=int)
    p.add_argument("fields", nargs="+", metavar="field=value")
    p.add_argument("--force", action="store_true", help="allow a backward status move")

    p = add("show", cmd_show, "Print matching rows as a pipe-separated table.")
    p.add_argument("--id", type=int)
    p.add_argument("--status", nargs="+")
    p.add_argument("--active", action="store_true",
                   help="exclude Not pursued, Rejected, Withdrawn, Closed")
    p.add_argument("--company")

    add("due", cmd_due, "FOLLOW-UP, UNSUBMITTED, STALE and UPCOMING items, or NONE.")

    p = add("account", cmd_account, "Find (no --email) or add/update a job-site account. "
            "Passwords are never stored.")
    p.add_argument("--site", required=True)
    p.add_argument("--email")
    p.add_argument("--notes")

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

    add("inbox-done", cmd_inbox_done, "Set Last inbox check to today.")
    add("stats", cmd_stats, "Counts by status, response and interview rates, applied per month.")
    add("statuses", cmd_statuses, "Print the valid statuses, one per line.", parents=False)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if args.cmd != "statuses" and openpyxl is None:
        print("MISSING openpyxl: run  pip install openpyxl  and run again")
        return 2
    try:
        return args.func(args)
    except TrackerError as e:
        print(f"ERROR {e}")
        return e.code
    except PermissionError:
        print("LOCKED applications.xlsx is open in another program (Excel?). Close it and run again.")
        return 4


if __name__ == "__main__":
    sys.exit(main())
