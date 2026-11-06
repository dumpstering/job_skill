"""The day's card and the next item, printed for the session (spec 1.2, 5.1, 7.8).

Usage:
    python today_view.py card     --root R [--date YYYY-MM-DD]
    python today_view.py next     --root R [--date YYYY-MM-DD]
    python today_view.py progress --root R [--date YYYY-MM-DD]
    python today_view.py diy      --root R [--date YYYY-MM-DD]

card prints the 1.2 card word for word from queue/<date>/queue.json and its
addenda, claude/<date>/morning.json (questions, sensitive line, drafts,
referral drafts, urgent and "while you were away" lists), the events overlay
and runner/out/status.json:
    Thursday, November 12
    Yesterday: 21 applications sent, 2 people contacted.
    Needs you: Acme Bio asked for phone-screen times by Fri 11/14 (say "draft" for a reply).
    Ready: 30. No account needed for 24; 2 new Workday sites come after #10 (about 5 minutes each).
    Questions (answer any time in one reply; 5 jobs wait for them):
    1. Have you worked in a BSL-2 lab? Where, and about how long? (3 jobs)
    Sensitive answers today: salary $95,000-$110,000 on #3, #7; "no sponsorship needed" on all. Reply ok, or name the ones to leave blank.
    3 people you could ask about a job: drafts after #10.
    Starting #1 now. Say "short" any time for an easy day.
A line whose count is zero never prints; "Needs you" prints only when
something is time-sensitive. One-line notes join when they apply: the PC job
finder not running (mode not L, or no daily for 26 hours), the workbook open
for 2 hours, a variant needing re-approval, a refused tracker change,
follow-ups ready, a posting set aside for text addressed to an AI. With no
morning.json the card says this morning's prep did not run and starts with
the first ready item. The card is checked with report.check_wording; a
banned word (from data) goes to stderr as WARN.

next prints the handover data of the first item with no final event
(applied, skipped, later, blocked; handed over items wait for his s):
    NEXT #02 <job key>
    COMPANY / ROLE / ATS <ats> (attach first|last) / OPEN <url> / FOLDER <path>
    UPLOAD <path> (<what>; sha256 ok|differs)
    RESUME <status> / ANSWERS <status> [needs ...] [sensitive ...]
    HANDOVER lines (5.7)
A Tier A item whose upload copy is not its resume.pdf (by sha256) names the
file in the job folder instead. NEXT none when the day's items are done.

progress prints PROGRESS applied=<n> handed_over=<n> skipped=<n> later=<n>
parked=<n> blocked=<n> left=<n> of <n>, then the closing line
("Today: 8 applications sent." plus people contacted when nonzero).

diy prints the do-yourself list (at most easy_apply_list_max, no links):
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
OPEN_AWAY = {"applied", "skipped", "later", "blocked", "handed_over"}
LOCK_LINE_HOURS = 2
RUNNER_STALE_HOURS = 26
ATS_NAMES = {"workday": "Workday", "oracle": "Oracle", "icims": "iCIMS", "successfactors": "SuccessFactors",
             "taleo": "Taleo", "adp": "ADP", "dayforce": "Dayforce", "phenom": "Phenom", "ukg": "UKG",
             "usajobs": "USAJOBS", "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
             "workable": "Workable", "bamboohr": "BambooHR", "breezy": "Breezy", "jazzhr": "JazzHR",
             "teamtailor": "Teamtailor", "rippling": "Rippling", "pinpoint": "Pinpoint", "paylocity": "Paylocity",
             "jobvite": "Jobvite", "personio": "Personio", "recruitee": "Recruitee"}
LAST_ATTACH = {"last"}
CATCH_UP_NOTE = ("This morning's prep did not run yet, so I'll read the inbox first and show what is urgent; "
                 "full tailoring for the Tier A jobs comes at the first break.")


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
    """(items in number order, account_block) from queue.json and its addenda."""
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


def ready_line(items, block, by_n, by_key):
    left = [it for it in items if state_of(it, by_n, by_key) not in FINAL]
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


def question_lines(root, day, m, cap):
    """The day's batch: morning.json's, else answers.py's unknown required ones."""
    if m is not None and "questions" in m:
        qs = [q for q in m.get("questions") or [] if isinstance(q, dict) and q.get("text")]
        waiting = int(m.get("waiting") or 0)
    else:
        try:
            import answers
            res = answers.questions(root, day, cap)
        except Exception:  # an unreadable plan never blocks the card
            res = {"questions": [], "waiting": 0}
        qs, waiting = res["questions"], res["waiting"]
    qs = qs[:cap]
    if not qs:
        return []
    head = "Questions (answer any time in one reply"
    head += f"; {plural(waiting, 'job')} wait{'s' if waiting == 1 else ''} for them):" if waiting else "):"
    out = [head]
    for i, q in enumerate(qs, 1):
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


def sensitive_text(root, day):
    """'salary $95,000-$110,000 on #3, #7; "no sponsorship needed" on all' from the fill plans."""
    try:
        import answers
        _, sensitive, planned, _ = answers.collect(root, day)
    except Exception:  # no plans yet
        return ""
    parts = []
    for (kind, answer), ns in sorted(sensitive.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        where = "all" if ns == planned and len(ns) > 1 else ", ".join(f"#{n}" for n in sorted(ns))
        k = str(kind).replace("_", " ").strip()
        a = clean(answer, 60)
        if "sponsor" in k.lower() and a.lower() in ("no", "yes"):
            text = '"no sponsorship needed"' if a.lower() == "no" else '"sponsorship needed"'
        elif "salary" in k.lower():
            text = f"salary {a}"
        else:
            text = f'{k} "{a}"'
        parts.append(f"{text} on {where}")
    return "; ".join(parts)


def sensitive_line(root, day, m):
    text = (m or {}).get("sensitive") if m is not None and "sensitive" in m else sensitive_text(root, day)
    text = clean(text, 400).rstrip(".")
    return f"Sensitive answers today: {text}. Reply ok, or name the ones to leave blank." if text else None


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


def draft_lines(m):
    out = []
    for d in (m or {}).get("drafts") or []:
        if isinstance(d, dict) and d.get("text"):
            label = clean(d.get("label") or "answer", 60)
            out.append(f"Draft for #{d.get('n', '?')} ({label}): {clean(d['text'], 90)} (full text in today.md)")
    return out[:6]


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


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
    if mode == "M" and not status:
        out.append("No job finder runs on your PC yet; paste a job link with /job <link> any time.")
    elif mode != "L" or stale:
        if when is not None:
            out.append(f"Your PC's job finder last ran {when:%a %H:%M}. "
                       "Double-click 'Run job finder now' on the desktop.")
        else:
            out.append("Your PC's job finder has not run yet. Double-click 'Run job finder now' on the desktop.")
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


def note_lines(root, day, items, view, store):
    out = []
    fams = sorted({it.get("family") for it in items if it.get("resume_status") == "prep_failed" and it.get("family")})
    for fam in fams:
        out.append(f"{fam} needs a quick re-approval.")
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


def first_open(items, by_n, by_key, m, skip_closed=()):
    for it in items:
        st = state_of(it, by_n, by_key)
        if st in FINAL or st == "parked" or it.get("job_key") in skip_closed:
            continue
        if m is None and it.get("tier") == "A" and it.get("resume_status") != "tier_a_ready":
            continue  # morning prep did not run: Tier B items go first (1.4)
        return it
    return None


def card(root, day):
    settings = jobfs.settings(root)
    items, block = queue(root, day)
    by_n, by_key, view = states(root, day)
    m = morning(root, day)
    store = open_store(root)
    status = verified(jobfs.Folder(root).status_json, {})
    cap = int(settings["daily"].get("questions_per_day", 6))
    lines = [f"{day:%A}, {day:%B} {day.day}"]
    for line in [yesterday_line(store, day)] + away_lines(m) + [needs_line(m), ready_line(items, block, by_n, by_key)]:
        if line:
            lines.append(line)
    lines += question_lines(root, day, m, cap)
    for line in [sensitive_line(root, day, m)] + draft_lines(m) + [referral_line(root, day, m, settings)]:
        if line:
            lines.append(line)
    weekly = [clean(x, 200) for x in (m or {}).get("weekly") or []][:3]
    lines += [w for w in weekly if w]
    lines += runner_lines(root, day, status, settings)
    lines += note_lines(root, day, items, view, store)
    if m is None and items:
        lines.append(CATCH_UP_NOTE)
    live = read_json(jobfs.Folder(root).runner_out / "liveness.json", {}).get("items", {})
    closed = {k for k, v in live.items() if v.get("closed")}
    nxt = first_open(items, by_n, by_key, m, closed)
    if nxt:
        lines.append(f"Starting #{int(nxt['n'])} now. Say \"short\" any time for an easy day.")
    elif items:
        lines.append("Today's list is done. Say \"more\" for up to 10 more from the pool.")
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

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_line(root, it):
    """(path text, note) for the file he attaches; a Tier A copy is checked by sha256."""
    f = jobfs.Folder(root)
    up = root / str(it.get("upload", ""))
    folder = root / str(it.get("folder", ""))
    tier_a = folder / "resume.pdf"
    if it.get("tier") == "A" and tier_a.exists():
        want = sha256_of(tier_a)
        staged = read_json(f.runner_out / "staged.json", {}).get("files", {}).get(up.name, {})
        if up.exists() and sha256_of(up) == want:
            ok = "sha256 ok" + ("" if not staged or staged.get("sha256") == want else ", staged record differs")
            return it.get("upload"), f"Tier A file, {ok}"
        return f"{it.get('folder')}/resume.pdf", "Tier A file in the job folder (the upload copy is not it yet)"
    if up.exists():
        src = folder / "resume_B.pdf"
        same = src.exists() and sha256_of(src) == sha256_of(up)
        note = "Tier B file" + (", sha256 ok" if same else ", sha256 differs from resume_B.pdf")
        if it.get("tier") == "A":
            note += "; the Tier A resume is not ready, so this is the fallback"
        return it.get("upload"), note
    if (folder / "resume_B.pdf").exists():
        return f"{it.get('folder')}/resume_B.pdf", "not staged in upload yet; attach it from the job folder"
    return "", "no built resume yet"


def handover(it, upload, settings):
    """The 5.7 handover block (at most 5 lines; it stops before Submit)."""
    n = int(it.get("n") or 0)
    ats = ATS_NAMES.get(str(it.get("ats", "")).lower(), str(it.get("ats", "")).title() or "site")
    lines = [f"#{n:02d} {it.get('company', '')} - {it.get('role', '')} ({ats})"]
    if upload.startswith("upload/"):
        lines.append(f"Attach the file starting with {n:02d} in JobSearch\\upload.")
    elif upload:
        lines.append(f"Attach {upload.replace('/', chr(92))} (in the JobSearch folder).")
    checks = []
    if it.get("sensitive"):
        checks.append(", ".join(str(s) for s in it["sensitive"]) + " as confirmed today (blank if not)")
    if checks:
        lines.append("Check: " + "; ".join(checks) + ".")
    lines.append("Then click Submit and type s.")
    return lines[:5]


def next_item(root, day):
    settings = jobfs.settings(root)
    items, _ = queue(root, day)
    by_n, by_key, _ = states(root, day)
    live = read_json(jobfs.Folder(root).runner_out / "liveness.json", {}).get("items", {})
    out = []
    for it in items:
        st = state_of(it, by_n, by_key)
        if st in FINAL or st == "parked":
            continue
        rec = live.get(it.get("job_key")) or {}
        if rec.get("closed"):
            out.append(f"SKIP #{int(it['n']):02d} closed (checked {rec.get('checked', '?')})")
            continue
        upload, note = upload_line(root, it)
        attach = "attach last" if it.get("attach") in LAST_ATTACH else "attach first"
        out += [f"NEXT #{int(it['n']):02d} {it.get('job_key', '')}",
                f"COMPANY {it.get('company', '')}", f"ROLE {it.get('role', '')}",
                f"ATS {it.get('ats', '')} ({attach}, account {it.get('account', 'none')})",
                f"OPEN {it.get('referral_url') or it.get('apply_url', '')}", f"FOLDER {it.get('folder', '')}",
                f"UPLOAD {upload or 'none'} ({note})",
                f"RESUME {it.get('resume_status', '')} tier {it.get('tier', '')}"]
        ans = f"ANSWERS {it.get('answers_status', '')}"
        if it.get("needs"):
            ans += " needs " + ",".join(it["needs"])
        if it.get("sensitive"):
            ans += " sensitive " + ",".join(it["sensitive"])
        out.append(ans)
        if st in ("started", "handed_over"):
            out.append(f"STATE {st}")
        out += ["HANDOVER " + ln for ln in handover(it, upload, settings)]
        return out
    out.append("NEXT none")
    return out


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
    if parts:
        out.append(f"Today: {', '.join(parts)}.")
    return out


def diy_list(root, day):
    settings = jobfs.settings(root)
    limit = int(settings["daily"].get("easy_apply_list_max", 5))
    f = jobfs.Folder(root)
    rows = []
    try:
        text = f.diy_md(day).read_text(encoding="utf-8")
        for line in text.splitlines():
            m = re.match(r"^- (E\d+) \| (.*?) \| (.*?) \| (.*)$", line)
            if m:
                rows.append(list(m.groups()))
    except OSError:
        pass
    try:
        import gates
        for it in gates.load_pool(root).get("items", []):
            if it.get("diy") and len(rows) < limit:
                rows.append([f"E{len(rows) + 1}", it.get("company", ""), it.get("title", ""), it.get("location", "")])
    except Exception:  # no pool yet
        pass
    out = []
    staged = {p.name.split("_", 1)[0]: p.name for p in f.upload.glob("E*_*.pdf")} if f.upload.exists() else {}
    for e, company, title, loc in rows[:limit]:
        line = f"DIY {e} | {clean(company, 80)} | {clean(title, 120)} | {clean(loc, 80)}"
        if e in staged:
            line += f" | upload/{staged[e]}"
        out.append(line)
    return out or ["DIY none"]


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="today_view.py", description="The day's card and next item (spec 1.2, 5.1).",
                allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, hlp in (("card", "Print the day's card."), ("next", "Print the next item's handover data."),
                      ("progress", "Counts so far and the closing line."), ("diy", "The do-yourself list.")):
        p = sub.add_parser(name, help=hlp, allow_abbrev=False)
        p.add_argument("--root", required=True)
        p.add_argument("--date")
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
    try:
        day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else jobfs.today(root)
    except ValueError:
        print(f"ERROR --date must be YYYY-MM-DD, got '{args.date}'")
        return 1
    try:
        if args.cmd == "card":
            lines = card(root, day)
            text = "\n".join(lines)
            for word, n, line in wording_warnings(text):
                print(f"WARN wording: '{word}' in card line {n}", file=sys.stderr)
        elif args.cmd == "next":
            lines = next_item(root, day)
        elif args.cmd == "progress":
            lines = progress(root, day)
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
