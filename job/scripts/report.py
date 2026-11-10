"""Reports after applying: the weekly summary, family weights, funnel and usage
(spec 6.5, 6.7, 7.7), and the wording check every card and summary passes.

Usage:
    python report.py weekly --root R [--date YYYY-MM-DD]
    python report.py adapt --root R [--apply] [--date YYYY-MM-DD]
    python report.py funnel --root R [--date YYYY-MM-DD] [--days N]
    python report.py usage --root R [--add --after-n N --session S --weekly W [--minutes M]
        [--plan P] [--tier-mix T] [--date YYYY-MM-DD]]
    python report.py check-wording (--file F | --text T)

weekly writes reports/week_<date>.md for the 7 days before --date (default
today): applications sent by tier and family, people contacted,
conversations started, employers that said no (listed only in this file),
"x of n" rates shrunk toward Beta(1, 30), discards by gate, the jobs set
aside for a second_ring place alone (one line ending "Say widen to include
them"), board health changes, widen steps, and at most one question. Only nonzero counts print,
and nothing is compared with an earlier week. Stdout is the card's lines:
    WROTE reports/week_2026-11-16.md
    CARD Last week: 12 applications sent, 2 people contacted.
    CARD 1 conversation started.
    CARD The weekly summary is in reports/week_2026-11-16.md.

adapt moves each family's family_w toward its rank by shrunk talk rate, by at
most 2.5 a week, within -10..+10, and only at n >= 30 applications. Without
--apply it prints what it would do. --apply writes state/weights.json (the
runner's file; the runner runs it) at most once a week.
    FAMILY qc_micro n 34 talk 3 rate 0.062 | w 0.0 -> 2.5
    HOLD assay_dev n 12 (moves at 30)

usage records Settings > Usage readings (--add) in reports/usage.csv
(date, after_n, session_pct, weekly_pct, minutes, plan, tier_mix) and
projects them:
    PER_APP session 4.0% weekly 0.8%
    DAY 22 applications: session 88.0% (two sittings 44.0% each), weekly 18.6% a day, 130.2% a week
    PLAN <the 7.7 decision rule's suggestion>

check-wording prints WORDING ok, or BANNED <word> line <n>: <line> (exit 1).
Library: BANNED, banned_words(text) -> sorted words found,
check_wording(text) -> [(word, line number, line)].

Exit codes: 0 ok, 1 bad input (ERROR ...) or a banned word, 2 openpyxl missing.
"""
import argparse
import csv
import io
import math
import re
import statistics
import sys
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import argfile  # noqa: E402
import jobfs  # noqa: E402

BANNED = ("streak", "missed", "behind", "overdue", "target", "goal", "only", "short of", "failed to",
          "should have", "catch up", "falling", "lagging")
BANNED_RE = re.compile(r"\b(" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in BANNED) + r")\b", re.I)

PRIOR_A, PRIOR_B = 1, 30          # Beta(1, 30): about 3% (volume A1)
MIN_N = 30                        # rates and weight moves start here
STEP, W_MIN, W_MAX = 2.5, -10.0, 10.0
TALK = {"screen", "interview", "offer"}
RESPONSE = {"assessment", "screen", "interview", "offer", "rejected"}
CONVERSATION = {"assessment", "screen", "interview", "offer"}
TALK_NOTE = re.compile(r"\b(screen|interview|offer) email\b", re.I)
RESPONSE_NOTE = re.compile(r"\b(assessment|screen|interview|offer|rejection) email\b", re.I)
NOT_SENT = {"started", "resume ready", "filled - awaiting submit", "answers ready - fill by hand",
            "blocked - login", "blocked - captcha", "blocked - site", "not pursued"}
SENT_ASK = {"sent", "replied", "referred", "no reply", "declined"}
BANDS = [(3, "0-3 days"), (7, "4-7 days"), (14, "8-14 days"), (30, "15-30 days")]
USAGE_COLS = ["date", "after_n", "session_pct", "weekly_pct", "minutes", "plan", "tier_mix"]
STARTED_PER_SUBMIT = 1.1          # 22 started for 20 sent (spec 7.7 "times 22")
WINDOW_LIMIT, WEEK_LIMIT = 70.0, 100.0
ARCHIVE_OFFER_ROWS = 5000


class ReportError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- wording ----------

def check_wording(text):
    """[(word, line number, line)] for each banned word (spec 6.5); empty when clean."""
    out = []
    for i, line in enumerate(str(text).splitlines(), 1):
        for m in BANNED_RE.finditer(line):
            out.append((re.sub(r"\s+", " ", m.group(1).lower()), i, line.strip()))
    return out


def banned_words(text):
    """The banned words found in text, sorted (today_view checks the card with this)."""
    return sorted({w for w, _, _ in check_wording(text)})


# ---------- small helpers ----------

def text(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v).strip()


def to_date(v):
    try:
        return datetime.strptime(text(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_day(label, s):
    d = to_date(s)
    if d is None:
        raise ReportError(f"{label} must be YYYY-MM-DD, got '{s}'")
    return d


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def shrunk(x, n):
    return (x + PRIOR_A) / (n + PRIOR_A + PRIOR_B)


def rel(root, p):
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(p)


def read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def open_store(root):
    import caps
    return caps.open_store(root)


def status_of(r):
    return text(r.get("Status")).lower()


def talked(r):
    return status_of(r) in TALK or bool(TALK_NOTE.search(text(r.get("Notes"))))


def responded(r):
    return status_of(r) in RESPONSE or bool(RESPONSE_NOTE.search(text(r.get("Notes"))))


def sent_rows(rows):
    """Rows of applications the user sent (an Applied date and a sent status)."""
    return [r for r in rows if to_date(r.get("Applied")) and status_of(r) not in NOT_SENT]


def family_labels(root):
    try:
        fams = jobfs.search_profile(root).get("family") or []
    except jobfs.ConfigError:
        fams = []
    return OrderedDict((str(f.get("id", "")).lower(), str(f.get("label") or f.get("id")))
                       for f in fams if isinstance(f, dict) and f.get("id"))


def band(r):
    posted, applied = to_date(r.get("Posted")), to_date(r.get("Applied"))
    if not posted or not applied:
        return ""
    age = (applied - posted).days
    for limit, name in BANDS:
        if age <= limit:
            return name
    return "over 30 days"


# ---------- weekly ----------

def rate_line(name, rows):
    n = len(rows)
    x = sum(1 for r in rows if talked(r))
    if x == 0:
        return f"- {name}: {plural(n, 'application')} sent, no conversations yet"
    return f"- {name}: {x} of {n} led to a conversation (about {100 * shrunk(x, n):.0f}% after shrinking)"


def working_section(rows, labels):
    sent = sent_rows(rows)
    if not sent:
        return []
    if len(sent) < MIN_N:
        return ["## What is working", "", f"Too early to tell ({plural(len(sent), 'application')} so far; "
                                         f"rates start at {MIN_N})."]
    out = ["## What is working", "", "Shown as x of n, shrunk toward about 3%. Correlation, not cause."]
    dims = [("Family", lambda r: labels.get(text(r.get("Family")).lower()) or text(r.get("Family"))),
            ("Variant", lambda r: text(r.get("Variant"))), ("Lane", lambda r: text(r.get("Found via"))),
            ("Account", lambda r: text(r.get("Account"))), ("Freshness at apply", band)]
    for title, key in dims:
        groups = OrderedDict()
        for r in sent:
            k = key(r)
            if k:
                groups.setdefault(k, []).append(r)
        if groups:
            out += ["", f"{title}:"] + [rate_line(k, g) for k, g in sorted(groups.items())]
    return out


def in_window(d, start, end):
    return d is not None and start <= d <= end


def discards(root, start, end):
    """Counter of discards by gate over the window's queue/<date>/discards.csv files."""
    c = Counter()
    d = start
    while d <= end:
        for r in read_csv(jobfs.Folder(root).discards_csv(d)):
            gate = (r.get("gate") or "").split(":")[0].strip()
            if gate:
                c[gate] += 1
        d += timedelta(days=1)
    return c


def second_ring_set_aside(root, start, end):
    """(n, Counter of places): the window's discards set aside for a
    second_ring place alone (G3 reason location:second_ring, D8)."""
    home = (jobfs.search_profile(root).get("home") or {})
    places = [str(p).strip() for p in home.get("second_ring") or [] if str(p).strip()]
    n, by = 0, Counter()
    d = start
    while d <= end:
        for r in read_csv(jobfs.Folder(root).discards_csv(d)):
            if (r.get("reason") or "").strip() != "location:second_ring":
                continue
            n += 1
            loc = r.get("location") or ""
            hit = next((p for p in places if re.search(r"(?<![A-Za-z])" + re.escape(p) + r"(?![A-Za-z])", loc, re.I)),
                       None)
            if hit:
                by[hit] += 1
        d += timedelta(days=1)
    return n, by


def second_ring_line(root, start, end, widen):
    """One weekly line: jobs set aside for their place alone, and how to
    include them. None when there are none or the ring is already on."""
    if "second_ring" in widen:
        return None
    n, by = second_ring_set_aside(root, start, end)
    if not n:
        return None
    where = ", ".join(f"{p} {k}" for p, k in by.most_common(3))
    return (f"{plural(n, 'job')} {'was' if n == 1 else 'were'} set aside for the place alone: "
            f"{'it is' if n == 1 else 'they are'} in your wider ring" + (f" ({where})" if where else "") +
            ". Say widen to include them.")


def queued_count(root, start, end):
    import tracker
    n, d = 0, start
    while d <= end:
        n += len(tracker.queue_items(root, d))
        d += timedelta(days=1)
    return n


def board_changes(root, day):
    """Boards now paused, gone, dormant or check_site (a Workday site that listed
    0 jobs on 3 polls) that no earlier weekly summary listed."""
    folder = jobfs.Folder(root)
    earlier = ""
    for p in sorted(folder.reports.glob("week_*.md")) if folder.reports.exists() else []:
        if p.name != folder.week_md(day).name:
            earlier += p.read_text(encoding="utf-8", errors="replace")
    out = []
    for r in read_csv(folder.board_health_csv):
        st, key = (r.get("status") or "").strip(), (r.get("key") or "").strip()
        if st in ("paused", "gone", "dormant", "check_site") and key and f"{key}: {st}" not in earlier:
            out.append(f"- {key}: {st}")
    return out


def widen_steps(root, end):
    import json
    for p in (jobfs.Folder(root).queue_json(end), jobfs.Folder(root).status_json):
        try:
            data = jobfs.read_json(p, jobfs.MAX_STATE)  # R3: the safe loader (BadJson is a ValueError)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        steps = data.get("widen") or data.get("widen_steps")
        if isinstance(steps, list) and steps:
            return [str(s) for s in steps]
    return []


def lane_resolution(root, end):
    """{lane: (resolved share, days)} from funnel.csv's resolution_by_lane over 14 days."""
    acc = {}
    for r in read_csv(jobfs.Folder(root).funnel_csv):
        d = to_date(r.get("date"))
        if not d or not end - timedelta(days=13) <= d <= end:
            continue
        for lane, v in pairs(r.get("resolution_by_lane", "")).items():
            acc.setdefault(lane, []).append(v)
    return {k: (sum(v) / len(v), len(v)) for k, v in acc.items()}


def pairs(s):
    """'a:1;b:2' (or | or , separated, or JSON) as {a: 1.0, b: 2.0}."""
    import json
    s = (s or "").strip()
    if s.startswith("{"):
        try:
            return {str(k): float(v) for k, v in jobfs.loads_json(s).items()}
        except (ValueError, TypeError, AttributeError):
            return {}
    out = {}
    for part in re.split(r"[;|,]", s):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def one_question(rows, day, labels, gate_counts, gate_total, lanes):
    """The single question for the week, or None (spec 6.5, 6.7 proposals)."""
    sent = sent_rows(rows)
    recent = [r for r in sent if in_window(to_date(r.get("Applied")), day - timedelta(days=14), day - timedelta(1))]
    if len(recent) >= 100 and not any(talked(r) for r in recent):
        return (f"{len(recent)} applications in two weeks have not led to a screen yet. As an experiment, "
                "shall we change the aim instead of adding volume: an adjacent family, a variant revision, "
                "or a wider ring? (name one, or say no)")
    fams = OrderedDict()
    for r in sent:
        fams.setdefault(text(r.get("Family")).lower(), []).append(r)
    for fam, group in fams.items():
        if fam and len(group) >= 40 and sum(1 for r in group if responded(r)) / len(group) < 0.05:
            return (f"{labels.get(fam, fam)} has {len(group)} applications and few replies. "
                    "Shall I draft a revised variant to try as an experiment? (yes or no)")
    if gate_total:
        gate, n = max(gate_counts.items(), key=lambda kv: kv[1]) if gate_counts else ("", 0)
        if gate and n / gate_total > 0.4:
            return (f"The {gate} filter set aside {100 * n // gate_total}% of what was found this week. "
                    "Shall I loosen it as an experiment? (yes or no)")
    for lane, (share, days) in sorted(lanes.items()):
        if days >= 14 and share < 0.2:
            return (f"Leads from {lane} resolved to a posting {share:.0%} of the time over two weeks. "
                    "Shall I turn that lane off? (yes or no)")
    return None


def weights_log(root, start, end):
    import json
    try:
        data = jobfs.read_json(jobfs.Folder(root).weights_json, jobfs.MAX_STATE)  # R3: the safe loader
    except (OSError, ValueError):
        return []
    out = []
    for e in data.get("log", []) if isinstance(data, dict) else []:
        if in_window(to_date(e.get("date")), start, end + timedelta(days=1)) and e.get("from") != e.get("to"):
            out.append(f"- {e.get('family')}: {e.get('from')} -> {e.get('to')}")
    return out


def weekly_text(root, store, day):
    """(markdown, card lines) for the week before day."""
    start, end = day - timedelta(days=7), day - timedelta(days=1)
    labels = family_labels(root)
    rows = store.app_rows() if store is not None else []
    every = rows + (store.archived_rows() if store is not None else [])
    week = [r for r in sent_rows(rows) if in_window(to_date(r.get("Applied")), start, end)]
    refs = store.referrals.rows() if store is not None and store.referrals is not None else []
    asked = {text(r.get("Contact")).lower() for r in refs
             if text(r.get("Status")).lower() in SENT_ASK and in_window(to_date(r.get("Date")), start, end)}
    talks = [r for r in rows if status_of(r) in CONVERSATION and in_window(to_date(r.get("Last update")), start, end)]
    noes = sorted({text(r.get("Company")) for r in rows
                   if status_of(r) == "rejected" and in_window(to_date(r.get("Last update")), start, end)})

    md = [f"# Week of {start.isoformat()} to {end.isoformat()}", ""]
    if week:
        md.append(f"Applications sent: {len(week)}")
        for title, key in (("By tier", lambda r: text(r.get("Tier"))),
                           ("By family", lambda r: labels.get(text(r.get("Family")).lower()) or text(r.get("Family")))):
            c = Counter(key(r) for r in week if key(r))
            if c:
                md.append(f"- {title.lower()}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    if asked:
        md.append(f"People contacted: {len(asked)}")
    if talks:
        md += ["", "## Conversations started", ""]
        for r in sorted(talks, key=lambda r: text(r.get("Last update"))):
            step = text(r.get("Next step"))
            md.append(f"- {text(r.get('Company'))}, {text(r.get('Role'))}: {text(r.get('Status'))} "
                      f"({step or 'updated ' + text(r.get('Last update'))[:10]})")
    if noes:
        md += ["", "## Employers that said no", ""] + [f"- {c}" for c in noes]
    quiet = [r for r in sent_rows(rows) if status_of(r) == "applied"
             and (day - to_date(r.get("Applied"))).days >= 30 and not responded(r)]
    if quiet:
        md += ["", f"{plural(len(quiet), 'application')} from 30 or more days ago "
                   f"{'has' if len(quiet) == 1 else 'have'} no reply. Say \"mark no response\" to set "
                   f"{'it' if len(quiet) == 1 else 'them'}."]
    working = working_section(every, labels)
    if working:
        md += [""] + working

    gates = discards(root, start, end)
    total = sum(gates.values()) + queued_count(root, start, end)
    if gates:
        md += ["", "## Not queued, by filter", ""] + [f"- {g}: {n}" for g, n in gates.most_common()]
    widen = widen_steps(root, end)
    ring = second_ring_line(root, start, end, widen)
    if ring:
        md += ["", ring]
    boards = board_changes(root, day)
    if boards:
        md += ["", "## Board changes", ""] + boards
    if widen:
        md += ["", "Wider search in use: " + ", ".join(widen) + "."]
    moved = weights_log(root, start, end)
    if moved:
        md += ["", "## Family weights moved", ""] + moved
    if len(rows) > ARCHIVE_OFFER_ROWS:
        md += ["", f"The tracker holds {len(rows)} rows. Say \"archive\" to move finished ones to a yearly file."]
    q = one_question(rows, day, labels, gates, total, lane_resolution(root, end))
    if q:
        md += ["", "## One question", "", f"Question: {q}"]

    card = []
    parts = [plural(len(week), "application") + " sent"] if week else []
    if asked:
        parts.append(plural(len(asked), "person", "people") + " contacted")
    if parts:
        card.append("Last week: " + ", ".join(parts) + ".")
    if talks:
        card.append(plural(len(talks), "conversation") + " started.")
    card.append(f"The weekly summary is in {rel(root, jobfs.Folder(root).week_md(day))}.")
    return "\n".join(md) + "\n", card


def cmd_weekly(a):
    day = parse_day("--date", a.date) if a.date else jobfs.today(a.root)
    store = open_store(a.root)
    md, card = weekly_text(a.root, store, day)
    hits = check_wording(md)
    if hits:
        for word, i, line in hits:
            print(f"WARN banned word '{word}' in line {i}: {line}")
    path = jobfs.Folder(a.root).week_md(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    jobfs.atomic_write_text(path, md)
    print(f"WROTE {rel(a.root, path)}")
    for line in card:
        print(f"CARD {line}")
    return 0


# ---------- adapt ----------

def read_weights(root):
    path = jobfs.Folder(root).weights_json
    if not path.exists() and not jobfs.prev_path(path).exists():
        return {"v": 1, "family_w": {}, "applied_on": "", "log": []}
    try:
        data = jobfs.read_json_verified(path, retries=0)
    except jobfs.NotReady:
        data = {}
    data.setdefault("family_w", {})
    data.setdefault("log", [])
    data.setdefault("applied_on", "")
    return data


def targets(stats):
    """Rank by shrunk talk rate -> a target weight from +10 (best) to -10 (worst)."""
    ranked = sorted(stats, key=lambda f: (-stats[f]["rate"], f))
    m = len(ranked)
    if m == 1:
        return {ranked[0]: 0.0}
    return {f: W_MAX - (W_MAX - W_MIN) * i / (m - 1) for i, f in enumerate(ranked)}


def step_toward(w, goal):
    moved = w + max(-STEP, min(STEP, goal - w))
    return round(max(W_MIN, min(W_MAX, moved)), 2)


def adapt_plan(rows, weights):
    """[(family, n, talk, rate, old w, new w or None)] with None for n < 30."""
    fams = OrderedDict()
    for r in sent_rows(rows):
        f = text(r.get("Family")).lower()
        if f:
            fams.setdefault(f, []).append(r)
    stats = {f: {"n": len(g), "talk": sum(1 for r in g if talked(r))} for f, g in fams.items()}
    for s in stats.values():
        s["rate"] = shrunk(s["talk"], s["n"])
    eligible = {f: s for f, s in stats.items() if s["n"] >= MIN_N}
    goal = targets(eligible) if eligible else {}
    out = []
    for f, s in sorted(stats.items()):
        old = float(weights["family_w"].get(f, 0.0))
        new = step_toward(old, goal[f]) if f in goal else None
        out.append((f, s["n"], s["talk"], s["rate"], old, new))
    return out


def cmd_adapt(a):
    day = parse_day("--date", a.date) if a.date else jobfs.today(a.root)
    store = open_store(a.root)
    rows = (store.app_rows() + store.archived_rows()) if store is not None else []
    weights = read_weights(a.root)
    last = to_date(weights.get("applied_on"))
    if a.apply and last and (day - last).days < 7:
        print(f"ADAPT skipped: weights last moved {last.isoformat()} (once a week)")
        return 0
    plan = adapt_plan(rows, weights)
    if not plan:
        print("ADAPT nothing to do (no family has applications yet)")
        return 0
    changed = False
    for f, n, talk, rate, old, new in plan:
        if new is None:
            print(f"HOLD {f} n {n} (moves at {MIN_N})")
            continue
        print(f"FAMILY {f} n {n} talk {talk} rate {rate:.3f} | w {old:.1f} -> {new:.1f}")
        if new != old:
            changed = True
            if a.apply:
                weights["family_w"][f] = new
                weights["log"].append({"date": day.isoformat(), "family": f, "n": n, "talk": talk,
                                       "rate": round(rate, 4), "from": old, "to": new})
    if not a.apply:
        print("DRY RUN (add --apply to write state/weights.json)")
        return 0
    weights["applied_on"] = day.isoformat()
    weights["log"] = weights["log"][-500:]
    path = jobfs.Folder(a.root).weights_json
    path.parent.mkdir(parents=True, exist_ok=True)
    jobfs.atomic_write_json(path, weights)
    print(f"WROTE {rel(a.root, path)}" + ("" if changed else " (no weight changed)"))
    return 0


# ---------- funnel ----------

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmd_funnel(a):
    day = parse_day("--date", a.date) if a.date else jobfs.today(a.root)
    start = day - timedelta(days=a.days - 1)
    rows = [r for r in read_csv(jobfs.Folder(a.root).funnel_csv) if in_window(to_date(r.get("date")), start, day)]
    if not rows:
        print(f"NONE no funnel rows from {start.isoformat()} to {day.isoformat()}")
        return 0
    stages = ("pool", "queued", "started", "submitted", "confirmed", "parked", "deferred")
    seen = Counter()
    for r in rows:
        for lane, v in pairs(r.get("seen_by_lane", "")).items():
            seen[lane] += v
    totals = {s: sum(num(r.get(s)) or 0 for r in rows) for s in stages}
    parts = [f"seen {int(sum(seen.values()))}"] + [f"{s} {int(totals[s])}" for s in stages if s != "pool"]
    print(f"FUNNEL {start.isoformat()} to {day.isoformat()} | " + " | ".join(parts))
    for lane, (share, days) in sorted(lane_resolution(a.root, day).items()):
        print(f"LANE {lane} seen {int(seen.get(lane, 0))} | resolved {share:.0%} over {days} days")
    fills = [v for v in (num(r.get("median_fill_min")) for r in rows) if v is not None]
    if fills:
        print(f"MEDIAN FILL {statistics.median(fills):.1f} minutes")
    return 0


# ---------- usage ----------

def usage_rows(root):
    out = []
    for r in read_csv(jobfs.Folder(root).usage_csv):
        d, n, s, w = to_date(r.get("date")), num(r.get("after_n")), num(r.get("session_pct")), num(r.get("weekly_pct"))
        if d and n is not None and s is not None and w is not None:
            out.append({"date": d, "after_n": n, "session": s, "weekly": w, "plan": (r.get("plan") or "").strip()})
    return out


def per_app(rows):
    """Mean percent per application (session, weekly) over days with two or more readings."""
    days = OrderedDict()
    for r in sorted(rows, key=lambda r: (r["date"], r["after_n"])):
        days.setdefault(r["date"], []).append(r)
    ses, wk = [], []
    for group in days.values():
        lo, hi = group[0], group[-1]
        if hi["after_n"] > lo["after_n"]:
            ses.append((hi["session"] - lo["session"]) / (hi["after_n"] - lo["after_n"]))
            wk.append((hi["weekly"] - lo["weekly"]) / (hi["after_n"] - lo["after_n"]))
    if not ses:
        return None
    return sum(ses) / len(ses), sum(wk) / len(wk), days


def morning_cost(days):
    """Weekly % spent between the last reading of one day and the first of the
    next (job-morning and job-inbox), averaged; 0 when unknown."""
    keys = list(days)
    costs = []
    for d1, d2 in zip(keys, keys[1:]):
        if (d2 - d1).days == 1:
            gap = days[d2][0]["weekly"] - days[d1][-1]["weekly"]
            if gap >= 0:
                costs.append(gap)
    return sum(costs) / len(costs) if costs else 0.0


def plan_line(s_app, w_app, morning, target, plan):
    """The 7.7 decision rule as one suggestion."""
    started = math.ceil(target * STARTED_PER_SUBMIT)
    day_s = s_app * started
    week = (w_app * started + morning) * 7
    if week > WEEK_LIMIT:
        fits = int((WEEK_LIMIT / 7 - morning) / w_app / STARTED_PER_SUBMIT) if w_app > 0 else target
        return f"PLAN the weekly limit binds: move to Ultra, or set the daily number to {max(fits, 0)}"
    if day_s > 2 * WINDOW_LIMIT:
        return "PLAN the 5-hour window binds even across two sittings: a lower daily number, or Ultra"
    if day_s > WINDOW_LIMIT:
        return "PLAN the 5-hour window binds, the weekly limit does not: work in two sittings about 5 hours apart"
    if day_s * 5 <= WINDOW_LIMIT and week * 5 <= WEEK_LIMIT:
        return "PLAN it fits in one fifth: step down to Pro with usage credits"
    return f"PLAN stay on {plan or 'the current plan'}"


def cmd_usage(a):
    folder = jobfs.Folder(a.root)
    if a.add:
        if a.after_n is None or a.session is None or a.weekly is None:
            raise ReportError("--add needs --after-n, --session and --weekly")
        day = parse_day("--date", a.date) if a.date else jobfs.today(a.root)
        new = not folder.usage_csv.exists()
        folder.usage_csv.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if new:
            w.writerow(USAGE_COLS)
        w.writerow([day.isoformat(), a.after_n, a.session, a.weekly, "" if a.minutes is None else a.minutes,
                    (a.plan or "").replace(",", " "), (a.tier_mix or "").replace(",", " ")])
        with open(folder.usage_csv, "a", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())
        print(f"ADDED {day.isoformat()} after #{a.after_n}: session {a.session}% weekly {a.weekly}%")
    rows = usage_rows(a.root)
    got = per_app(rows) if rows else None
    if not got:
        print("NONE need two readings on one day (before #1 and after #3 or #5)")
        return 0
    s_app, w_app, days = got
    morning = morning_cost(days)
    target = int(jobfs.settings(a.root)["daily"].get("target", 20))
    started = math.ceil(target * STARTED_PER_SUBMIT)
    print(f"PER_APP session {s_app:.1f}% weekly {w_app:.1f}%")
    if morning:
        print(f"MORNING weekly {morning:.1f}% a day (job-morning and job-inbox)")
    day_s, day_w = s_app * started, w_app * started + morning
    print(f"DAY {started} applications: session {day_s:.1f}% (two sittings {day_s / 2:.1f}% each), "
          f"weekly {day_w:.1f}% a day, {day_w * 7:.1f}% a week")
    plan = next((r["plan"] for r in reversed(rows) if r["plan"]), "")
    print(plan_line(s_app, w_app, morning, target, plan))
    return 0


# ---------- check-wording ----------

def cmd_check_wording(a):
    if a.file:
        try:
            body = Path(a.file).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise ReportError(f"cannot read {a.file}: {e}")
    elif a.text is not None:
        body = a.text
    else:
        raise ReportError("give --file or --text")
    hits = check_wording(body)
    for word, i, line in hits:
        print(f"BANNED {word} line {i}: {line}")
    if not hits:
        print("WORDING ok")
    return 1 if hits else 0


# ---------- CLI ----------

def build_parser():
    ap = Parser(prog="report.py", description="Weekly summary, weights, funnel, usage, wording.",
                allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    p = sub.add_parser("weekly", help="Write reports/week_<date>.md and print the card lines.")
    p.add_argument("--root", required=True)
    p.add_argument("--date", help="the Monday it is written on (default today)")
    p.set_defaults(func=cmd_weekly)
    p = sub.add_parser("adapt", help="Move family weights toward their talk-rate rank (bounded).")
    p.add_argument("--root", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--date")
    p.set_defaults(func=cmd_adapt)
    p = sub.add_parser("funnel", help="Totals from reports/funnel.csv.")
    p.add_argument("--root", required=True)
    p.add_argument("--date")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_funnel)
    p = sub.add_parser("usage", help="Record and project Settings > Usage readings.")
    p.add_argument("--root", required=True)
    p.add_argument("--add", action="store_true")
    p.add_argument("--after-n", type=int)
    p.add_argument("--session", type=float)
    p.add_argument("--weekly", type=float)
    p.add_argument("--minutes", type=float)
    p.add_argument("--plan")
    p.add_argument("--tier-mix")
    p.add_argument("--date")
    p.set_defaults(func=cmd_usage)
    p = sub.add_parser("check-wording", help="Find banned words in a file or text.")
    p.add_argument("--file")
    p.add_argument("--text")
    p.set_defaults(func=cmd_check_wording)
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
    except (ReportError, jobfs.ConfigError) as e:
        print(f"ERROR {e}")
        return 1
    except Exception as e:
        if type(e).__name__ == "MissingPackage":  # a tracker in mode M needs openpyxl
            print("MISSING openpyxl: run  pip install openpyxl  and run again")
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
