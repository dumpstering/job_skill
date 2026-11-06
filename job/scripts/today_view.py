"""The day's card and the next item, printed for the session (spec 1.2, 5.1, 7.8).

Usage:
    python today_view.py card     --root R [--date YYYY-MM-DD] [--short]
    python today_view.py next     --root R [--date YYYY-MM-DD] [--short]
    python today_view.py progress --root R [--date YYYY-MM-DD]
    python today_view.py diy      --root R [--date YYYY-MM-DD]
    python today_view.py drafts   --root R [--date YYYY-MM-DD] [--n N]

The day's items are queue/<date>/queue.json, its addenda, and the jobs from
links (/job <link>, /job add <link>) whose added event the runner has not
written into addendum_user.json yet (events.user_items), so a job added a
minute ago is in card, next and progress at once.

card prints the 1.2 card word for word from those items,
claude/<date>/morning.json (urgent and "while you were away" lists, the
questions' saved flags, drafts, referral drafts, weekly lines), the fill
plans, the events overlay and runner/out/status.json:
    Thursday, November 12
    Yesterday: 21 applications sent, 2 people contacted.
    Needs you: Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply).
    Ready: 30. No account needed for 24; 2 new Workday sites come after #10 (about 5 minutes each).
    Questions (answer any time in one reply; 5 jobs wait for them):
    1. Have you worked in a BSL-2 lab? Where, and about how long? (3 jobs)
    Sensitive answers today: salary $95,000-$110,000 on #3, #7; "no sponsorship needed" on all. Reply yes, or name the ones to leave blank.
    3 people you could ask about a job: drafts after #10.
    Starting #1 now. Say "short" any time for an easy day.
A line whose count is zero never prints; "Needs you" prints only when
something is time-sensitive. Questions drop out once the bank answers them
or their jobs are done; a job whose field has a morning draft is not asked
(the draft is used). The sensitive line comes from the fill plans of the
jobs not done, leaves out the jobs claude/<date>/sensitive_ok.json already
settles, says "all" or "all but #2" when it can, and never cuts a list (past
6 numbers it gives the count). A free-text draft gets one line with the
start of its text and (say "drafts" for the full text). One-line notes join
when they apply: no job finder installed (no runner/out/status.json, or
mode M: the line says to type /job setup, setup.md sitting 2), the PC job
finder not running (mode S, or no daily for 26 hours), this morning's run
stopped early or reached no job site (mode L: a tick starts it again within
30 minutes, while it still may, else the line says to double-click "Run job
finder now"), a config file that
does not parse (the last good copy is in use), the workbook open for 2
hours, jobs waiting for a base resume's ok (named, with how many wait), a
refused tracker change, follow-ups ready, a posting set aside for text
addressed to an AI. Without this morning's prep (no morning.json, one with
"prepared": false, or one older than queue.json) the card takes its
questions from the fill plans and says the prep did not run, until the
tier_a.done marker exists. The "while you were away", weekly and catch-up
lines print only before the day's first item; the referral line only before
#batch_size. In mode L with no list for today while a daily that started
under 2 hours ago runs (status.json daily_running), the last line is "Today's
list is being built; say card again in a few minutes." and the double-click
line is left out. --short prints the easy day's card (spec 1.3): one line, the
sensitive line for the quick items when any of them still needs it, and the
first quick item. The card is checked with report.check_wording; a banned
word (from data) goes to stderr as WARN.

next prints the handover data of the first item to serve, by the same rule
as the card's "Starting #N": an item in progress (started, handed_over; it
waits for the user's s), then unparked items, then the rest in number order.
Without this morning's prep, Tier A items whose Tier A build is not ready
come after the others. Items with no resume (prep_failed, or nothing built)
wait: one last WAIT line counts them by family.
    NEXT #02 <job key>
    COMPANY / ROLE / ATS <ats> (attach first|last) / OPEN <url> / FOLDER <path>
    UPLOAD <path> (<what>; sha256 ok, else the job-folder file)
    LETTER <path> (<what>)                     when a cover letter is built
    RESUME <status> / ANSWERS <status> [needs ...] [sensitive ...]
    SENSITIVE enter ... | leave blank ...      when the job has sensitive fields
    DRAFT <label>                              when a morning draft fills a field
    ATTACH <the attach-first prompt>           attach-first sites
    HANDOVER lines (5.7)
The resume is the job folder's final.json "resume" (a strict Tier A build
that passed every check), else resume_B.pdf; the upload copy is checked by
sha256, and a copy that differs (an earlier day's file under today's name)
is never handed over: the job-folder file is named instead, as is a file
not staged in upload/ yet. The
SENSITIVE line and the handover's Check line read claude/<date>/
sensitive_ok.json ({v, date, ok: [n...] or "all", blank: [n...]}): a job
confirmed today has its values entered and no Check line; otherwise the
fields stay blank and the Check line says so. The Check line also shows each
computed answer of the fill plan (status check, such as years of a technique:
'<label> "<answer>" (<the arithmetic>)'). NEXT none when nothing is left
to serve. --short (an easy day, spec 1.3) takes only no-account items,
easiest ATS first (Greenhouse, Lever, Ashby, Workable, BambooHR, Breezy,
Teamtailor, Rippling, Pinpoint, then the rest), an item already started or
handed over before them, and passes over items with an open question, a
sensitive field other than the profile's salary and sponsorship that
sensitive_ok.json does not settle today, or no resume yet; those get no
event and stay for a normal day.

drafts prints the free-text drafts of claude/<date>/morning.json in full
(DRAFT #<n> (<label>), then the text), or DRAFTS none.

progress prints PROGRESS applied=<n> handed_over=<n> skipped=<n> later=<n>
parked=<n> blocked=<n> left=<n> of <n>, then the closing line
("Today: 8 applications sent." plus people contacted when nonzero, or
"Today: no applications sent yet.").

diy prints the do-yourself list in the order the runner stages it (LinkedIn
leads given up, then chat-apply pool items; at most easy_apply_list_max, no
links), leaving out the ones a "By hand" tracker row already logs:
    DIY E1 | <company> | <title> | <location> | upload/E1_... (when staged)
Stdlib only.

Exit codes: 0 ok, 1 bad input (ERROR ...).
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402
import jobfs  # noqa: E402

FINAL = {"applied", "skipped", "later", "blocked"}
IN_PROGRESS = {"started", "handed_over"}
# A job whose question is settled: done for today, filled, or unparked by an answer.
ANSWERED = FINAL | {"handed_over", "unparked"}
LOCK_LINE_HOURS = 2
RUNNER_STALE_HOURS = 26
ATS_NAMES = {"workday": "Workday", "oracle": "Oracle", "icims": "iCIMS", "successfactors": "SuccessFactors",
             "taleo": "Taleo", "adp": "ADP", "dayforce": "Dayforce", "phenom": "Phenom", "ukg": "UKG",
             "usajobs": "USAJOBS", "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
             "workable": "Workable", "bamboohr": "BambooHR", "breezy": "Breezy", "jazzhr": "JazzHR",
             "teamtailor": "Teamtailor", "rippling": "Rippling", "pinpoint": "Pinpoint", "paylocity": "Paylocity",
             "jobvite": "Jobvite", "personio": "Personio", "recruitee": "Recruitee"}
LAST_ATTACH = {"last"}
# The profile's standard sensitive answers: a short day keeps jobs whose only sensitive fields are these.
STANDARD_SENSITIVE = {"salary", "sponsorship"}
CATCH_UP_NOTE = ("This morning's prep did not run yet, so I'll read the inbox first and show what is urgent; "
                 "full tailoring for the Tier A jobs comes at the first break.")
CATCH_UP_TIER_A = "This morning's prep did not finish, so full tailoring for the Tier A jobs comes at the first break."
# No runner/out/status.json (any mode), or mode M with one (installed, not switched on): setup.md sitting 2.
NOT_INSTALLED = ("The job finder is not installed on your PC yet: type /job setup and I'll walk you through it "
                 "(about 10 minutes).")
NOT_SWITCHED_ON = ("The job finder on your PC is installed but not switched on yet: type /job setup and I'll "
                   "finish it (about 2 minutes).")
DRAFT_POINTER = '(say "drafts" for the full text)'
SENSITIVE_ASK = "Reply yes, or name the ones to leave blank."
SHORT_HEAD = "Short day: I'll do the quick ones."
# Mode L, no list for today yet, a daily started under 2 hours ago (status.json daily_running).
BUILDING = "Today's list is being built; say card again in a few minutes."
BUILDING_HOURS = 2
DAILY_STARTS_MAX = 3   # the starts a tick makes per day (jobrunner.DAILY_STARTS_MAX)
STOPPED_RETRY = "This morning's job finder run stopped early; it starts again within 30 minutes."
STOPPED_MANUAL = "This morning's job finder run stopped early. Double-click 'Run job finder now' on the desktop."
OFFLINE_RETRY = "The job sites could not be reached this morning; the job finder tries again later today."
OFFLINE_MANUAL = ("The job sites could not be reached this morning. Double-click 'Run job finder now' on the "
                  "desktop once the internet works.")
MAX_LISTED = 6   # job numbers a sensitive line lists before it gives a count


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- reading ----------

def read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def verified(path, default):
    """A runner JSON, hash-checked, retried once, then its .prev.json (2.7.6)."""
    try:
        data = jobfs.read_json_verified(path, retries=1, wait=2.0)
        return data if isinstance(data, type(default)) else default
    except jobfs.NotReady:
        return default


def queue(root, day):
    """(items in number order, account_block) from queue.json, its addenda and
    the jobs from links not yet in addendum_user.json (added events)."""
    qd = jobfs.Folder(root).queue_dir(day)
    items, block = [], {}
    paths = [qd / "queue.json"] + (sorted(x for x in qd.glob("addendum_*.json") if not x.name.endswith(".prev.json")) if qd.exists() else [])
    for p in paths:
        if not p.exists():
            continue
        data = verified(p, {})
        items += [it for it in data.get("items") or [] if isinstance(it, dict)]
        if p.name == "queue.json":
            block = data.get("account_block") or {}
    keys = {it.get("job_key") for it in items}
    items += [it for it in events.user_items(root, day) if it["job_key"] not in keys]
    if any(it.get("added_by") for it in items):
        import jobqueue  # their resume and plan are the session's, built around the add: read them again
        for it in items:
            if it.get("added_by"):
                jobqueue.refresh_user_item(root, it)
    return sorted(items, key=lambda it: int(it.get("n") or 0)), block


def states(root, day):
    """{n: state} and {job_key: state} from the events overlay."""
    view = events.overlay(root, day)
    by_n, by_key = {}, {}
    for it in view["items"].values():
        if it.get("n") is not None:
            by_n[it["n"]] = it.get("state")
        if it.get("job_key"):
            by_key[it["job_key"]] = it.get("state")
    return by_n, by_key, view


def state_of(it, by_n, by_key):
    return by_n.get(it.get("n")) or by_key.get(it.get("job_key"))


def morning(root, day):
    """claude/<date>/morning.json, or None when job-morning has not written it."""
    path = jobfs.Folder(root).morning_json(day)
    if not path.exists():
        return None
    return read_json(path, {})


def morning_fresh(root, day, m):
    """True when this morning's prep ran on today's list: morning.json exists,
    is not marked "prepared": false (job-morning ran before the list was
    built), and is no older than queue.json (a list built after the prep has
    questions the prep never saw)."""
    if m is None or m.get("prepared") is False:
        return False
    f = jobfs.Folder(root)
    try:
        return f.morning_json(day).stat().st_mtime >= f.queue_json(day).stat().st_mtime
    except OSError:
        return True  # no list yet: nothing for the prep to be stale against


def tier_a_prepped(root, day, m):
    """This morning's Tier A step ran (the prep, or the catch-up at the break)."""
    return morning_fresh(root, day, m) or jobfs.Folder(root).day_marker(day, "tier_a").exists()


def open_store(root):
    """The tracker view (snapshot plus overlay in mode L); None when unreadable here."""
    try:
        import caps
        return caps.open_store(root)
    except Exception:  # no tracker yet, or openpyxl missing in mode M: the card goes on
        return None


def to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def clean(text, limit=160):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def numbers(ns):
    return ", ".join(f"#{n}" for n in sorted(ns))


# ---------- the job folder's files ----------

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def final_files(folder):
    """(resume, letter) named by the job folder's final.json, the record a strict
    Tier A build writes only when every check passed: {"v": 1, "resume":
    "<file name>", "letter": "<file name>" or null, "built_at", "verdict": "ok"}.
    None for a file that is not named, not a plain file name, or missing."""
    folder = Path(folder)
    data = read_json(folder / "final.json", {})
    if data.get("v") != 1 or str(data.get("verdict", "")).lower() != "ok":
        return None, None

    def one(name):
        name = str(name or "")
        if not name or "/" in name or "\\" in name or name in (".", "..") or Path(name).name != name:
            return None
        p = folder / name
        return p if p.is_file() else None
    return one(data.get("resume")), one(data.get("letter"))


def letter_name(upload_rel):
    """upload/NNc_<Last>_Cover_<Employer>.pdf beside the item's resume name (2.5)."""
    name = Path(str(upload_rel or "")).name
    if not name:
        return ""
    return re.sub(r"^(\d{2})_", r"\1c_", name).replace("_Resume_", "_Cover_")


def upload_line(root, it):
    """(path text, note) for the resume the user attaches: the Tier A file that
    final.json names, else resume_B.pdf; the upload copy is checked by sha256."""
    f = jobfs.Folder(root)
    up = root / str(it.get("upload", ""))
    folder_rel = str(it.get("folder", ""))
    folder = root / folder_rel
    final, _ = final_files(folder)
    if final is not None:
        want = sha256_of(final)
        if up.is_file() and sha256_of(up) == want:
            staged = read_json(f.runner_out / "staged.json", {}).get("files", {}).get(up.name, {})
            ok = "sha256 ok" + ("" if not staged or staged.get("sha256") == want else ", staged record differs")
            return it.get("upload"), f"Tier A file, {ok}"
        return f"{folder_rel}/{final.name}", "Tier A file in the job folder (the upload copy is not it yet)"
    if it.get("added_by") and not (folder / "resume_B.pdf").exists():
        import jobqueue  # a job from a link with no Tier B file: the resume written for it, in its folder
        built = jobqueue.user_resume(folder)
        if built is None:
            return "", "no built resume yet"
        return f"{folder_rel}/{built.name}", "the resume written for this job, in its folder"
    if it.get("resume_status") == "prep_failed":
        return "", "no resume yet: its base resume needs the user's ok"
    if up.is_file():
        src = folder / "resume_B.pdf"
        same = src.exists() and sha256_of(src) == sha256_of(up)
        if src.exists() and not same:
            # an earlier day's file under today's name, or one changed by hand: never attach it for this job
            return f"{folder_rel}/resume_B.pdf", "the upload copy is not this job's file; attach it from the job folder"
        note = "Tier B file" + (", sha256 ok" if same else ", not checked: no resume_B.pdf in the job folder")
        if it.get("tier") == "A":
            note += "; the Tier A resume is not ready, so this is the fallback"
        return it.get("upload"), note
    if (folder / "resume_B.pdf").exists():
        return f"{folder_rel}/resume_B.pdf", "not staged in upload yet; attach it from the job folder"
    return "", "no built resume yet"


def letter_line(root, it):
    """(path text, note) for a built cover letter (final.json's letter, else
    cover_B.pdf), or ("", "") when the job has none."""
    folder_rel = str(it.get("folder", ""))
    folder = root / folder_rel
    final, final_letter = final_files(folder)
    cover_b = folder / "cover_B.pdf"
    src = final_letter or (cover_b if cover_b.is_file() else None)
    if src is None:
        return "", ""
    what = "Tier A letter" if final_letter else "Tier B letter"
    cname = letter_name(it.get("upload"))
    up = jobfs.Folder(root).upload / cname if cname else None
    if up is not None and up.is_file() and sha256_of(up) == sha256_of(src):
        return f"upload/{cname}", f"{what}, sha256 ok"
    return f"{folder_rel}/{src.name}", f"{what} in the job folder (not staged in upload yet)"


def has_resume(root, it, st=None):
    """True when the item can be served now: a resume to attach, a job from a
    link (the session writes its resume), or an item already in progress."""
    return st in IN_PROGRESS or bool(it.get("added_by")) or bool(upload_line(root, it)[0])


def tier_a_ready(root, it):
    return final_files(root / str(it.get("folder", "")))[0] is not None


# ---------- sensitive answers ----------

def sensitive_ok(root, day):
    """(ok, blank) from claude/<date>/sensitive_ok.json: ok is "all" or a set of
    item numbers, blank a set; empty when the file is missing or another day's."""
    data = read_json(jobfs.Folder(root).claude / jobfs.iso_date(day) / "sensitive_ok.json", {})
    if str(data.get("date", "")) != jobfs.iso_date(day):
        return set(), set()

    def nums(v):
        out = set()
        for x in v if isinstance(v, list) else []:
            if isinstance(x, int) and not isinstance(x, bool):
                out.add(x)
            elif isinstance(x, str) and x.strip().lstrip("#").isdigit():
                out.add(int(x.strip().lstrip("#")))
        return out
    ok = data.get("ok")
    return ("all" if str(ok).lower() == "all" else nums(ok)), nums(data.get("blank"))


def settled(n, confirmed):
    ok, blank = confirmed
    return ok == "all" or n in ok or n in blank


def plan_sensitive(root, it):
    """[(kind, answer, source)] for the item's sensitive_confirm fields, from its fill plan."""
    path = root / str(it.get("folder", "")) / "fill_plan.json"
    plan = verified(path, {}) if path.exists() else {}
    fields = [f for f in plan.get("fields") or [] if isinstance(f, dict) and f.get("status") == "sensitive_confirm"]
    if not fields:
        return []
    import jobqueue  # the kinds the runner records (salary, sponsorship, ...)
    out, seen = [], set()
    for f in fields:
        kind = jobqueue.sensitive_kind(f)
        if kind and kind not in seen:
            seen.add(kind)
            out.append((kind, str(f.get("answer") or ""), str(f.get("answer_source") or "")))
    return out


def sensitive_values(root, it):
    """{kind: answer} from the item's fill plan, for its sensitive_confirm fields."""
    return {k: a for k, a, _ in plan_sensitive(root, it)}


def plan_checks(root, it):
    """The fill plan's computed answers (status "check" with an answer, such as
    years of a technique) as '<label> "<answer>" (<the arithmetic, else the
    source>)': the handover's Check line shows them before Submit (spec 4.6)."""
    path = root / str(it.get("folder", "")) / "fill_plan.json"
    plan = verified(path, {}) if path.exists() else {}
    out = []
    for f in plan.get("fields") or []:
        if not isinstance(f, dict) or f.get("status") != "check" or not str(f.get("answer") or "").strip():
            continue
        why = clean(f.get("note") or f.get("answer_source"), 160)
        out.append(f'{clean(f.get("label"), 50)} "{clean(f.get("answer"), 60)}"' + (f" ({why})" if why else ""))
    return out


def standard_only(root, it):
    """True when every sensitive field of the job is the profile's own salary or
    sponsorship answer (a short day keeps these jobs)."""
    fields = plan_sensitive(root, it)
    if not fields:
        return set(str(s) for s in it.get("sensitive") or []) <= STANDARD_SENSITIVE
    return all(k in STANDARD_SENSITIVE and src == f"profile:{k}" for k, _, src in fields)


def kind_order(kind):
    k = str(kind).lower()
    return (0 if "salary" in k else 1 if "sponsor" in k else 2, k)


def where_text(ns, ref):
    """'all', 'all but #2', '#3, #7' or '14 jobs': never a cut list."""
    if ns == ref and len(ns) > 1:
        return "all"
    rest = ref - ns
    if len(ns) > 1 and rest and len(rest) <= 3 and len(rest) < len(ns):
        return f"all but {numbers(rest)}"
    if len(ns) <= MAX_LISTED:
        return numbers(ns)
    return plural(len(ns), "job")


def sensitive_text(root, items):
    """'salary $95,000-$110,000 on #3, #7; "no sponsorship needed" on all' for the given items."""
    groups, ref = {}, set()
    for it in items:
        n = int(it.get("n") or 0)
        for kind, answer, _ in plan_sensitive(root, it):
            groups.setdefault((kind, answer), set()).add(n)
            ref.add(n)
    parts = []
    for (kind, answer), ns in sorted(groups.items(), key=lambda kv: (kind_order(kv[0][0]), kv[0][1])):
        k = str(kind).replace("_", " ").strip()
        a = clean(answer, 60)
        if "sponsor" in k.lower() and a.lower() in ("no", "yes"):
            text = '"no sponsorship needed"' if a.lower() == "no" else '"sponsorship needed"'
        elif "salary" in k.lower():
            text = f"salary {a}"
        else:
            text = f'{k} "{a}"'
        parts.append(f"{text} on {where_text(ns, ref)}")
    return "; ".join(parts)


def sensitive_line(root, day, items, by_n, by_key):
    """The card's one sensitive line, for the jobs not done that today's
    sensitive_ok.json does not settle yet; None when there are none."""
    confirmed = sensitive_ok(root, day)
    todo = [it for it in items if state_of(it, by_n, by_key) not in FINAL
            and not settled(int(it.get("n") or 0), confirmed)]
    text = sensitive_text(root, todo).rstrip(".")
    return f"Sensitive answers today: {text}. {SENSITIVE_ASK}" if text else None


def sensitive_fill(root, it, confirmed):
    """(SENSITIVE line, Check text) for one job: entered when confirmed today
    (no Check line), else left blank and the Check line says so (5.7)."""
    kinds = [str(s) for s in it.get("sensitive") or [] if str(s)]
    if not kinds:
        return "", ""
    ok, blank = confirmed
    n = int(it.get("n") or 0)
    if n in blank:
        return f"SENSITIVE leave blank {', '.join(kinds)} (the user asked)", f"{', '.join(kinds)} left blank, as you asked"
    if ok == "all" or n in ok:
        values = sensitive_values(root, it)
        shown = ", ".join(f'{k} "{clean(values[k], 60)}"' if values.get(k) else k for k in kinds)
        return f"SENSITIVE enter {shown} (confirmed today)", ""
    return (f"SENSITIVE leave blank {', '.join(kinds)} (not confirmed today)",
            f"{', '.join(kinds)} left blank (not confirmed today)")


# ---------- drafts ----------

def drafts_of(m):
    return [d for d in (m or {}).get("drafts") or [] if isinstance(d, dict) and str(d.get("text") or "").strip()]


def drafted(m):
    """{(n, question id)} of the morning's drafts: those fields are filled from the draft, not asked."""
    out = set()
    for d in drafts_of(m):
        try:
            n = int(d.get("n"))
        except (TypeError, ValueError):
            continue
        if d.get("question_id"):
            out.add((n, str(d["question_id"])))
    return out


def draft_for(m, n):
    return next((d for d in drafts_of(m) if str(d.get("n")) == str(n)), None)


# ---------- the order items are served in ----------

def serving(root, day, items, by_n, by_key, m, short=False):
    """(order, waiting): [(item, state)] in the order next serves them (the card's
    "Starting #N" is the first), and the items that wait for a resume.
    In progress first, then unparked, then number order; without this
    morning's prep, Tier A items with no Tier A build yet come after the rest."""
    prepped = tier_a_prepped(root, day, m)
    order, waiting = [], []
    for it in items:
        st = state_of(it, by_n, by_key)
        if st in FINAL or st == "parked":
            continue
        if not has_resume(root, it, st):
            waiting.append(it)
            continue
        defer = not prepped and it.get("tier") == "A" and not it.get("added_by") and not tier_a_ready(root, it)
        rank = 0 if st in IN_PROGRESS else 1 if st == "unparked" else 2
        order.append(((rank, 1 if defer else 0, int(it.get("n") or 0)), it, st))
    order = [(it, st) for _, it, st in sorted(order, key=lambda x: x[0])]
    if short:
        order = short_order(root, order, sensitive_ok(root, day))
    return order, waiting


def short_order(root, cands, confirmed):
    """The easy day (spec 1.3): an item already started or handed over first,
    then no-account items by ATS effort. Passed over (no event: they stay):
    an open question, a sensitive field other than the profile's salary and
    sponsorship that today's sensitive_ok.json does not settle, no resume."""
    import jobqueue  # ATS_PROFILES effort: Greenhouse 1 ... Pinpoint 9, the rest after
    keep = []
    for it, st in cands:
        n = int(it.get("n") or 0)
        if st not in IN_PROGRESS:
            if it.get("account") != "none" or it.get("needs") or it.get("answers_status") == "needs_you" \
                    or (it.get("sensitive") and not settled(n, confirmed) and not standard_only(root, it)) \
                    or not upload_line(root, it)[0]:
                continue
        keep.append((0 if st in IN_PROGRESS else 1, jobqueue.profile_of(it.get("ats")).get("effort", 99), n, it, st))
    return [(it, st) for *_, it, st in sorted(keep, key=lambda k: k[:3])]


def closed_keys(root):
    live = read_json(jobfs.Folder(root).runner_out / "liveness.json", {}).get("items", {})
    return {k for k, v in live.items() if isinstance(v, dict) and v.get("closed")}, live


# ---------- the card's lines ----------

def yesterday_line(store, day):
    if store is None:
        return None
    y = day - timedelta(days=1)
    sent = sum(1 for r in store.app_rows() if to_date(r.get("Applied")) == y)
    people = 0
    if getattr(store, "referrals", None) is not None:
        people = sum(1 for r in store.referrals.rows() if to_date(r.get("Date")) == y
                     and str(r.get("Status") or "") not in ("", "Drafted"))
    parts = []
    if sent:
        parts.append(f"{plural(sent, 'application')} sent")
    if people:
        parts.append(f"{plural(people, 'person', 'people')} contacted")
    return f"Yesterday: {', '.join(parts)}." if parts else None


def away_lines(m):
    away = [clean(x.get("text") if isinstance(x, dict) else x) for x in (m or {}).get("away") or []]
    away = [a for a in away if a]
    return (["While you were away:"] + [f"- {a}" for a in away]) if away else []


def needs_line(m):
    urgent = [clean(x.get("text") if isinstance(x, dict) else x, 200) for x in (m or {}).get("urgent") or []]
    urgent = [u.rstrip(".") for u in urgent if u]
    return f"Needs you: {'; '.join(urgent)}." if urgent else None


def ready_line(items, block, by_n, by_key, root=None):
    left = [it for it in items if state_of(it, by_n, by_key) not in FINAL
            and (root is None or has_resume(root, it, state_of(it, by_n, by_key)))]
    if not left:
        return None
    no_acct = sum(1 for it in left if it.get("account") == "none")
    line = f"Ready: {len(left)}."
    if no_acct == len(left):
        line += " No account needed for any of them."
    elif no_acct:
        line += f" No account needed for {no_acct}"
    tenants = [t for t in block.get("tenants") or []
               if any(it.get("account") == "new" and (it.get("host") == t or t in str(it.get("apply_url")))
                      for it in left)]
    if tenants:
        kinds = []
        for t in tenants:
            it = next((x for x in left if x.get("host") == t or t in str(x.get("apply_url"))), {})
            name = ATS_NAMES.get(str(it.get("ats", "")).lower(), "account")
            if name not in kinds:
                kinds.append(name)
        what = f"{kinds[0]} " if len(kinds) == 1 else "account "
        n = len(tenants)
        verb = "comes" if n == 1 else "come"
        each = "about 5 minutes" if n == 1 else "about 5 minutes each"
        sep = "; " if no_acct and no_acct != len(left) else " "
        line += f"{sep}{n} new {what}site{'s' if n != 1 else ''} {verb} after #{block.get('after_n', 0)} ({each})."
    elif no_acct and no_acct != len(left):
        line += "."
    return line


def bank_answers(root):
    """A function telling whether answers.md now answers a question text; never raises."""
    try:
        import answers
        bank = answers.load_bank(jobfs.Folder(root).answers)
    except Exception:  # no bank yet
        return lambda text, also=(): False
    return lambda text, also=(): any(answers.find_answer(t, bank) for t in [text, *also] if t)


def question_lines(root, day, m, cap, by_n, by_key):
    """The day's batch: this morning's questions when its prep ran on today's
    list (their saved flags), else answers.py's from the fill plans. A job
    leaves a question when it is done, filled or unparked, or when a morning
    draft fills that field; a question leaves when the bank answers it or no
    job is left on it."""
    done = {int(it_n) for it_n, st in by_n.items() if st in ANSWERED and str(it_n).isdigit()}
    skip = drafted(m)
    answered = bank_answers(root)
    if morning_fresh(root, day, m) and "questions" in m:
        qs = [q for q in m.get("questions") or [] if isinstance(q, dict) and q.get("text")]
        waiting = int(m.get("waiting") or 0)
        live = False
    else:
        try:
            import answers
            res = answers.questions(root, day, 10 ** 6)
        except Exception:  # an unreadable plan never blocks the card
            res = {"questions": [], "waiting": 0}
        qs, waiting, live = res["questions"], res["waiting"], True
    shown, gone, kept = [], set(), set()
    for q in qs:
        jobs = q.get("jobs")
        listed = [int(n) for n in jobs if str(n).isdigit()] if isinstance(jobs, list) else []
        qid = str(q.get("id") or "")
        left = [n for n in listed if n not in done and (n, qid) not in skip]
        gone |= set(listed) - set(left)
        if answered(q["text"], q.get("also") or ()) or (listed and not left):
            continue
        kept |= set(left)
        shown.append(dict(q, jobs=left if isinstance(jobs, list) else jobs))
    if live:
        waiting = len(kept)
    else:
        waiting = max(len(kept), waiting - len(gone - kept))
    shown = shown[:cap]
    if not shown:
        return []
    head = "Questions (answer any time in one reply"
    head += f"; {plural(waiting, 'job')} wait{'s' if waiting == 1 else ''} for them):" if waiting else "):"
    out = [head]
    for i, q in enumerate(shown, 1):
        jobs = q.get("jobs")
        n = len(jobs) if isinstance(jobs, list) else int(jobs or 0)
        notes = []
        if q.get("all_jobs"):
            notes.append("all jobs")
        elif n:
            notes.append(plural(n, "job"))
        if q.get("saved"):
            notes.append("saved for next time")
        out.append(f"{i}. {clean(q['text'], 240)}" + (f" ({'; '.join(notes)})" if notes else ""))
    return out


def referral_line(root, day, m, settings):
    if not settings["referrals"].get("enabled", True):
        return None
    after = int(settings["daily"].get("batch_size", 10))
    if m is not None and "referral_drafts" in m:
        n = len(m.get("referral_drafts") or [])
    else:
        n = 0
        path = jobfs.Folder(root).matches_csv(day)
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                jobs = {r.get("job_key") for r in csv.DictReader(f) if str(r.get("tier", "")).strip() in ("1", "tier1")}
            n = min(len(jobs), int(settings["referrals"].get("asks_per_day", 3)))
        except OSError:
            n = 0
    if not n:
        return None
    return f"{plural(n, 'person', 'people')} you could ask about a job: drafts after #{after}."


def draft_lines(m, done):
    out = []
    for d in drafts_of(m):
        if str(d.get("n")).isdigit() and int(d.get("n")) in done:
            continue
        label = clean(d.get("label") or "answer", 60)
        out.append(f"Draft for #{d.get('n', '?')} ({label}): {clean(d['text'], 90)} {DRAFT_POINTER}")
    return out[:6]


def drafts(root, day, n=None):
    """The free-text drafts of morning.json in full: DRAFT #<n> (<label>), the text."""
    out = []
    for d in drafts_of(morning(root, day)):
        if n is not None and str(d.get("n")) != str(n):
            continue
        if out:
            out.append("")
        out.append(f"DRAFT #{d.get('n', '?')} ({clean(d.get('label') or 'answer', 60)})")
        out += [ln.rstrip() for ln in str(d["text"]).strip().splitlines()]
    return out or ["DRAFTS none"]


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def no_runner(status, settings):
    """True when no job finder runs: no runner/out/status.json, or mode M."""
    return not status or str(settings["runner"].get("mode", "L")).upper() == "M"


def daily_building(root, day, status, settings):
    """True in mode L while today's list does not exist and a daily that
    started under 2 hours ago is running (status.json daily_running)."""
    if str(settings["runner"].get("mode", "L")).upper() != "L" or jobfs.Folder(root).queue_json(day).exists():
        return False
    return daily_running(root, status)


def daily_running(root, status):
    """True while status.json daily_running names a daily started under 2 hours ago."""
    running = status.get("daily_running")
    try:
        at = datetime.fromisoformat(str(running.get("at"))) if isinstance(running, dict) else None
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        return False
    return timedelta(minutes=-5) < jobfs.now(root) - at < timedelta(hours=BUILDING_HOURS)


def starts_left(status, day):
    """True while a tick may still start today's daily again (3 starts a day)."""
    starts = status.get("daily_starts") or {}
    return str(starts.get("date")) != day.isoformat() or int(starts.get("count") or 0) < DAILY_STARTS_MAX


def runner_lines(root, day, status, settings):
    out = []
    mode = str(settings["runner"].get("mode", "L")).upper()
    last = (status.get("last_daily") or {}).get("at")
    when = None
    try:
        when = datetime.fromisoformat(last) if last else None
    except ValueError:
        when = None
    stale = when is None or (jobfs.now(root) - when) > timedelta(hours=RUNNER_STALE_HOURS)
    building = bool(status) and daily_building(root, day, status, settings)
    if not status:
        out.append(NOT_INSTALLED)
    elif mode == "M":
        out.append(NOT_SWITCHED_ON)
    elif building:
        pass  # the job finder is running now: the card's last line says the list is being built
    elif mode != "L" or stale:
        if when is not None:
            out.append(f"Your PC's job finder last ran {when:%a %H:%M}. "
                       "Double-click 'Run job finder now' on the desktop.")
        else:
            out.append("Your PC's job finder has not run yet. Double-click 'Run job finder now' on the desktop.")
    if status and mode == "L" and not building:
        daily = status.get("last_daily") or {}
        if daily.get("date") == day.isoformat() and daily.get("ok") is False and not daily_running(root, status):
            out.append(STOPPED_RETRY if starts_left(status, day) else STOPPED_MANUAL)
        if any(str(e).startswith(f"offline {day.isoformat()}") for e in status.get("errors") or []):
            out.append(OFFLINE_RETRY if starts_left(status, day) else OFFLINE_MANUAL)
    if status:
        if version_tuple(status.get("min_skill", "0")) > version_tuple(jobfs.VERSION):
            out.append("The job finder on your PC is newer than this skill: upload the new skill zip when you can.")
        elif version_tuple(status.get("version", jobfs.VERSION)) < version_tuple(jobfs.VERSION):
            out.append("Double-click the new install_runner.cmd to update the job finder on your PC.")
    since = status.get("tracker_locked_since")
    try:
        locked = since and (jobfs.now(root) - datetime.fromisoformat(since)) > timedelta(hours=LOCK_LINE_HOURS)
    except ValueError:
        locked = False
    if locked:
        out.append("When you're done looking at the spreadsheet, close it and I'll update it.")
    return out


def config_lines(root, settings):
    """One line per config file that does not parse (the user edits them in
    Notepad): the job finder and the skill use the last good copy meanwhile."""
    out = []
    for name, cfg in (("settings.toml", settings), ("search_profile.toml", None)):
        if cfg is None:
            try:
                cfg = jobfs.search_profile(root)
            except Exception:  # unreadable in a way the loader does not catch: say nothing here
                continue
        if not getattr(cfg, "error", ""):
            continue
        m = re.search(r"line (\d+)", str(cfg.error))
        where = f" near line {m.group(1)}" if m else ""
        using = "the last copy that worked" if getattr(cfg, "source", "") == "last_good" else "the default settings"
        out.append(f"Your {name} has a typo{where}, so I'm using {using}. Type /job settings to fix it.")
    return out


def family_label(root, fam):
    try:
        for f in jobfs.search_profile(root).get("family") or []:
            if isinstance(f, dict) and str(f.get("id", "")) == fam:
                return clean(f.get("label") or fam, 60)
    except Exception:  # an unreadable profile: the id is the label
        pass
    return fam


def variant_wait(root, day):
    """{family: jobs waiting} from queue.json counts.variant_wait: every job the
    builder held for a family's approval, queued or not (shared decision 2)."""
    data = verified(jobfs.Folder(root).queue_json(day), {}) if jobfs.Folder(root).queue_json(day).exists() else {}
    raw = (data.get("counts") or {}).get("variant_wait") or {}
    out = {}
    for fam, k in (raw.items() if isinstance(raw, dict) else []):
        try:
            if int(k) > 0:
                out[str(fam)] = int(k)
        except (TypeError, ValueError):
            continue
    return out


def approval_lines(root, waiting, held=None):
    """One line per family whose jobs wait for a base resume (shared decision 2):
    never made, never approved, or changed since its approval, with how many
    jobs wait (the queued ones, or the builder's count when larger); one line
    for jobs whose resume file is simply not built yet."""
    by_fam, unbuilt, usable = {}, 0, {}
    for it in waiting:
        fam = str(it.get("family") or "")
        if fam not in usable:
            usable[fam] = bool(fam) and approved_now(root, fam)
        # a Tier A item with no file at all lacks its Tier B fallback for the same reason
        if it.get("resume_status") == "prep_failed" or (it.get("tier") == "A" and fam and not usable[fam]):
            by_fam.setdefault(fam, []).append(it)
        else:
            unbuilt += 1
    counts = {fam: len(v) for fam, v in by_fam.items()}
    for fam, k in (held or {}).items():
        if fam not in usable:
            usable[fam] = approved_now(root, fam)
        if not usable[fam]:  # once approved, only the queued jobs still wait (for their build)
            counts[fam] = max(counts.get(fam, 0), k)
    out = []
    if unbuilt:
        out.append(f"{plural(unbuilt, 'job')} {'has' if unbuilt == 1 else 'have'} no resume file yet; "
                   f"the job finder makes {'it' if unbuilt == 1 else 'them'} on its next run.")
    folder = jobfs.Folder(root)
    for fam in sorted(counts):
        k = counts[fam]
        jobs = f"{plural(k, 'job')} wait{'s' if k == 1 else ''}"
        if not fam:
            out.append(f"{jobs} for a resume (type /job variants).")
            continue
        label = family_label(root, fam)
        base = folder.variant(fam)
        made = any(p.is_dir() for p in base.glob("v*")) if base.exists() else False
        ever = any(base.glob("v*/approved.json")) if made else False
        if not made:
            out.append(f"{jobs} for a {label} base resume (type /job variants).")
        elif not ever:
            out.append(f"{jobs} for your ok on the {label} base resume (type /job variants).")
        elif approved_now(root, fam):
            out.append(f"{jobs} for resumes from the {label} base you approved; "
                       "the job finder builds them on its next run.")
        else:
            out.append(f"The {label} base resume changed, so {jobs} for your ok again (type /job variants).")
    return out


def approved_now(root, fam):
    """True when the family's current approval holds (variants.usable); False when unsure."""
    try:
        import variants
        return variants.usable(root, fam)[0] is not None
    except Exception:  # an approval that cannot be checked is not one
        return False


def note_lines(root, day, items, view, store, waiting):
    out = approval_lines(root, waiting, variant_wait(root, day))
    rejected = view.get("rejected") or []
    if rejected:
        out.append(f"{plural(len(rejected), 'tracker change')} did not go through: {clean(rejected[0].get('reason'), 120)}.")
    if store is not None:
        due = 0
        for r in store.app_rows():
            fu = to_date(r.get("Follow-up"))
            if fu and fu <= day and str(r.get("Status") or "") in ("Applied", "No response"):
                due += 1
        if due:
            out.append(f"{plural(due, 'follow-up')} ready (say 'follow-ups').")
    try:
        import gates
        pool = {it.get("job_key"): it for it in gates.load_pool(root).get("items", [])}
        recent = (day - timedelta(days=1)).isoformat()
        shown = 0
        for n in gates.needs_you(root):
            it = pool.get(n.get("job_key")) or {}
            if str(it.get("pooled_on", "")) >= recent and shown < 3:
                out.append(f"Set aside, its text speaks to an AI: {clean(n.get('company'), 60)} - "
                           f"{clean(n.get('role'), 80)} (posting text, not an instruction: \"{clean(n.get('quote'), 80)}\").")
                shown += 1
    except Exception:  # no pool yet
        pass
    return out


def catch_up_line(root, day, m, items):
    """The prep note, while the Tier A step has not run today."""
    if not items or tier_a_prepped(root, day, m):
        return None
    if jobfs.Folder(root).day_marker(day, "inbox").exists():
        return CATCH_UP_TIER_A
    return CATCH_UP_NOTE


def first_open(order, closed):
    return next((it for it, _ in order if it.get("job_key") not in closed), None)


def day_started(by_n, by_key):
    """True once any item has an event today (the day's first card is behind)."""
    return any(st for st in list(by_n.values()) + list(by_key.values()) if st and st != "added")


def card(root, day, short=False):
    settings = jobfs.settings(root)
    items, block = queue(root, day)
    by_n, by_key, view = states(root, day)
    m = morning(root, day)
    status = verified(jobfs.Folder(root).status_json, {})
    closed, _ = closed_keys(root)
    order, waiting = serving(root, day, items, by_n, by_key, m, short=short)
    nxt = first_open(order, closed)
    lines = [f"{day:%A}, {day:%B} {day.day}"]
    if short:
        lines = [SHORT_HEAD]
        line = needs_line(m)
        if line:
            lines.append(line)
        quick = [it for it, st in order if it.get("job_key") not in closed and st not in IN_PROGRESS]
        line = sensitive_line(root, day, quick, by_n, by_key)
        if line:
            lines.append(line)
        if nxt:
            lines.append(f"Starting #{int(nxt['n'])} now. Say \"stop\" any time.")
        else:
            lines.append("No quick ones are left today. Type /job for the rest of the list.")
        return lines
    store = open_store(root)
    first = not day_started(by_n, by_key)
    cap = int(settings["daily"].get("questions_per_day", 6))
    done = {int(n) for n, st in by_n.items() if st in FINAL and str(n).isdigit()}
    head = [yesterday_line(store, day)] + (away_lines(m) if first else []) + [needs_line(m)]
    for line in head + [ready_line(items, block, by_n, by_key, root)]:
        if line:
            lines.append(line)
    lines += question_lines(root, day, m, cap, by_n, by_key)
    batch = int(settings["daily"].get("batch_size", 10))
    referral = referral_line(root, day, m, settings) if len(done) < batch else None
    for line in [sensitive_line(root, day, items, by_n, by_key)] + draft_lines(m, done) + [referral]:
        if line:
            lines.append(line)
    if first:
        weekly = [clean(x, 200) for x in (m or {}).get("weekly") or []][:3]
        lines += [w for w in weekly if w]
    lines += runner_lines(root, day, status, settings)
    lines += config_lines(root, settings)
    lines += note_lines(root, day, items, view, store, waiting)
    if first:
        line = catch_up_line(root, day, m, items)
        if line:
            lines.append(line)
    if nxt:
        lines.append(f"Starting #{int(nxt['n'])} now. Say \"short\" any time for an easy day.")
    elif waiting:
        lines.append("Nothing else is ready until then.")
    elif items:
        lines.append("Today's list is done. Say \"more\" for up to 10 more from the pool.")
    elif status and daily_building(root, day, status, settings):
        lines.append(BUILDING)
    elif no_runner(status, settings):
        lines.append("No list today yet. Paste a job link with /job <link>.")
    else:
        lines.append("No list today yet. Paste a job link with /job <link>, or say \"more\" after the job finder runs.")
    return lines


def wording_warnings(text):
    try:
        import report
        return report.check_wording(text)
    except ImportError:
        return []


# ---------- next ----------

def attach_text(n, upload, letter):
    """The attach instruction: the NN files in upload/, else the job-folder files."""
    if upload.startswith("upload/") and (not letter or letter.startswith("upload/")):
        if letter:
            return (f"Attach the files starting with {n:02d} in JobSearch\\upload ({n:02d}_ is the resume, "
                    f"{n:02d}c_ the cover letter)")
        return f"Attach the file starting with {n:02d} in JobSearch\\upload"
    files = [p.replace("/", "\\") for p in (upload, letter) if p]
    if not files:
        return ""
    return f"Attach {' and '.join(files)} (in the JobSearch folder)"


def handover(it, upload, settings, check="", letter=""):
    """The 5.7 handover block (at most 5 lines; it stops before Submit). An
    attach-first site was attached before the fill, so its block names no file."""
    n = int(it.get("n") or 0)
    ats = ATS_NAMES.get(str(it.get("ats", "")).lower(), str(it.get("ats", "")).title() or "site")
    lines = [f"#{n:02d} {it.get('company', '')} - {it.get('role', '')} ({ats})"]
    if it.get("attach") in LAST_ATTACH:
        text = attach_text(n, upload, letter)
        if text:
            lines.append(text + ".")
    if check:
        lines.append(f"Check: {check}.")
    lines.append("Then click Submit and type s.")
    return lines[:5]


def referral_links(root):
    """job_key -> the referral link the user stored with `ref <n> <url>` after
    the list was built (tracker.referral_urls)."""
    try:
        import tracker
        return tracker.referral_urls(open_store(root))
    except Exception:  # no tracker here: the queue's own referral_url stands
        return {}


def next_item(root, day, short=False):
    settings = jobfs.settings(root)
    items, _ = queue(root, day)
    by_n, by_key, _ = states(root, day)
    m = morning(root, day)
    closed, live = closed_keys(root)
    order, waiting = serving(root, day, items, by_n, by_key, m, short=short)
    confirmed = sensitive_ok(root, day)
    out, wait = [], []
    if waiting:
        fams = {}
        for it in waiting:
            fam = str(it.get("family") or "no family")
            fams[fam] = fams.get(fam, 0) + 1
        wait = [f"WAIT {plural(len(waiting), 'item')} with no resume yet: "
                + ", ".join(f"{k} {v}" for k, v in sorted(fams.items()))]
    for it, st in order:
        n = int(it.get("n") or 0)
        if it.get("job_key") in closed:
            rec = live.get(it.get("job_key")) or {}
            out.append(f"SKIP #{n:02d} closed (checked {rec.get('checked', '?')})")
            continue
        upload, note = upload_line(root, it)
        letter, lnote = letter_line(root, it)
        attach = "attach last" if it.get("attach") in LAST_ATTACH else "attach first"
        out += [f"NEXT #{n:02d} {it.get('job_key', '')}",
                f"COMPANY {it.get('company', '')}", f"ROLE {it.get('role', '')}",
                f"ATS {it.get('ats', '')} ({attach}, account {it.get('account', 'none')})",
                f"OPEN {it.get('referral_url') or referral_links(root).get(it.get('job_key'), '') or it.get('apply_url', '')}",
                f"FOLDER {it.get('folder', '')}",
                f"UPLOAD {upload or 'none'} ({note})"]
        if letter:
            out.append(f"LETTER {letter} ({lnote})")
        out.append(f"RESUME {it.get('resume_status', '')} tier {it.get('tier', '')}")
        ans = f"ANSWERS {it.get('answers_status', '')}"
        if it.get("needs"):
            ans += " needs " + ",".join(it["needs"])
        if it.get("sensitive"):
            ans += " sensitive " + ",".join(it["sensitive"])
        out.append(ans)
        sens, check = sensitive_fill(root, it, confirmed)
        if sens:
            out.append(sens)
        draft = draft_for(m, n)
        checks = []
        if draft is not None:
            label = clean(draft.get("label") or "answer", 60)
            out.append(f"DRAFT {label}")
            checks.append(f"your {label} answer (my draft)")
        if check:
            checks.append(check)
        checks += plan_checks(root, it)  # computed answers (years): shown before Submit
        if st in IN_PROGRESS:
            out.append(f"STATE {st}")
        elif "unsubmitted" in (it.get("flags") or []):  # handed over on an earlier day, no s (jobqueue)
            out.append("STATE unsubmitted (handed over on an earlier day with no s: ask whether it went through)")
        if it.get("attach") not in LAST_ATTACH:
            text = attach_text(n, upload, letter)
            if text:
                out.append(f"ATTACH {text}, then type ok.")
        out += ["HANDOVER " + ln for ln in handover(it, upload, settings, "; ".join(checks), letter)]
        return out + wait
    out.append("NEXT none")
    return out + wait


# ---------- progress and diy ----------

def progress(root, day):
    items, _ = queue(root, day)
    by_n, by_key, view = states(root, day)
    counts = {k: 0 for k in ("applied", "handed_over", "skipped", "later", "parked", "blocked")}
    for it in items:
        st = state_of(it, by_n, by_key)
        if st in counts:
            counts[st] += 1
    left = sum(1 for it in items if state_of(it, by_n, by_key) not in FINAL)
    out = ["PROGRESS " + " ".join(f"{k}={v}" for k, v in counts.items()) + f" left={left} of {len(items)}"]
    store = open_store(root)
    people = 0
    if store is not None and getattr(store, "referrals", None) is not None:
        people = sum(1 for r in store.referrals.rows() if to_date(r.get("Date")) == day
                     and str(r.get("Status") or "") not in ("", "Drafted"))
    sent = counts["applied"]
    parts = [f"{plural(sent, 'application')} sent"] if sent else []
    if people:
        parts.append(f"{plural(people, 'person', 'people')} contacted")
    out.append(f"Today: {', '.join(parts)}." if parts else "Today: no applications sent yet.")
    return out


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def logged_by_hand(root):
    """{(company, title)} of tracker rows with Source "By hand" (log e1 and /job log)."""
    store = open_store(root)
    if store is None:
        return set()
    return {(norm(r.get("Company")), norm(r.get("Role"))) for r in store.app_rows()
            if str(r.get("Source") or "").strip().lower() == "by hand"}


def diy_rows(root, day, limit=None):
    """[(company, title, location)] of the day's do-yourself items, in the
    runner's staging order (jobrunner.stage_diy numbers them E1, E2, ... the
    same way): LinkedIn leads given up (resolve.diy_items), then chat-apply
    pool items, at most easy_apply_list_max."""
    if limit is None:
        limit = int(jobfs.settings(root)["daily"].get("easy_apply_list_max", 5))
    rows = []
    try:
        import resolve
        rows += [(x.get("company", ""), x.get("title", ""), x.get("location", "")) for x in resolve.diy_items(root, day)]
    except Exception:  # no lead state yet
        pass
    try:
        import gates
        rows += [(it.get("company", ""), it.get("title", ""), it.get("location", ""))
                 for it in gates.load_pool(root).get("items", []) if it.get("diy")]
    except Exception:  # no pool yet
        pass
    return rows[:limit]


def diy_list(root, day):
    """The list as staged, numbered as staged; a job already logged by hand
    keeps its number but leaves the list."""
    f = jobfs.Folder(root)
    staged = {p.name.split("_", 1)[0]: p.name for p in f.upload.glob("E*_*.pdf")} if f.upload.exists() else {}
    rows = diy_rows(root, day)
    done = logged_by_hand(root) if rows else set()
    out = []
    for i, (company, title, loc) in enumerate(rows, 1):
        if (norm(company), norm(title)) in done:
            continue
        line = f"DIY E{i} | {clean(company, 80)} | {clean(title, 120)} | {clean(loc, 80)}"
        if f"E{i}" in staged:
            line += f" | upload/{staged[f'E{i}']}"
        out.append(line)
    return out or ["DIY none"]


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="today_view.py", description="The day's card and next item (spec 1.2, 5.1).",
                allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, hlp in (("card", "Print the day's card."), ("next", "Print the next item's handover data."),
                      ("progress", "Counts so far and the closing line."), ("diy", "The do-yourself list."),
                      ("drafts", "The card's free-text drafts in full.")):
        p = sub.add_parser(name, help=hlp, allow_abbrev=False)
        p.add_argument("--root", required=True)
        p.add_argument("--date")
        if name == "next":
            p.add_argument("--short", action="store_true",
                           help="an easy day: no-account items, easiest first; none with an open question, "
                                "a sensitive field beyond the profile's salary and sponsorship not settled "
                                "today, or no resume")
        if name == "card":
            p.add_argument("--short", action="store_true",
                           help="the easy day's card: one line, the sensitive line for the quick items, the first one")
        if name == "drafts":
            p.add_argument("--n", type=int, help="only this item's draft")
        p.set_defaults(cmd=name)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR --root {args.root} is not a folder")
        return 1
    root = root.resolve()
    try:
        day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else jobfs.today(root)
    except ValueError:
        print(f"ERROR --date must be YYYY-MM-DD, got '{args.date}'")
        return 1
    try:
        if args.cmd == "card":
            lines = card(root, day, short=args.short)
            text = "\n".join(lines)
            for word, n, line in wording_warnings(text):
                print(f"WARN wording: '{word}' in card line {n}", file=sys.stderr)
        elif args.cmd == "next":
            lines = next_item(root, day, short=args.short)
        elif args.cmd == "progress":
            lines = progress(root, day)
        elif args.cmd == "drafts":
            lines = drafts(root, day, args.n)
        else:
            lines = diy_list(root, day)
    except (jobfs.ConfigError, OSError) as e:
        print(f"ERROR {e}")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
