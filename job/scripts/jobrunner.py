"""The job finder that runs on the user's PC (spec 2.1-2.2, 2.7, 3, 4.3, 7.5, 7.9).

Usage:
    python jobrunner.py daily         --root R [--mode L|S] [--offline DIR] [--date YYYY-MM-DD]
    python jobrunner.py tick          --root R [--mode L|S] [--offline DIR] [--date YYYY-MM-DD]
    python jobrunner.py selftest      --root R [--net] [--offline DIR]
    python jobrunner.py status        --root R
    python jobrunner.py rebuild-queue --root R [--offline DIR] [--date YYYY-MM-DD]
    python jobrunner.py request <op>  --root R [--json ARGS]

Installed, Task Scheduler runs it as
    %LOCALAPPDATA%\\JobRunner\\venv\\Scripts\\pythonw.exe -I -m jobrunner <cmd>
from the hash-checked app folder (the venv's jobrunner_app.pth names that
folder). The JobSearch folder comes from %LOCALAPPDATA%\\JobRunner\\config.toml
(folder = "...") when --root is not given. Nothing is ever imported or run from
the JobSearch folder: only this file's own folder is on sys.path.

daily (05:30): every watch-list board and aggregator lane (one shared
netfetch.Fetcher, list gates before detail calls), the lead resolver, the
detail gates into state/pool.json, the queue (jobqueue.build), per item the
Tier B build (variants.tier_b) and the fill plan (answers.plan), upload
staging, the do-yourself files (upload/E<n>_... and applications/_diy/<date>/),
network import (when network/raw changed) and match, today.md, review.html,
reports/funnel.csv, family weights on the weekly day, and
runner/out/status.json. It prints the BOARD, SOURCE, RESOLVE, GATES, QUEUE,
MIX, STAGED and DIY lines (a failed optional step is a WARN line), then
    DAILY ok queue=<n> pool_left=<n> discards=<n> tier_b_built=<n> prep_failed=<n>
    DAILY ok queue=<n> unchanged          (a second run on the same date)
    DAILY WAIT a session is active ...    (the next tick runs it after the session)
tier_b_built counts items whose resume_B files were built (tuned, or the
approved base when tuning failed a check); discards counts today's rows in
queue/<date>/discards.csv (gates and unresolved leads).

tick (every 15 minutes, 06:00-22:00): applies event files to the tracker
(tracker.py apply-events) and folds queue events, writes the jobs from links
(added events) into queue/<date>/addendum_user.json with the numbers the
session gave them (ADDENDUM addendum_user.json items=...; their job folders
are the session's, so it never preps them), gives an unparked job a
fresh fill plan (REPLAN), moves upload files of applied items, answers
requests in runner/in/requests (REQUEST <id> <op> ok|failed|REJECTED; results
in runner/out/results/<id>.json), stages late files once no session is
active, re-reads the next 3 items' postings once a day (LIVENESS, CLOSED;
runner/out/liveness.json), and runs a daily that waited for a session. With
nothing to do it prints TICK idle and exits within a second, without opening
the workbook; otherwise it ends with TICK done <n> steps. While
claude/session.json has a beat under 20 minutes old it only appends
addenda with new numbers: queue.json and upload/ stay as they are, and a
rebuild_queue request waits.

Requests (spec 2.5): resolve_leads, probe_url {url, company, title, location},
fetch_posting {url | n | job_key}, build {folder, json}, stage_tier_a
{folder | n}, more {limit}, import_network, rebuild_queue. Arguments are
company names, URLs (netfetch policy) and folders inside the root; anything
else is rejected with ok=false.

selftest prints PASS or FAIL lines for python, packages, tzdata, folder
write, robots and build, plus the reach checks with --net; exit 1 on any FAIL.

The lock: %LOCALAPPDATA%\\JobRunner\\run.lock (state/run.lock in mode S or
with --offline). A held lock prints LOCKED and exits 8; a lock older than
2 hours is taken over with a STALE LOCK line.

Exit codes: 0 ok (also WAIT and idle), 1 bad input or a FAIL (ERROR ...),
2 missing package, 8 runner LOCKED.
"""
import argparse
import contextlib
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import statistics
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))  # the app folder only, never the JobSearch folder (8.6)
import events  # noqa: E402
import jobfs  # noqa: E402

EXIT_LOCKED = 8
EXIT_MISSING = 2
STALE_LOCK_SECONDS = 2 * 3600
RUNNER_STALE_HOURS = 26
LIVENESS_HOURS = 24
LIVENESS_NEXT = 3
BEAT_MINUTES = 20
REQUEST_MAX = 64 * 1024
REQUEST_YOUNG = 5
REQUEST_TRIES = 3
NAME_MAX = 120
URL_MAX = 2000
FUNNEL_COLUMNS = ["date", "seen_by_lane", "discarded_by_gate", "pool", "queued", "started", "submitted",
                  "confirmed", "parked", "deferred", "resolution_by_lane", "median_fill_min"]
REQ_NAME = re.compile(r"^(\d{8}T\d{6}Z_[0-9a-f]{8})_([a-z_]+)\.json$")
SECRET_RE = re.compile(r"(?i)\b(app_key|app_id|api_key|key|authorization-key|token)=([^&\s\"']+)")
URLISH = re.compile(r"https?://|www\.|@", re.I)
# Request ops and the arguments each one may carry: url (netfetch policy),
# name (a company, title or place), folder (inside the root), int, text.
OPS = {
    "resolve_leads": {},
    "probe_url": {"url": "url", "company": "name", "title": "name", "location": "name"},
    "fetch_posting": {"url": "url", "n": "int", "job_key": "text"},
    "build": {"folder": "folder", "json": "file"},
    "stage_tier_a": {"folder": "folder", "n": "int", "job_key": "text"},
    "more": {"limit": "int"},
    "import_network": {},
    "rebuild_queue": {},
}
FOLDER_OPS = {"build", "stage_tier_a"}   # folders must be job folders under applications/
SELFTEST_HOSTS = [
    ("greenhouse", "GET", "https://boards-api.greenhouse.io/v1/boards/vaxcyte/jobs"),
    ("lever", "GET", "https://api.lever.co/v0/postings/synthego?mode=json&limit=1"),
    ("ashby", "GET", "https://api.ashbyhq.com/posting-api/job-board/insitro"),
    ("workday", "POST", "https://gilead.wd1.myworkdayjobs.com/wday/cxs/gilead/gileadcareers/jobs"),
    ("usajobs", "GET", "https://data.usajobs.gov/api/codelist/countries"),
]
SELFTEST_RECRUITEE = "https://bunq.recruitee.com/api/offers/"   # the example in the ats research
SELFTEST_ROBOTS = "https://boards-api.greenhouse.io/robots.txt"
SAMPLE_RESUME = {
    "name": "Sample Person", "contact": ["Springfield", "sample@example.com"],
    "sections": [{"heading": "Summary", "paragraph": "Scientist with 5 years of laboratory work."},
                 {"heading": "Experience", "entries": [
                     {"title": "Scientist", "dates": "2020 - Present", "org": "Example Labs",
                      "location": "Springfield", "bullets": ["Ran 3 assays a week for the team."]}]}]}


def _patient_replace(src, dst, _replace=os.replace):
    """os.replace that waits out a Windows sharing violation: an antivirus or
    indexer often holds a file for a moment just after it was written, and the
    runner rewrites state files (the robots cache) many times a run."""
    for attempt in range(12):
        try:
            return _replace(src, dst)
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.02 * (attempt + 1))


if os.name == "nt":
    os.replace = _patient_replace


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


class RequestError(Exception):
    """A request that breaks the 2.5 rules; the text is the result line."""


# ---------- small helpers ----------

def read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def verified(path, default):
    """A runner JSON with its _sha256 checked (no retry wait: the runner wrote it)."""
    try:
        data = jobfs.read_json_verified(path, retries=0)
        return data if isinstance(data, type(default)) else default
    except jobfs.NotReady:
        return default


def redact(line):
    return SECRET_RE.sub(lambda m: f"{m.group(1)}=REDACTED", str(line))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_day(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"--date must be YYYY-MM-DD, got '{s}'")


def pin_clock(root, day):
    """--date D: the clock reads D at 05:30 local (JOB_SKILL_NOW, which
    subprocesses inherit), unless it already reads a time on D."""
    if os.environ.get("JOB_SKILL_NOW"):
        try:
            if jobfs.today(root) == day:
                return
        except (ValueError, jobfs.ConfigError):
            pass
    local = datetime(day.year, day.month, day.day, 5, 30, tzinfo=jobfs.zone(
        jobfs.settings(root)["time"]["timezone"]))
    os.environ["JOB_SKILL_NOW"] = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def home_config():
    """%LOCALAPPDATA%\\JobRunner\\config.toml, written by install_runner.ps1."""
    try:
        return tomllib.loads((jobfs.runner_home() / "config.toml").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def optional(run, name, fn, *args):
    """A step whose failure must not stop the run (network, weights, the
    do-yourself list, review.html): it becomes a WARN line and a status error."""
    try:
        return fn(*args)
    except Exception as e:  # any bug or bad input in an optional step
        run.out(f"WARN {name} failed: {type(e).__name__}: {str(e)[:160]}")
        run.errors.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")
        return None


def quiet_call(fn, *args, **kw):
    """Run a v1.1-style main() that prints; returns (code, lines)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = fn(*args, **kw)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, [ln for ln in buf.getvalue().splitlines() if ln.strip()]


# ---------- one run: output, log, lock, status ----------

class Run:
    """The shared state of one command: root, date, mode, output and log."""

    def __init__(self, args, cmd):
        self.args = args
        self.cmd = cmd
        self.root = Path(args.root)
        self.folder = jobfs.Folder(self.root)
        self.offline = getattr(args, "offline", None)
        if getattr(args, "date", None):
            pin_clock(self.root, parse_day(args.date))
        self.day = jobfs.today(self.root)
        self.mode = (getattr(args, "mode", None) or "").upper() or self.settings_mode()
        self._fetcher = None
        self.log_lines = []
        self.errors = []

    def settings_mode(self):
        mode = str(jobfs.settings(self.root)["runner"].get("mode", "L")).upper()
        return "S" if mode == "S" else "L"

    # output: print the line (stdout is None under pythonw) and keep it for the log
    def out(self, line):
        line = redact(line)
        print(line)
        self.log_lines.append(line)

    def log(self, line):
        self.log_lines.append(redact(line))

    def flush_log(self):
        if not self.log_lines:
            return
        path = self.folder.runner_log(self.day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = jobfs.now_iso(self.root)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                for line in self.log_lines:
                    f.write(f"{stamp} {self.cmd} {line}\n")
        except OSError:
            pass
        self.log_lines = []

    def fetcher(self):
        """One shared netfetch.Fetcher per run, so politeness spans every call."""
        if self._fetcher is None:
            import netfetch
            self._fetcher = netfetch.Fetcher(root=self.root, offline=self.offline, cookies=True)
        return self._fetcher

    def lock_path(self):
        if self.mode == "S" or self.offline:
            return self.folder.state / "run.lock"
        return jobfs.runner_home() / "run.lock"

    def status(self):
        return verified(self.folder.status_json, {})

    def write_status(self, **values):
        """Merge keys into runner/out/status.json (tracker.py adds tracker_locked_since)."""
        data = self.status()
        data.update(values)
        data.update(version=jobfs.VERSION, min_skill=jobfs.VERSION, mode=self.mode)
        data.setdefault("tracker_locked_since", None)
        jobfs.atomic_write_json(self.folder.status_json, data)


def take_lock(run):
    """The runner self-lock (2.7.2), or None after printing LOCKED."""
    lock = jobfs.RunLock(run.lock_path(), stale_after=STALE_LOCK_SECONDS, cmd=run.cmd)
    info = lock._holder() if lock.path.exists() else None
    if lock.path.exists() and lock._is_stale(info):
        who = info or {}
        run.out(f"STALE LOCK {lock.path.name} pid={who.get('pid', '?')} cmd={who.get('cmd', '?')} "
                f"started={who.get('started', '?')}: taken over")
    try:
        return lock.acquire()
    except jobfs.LockBusy as e:
        who = e.info or {}
        run.out(f"LOCKED the runner is busy (pid {who.get('pid', '?')}, {who.get('cmd', '?')} "
                f"since {who.get('started', '?')}); try again later")
        return None


def session_active(root):
    """claude/session.json beat under 20 minutes old (2.7.3), as jobqueue reads
    it (a beat up to 5 minutes ahead counts too), without importing jobqueue,
    so an idle tick stays light."""
    data = read_json(jobfs.Folder(root).session_json, {})
    try:
        beat = datetime.fromisoformat(str(data.get("beat", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=jobfs.now(root).tzinfo)
    age = jobfs.utcnow() - beat
    return timedelta(minutes=-5) < age < timedelta(minutes=BEAT_MINUTES)


def queue_files(root, day):
    qd = jobfs.Folder(root).queue_dir(day)
    return [qd / "queue.json"] + (sorted(x for x in qd.glob("addendum_*.json") if not x.name.endswith(".prev.json")) if qd.exists() else [])


# ---------- job folders ----------

def runner_names(folder):
    path = Path(folder) / ".runner_files"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return set()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return {str(x) for x in data}
    except ValueError:
        pass
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def may_write(folder, name):
    """The runner writes a job-folder file only when it is not claimed and the
    file is new or listed in .runner_files (2.4)."""
    folder = Path(folder)
    if (folder / ".claimed").exists():
        return False
    return not (folder / name).exists() or name in runner_names(folder)


def register(folder, names):
    names = set(names)
    have = runner_names(folder)
    if not names <= have:
        jobfs.atomic_write_text(Path(folder) / ".runner_files", "\n".join(sorted(have | names)) + "\n")


def plan_fields(plan):
    """(answers_status, needs [Q- ids], sensitive [kinds]) from a fill plan
    (jobqueue.plan_fields, shared with the jobs added from links)."""
    import jobqueue
    return jobqueue.plan_fields(plan)


class Prep:
    """Per queue item: the Tier B build and the fill plan (4.3, 4.6). Every
    Tier A item gets Tier B files too: they are its fallback (4.1)."""

    def __init__(self, run):
        self.run = run
        self.tally = {"built": 0, "fallback_base": 0, "prep_failed": 0, "claimed": 0, "plans": 0, "kept": 0}

    def item(self, q, folder):
        import answers
        import variants
        folder = Path(folder)
        if (folder / ".claimed").exists():
            self.tally["claimed"] += 1
            return
        if (folder / "resume_B.pdf").exists() and "resume_B.pdf" in runner_names(folder):
            status = "fallback_base" if self.fell_back(folder) else "built"
            self.tally["kept"] += 1
        else:
            try:
                status = variants.tier_b(self.run.root, folder, family=q.get("family") or None, emit=self.run.log)
            except variants.Refused as e:
                for line in str(e).splitlines():
                    self.run.log(line)
                status = "prep_failed"
            self.tally[status] = self.tally.get(status, 0) + 1
        if q.get("tier") == "B" and status in ("built", "fallback_base", "prep_failed"):
            q["resume_status"] = status
        if may_write(folder, "fill_plan.json") and (folder / "posting.md").exists():
            try:
                plan = answers.plan(self.run.root, folder, family=q.get("family") or None, write=True)
                register(folder, ["fill_plan.json", "fill_plan.prev.json"])
                self.tally["plans"] += 1
            except answers.Refused as e:
                self.run.log(str(e))
                plan = None
        else:
            plan = verified(folder / "fill_plan.json", {})
        if plan:
            q["answers_status"], q["needs"], q["sensitive"] = plan_fields(plan)

    @staticmethod
    def fell_back(folder):
        log = read_json(Path(folder) / "tune_log.json", {})
        return str(log.get("status", "")).startswith("fallback")

    def built(self):
        return self.tally["built"] + self.tally["fallback_base"]


def prep_existing(run, day, prep):
    """Prep every item of today's queue files that lacks its files; rewrite a
    queue file only when one of its items changed and no event exists yet."""
    import jobqueue
    changed = 0
    for path in queue_files(run.root, day):
        data = verified(path, {})
        if not data.get("items"):
            continue
        dirty = False
        for q in data["items"]:
            folder = run.root / str(q.get("folder", ""))
            if not folder.is_dir() or q.get("added_by"):
                continue  # a job from a link: its folder is the session's (addendum_user)
            before = json.dumps(q, sort_keys=True)
            missing = not (folder / "resume_B.pdf").exists() or not (folder / "fill_plan.json").exists()
            if missing and not (folder / ".claimed").exists():
                prep.item(q, folder)
                changed += 1
            dirty |= json.dumps(q, sort_keys=True) != before
        if dirty and not jobqueue.events_for(run.root, day):
            if path.name.startswith("addendum_"):
                rewrite_addendum(path, data)
            else:
                jobfs.atomic_write_json(path, data)
    return changed


# ---------- discovery ----------

def load_keys(run):
    """keys.toml from %LOCALAPPDATA%\\JobRunner; with --offline, the fixture's own."""
    import aggregators
    if run.offline:
        path = Path(run.offline) / "keys.toml"
        return aggregators.load_keys(path) if path.exists() else {}
    return aggregators.load_keys()


def discover(run):
    """Boards, aggregators, the resolver and the gates. Returns (counts, results, lanes)."""
    import aggregators
    import boards
    import gates
    import leads
    import resolve
    f = run.fetcher()
    gate_run = gates.GateRun(run.root, run.day, fetcher=f)
    results = boards.poll(run.root, fetcher=f, keep=gate_run.keep, emit=run.out)
    lanes = aggregators.run(run.root, fetcher=f, keys=load_keys(run), salt=leads.salt(run.root), emit=run.out)
    readings = [rd for res in results for rd in res.readings] + [rd for ln in lanes for rd in ln.postings]
    extra = [ld for ln in lanes for ld in ln.leads]
    out = resolve.Resolver(run.root, run.day, f, emit=run.log).run(readings=readings, extra_leads=extra)
    run.out(next((ln for ln in reversed(run.log_lines) if ln.startswith("RESOLVE ")), "RESOLVE leads=0"))
    gate_run.process(readings + out.readings)
    counts = gate_run.save()
    if out.discarded:  # unresolved leads are discards too (3.6 step 6, 3.13)
        counts["discarded"] += len(out.discarded)
        counts["seen"] += len(out.discarded)
        pool = gates.load_pool(run.root)
        gates.save_pool(run.root, pool["items"], counts=counts, needs_you=pool.get("needs_you"), date_=run.day)
    run.out("GATES " + " ".join(f"{k}={counts[k]}" for k in ("seen", "passed", "discarded", "parked", "diy",
                                                             "skipped")))
    return counts, results, lanes


def gate_new(run, readings):
    """Gate late finds (a tick's leads or probe_url); returns the new pool items."""
    import gates
    if not readings:
        return []
    gate_run = gates.GateRun(run.root, run.day, fetcher=run.fetcher())
    new = gate_run.process(readings)
    gate_run.save()
    return [it for it in new if not it.get("parked") and not it.get("diy")]


def addendum_of(run, items, prep=None):
    """An addendum holding exactly these new pool items (numbers after the
    highest given today); each gets its Tier B files and fill plan."""
    import jobqueue
    if not items:
        return None
    tmp = run.folder.state / f".addendum_pool_{os.getpid()}.json"
    jobfs.atomic_write_text(tmp, json.dumps({"v": 1, "items": items}, ensure_ascii=True) + "\n")
    try:
        path = jobqueue.addendum(run.root, run.day, pool_path=tmp, limit=len(items), emit=run.out)
    finally:
        tmp.unlink(missing_ok=True)
    if path:
        prep_addendum(run, path, prep or Prep(run))
    return path


def prep_addendum(run, path, prep):
    data = verified(path, {})
    for q in data.get("items") or []:
        folder = run.root / str(q.get("folder", ""))
        if folder.is_dir():
            prep.item(q, folder)
    if data:
        rewrite_addendum(path, data)


def rewrite_addendum(path, data):
    """Rewrite an addendum the runner made this tick. Its .prev.json copy is
    removed at once, so queue/<date>/ holds only real addenda (the readers
    also skip *.prev.json when they glob addendum_*.json)."""
    jobfs.atomic_write_json(path, data)
    jobfs.prev_path(path).unlink(missing_ok=True)


# ---------- network, weights ----------

def raw_fingerprint(folder):
    raw = folder.network_raw
    if not raw.is_dir():
        return ""
    parts = []
    for p in sorted(raw.rglob("*")):
        if p.is_file():
            st = p.stat()
            parts.append(f"{p.relative_to(raw).as_posix()}|{st.st_size}|{int(st.st_mtime)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest() if parts else ""


def network_import(run, force=False):
    """network.py import when network/raw changed (or on request)."""
    import network
    path = run.folder.state / "network_raw.json"
    fp = raw_fingerprint(run.folder)
    if not fp or (not force and read_json(path, {}).get("fingerprint") == fp):
        return None
    code, lines = quiet_call(network.main, ["import", "--root", str(run.root)])
    for line in lines:
        run.log(line)
    # Recorded even after a failure, so a bad file is not retried every tick;
    # a new drop changes the fingerprint.
    jobfs.atomic_write_text(path, json.dumps({"fingerprint": fp, "on": run.day.isoformat(), "exit": code}) + "\n")
    run.out(next((ln for ln in reversed(lines) if ln.startswith(("WROTE", "NONE", "IMPORTED"))),
                 f"NETWORK import exit {code}"))
    return code


def network_match(run):
    import network
    if not run.folder.network_csv.exists():
        return
    code, lines = quiet_call(network.main, ["match", "--root", str(run.root), "--date", run.day.isoformat()])
    for line in lines:
        run.log(line)
    summary = next((ln for ln in lines if ln.startswith("MATCHES")), None)
    if summary:
        run.out(summary)


def weekly_adapt(run):
    """report.py adapt --apply on the weekly day (6.7); it moves weights once a week."""
    import report
    weekly = str(jobfs.settings(run.root)["schedule"].get("weekly_day", "monday")).lower()
    if run.day.strftime("%A").lower() != weekly:
        return
    code, lines = quiet_call(report.main, ["adapt", "--apply", "--root", str(run.root),
                                           "--date", run.day.isoformat()])
    for line in lines:
        run.log(line)
    if lines:
        run.out(lines[0])


# ---------- queue outputs: today.md, review.html, funnel.csv ----------

def queue_items(root, day):
    items, block = [], {}
    for p in queue_files(root, day):
        data = verified(p, {})
        items += [it for it in data.get("items") or [] if isinstance(it, dict)]
        if p.name == "queue.json":
            block = data.get("account_block") or {}
    return sorted(items, key=lambda it: it.get("n") or 0), block


def discard_rows(root, day):
    path = jobfs.Folder(root).discards_csv(day)
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def effort_label(it):
    acct = it.get("account", "none")
    word = {"none": "no account", "exists": "account", "new": "new account"}.get(acct, acct)
    return f"{it.get('ats', '')}/{word}"


def tier_note(root, it):
    """The Tier B note from tune_log.json ("summary 2; bullets 3,5 up; ...")."""
    folder = Path(root) / str(it.get("folder", ""))
    log = read_json(folder / "tune_log.json", {})
    if it.get("tier") == "A":
        return "Tier A, prepared this morning" if it.get("resume_status") == "tier_a_ready" else "Tier A prep this morning"
    if it.get("resume_status") == "prep_failed":
        return "needs a variant re-approval"
    note = str(log.get("note") or "").strip()
    if it.get("resume_status") == "fallback_base":
        note = "base variant"
    return (note + "; checks ok") if note else "checks ok"


def cell(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).replace("|", "/").strip()


def write_today_md(run, items, block, counts):
    """queue/<date>/today.md: about 80 lines at most (4.10)."""
    import answers
    import resolve
    d = run.day
    lines = [f"# {d:%A}, {d:%B} {d.day}", ""]
    import today_view
    no_acct = sum(1 for it in items if it.get("account") == "none")
    acct = "any of them" if no_acct == len(items) else str(no_acct)
    lines += [f"Ready: {len(items)}. No account needed for {acct}.", ""]
    try:
        qs = answers.questions(run.root, d)
        qs["sensitive"] = today_view.sensitive_text(run.root, d)
    except (OSError, ValueError, KeyError):
        qs = {"questions": [], "sensitive": ""}
    if qs["questions"]:
        lines += ["## Questions", ""]
        for i, q in enumerate(qs["questions"], 1):
            n = len(q["jobs"])
            who = " (all jobs)" if q["all_jobs"] else f" ({n} job{'s' if n != 1 else ''})" if n else ""
            lines.append(f"{i}. {cell(q['text'])}{who}")
        lines.append("")
    if qs.get("sensitive"):
        lines += [f"Sensitive answers: {qs['sensitive']}.", ""]
    lines += ["## Ready items", "", "NN | Company | Role | Posted | ATS/effort | Tier | Note", "---|---|---|---|---|---|---"]
    for it in items[:40]:
        lines.append(" | ".join([f"{int(it.get('n') or 0):02d}", cell(it.get("company")), cell(it.get("role")),
                                 cell(it.get("posted") or "undated"), effort_label(it), cell(it.get("tier")),
                                 cell(tier_note(run.root, it))]))
    lines.append("")
    if block.get("tenants"):
        lines += [f"Account block after #{block.get('after_n', 0)}: " + ", ".join(block["tenants"]) + ".", ""]
    diy = resolve.diy_items(run.root, d)[:int(jobfs.settings(run.root)["daily"].get("easy_apply_list_max", 5))]
    if diy:
        lines += ["<details><summary>Do it yourself</summary>", ""]
        lines += [f"- E{i} | {cell(x.get('company'))} | {cell(x.get('title'))} | {cell(x.get('location'))}"
                  for i, x in enumerate(diy, 1)]
        lines += ["", "</details>", ""]
    gates_n = {}
    for r in discard_rows(run.root, d):
        g = r.get("reason") if r.get("gate") == "resolve" else (r.get("reason") or "").split(":")[0]
        gates_n[g] = gates_n.get(g, 0) + 1
    if gates_n:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(gates_n.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append(f"Not queued today: {sum(gates_n.values())} by gate ({parts}).")
    jobfs.atomic_write_text(run.folder.today_md(d), "\n".join(lines[:80]) + "\n")


def write_review_html(run, items):
    """queue/<date>/review.html: static, offline, ASCII, no external resources."""
    esc = html.escape
    d = run.day
    rows = []
    for it in items:
        folder = run.root / str(it.get("folder", ""))
        log = read_json(folder / "tune_log.json", {})
        parts = [f"<h2>#{int(it.get('n') or 0):02d} {esc(str(it.get('company', '')))} - {esc(str(it.get('role', '')))}</h2>",
                 f"<p>Tier {esc(str(it.get('tier', '')))} | {esc(str(it.get('resume_status', '')))} | "
                 f"{esc(str(it.get('variant', '')))} | {esc(effort_label(it))}</p>",
                 f"<p>{esc(tier_note(run.root, it))}</p>"]
        for name, label in (("audit.md", "Audit"), ("notes.md", "Verify before sending")):
            p = folder / name
            if it.get("tier") == "A" and p.exists():
                text = p.read_text(encoding="utf-8", errors="replace")[:4000]
                parts.append(f"<h3>{label}</h3><pre>{esc(text)}</pre>")
        if log.get("changes"):
            parts.append("<pre>" + esc(json.dumps(log["changes"], indent=1)[:4000]) + "</pre>")
        rows.append("<section>" + "\n".join(parts) + "</section>")
    body = "\n".join(rows) or "<p>No items today.</p>"
    page = ("<!doctype html>\n<html><head><meta charset=\"ascii\"><title>Review " + d.isoformat() +
            "</title><style>body{font-family:Arial,sans-serif;max-width:60em;margin:1em auto;padding:0 1em}"
            "pre{white-space:pre-wrap;background:#f4f4f4;padding:.5em}</style></head><body>\n"
            f"<h1>Review, {d:%A}, {d:%B} {d.day}</h1>\n{body}\n</body></html>\n")
    page = page.encode("ascii", "xmlcharrefreplace").decode("ascii")
    jobfs.atomic_write_text(run.folder.review_html(d), page)


def kv(counter):
    return ";".join(f"{k}:{v}" for k, v in sorted(counter.items()) if k)


def lane_resolution(root, day, days=14):
    """{lane: resolved share} over leads seen in the last 14 days."""
    import resolve
    state = read_json(jobfs.Folder(root).state / "leads_state.json", {}).get("leads", {})
    first = (day - timedelta(days=days)).isoformat()
    tot, res = {}, {}
    for ld in state.values():
        if str(ld.get("seen", "")) < first:
            continue
        lane = resolve.lane_of(str(ld.get("source", "")))
        tot[lane] = tot.get(lane, 0) + 1
        res[lane] = res.get(lane, 0) + (1 if ld.get("status") == "resolved" else 0)
    return {k: round(res[k] / tot[k], 2) for k in tot if tot[k]}


def median_fill(root, day):
    starts, mins = {}, []
    for ev in events.recent(root, day):
        if ev.get("date") != day.isoformat() or ev["type"] not in ("started", "handed_over"):
            continue
        try:
            at = datetime.fromisoformat(ev["at"])
        except (KeyError, ValueError):
            continue
        key = (ev.get("n"), ev.get("job_key"))
        if ev["type"] == "started":
            starts.setdefault(key, at)
        elif key in starts:
            mins.append((at - starts.pop(key)).total_seconds() / 60)
    return round(statistics.median(mins), 1) if mins else ""


def upsert_funnel(run, day, counts=None):
    """One reports/funnel.csv row per date (6.7), rewritten as the day goes on."""
    path = run.folder.funnel_csv
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        rows = []
    old = next((r for r in rows if r.get("date") == day.isoformat()), None)
    if old is None and counts is None:
        return
    items, _ = queue_items(run.root, day)
    q = verified(run.folder.queue_json(day), {})
    view = events.overlay(run.root, day)
    states = [it.get("history") or [] for it in view["items"].values()]
    seen, gates_n = {}, {}
    for r in discard_rows(run.root, day):
        seen[r.get("lane", "")] = seen.get(r.get("lane", ""), 0) + 1
        g = r.get("gate", "")
        gates_n[g] = gates_n.get(g, 0) + 1
    import gates
    for it in gates.load_pool(run.root).get("items", []):
        if it.get("pooled_on") == day.isoformat():
            seen[it.get("lane", "")] = seen.get(it.get("lane", ""), 0) + 1
    row = dict(old or {}, date=day.isoformat())
    if counts is not None or not old:
        row.update(seen_by_lane=kv(seen), discarded_by_gate=kv(gates_n),
                   pool=(q.get("size", 0) + q.get("pool_left", 0)) if q else "",
                   deferred=(q.get("counts") or {}).get("deferred", ""),
                   resolution_by_lane=kv(lane_resolution(run.root, day)))
    row.update(queued=len(items), started=sum(1 for h in states if "started" in h),
               submitted=sum(1 for h in states if "applied" in h),
               confirmed=sum(1 for it in view["items"].values()
                             if it.get("state") == "applied" and it.get("confirmation") in ("page", "email")),
               parked=sum(1 for h in states if "parked" in h), median_fill_min=median_fill(run.root, day))
    rows = [r for r in rows if r.get("date") != day.isoformat()] + [row]
    rows.sort(key=lambda r: r.get("date", ""))
    buf = io.StringIO()
    w = csv.DictWriter(buf, FUNNEL_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FUNNEL_COLUMNS})
    jobfs.atomic_write_text(path, buf.getvalue())


# ---------- uploads ----------

def move_applied(run, day):
    """While a session is active: only the files of applied items leave upload/."""
    import jobqueue
    items, _ = queue_items(run.root, day)
    applied = {it["job_key"] for it in events.overlay(run.root, day)["items"].values() if it.get("state") == "applied"}
    moved = 0
    for it in items:
        if it.get("job_key") not in applied:
            continue
        name = Path(it.get("upload", "")).name
        for target in (run.folder.upload / name,
                       run.folder.upload / re.sub(r"^(\d{2})_", r"\1c_", name).replace("_Resume_", "_Cover_")):
            if name and target.exists():
                jobqueue.move_to_done(run.root, target, day)
                moved += 1
    if moved:
        run.out(f"STAGED 0 files, moved {moved} to upload/_done/, session active")
    return moved


def stage(run, day):
    import jobqueue
    if session_active(run.root):
        return move_applied(run, day)
    buf = []
    staged, moved, _ = jobqueue.stage_uploads(run.root, day, emit=buf.append)
    for line in buf:
        (run.out if line.startswith("STAGED") else run.log)(line)
    if moved and day == run.day:  # stage_uploads moves every name it did not plan, E<n>_ files too
        optional(run, "do-yourself list", stage_diy, run)
    return staged + moved


def stage_tier_a(run, item):
    """Replace upload/NN_* with the item's Tier A resume.pdf; record its sha256."""
    folder = run.root / str(item.get("folder", ""))
    src = folder / "resume.pdf"
    if not src.exists():
        raise RequestError(f"no resume.pdf in {item.get('folder')}")
    name = Path(item.get("upload", "")).name
    if not name:
        raise RequestError(f"#{item.get('n')} has no upload name")
    run.folder.upload.mkdir(parents=True, exist_ok=True)
    dest = run.folder.upload / name
    tmp = dest.with_name(f".{name}.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    digest = sha256_file(dest)
    staged = verified(run.folder.runner_out / "staged.json", {})
    files = dict(staged.get("files") or {})
    files[name] = {"job_key": item.get("job_key"), "n": item.get("n"), "source": f"{item.get('folder')}/resume.pdf",
                   "sha256": digest, "at": jobfs.now_iso(run.root), "tier": "A"}
    jobfs.atomic_write_json(run.folder.runner_out / "staged.json", {"v": 1, "date": run.day.isoformat(),
                                                                     "files": files})
    return name, digest


def diy_rows(run, limit):
    """(company, title, location) of the day's do-yourself items: LinkedIn
    leads given up (resolve.py) and chat-apply postings from the pool."""
    import gates
    import resolve
    rows = [(x.get("company", ""), x.get("title", ""), x.get("location", ""))
            for x in resolve.diy_items(run.root, run.day)]
    rows += [(it.get("company", ""), it.get("title", ""), it.get("location", ""))
             for it in gates.load_pool(run.root).get("items", []) if it.get("diy")]
    return rows[:limit]


def family_for(profile, title):
    """The first active family whose title words the title holds, else the first active one."""
    words = set(re.findall(r"[a-z0-9]+", str(title).lower()))
    fams = [f for f in profile.get("family") or [] if f.get("status", "active") == "active" and f.get("id")]
    for fam in fams:
        terms = [str(t).strip("*").lower() for t in fam.get("title_any") or [] if "+" not in str(t)]
        terms += [w.lower() for t in fam.get("core_titles") or [] for w in str(t).split()]
        if any(t and (t in words or any(w.startswith(t) for w in words)) for t in terms):
            return fam["id"]
    return fams[0]["id"] if fams else ""


def base_pdf(run, family, cache):
    """The approved base variant built once (state/diy_base/<family>_v<N>.pdf)."""
    import build_resume
    import variants
    if family in cache:
        return cache[family]
    v, approved, _ = variants.usable(run.root, family) if family else (None, None, None)
    path = None
    if v is not None:
        out = run.folder.state / "diy_base"
        path = out / f"{family}_v{v}.pdf"
        if not path.exists():
            argv = [str(run.folder.variant(family, v) / "resume.json"), "--out", str(out), "--base", f"{family}_v{v}",
                    "--mode", str(approved.get("mode") or "industry")]
            if run.folder.publications.exists():
                argv += ["--publications", str(run.folder.publications)]
            lines = []
            if build_resume.main(argv, emit=lines.append) != 0 or not path.exists():
                for line in lines:
                    run.log(line)
                path = None
    cache[family] = path
    return path


def diy_sheet(run, company, title, location, upload):
    """applications/_diy/<date>/<Company>.md: what the user types, from the profile and the bank. No link (R2)."""
    import answers
    values, _ = answers.load_profile(run.folder.profile)
    lines = [f"# {company} - {title}", ""]
    if location:
        lines += [f"Location: {location}", ""]
    lines += ["Apply on the job site in your own browser" + (f" and attach {upload}." if upload else "."),
              "When it is sent, say `log e<n>` in the session.", "", "## From your profile", ""]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in sorted(values.items()) if str(v).strip()]
    lines += ["", "## Answers you gave before", ""]
    def slots(text):  # template answers name the employer and role
        return str(text).replace("{company}", company).replace("{role}", title)
    lines += [f"- {slots(a.question)}: {slots(a.answer)}" for a in answers.load_bank(run.folder.answers)
              if a.answer.strip()]
    return "\n".join(lines) + "\n"


def stage_diy(run):
    """upload/E<n>_<Last>_Resume_<Employer12>.pdf (the family's approved base
    variant) and one answer sheet per do-yourself item (5.8)."""
    import jobqueue
    settings, profile = jobfs.settings(run.root), jobfs.search_profile(run.root)
    rows = diy_rows(run, int(settings["daily"].get("easy_apply_list_max", 5)))
    if not rows:
        return 0
    last = jobqueue.last_name(run.root)
    cache, staged = {}, 0
    for i, (company, title, location) in enumerate(rows, 1):
        pdf = base_pdf(run, family_for(profile, title), cache)
        dest = run.folder.upload_file(i, last, company or "Employer", diy=True)
        if pdf and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf, dest)
            staged += 1
        sheet = run.folder.diy_dir(run.day) / f"{jobfs.ascii_name(company or 'Employer', 40)}.md"
        jobfs.atomic_write_text(sheet, diy_sheet(run, company, title, location,
                                                 f"upload\\{dest.name}" if pdf else ""))
    run.out(f"DIY {len(rows)} item{'s' if len(rows) != 1 else ''}, {staged} file{'s' if staged != 1 else ''} staged")
    return staged


# ---------- daily ----------

def daily(run):
    import jobqueue
    status = run.status()
    last = status.get("last_daily") or {}
    if session_active(run.root):
        run.write_status(daily_pending=run.day.isoformat())
        run.out("DAILY WAIT a session is active (claude/session.json beat under 20 minutes); "
                "the next tick runs it after the session")
        return 0
    jobfs.settings(run.root, keep_last_good=True)
    jobfs.search_profile(run.root, keep_last_good=True)
    prep = Prep(run)
    qpath = run.folder.queue_json(run.day)
    if qpath.exists() and last.get("date") == run.day.isoformat() and last.get("ok"):
        work = prep_existing(run, run.day, prep) + stage(run, run.day) + (optional(run, "do-yourself list",
                                                                                stage_diy, run) or 0)
        q = verified(qpath, {})
        if not work:
            run.write_status(daily_pending=None)
            run.out(f"DAILY ok queue={q.get('size', 0)} unchanged")
            return 0
        return finish_daily(run, q, prep, last.get("counts") or {})
    counts, results, _ = discover(run)
    optional(run, "network import", network_import, run)
    lines = []
    written = jobqueue.build(run.root, run.day, prep=prep.item, emit=lines.append)
    for line in lines:
        run.out(line)
    if written is None:
        written = verified(qpath, {})
        if any(ln.startswith("WAIT") for ln in lines):
            run.write_status(daily_pending=run.day.isoformat())
            run.out("DAILY WAIT a session is active; the next tick runs the build after the session")
            return 0
        prep_existing(run, run.day, prep)
    stage(run, run.day)
    optional(run, "do-yourself list", stage_diy, run)
    optional(run, "network match", network_match, run)
    optional(run, "weights", weekly_adapt, run)
    sources = {}
    for res in results:
        if res.status == "SKIP":
            continue
        tally = sources.setdefault(res.spec.ats, {"ok": 0, "failed": 0})
        tally["ok" if res.status in ("OK", "EMPTY") else "failed"] += 1
    return finish_daily(run, written, prep, counts, sources)


def finish_daily(run, q, prep, counts, sources=None):
    import boards
    items, block = queue_items(run.root, run.day)
    write_today_md(run, items, block, counts)
    optional(run, "review.html", write_review_html, run, items)
    upsert_funnel(run, run.day - timedelta(days=1))
    upsert_funnel(run, run.day, counts)
    discards = len(discard_rows(run.root, run.day))
    failed = sum(1 for it in items if it.get("resume_status") == "prep_failed")
    paused = sorted(k for k, r in boards.load_health(run.root).items()
                    if r.get("status") in ("paused", "robots_blocked"))
    settings_cfg, profile_cfg = jobfs.settings(run.root), jobfs.search_profile(run.root)
    errors = [c.error for c in (settings_cfg, profile_cfg) if c.error] + run.errors
    upd = {"last_daily": {"at": jobfs.now_iso(run.root), "date": run.day.isoformat(), "ok": True,
                          "counts": dict(counts, queue=q.get("size", 0), pool_left=q.get("pool_left", 0),
                                         discards=discards, tier_b_built=prep.built(), prep_failed=failed)},
           "daily_pending": None, "boards_paused": paused, "errors": errors,
           "widen": list((read_json(run.folder.queue_state_json, {}).get("widen") or {}).get("on") or [])}
    if sources is not None:
        upd["sources"] = sources
    run.write_status(**upd)
    run.out(f"DAILY ok queue={q.get('size', 0)} pool_left={q.get('pool_left', 0)} discards={discards} "
            f"tier_b_built={prep.built()} prep_failed={failed}")
    return 0


# ---------- tick ----------

def snapshot_stale(folder):
    try:
        snap = folder.tracker_json.stat().st_mtime
    except OSError:
        return folder.tracker.exists()
    books = [folder.tracker] + sorted(folder.root.glob("applications_archive_*.xlsx"))
    return any(b.exists() and b.stat().st_mtime > snap for b in books)


def pending_requests(folder):
    if not folder.requests.exists():
        return []
    return sorted((p for p in folder.requests.glob("*.json") if not p.name.startswith(".")), key=lambda p: p.name)


def liveness_due(run):
    """The next few open items whose posting was last read over 24 h ago."""
    items, _ = queue_items(run.root, run.day)
    if not items:
        return []
    view = events.overlay(run.root, run.day)["items"]
    done = {k[1] for k, v in view.items() if v.get("state") in ("applied", "skipped", "later", "blocked",
                                                                "handed_over", "parked")}
    live = read_json(run.folder.runner_out / "liveness.json", {}).get("items", {})
    now = jobfs.utcnow()
    due = []
    for it in [it for it in items if it.get("job_key") not in done][:LIVENESS_NEXT]:
        rec = live.get(it.get("job_key")) or {}
        last = rec.get("checked") or ""
        if not last:
            posting = read_json(run.root / str(it.get("folder", "")) / "posting.json", {})
            last = posting.get("first_seen") or ""
        try:
            t = datetime.fromisoformat(last)
            t = t if t.tzinfo else datetime.combine(t.date(), datetime.min.time(), tzinfo=jobfs.now(run.root).tzinfo)
        except ValueError:
            t = None
        if rec.get("closed"):
            continue
        if t is None or now - t > timedelta(hours=LIVENESS_HOURS):
            due.append(it)
    return due


def staging_due(run):
    """True when an open item's built file is not in upload/ yet and no session
    is active (late addenda wait for the session to end, 2.7.3)."""
    items, _ = queue_items(run.root, run.day)
    if not items or session_active(run.root):
        return False
    view = events.overlay(run.root, run.day)["items"]
    applied = {k[1] for k, v in view.items() if v.get("state") == "applied"}
    for it in items:
        name = Path(str(it.get("upload", ""))).name
        if not name or it.get("job_key") in applied or (run.folder.upload / name).exists():
            continue
        if (run.root / str(it.get("folder", "")) / "resume_B.pdf").exists():
            return True
    return False


def idle(run):
    """True when a tick has nothing to do (checked without opening the workbook)."""
    f = run.folder
    if f.events.exists() and any(p.suffix == ".json" for p in f.events.iterdir()):
        return False
    if pending_requests(f) or snapshot_stale(f):
        return False
    if run.status().get("daily_pending") and not session_active(run.root):
        return False
    fp = raw_fingerprint(f)
    if fp and read_json(f.state / "network_raw.json", {}).get("fingerprint") != fp:
        return False
    return not liveness_due(run) and not staging_due(run)


def pending_unparked(run):
    """(n, job_key, date) of unparked events waiting to be applied."""
    out = []
    for path in events.pending_files(run.root):
        if not path.name.endswith("_unparked.json"):
            continue
        ev, err, _ = events.read_file(path)
        if not err and isinstance(ev, dict):
            out.append((ev.get("n"), ev.get("job_key", ""), ev.get("date") or run.day.isoformat()))
    return out


def replan(run, unparked):
    """An unparked job gets a fresh fill plan, now that answers.md holds the
    answer (WP5). A claimed folder is the session's: it plans in memory."""
    import answers
    for n, key, day in unparked:
        items, _ = queue_items(run.root, day)
        it = next((x for x in items if (n is not None and x.get("n") == n) or (key and x.get("job_key") == key)), None)
        folder = run.root / str((it or {}).get("folder", ""))
        if not it or not folder.is_dir():
            continue
        if it.get("added_by"):
            run.log(f"REPLAN skipped #{n}: a job from a link (the session plans it)")
            continue
        if not may_write(folder, "fill_plan.json"):
            run.log(f"REPLAN skipped #{n}: the folder is claimed (the session plans it in memory)")
            continue
        try:
            answers.plan(run.root, folder, family=it.get("family") or None, write=True)
            register(folder, ["fill_plan.json", "fill_plan.prev.json"])
            run.out(f"REPLAN #{int(it.get('n') or 0):02d} {it.get('job_key')}")
        except answers.Refused as e:
            run.log(f"REPLAN #{n}: {e}")


def apply_events(run):
    """tracker.py apply-events (it handles every event type), then the fold."""
    import jobqueue
    import tracker
    unparked = pending_unparked(run)
    code, lines = quiet_call(tracker.main, ["apply-events", "--root", str(run.root)])
    for line in lines:
        (run.out if line.startswith(("APPLIED", "LOCKED", "REJECTED", "MISSING")) else run.log)(line)
    applied = 0
    m = re.search(r"APPLIED (\d+)", " ".join(lines))
    if m:
        applied = int(m.group(1))
    buf = []
    jobqueue.fold_events(run.root, run.day, emit=buf.append)
    for line in buf:
        run.log(line)
    if unparked and code == 0:
        optional(run, "re-plan", replan, run, unparked)
    return code, applied


def user_addendum(run):
    """Jobs from /job <link> and /job add <link> (added events) into
    queue/<date>/addendum_user.json, with the numbers the session gave them."""
    import jobqueue
    return jobqueue.user_addendum(run.root, run.day, emit=run.out)


def liveness(run, items):
    """Re-read the next items' postings; a closed one leaves the pool."""
    import gates
    import netfetch
    import posting
    path = run.folder.runner_out / "liveness.json"
    data = read_json(path, {})
    live = data.setdefault("items", {})
    closed = []
    for it in items:
        url = it.get("apply_url") or ""
        rec = {"checked": jobfs.now_iso(run.root), "n": it.get("n"), "closed": False, "why": ""}
        try:
            f = run.fetcher()
            f.policy.allow_host(netfetch.host_of(url))
            reading = posting.read_posting(url, fetcher=f, root=run.root, lane="liveness",
                                           first_seen=run.day.isoformat(), ats=it.get("ats") or None)
            rec["closed"] = bool(reading.posting.closed)
        except netfetch.Denied as e:
            rec["why"] = f"deny {e.reason}"
        except (netfetch.FetchError, OSError, ValueError) as e:
            rec["why"] = f"error {str(e)[:120]}"
        live[it["job_key"]] = rec
        if rec["closed"]:
            closed.append(it["job_key"])
            run.out(f"CLOSED #{int(it.get('n') or 0):02d} {it['job_key']}")
    jobfs.atomic_write_text(path, json.dumps(data, indent=1, sort_keys=True) + "\n")
    if closed:
        pool = gates.load_pool(run.root)
        for it in pool["items"]:
            if it.get("job_key") in closed:
                it["closed"] = True
        gates.save_pool(run.root, pool["items"], date_=run.day)
    run.out(f"LIVENESS checked {len(items)} closed {len(closed)}")


def tick(run):
    if idle(run):
        run.write_status(last_tick=jobfs.now_iso(run.root))
        run.out("TICK idle")
        return 0
    worked = []
    f = run.folder
    has_events = f.events.exists() and any(p.suffix == ".json" for p in f.events.iterdir())
    if has_events or snapshot_stale(f):
        code, applied = apply_events(run)
        if code == 2:
            run.out("MISSING openpyxl: run  pip install openpyxl  and run again")
        worked.append("events")
        if has_events:
            optional(run, "jobs from links", user_addendum, run)
        if applied:
            stage(run, run.day)
            upsert_funnel(run, run.day)
    for path in pending_requests(f):
        if handle_request(run, path):
            worked.append(path.name)
    if optional(run, "network import", network_import, run) is not None:
        worked.append("network")
    if staging_due(run):
        stage(run, run.day)
        worked.append("uploads")
    pending = run.status().get("daily_pending")
    if pending and not session_active(run.root):
        run.out(f"TICK runs the daily that waited ({pending})")
        daily(run)
        worked.append("daily")
    due = liveness_due(run)
    if due:
        liveness(run, due)
        worked.append("liveness")
    run.write_status(last_tick=jobfs.now_iso(run.root))
    run.out(f"TICK done {len(worked)} step{'s' if len(worked) != 1 else ''}")
    return 0


# ---------- requests ----------

def check_arg(run, op, key, kind, value):
    if kind == "int":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 10000:
            raise RequestError(f"{key} must be a whole number")
        return value
    if not isinstance(value, str):
        raise RequestError(f"{key} must be text")
    value = value.strip()
    if kind == "url":
        import netfetch
        if len(value) > URL_MAX or not re.match(r"^https?://", value, re.I):
            raise RequestError(f"{key} is not an http(s) URL")
        deny = netfetch.hard_deny(value)
        if deny:
            raise RequestError(f"DENY {deny}")
        return value
    if kind == "name":
        if len(value) > NAME_MAX or URLISH.search(value) or "\n" in value:
            raise RequestError(f"{key} must be a plain name (no links or addresses)")
        return value
    if kind == "folder":
        p = Path(value)
        if not run.folder.inside(p):
            raise RequestError(f"{key} {value!r} is outside the JobSearch folder")
        full = (run.root / p).resolve()
        if op in FOLDER_OPS:
            try:
                rel = full.relative_to(run.folder.applications.resolve())
            except ValueError:
                raise RequestError(f"{key} {value!r} is not a job folder under applications/")
            if rel == Path(".") or not full.is_dir():
                raise RequestError(f"{key} {value!r} is not a job folder under applications/")
        return full.relative_to(run.root.resolve()).as_posix()
    if kind == "file":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}\.json", value) or ".." in value:
            raise RequestError(f"{key} must be a JSON file name in the job folder")
        return value
    if len(value) > 300 or "\n" in value:
        raise RequestError(f"{key} is too long")
    return value


def read_request(run, path):
    """(request, None) or (None, reason); (None, None) means wait for it."""
    m = REQ_NAME.match(path.name)
    if not m:
        return None, "bad file name (expected <stamp>_<8hex>_<op>.json)"
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, f"unreadable: {e}"
    if len(raw) > REQUEST_MAX:
        return None, "request over 64 KB"
    try:
        req = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        if events.is_young(path) or events.note_failure(run.root, path.name) < REQUEST_TRIES:
            return None, None
        return None, f"not JSON after {REQUEST_TRIES} tries: {e}"
    if not isinstance(req, dict) or req.get("v") != 1:
        return None, "not a v1 request"
    if req.get("id") != m.group(1) or req.get("op") != m.group(2):
        return None, "id or op does not match the file name"
    op = req["op"]
    if op not in OPS:
        return None, f"unknown op {op}"
    args = req.get("args") or {}
    if not isinstance(args, dict):
        return None, "args must be an object"
    clean = {}
    try:
        for k, v in args.items():
            if k not in OPS[op]:
                raise RequestError(f"{op} takes no argument {k!r}")
            clean[k] = check_arg(run, op, k, OPS[op][k], v)
    except RequestError as e:
        return None, str(e)
    req["args"] = clean
    return req, None


def write_result(run, req_id, op, ok, lines, result=None):
    jobfs.atomic_write_json(run.folder.result(req_id), {
        "id": req_id, "op": op, "ok": bool(ok), "at": jobfs.now_iso(run.root),
        "lines": [redact(x) for x in lines][-200:], "result": result or {}})


def finish_request(run, path):
    dest = run.folder.runner_out / "requests_done" / run.day.isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest / path.name)


def handle_request(run, path):
    """Answer one request file; True when it was handled (and moved)."""
    req, why = read_request(run, path)
    m = REQ_NAME.match(path.name)
    req_id, op = (m.group(1), m.group(2)) if m else (path.stem, "unknown")
    if req is None and why is None:
        return False  # still arriving
    if req is None:
        write_result(run, req_id, op, False, [f"REJECTED {why}"])
        run.out(f"REQUEST {req_id} {op} REJECTED {why}")
        finish_request(run, path)
        return True
    if op == "rebuild_queue" and session_active(run.root):
        run.log(f"REQUEST {req_id} rebuild_queue waits for the session to end")
        return False
    lines = []
    start = len(run.log_lines)
    try:
        ok, result = REQUEST_HANDLERS[op](run, req["args"], lines)
    except RequestError as e:
        ok, result = False, {}
        lines.append(f"ERROR {e}")
    except Exception as e:  # one bad request never stops the tick
        ok, result = False, {}
        lines.append(f"ERROR {type(e).__name__}: {str(e)[:200]}")
    lines = run.log_lines[start:] + lines
    write_result(run, req_id, op, ok, lines, result)
    run.out(f"REQUEST {req_id} {op} {'ok' if ok else 'failed'}")
    finish_request(run, path)
    return True


def find_item(run, args):
    items, _ = queue_items(run.root, run.day)
    for it in items:
        if args.get("n") and it.get("n") == args["n"]:
            return it
        if args.get("job_key") and it.get("job_key") == args["job_key"]:
            return it
        if args.get("folder") and str(it.get("folder", "")).replace("\\", "/").rstrip("/") == args["folder"]:
            return it
    raise RequestError("no item in today's queue matches")


def req_resolve_leads(run, args, lines):
    import resolve
    out = resolve.Resolver(run.root, run.day, run.fetcher(), emit=run.log).run()
    new = gate_new(run, out.readings)
    path = addendum_of(run, new)
    result = {"resolved": len(out.resolved), "new_items": len(new), "addendum": path.name if path else "",
              "needs_search": len(out.needs_search), "diy": len(out.diy), "discarded": len(out.discarded)}
    return True, result


def req_probe_url(run, args, lines):
    import resolve
    res = resolve.probe_url(run.root, args["url"], args.get("company", ""), args.get("title", ""),
                            args.get("location", ""), fetcher=run.fetcher(), today=run.day, emit=run.log)
    if res["deny"]:
        return False, {"deny": res["deny"]}
    new = gate_new(run, res["readings"])
    path = addendum_of(run, new)
    return res["ok"], {"detect": res["detect"], "new_items": len(new), "addendum": path.name if path else ""}


def req_fetch_posting(run, args, lines):
    import netfetch
    import posting
    url = args.get("url")
    item = None
    if not url:
        item = find_item(run, args)
        url = item.get("apply_url")
    f = run.fetcher()
    f.policy.allow_host(netfetch.host_of(url))
    try:
        reading = posting.read_posting(url, fetcher=f, root=run.root, lane="liveness",
                                       first_seen=run.day.isoformat(), ats=(item or {}).get("ats") or None)
    except netfetch.Denied as e:
        return False, {"deny": e.reason}
    except netfetch.FetchError as e:
        return False, {"error": str(e)[:200]}
    p = reading.posting
    if item:
        live = read_json(run.folder.runner_out / "liveness.json", {})
        live.setdefault("items", {})[item["job_key"]] = {"checked": jobfs.now_iso(run.root), "n": item.get("n"),
                                                         "closed": bool(p.closed), "why": "request"}
        jobfs.atomic_write_text(run.folder.runner_out / "liveness.json", json.dumps(live, indent=1, sort_keys=True) + "\n")
    return True, {"closed": bool(p.closed), "job_key": p.job_key, "title": p.title, "company": p.company,
                  "posted": p.posted, "ai_text": len(reading.ai_text), "hidden": len(reading.hidden)}


def req_build(run, args, lines):
    """Build a Tier A resume.json (or letter JSON) the sandbox could not build (4.4)."""
    import build_resume
    folder = run.root / args["folder"]
    name = args.get("json") or "resume.json"
    src = folder / name
    if not src.exists():
        raise RequestError(f"{args['folder']}/{name} not found")
    # A claimed folder is fine here: the session that claimed it asked for the build.
    argv = [str(src), "--out", str(folder), "--base", src.stem, "--strict"]
    sources = [p for p in (run.folder.library, run.folder.master_resume) if p.exists()]
    if sources:
        argv += ["--sources"] + [str(p) for p in sources]
    if run.folder.publications.exists():
        argv += ["--publications", str(run.folder.publications)]
    if (folder / "terms.json").exists():
        argv += ["--terms", str(folder / "terms.json")]
    if "letter" in name.lower() and (folder / "posting.md").exists():
        argv += ["--posting", str(folder / "posting.md")]
    out = []
    code = build_resume.main(argv, emit=out.append)
    lines += out
    return code == 0, {"exit": code, "base": src.stem}


def req_stage_tier_a(run, args, lines):
    item = find_item(run, args)
    name, digest = stage_tier_a(run, item)
    lines.append(f"STAGED {name} sha256 {digest}")
    return True, {"file": f"upload/{name}", "sha256": digest, "n": item.get("n")}


def req_more(run, args, lines):
    import jobqueue
    buf = []
    path = jobqueue.addendum(run.root, run.day, limit=args.get("limit"), emit=buf.append)
    for line in buf:
        run.out(line)
    if path:
        prep_addendum(run, path, Prep(run))
        if not session_active(run.root):
            stage(run, run.day)
    return bool(path), {"addendum": path.name if path else ""}


def req_import_network(run, args, lines):
    code = network_import(run, force=True)
    return code in (0, None), {"exit": code}


def req_rebuild_queue(run, args, lines):
    import jobqueue
    buf = []
    prep = Prep(run)
    written = jobqueue.build(run.root, run.day, prep=prep.item, emit=buf.append)
    for line in buf:
        run.out(line)
    if written is not None:
        stage(run, run.day)
        items, block = queue_items(run.root, run.day)
        write_today_md(run, items, block, {})
    return written is not None or any(x.startswith("FROZEN") for x in buf), {"size": (written or {}).get("size")}


REQUEST_HANDLERS = {"resolve_leads": req_resolve_leads, "probe_url": req_probe_url,
                    "fetch_posting": req_fetch_posting, "build": req_build, "stage_tier_a": req_stage_tier_a,
                    "more": req_more, "import_network": req_import_network, "rebuild_queue": req_rebuild_queue}


def make_request(root, op, args):
    """Claude's side: validate and write one request file, create-only."""
    if op not in OPS:
        raise RequestError(f"unknown op {op}; one of {', '.join(OPS)}")
    run = argparse.Namespace(root=Path(root), folder=jobfs.Folder(root))
    clean = {}
    for k, v in (args or {}).items():
        if k not in OPS[op]:
            raise RequestError(f"{op} takes no argument {k!r}")
        clean[k] = check_arg(run, op, k, OPS[op][k], v)
    for _ in range(5):
        req_id = events.new_id(root)
        req = {"v": 1, "id": req_id, "op": op, "args": clean}
        try:
            path = jobfs.create_only(jobfs.Folder(root).request(req_id, op), req)
            return req_id, path
        except FileExistsError:
            continue
    raise RequestError("could not find a free request name")


# ---------- selftest ----------

def selftest(run, net=False):
    checks = []

    def report(name, ok, detail):
        checks.append((name, ok))
        run.out(f"{'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())

    v = sys.version_info
    report("python", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 11) else " (needs 3.11+; 3.13 is installed by the installer)"))
    missing = []
    for mod, pkg in (("openpyxl", "openpyxl"), ("docx", "python-docx"), ("reportlab", "reportlab"),
                     ("pypdf", "pypdf")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    report("packages", not missing, "missing " + ", ".join(missing) if missing else "openpyxl python-docx reportlab pypdf")
    try:
        from zoneinfo import ZoneInfo
        tzname = jobfs.settings(run.root)["time"]["timezone"]
        ZoneInfo(tzname)
        report("tzdata", True, tzname)
    except Exception as e:  # ZoneInfoNotFoundError and friends
        report("tzdata", False, f"{type(e).__name__}: install tzdata")
    try:
        probe = run.folder.runner_out / f".selftest_{os.getpid()}.tmp"
        jobfs.atomic_write_text(probe, "ok\n")
        ok = probe.read_text(encoding="utf-8") == "ok\n"
        probe.unlink()
        report("folder", ok, f"write {run.folder.runner_out}")
    except OSError as e:
        report("folder", False, f"cannot write {run.folder.runner_out}: {e}")
    report(*robots_check(run, net))
    report(*build_check(run))
    recruitee = None
    if net:
        f = run.fetcher()
        for name, method, url in SELFTEST_HOSTS:
            report(*reach(f, f"reach {name}", method, url))
        name, ok, detail = reach(f, "reach recruitee", "GET", SELFTEST_RECRUITEE, want="offers")
        report(name, ok, detail)
        recruitee = "PASS" if ok else "FAIL"
    failed = [n for n, ok in checks if not ok]
    st = {"at": jobfs.now_iso(run.root), "checks": {n: ("PASS" if ok else "FAIL") for n, ok in checks}}
    if net:
        st["net"] = "FAIL" if any(n.startswith("reach") for n in failed) else "PASS"
        st["recruitee"] = recruitee
    else:
        st["net"] = run.status().get("selftest", {}).get("net", "not run")
        st["recruitee"] = run.status().get("selftest", {}).get("recruitee")
    try:
        run.write_status(selftest=st)
    except OSError:
        pass
    run.out(f"SELFTEST {'PASS' if not failed else 'FAIL ' + ' '.join(failed)}")
    return 1 if failed else 0


def robots_check(run, net):
    """A robots.txt fetch through netfetch (offline fixture or live with --net);
    without either, the parser on a built-in file."""
    import netfetch
    if net or run.offline:
        try:
            f = run.fetcher()
            r = f.get(SELFTEST_ROBOTS, accept="text/plain")
            return "robots", True, f"{SELFTEST_ROBOTS} HTTP {r.status}"
        except netfetch.Denied as e:
            return "robots", False, f"DENY {e.reason}"
        except netfetch.FetchError as e:
            return "robots", False, f"{SELFTEST_ROBOTS}: {str(e)[:120]}"
    try:
        rules = netfetch.RobotRules("User-agent: *\nDisallow: /private\nAllow: /private/jobs\n")
        ok = rules.allowed("/jobs") and not rules.allowed("/private/x") and rules.allowed("/private/jobs/1")
        return "robots", ok, "parser (no network: run selftest --net for a live fetch)"
    except Exception as e:  # an API change in netfetch
        return "robots", False, f"parser: {type(e).__name__}: {e}"


def build_check(run):
    import tempfile
    try:
        import build_resume
    except ImportError as e:
        return "build", False, f"cannot load build_resume: {e}"
    tmp = Path(tempfile.mkdtemp(prefix="jobrunner_selftest_"))
    try:
        src = tmp / "sample.json"
        src.write_text(json.dumps(SAMPLE_RESUME), encoding="ascii")
        out = []
        code = build_resume.main([str(src), "--out", str(tmp), "--base", "sample"], emit=out.append)
        ok = code == 0 and (tmp / "sample.pdf").exists()
        return "build", ok, "sample resume to PDF" if ok else f"exit {code}: " + "; ".join(out[-2:])
    except Exception as e:  # a broken package must give a FAIL line, never a crash
        return "build", False, f"{type(e).__name__}: {str(e)[:120]}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reach(f, name, method, url, want=None):
    import netfetch
    try:
        if method == "POST":
            r = f.post_json(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        else:
            r = f.get(url, accept="application/json")
        ok = r.status < 500 and (want is None or (r.ok and want in r.text()[:200000]))
        return name, ok, f"{netfetch.host_of(url)} HTTP {r.status}"
    except netfetch.Denied as e:
        return name, False, f"DENY {e.reason}"
    except netfetch.FetchError as e:
        return name, False, f"{netfetch.host_of(url)}: {str(e)[:120]}"


# ---------- status ----------

def show_status(run):
    st = run.status()
    if not st:
        print("STATUS none (the runner has not run in this folder)")
        return 0
    last = st.get("last_daily") or {}
    print(f"STATUS mode={st.get('mode', '?')} version={st.get('version', '?')} "
          f"last_daily={last.get('at', 'never')} ok={last.get('ok', '-')} last_tick={st.get('last_tick') or 'never'}")
    c = last.get("counts") or {}
    if c:
        print("COUNTS " + " ".join(f"{k}={c[k]}" for k in ("queue", "pool_left", "discards", "tier_b_built",
                                                          "prep_failed") if k in c))
    if st.get("tracker_locked_since"):
        print(f"TRACKER LOCKED since {st['tracker_locked_since']}")
    if st.get("daily_pending"):
        print(f"DAILY WAITING {st['daily_pending']}")
    sel = st.get("selftest") or {}
    if sel:
        print(f"SELFTEST {sel.get('at', '?')} net={sel.get('net', '-')}")
    for b in st.get("boards_paused") or []:
        print(f"PAUSED {b}")
    for e in st.get("errors") or []:
        print(f"ERROR {e}")
    return 0


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="jobrunner", description="The job finder on the user's PC (spec 2.2).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, hlp in (("daily", "The 05:30 run: discovery, queue, Tier B, staging, reports."),
                      ("tick", "The 15-minute run: events, requests, uploads, liveness."),
                      ("selftest", "PASS/FAIL checks of this install."),
                      ("status", "Print runner/out/status.json."),
                      ("rebuild-queue", "Build today's queue again (waits while a session is active).")):
        p = sub.add_parser(name, help=hlp, allow_abbrev=False)
        p.add_argument("--root", help="the JobSearch folder (default: config.toml in %%LOCALAPPDATA%%\\JobRunner)")
        if name != "status":
            p.add_argument("--mode", choices=["L", "S", "l", "s"], help="L on the user's PC (default), S in the sandbox")
            p.add_argument("--offline", help="a netfetch fixture folder instead of the network (tests)")
            p.add_argument("--date", help="run as of this date (tests)")
        if name == "selftest":
            p.add_argument("--net", action="store_true", help="also check reach to the job APIs")
        p.set_defaults(cmd=name)
    p = sub.add_parser("request", help="Write a request file for the runner (Claude's side).", allow_abbrev=False)
    p.add_argument("op", choices=sorted(OPS))
    p.add_argument("--root")
    p.add_argument("--json", default="{}", help="the request arguments as a JSON object")
    p.set_defaults(cmd="request")
    return ap


def resolve_root(args):
    if not args.root:
        args.root = home_config().get("folder") or ""
    if not args.root or not Path(args.root).is_dir():
        print(f"ERROR --root {args.root or '(none)'} is not a folder")
        return False
    return True


def cmd_request(args):
    try:
        req_args = json.loads(args.json)
        if not isinstance(req_args, dict):
            raise ValueError("not an object")
        req_id, path = make_request(args.root, args.op, req_args)
    except ValueError as e:
        print(f"ERROR --json: {e}")
        return 1
    except RequestError as e:
        print(f"ERROR {e}")
        return 1
    print(f"REQUEST {req_id} {args.op}")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if not resolve_root(args):
        return 1
    if args.cmd == "request":
        return cmd_request(args)
    try:
        run = Run(args, args.cmd)
    except (ValueError, jobfs.ConfigError) as e:
        print(f"ERROR {e}")
        return 1
    if args.cmd == "status":
        return show_status(run)
    lock = take_lock(run)
    if lock is None:
        run.flush_log()
        return EXIT_LOCKED
    try:
        if args.cmd == "daily":
            return daily(run)
        if args.cmd == "tick":
            return tick(run)
        if args.cmd == "selftest":
            return selftest(run, args.net)
        return rebuild(run)
    except ImportError as e:
        run.out(f"MISSING {e.name or e}: run the installer again (pip install --require-hashes)")
        return EXIT_MISSING
    except (jobfs.ConfigError, ValueError) as e:
        run.out(f"ERROR {e}")
        return 1
    except Exception as e:  # unattended under pythonw: the log and status.json are all the user has
        import traceback
        run.out(f"ERROR {args.cmd} stopped: {type(e).__name__}: {str(e)[:200]}")
        for line in traceback.format_exc().splitlines():
            run.log(line)
        try:
            if args.cmd == "daily":
                run.write_status(last_daily={"at": jobfs.now_iso(run.root), "date": run.day.isoformat(), "ok": False,
                                             "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except OSError:
            pass
        return 1
    finally:
        lock.release()
        run.flush_log()


def rebuild(run):
    if session_active(run.root):
        run.out("WAIT a session is active (claude/session.json beat under 20 minutes); rebuild later")
        return 0
    ok, _ = req_rebuild_queue(run, {}, [])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
