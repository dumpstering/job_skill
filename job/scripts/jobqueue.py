"""Queue builder for the runner (spec 3.12, 2.5 queue.json, 2.7.3, 5.2, 5.3).

Usage:
    python jobqueue.py build         --root R [--date YYYY-MM-DD] [--pool pool.json]
    python jobqueue.py addendum      --root R [--date YYYY-MM-DD] [--pool new.json] [--limit N]
    python jobqueue.py stage-uploads --root R [--date YYYY-MM-DD]
    python jobqueue.py fold-events   --root R [--date YYYY-MM-DD]
    python jobqueue.py user-addendum --root R [--date YYYY-MM-DD]

build scores the pool (state/pool.json, or --pool), walks it by score and
writes queue/<date>/queue.json with N = ceil(target x queue_factor) items:
    score = fresh[band] + 30*fit + 20*referral_tier1 + priority(p1=10,p2=5,p3=0)
            + family_w - cost(account: none 0, exists 3, new 8) - 10 if the company
            had an application in the last 30 days
taking items while caps.caps_verdict allows them (each pick is reserved for
the day, so "apply to all" never breaks a cap; a DEFER keeps the item in the
pool, and every build checks it again), at most 3 new-account tenants, 6
account-required items and 1 USAJOBS item, and each active family's
min_share where the pool allows. The top tier_a_per_day items are Tier A (a
tier-1 referral, USAJOBS, band 0-3 with fit >= 0.7, then the highest fit).
Until a family's variant is approved (or after an approved file changed) at
most tier_a_per_day of its items are queued, as Tier A; the rest wait for the
re-approval. They fill only slots nothing else can, queued last as Tier B with
resume_status prep_failed (the card's re-approval line), and counts.variant_wait
holds {family: jobs waiting}. The order: an item handed over on an earlier
day with no `s` first (flag `unsubmitted`: ask whether it went through), then
batch 1 (no-account Tier B items by fit, best first), the account block
(new-account tenants grouped by tenant, only a tenant whose best job is at or
above the day's median fit; another tenant's jobs take their places by fit),
the rest by fit, the jobs whose seniority asks more than the user has
(the gates' borderline mark, R6), then the jobs the gates marked low_rank
(shift work when [gates] shift_work is "last"; a job in a relocation region),
and the prep_failed items last; never the
same employer twice in a row and at most 3 in a row on one ATS host.
Borderline jobs are also taken last for the day's slots. Job folders get posting.md, posting.json and .runner_files. Folders
of jobs that got no tracker row within 30 days move to
applications/_not_sent/<YYYY-MM>/ (never deleted). When fewer than target
pool items pass the caps with a resume to send (an approved variant, or one
of the day's Tier A places), one widen step a day is switched on for the next
gates run.
    QUEUE size=<n> pool_left=<n> new_account_tenants<=3 account_required<=6 usajobs<=1
    MIX tier_a=<n> new_account_tenants=<n> account_required=<n> usajobs=<n> deferred=<n>
    WAIT_VARIANT <family> <n> jobs        (one per family waiting for a re-approval)
    WIDEN <step>
    WROTE queue/<date>/queue.json
The build first folds the day's queue events (fold-events), so a session's
late events count even when no tick ran after them. What comes back, by the
job's last queue event: applied, and skipped or blocked for good (closed,
knockout_at_form, unsafe_verification, sso_required) leave the pool; skipped
by the user (`k`) waits 7 days, and a second `k` on another day drops it;
blocked account_locked keeps that tenant's items out for 7 days; parked and
handed_over hold for their own day only, so a parked job (a question, a usage
limit) is back the next day, never dropped. queue.json is immutable once any
event for that date exists (FROZEN), and a rebuild of a queue.json that
exists waits while claude/session.json has a beat under 20 minutes old
(WAIT); the first build of the day never waits. Items already
given that day (an addendum or a job from a link before queue.json) keep
their numbers: the build numbers after them and leaves their jobs out.

addendum appends new items after the highest number already given (late finds
from --pool, or `more`: the next more_size items from the pool) to
queue/<date>/addendum_<HHMM>.json; queue.json and upload/ are never touched
while a session is active. Numbers given to jobs from links (added events)
count as given, and they are checked again just before the file is written.
Before today's queue.json exists it writes nothing (late finds are in the
pool for the build):
    ADDENDUM none: no list for <date> yet (the daily builds it first)
    ADDENDUM none: <family> needs a quick re-approval (<n> jobs wait)
    ADDENDUM none: nothing new passes the caps

stage-uploads copies each item's built resume (resume_B.pdf, or resume.pdf for
a ready Tier A item) to upload/NN_<Last>_Resume_<Employer12>.pdf (NNc_ for a
cover letter), moves files of applied items and earlier days' leftovers to
upload/_done/<date>/, and deletes nothing. File times are read in the
folder's [time] timezone.

fold-events folds queue events (started ... blocked) into state/queue_state.json;
running it twice gives the same bytes.

user-addendum writes queue/<date>/addendum_user.json from the day's added
events (jobs from /job <link> and /job add <link>, spec 5.11), keeping the
numbers the session gave them; it never writes in their job folders, which
are the session's. It writes only when an item changed:
    ADDENDUM addendum_user.json items=31,32
    ADDENDUM addendum_user.json unchanged      (the CLI, when nothing changed)
Stdlib only (plus openpyxl when the tracker is read directly, mode M).

Exit codes: 0 ok (also FROZEN and WAIT), 1 bad input (ERROR ...).
"""
import argparse
import json
import math
import os
import re
import shutil
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import caps  # noqa: E402
import events  # noqa: E402
import gates  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import netfetch  # noqa: E402

BEAT_MINUTES = 20
NOT_SENT_DAYS = 30
FOLDED_DAYS = 8
RUNNER_FILES = ".runner_files"
PRIORITY_POINTS = {1: 10, 2: 5, 3: 0}
COST = {"none": 0, "exists": 3, "new": 8}
# A job's last queue event, from state/queue_state.json:
DAY_ONLY_STATES = {"handed_over", "parked"}      # held out on their own day only (decision: parked comes back)
PERMANENT_BLOCKS = {"sso_required", "unsafe_verification", "knockout_at_form", "closed"}
PERMANENT_SKIPS = {"closed", "knockout_at_form", "unsafe_verification"}
USER_SKIP_HOLD_DAYS = 7      # `k`: back after a week, not tomorrow's top
USER_SKIP_DROP = 2           # `k` on this many different days: not for this user, out of the pool
TENANT_LOCK_DAYS = 7         # account_locked: that tenant's items wait a week (apply_batch.md)

# Per ATS: account (none, or one per tenant), attach order (first everywhere
# until the day-1 supervised test, 5.4), CAPTCHA risk, the user's minutes (1.6) and
# the short-day effort rank (1.3; lower is easier).
ATS_PROFILES = {
    "greenhouse": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 1},
    "lever": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.3, "effort": 2},
    "ashby": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 3},
    "workable": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 4},
    "bamboohr": {"account": "none", "attach": "first", "captcha_risk": "high", "est_user_min": 1.5, "effort": 5},
    "breezy": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 6},
    "teamtailor": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 7},
    "rippling": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 8},
    "pinpoint": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.2, "effort": 9},
    "jazzhr": {"account": "none", "attach": "first", "captcha_risk": "high", "est_user_min": 1.5, "effort": 10},
    "paylocity": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.5, "effort": 10},
    "personio": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.3, "effort": 10},
    "recruitee": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.3, "effort": 10},
    "comeet": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.3, "effort": 10},
    "manatal": {"account": "none", "attach": "first", "captcha_risk": "low", "est_user_min": 1.3, "effort": 10},
    "hireology": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.5, "effort": 11},
    "jobvite": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.5, "effort": 11},
    "smartrecruiters": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.5, "effort": 11},
    "workday": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.0, "effort": 20},
    "oracle": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.0, "effort": 20},
    "successfactors": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.0, "effort": 20},
    "taleo": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.0, "effort": 20},
    "icims": {"account": "tenant", "attach": "first", "captcha_risk": "med", "est_user_min": 2.0, "effort": 20},
    "ukg": {"account": "tenant", "attach": "first", "captcha_risk": "med", "est_user_min": 2.0, "effort": 20},
    "adp": {"account": "tenant", "attach": "first", "captcha_risk": "med", "est_user_min": 2.0, "effort": 20},
    "dayforce": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.0, "effort": 20},
    "phenom": {"account": "tenant", "attach": "first", "captcha_risk": "med", "est_user_min": 2.0, "effort": 20},
    "usajobs": {"account": "tenant", "attach": "first", "captcha_risk": "low", "est_user_min": 2.5, "effort": 30},
    "other": {"account": "none", "attach": "first", "captcha_risk": "med", "est_user_min": 1.5, "effort": 15},
}
NEW_TENANT_MIN = 4.5
QUEUE_KEYS = {"v": int, "date": str, "built_at": str, "runner_version": str, "target": int, "size": int,
              "pool_left": int, "items": list, "account_block": dict, "counts": dict}
ITEM_KEYS = {"n": int, "job_key": str, "company": str, "role": str, "location": str, "ats": str, "host": str,
             "apply_url": str, "referral_url": str, "folder": str, "family": str, "mode": str, "tier": str,
             "tier_reason": str, "variant": str, "posted": (str, type(None)), "age_days": (int, type(None)),
             "band": str, "repost_of": (str, type(None)), "score": (int, float), "score_parts": dict,
             "account": str, "attach": str, "captcha_risk": str, "est_user_min": (int, float), "upload": str,
             "resume_status": str, "answers_status": str, "needs": list, "sensitive": list, "referral": dict,
             "posting_status": str, "flags": list}
ENUMS = {"mode": {"industry", "cv", "usajobs"}, "tier": {"A", "B"}, "account": {"none", "exists", "new"},
         "attach": {"first", "last"}, "captcha_risk": {"low", "med", "high"},
         "resume_status": {"built", "fallback_base", "tier_a_pending", "tier_a_ready", "prep_failed"},
         "answers_status": {"complete", "needs_you", "sensitive_confirm"}, "posting_status": {"read", "browser_read"}}
SCORE_PARTS = ("fresh", "fit", "referral", "priority", "family_w", "cost", "repeat_company")
UPLOAD_RE = re.compile(r"^\d{2}[cw]?_[A-Za-z0-9]{1,20}_(Resume|Cover)_[A-Za-z0-9]{1,12}\.(pdf|docx)$")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(f"ERROR {message}")
        raise SystemExit(1)


# ---------- small helpers ----------

def profile_of(ats):
    return ATS_PROFILES.get((ats or "other").lower(), ATS_PROFILES["other"])


def read_json(path, default):
    """The safe loader (R3): missing is default; a bad file is default with one ERROR line."""
    return jobfs.read_json_or(path, jobfs.MAX_STATE, default)


def session_active(root, now=None):
    """claude/session.json beat under 20 minutes old (2.7.3)."""
    data = read_json(jobfs.Folder(root).session_json, {})
    try:
        beat = datetime.fromisoformat(str(data.get("beat", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=jobfs.now(root).tzinfo)
    now = now or jobfs.utcnow()
    return timedelta(0) <= now - beat < timedelta(minutes=BEAT_MINUTES) or \
        timedelta(minutes=-5) < now - beat < timedelta(0)


def last_name(root):
    try:
        import answers
        values, _ = answers.load_profile(jobfs.Folder(root).profile)
        name = values.get("last_name") or ""
    except (ImportError, OSError):
        name = ""
    return jobfs.ascii_name(name, 20, alnum=True) or "Applicant"


def plan_upload(n, last, company, kind="resume"):
    return jobfs.upload_name(n, last[:20], company, kind)


def queue_files(root, day):
    qd = jobfs.Folder(root).queue_dir(day)
    return [qd / "queue.json"] + (sorted(x for x in qd.glob("addendum_*.json") if not x.name.endswith(".prev.json")) if qd.exists() else [])


def events_for(root, day):
    day = jobfs.iso_date(day)
    return [ev for ev in events.recent(root) if ev.get("date") == day]


def day_items(root, day):
    """Every item of the day in number order: queue.json, its addenda, and the
    jobs from links whose added event the runner has not written into
    addendum_user.json yet."""
    items, keys, numbers = [], set(), set()
    for p in queue_files(root, day):
        if p.exists():
            for it in read_json(p, {}).get("items") or []:
                if isinstance(it, dict):
                    items.append(it)
                    keys.add(it.get("job_key"))
                    numbers.add(it.get("n"))
    # A job from a link never takes a number a queue file gave (SEC-14).
    items += [it for it in events.user_items(root, day) if it["job_key"] not in keys and it["n"] not in numbers]
    return sorted(items, key=lambda it: int(it.get("n") or 0))


# ---------- the context of one build ----------

class Context:
    """Everything scoring and assembly need, read once."""

    def __init__(self, root, today, include_queued=False):
        self.root, self.today = Path(root), today
        self.folder = jobfs.Folder(root)
        self.settings = jobfs.settings(root)
        self.daily = self.settings["daily"]
        self.profile = jobfs.search_profile(root)
        self.families = {f.get("id"): f for f in self.profile.get("family") or [] if f.get("id")}
        self.store = caps.open_store(root)
        index = caps.load_index(root, today, store=self.store)
        if not include_queued:
            # A rebuild replaces queue.json: its old picks are not reservations.
            # Addenda and jobs from links stay, so theirs still are.
            old = {it.get("job_key") for it in read_json(self.folder.queue_json(today), {}).get("items") or []
                   if isinstance(it, dict)}
            index = [e for e in index if not (e["status"] == "Queued" and e["job_key"] in old)]
        self.base_index = list(index)
        self.index = index
        self.rules = caps.load_rules(root, self.store)
        self.sites = self.account_sites()
        self.weights = (read_json(self.folder.weights_json, {}).get("family_w") or {})
        self.referrals = {}
        self.referral_urls = caps.tracker.referral_urls(self.store)
        self.qstate = read_json(self.folder.queue_state_json, {}).get("items", {})
        sent = [e for e in self.base_index if e["job_key"] and e["status"] != "Queued"]
        # A job handed over on an earlier day with no `s` (its row still at
        # Filled - awaiting submit) comes back to ask whether it went through.
        self.carry = {e["job_key"] for e in sent if self.handover_open(e)}
        self.acted = {e["job_key"] for e in sent} - self.carry
        self.recent_companies = {caps.group(self.rules, e["company_norm"]) for e in self.base_index
                                 if e["date"] and caps.in_window(e["date"], today, 30)}
        self.locked = self.locked_jobs()
        self.variants_on = self.folder.variants.exists()
        self._approved = {}
        self.last = last_name(root)
        self._experience = False  # read on first use (a pool item pooled before the seniority mark)

    def borderline(self, it):
        """True for an item whose seniority asks more than the user has (R6):
        the gates' mark, or for an item pooled before the mark existed, the
        same judgment now. Such items are queued after the rest."""
        if "seniority" in it:
            return it.get("seniority") == "borderline"
        try:
            if self._experience is False:
                self._experience = gates.experience(self.root, self.today)
            fam = self.families.get(it.get("family")) or {}
            verdict, _ = gates.seniority(str(it.get("title") or ""), str(it.get("text") or ""), fam,
                                         self._experience)
        except Exception:  # noqa: BLE001  never stops the build (D2): judged as not borderline
            self._experience, verdict = None, "ok"
        it["seniority"] = "borderline" if verdict != "ok" else ""
        return verdict != "ok"

    def state(self, key):
        """(state, reason, date) of a job's last queue event, or (None, '', None)."""
        st = self.qstate.get(key) or {}
        return st.get("state"), st.get("reason") or "", gates.to_date(st.get("date"))

    def handover_open(self, e):
        state, _, day = self.state(e["job_key"])
        if e["status"] == caps.HANDOVER and state in ("handed_over", "started") and day and day < self.today:
            return all(x["status"] == caps.HANDOVER for x in self.base_index if x["job_key"] == e["job_key"]
                       and x["status"] != "Queued")
        return False

    def locked_jobs(self):
        """job_key -> (tenant or '', first day back) for account_locked blocks of the last 7 days."""
        out = {}
        for key, st in self.qstate.items():
            day = gates.to_date(st.get("date"))
            if st.get("state") == "blocked" and st.get("reason") == "account_locked" and day \
                    and self.today < day + timedelta(days=TENANT_LOCK_DAYS):
                out[key] = (str(st.get("tenant") or ""), day + timedelta(days=TENANT_LOCK_DAYS))
        return out

    def account_sites(self):
        rows = []
        if self.store is not None and getattr(self.store, "accounts", None) is not None:
            rows = self.store.accounts.rows()
        out = set()
        for r in rows:
            s = str(r.get("Site") or "").strip().lower()
            s = (netfetch.host_or_blank(s) if "://" in s else s).removeprefix("www.")
            if s:
                out.add(s)
        return out

    def load_referrals(self, items):
        """job_key -> {"referral": {state, contact_ref, hold_until}, "tier1": bool}
        from network.py (6.3), when network/network.csv exists."""
        if not self.folder.network_csv.exists():
            return
        import network  # same folder (WP6)
        self.referrals.update(network.referrals_for_items(self.root, items, self.today, store=self.store))

    def referral(self, it):
        return self.referrals.get(it.get("job_key"), {})

    def approved(self, family):
        """'<family>@v<N>' when the family has an approved variant, else ''."""
        if family not in self._approved:
            label = ""
            if self.variants_on and family:
                import variants
                v, _, _ = variants.usable(self.root, family)
                label = f"{family}@v{v}" if v is not None else ""
            self._approved[family] = label
        return self._approved[family]

    def account_of(self, it):
        if profile_of(it.get("ats")).get("account") == "none":
            return "none"
        host = tenant_of(it)
        return "exists" if any(host == s or host.endswith("." + s) or s in host for s in self.sites) else "new"


def tenant_of(it):
    if (it.get("ats") or "") == "usajobs" or it.get("lane") == "usajobs":
        return "usajobs.gov"
    return netfetch.host_or_blank(it.get("apply_url") or it.get("url") or "")


# ---------- scoring ----------

def age_now(it, today):
    """(age_days, band, points) at today, or (None, None, 0) when stale. A
    posted date wins over a stored 'undated' basis (a pool item from before
    the detail date was judged), and an undated item first seen more than
    STALE_DAYS ago is stale: it is at least that old."""
    basis = it.get("date_basis") or ("posted" if it.get("posted") else "undated")
    if gates.to_date(it.get("posted")):
        basis = "posted"
    if basis == "undated":
        seen = gates.to_date(it.get("first_seen"))
        if seen and (today - seen).days > gates.STALE_DAYS:
            return (today - seen).days, None, 0
        return None, gates.UNDATED[0], gates.UNDATED[1]
    start = gates.to_date(it.get("posted") if basis == "posted" else it.get("first_seen"))
    if start is None:
        return None, gates.UNDATED[0], gates.UNDATED[1]
    age = max(0, (today - start).days)
    band, pts = gates.band_of(age)
    return age, band, pts


def score(it, ctx):
    age, band, pts = age_now(it, ctx.today)
    it["age_days"], it["band"] = age, band
    fam = ctx.families.get(it.get("family")) or {}
    prio = int(it.get("family_priority") or fam.get("priority") or 2)
    account = ctx.account_of(it)
    cn = caps.group(ctx.rules, jobkey.norm_company(it.get("company", "")))
    parts = {"fresh": pts, "fit": round(30 * float(it.get("fit") or 0), 2),
             "referral": 20 if ctx.referral(it).get("tier1") else 0,
             "priority": PRIORITY_POINTS.get(prio, 0),
             "family_w": round(max(-10.0, min(10.0, float(ctx.weights.get(it.get("family"), 0) or 0))), 2),
             "cost": -COST[account], "repeat_company": -10 if cn in ctx.recent_companies else 0}
    it["_account"], it["_parts"] = account, parts
    it["_score"] = round(sum(parts.values()), 2)
    return it["_score"]


# ---------- eligibility and assembly ----------

def gone_for_good(st, reason, user_skips):
    """The job's last queue event takes it out of the pool for good."""
    return st == "applied" or (st == "blocked" and reason in PERMANENT_BLOCKS) or \
        (st == "skipped" and reason in PERMANENT_SKIPS) or user_skips >= USER_SKIP_DROP


def refused_route(it):
    """True when an item's apply link is one shared decision 1 refuses: a job
    board, or a host that is not a plain web address. Such an item is never
    queued, so no OPEN line points the browser at it."""
    url = str(it.get("apply_url") or it.get("url") or "")
    return bool(url) and bool(events.url_refusal(url))


def eligible(pool, ctx, keys_taken=()):
    """Pool items that may be queued today, and the ones that leave the pool.
    Caps are not judged here: every build checks them again (a stored
    deferred_until is only a note), so a slot an untaken item held is free."""
    keep, drop = [], []
    taken = set(keys_taken)
    # The tenants an account_locked block pauses: recorded at the fold, else the blocked job's own.
    locked = {t: until for t, until in ctx.locked.values() if t}
    for it in pool:
        key = it.get("job_key", "")
        if key in ctx.locked and not ctx.locked[key][0]:
            locked[tenant_of(it)] = ctx.locked[key][1]
    for it in pool:
        key = it.get("job_key", "")
        st, reason, day = ctx.state(key)
        skips = len((ctx.qstate.get(key) or {}).get("user_skips") or [])
        age, band, _ = age_now(it, ctx.today)
        if band is None or it.get("closed") or gone_for_good(st, reason, skips) or key in ctx.acted:
            drop.append(it)
            continue
        if key in taken or it.get("parked") or it.get("diy"):
            continue
        if refused_route(it):
            continue  # a job-board or bad-url apply link (G12 discards these; an older pool may hold one)
        if st in DAY_ONLY_STATES and day == ctx.today:
            continue
        if st == "skipped" and reason == "user" and day and ctx.today < day + timedelta(days=USER_SKIP_HOLD_DAYS):
            continue
        if key in ctx.locked or (profile_of(it.get("ats")).get("account") != "none" and tenant_of(it) in locked):
            continue  # the locked account's tenant (a shared no-account host is never paused whole)
        it["_carried"] = key in ctx.carry
        keep.append(it)
    return keep, drop


class Assembly:
    def __init__(self, ctx, size):
        self.ctx, self.size = ctx, size
        d = ctx.daily
        self.max_new = int(d.get("new_account_tenants_per_day", 3))
        self.max_acct = int(d.get("account_required_per_day", 6))
        self.max_usa = int(d.get("usajobs_per_day", 1))
        self.tier_a = int(d.get("tier_a_per_day", 4))
        self.picked, self.deferred = [], {}
        self.new_tenants, self.acct, self.usa, self.forced = [], 0, 0, 0
        self.held, self.take_held = {}, False   # items waiting for their family's variant approval

    def admit(self, it):
        ctx = self.ctx
        if any(p["job_key"] == it["job_key"] for p in self.picked):
            return False
        account = it["_account"]
        usajobs = it.get("ats") == "usajobs" or it.get("lane") == "usajobs"
        if usajobs and self.usa >= self.max_usa:
            return False
        if account != "none" and self.acct >= self.max_acct:
            return False
        tenant = tenant_of(it)
        if account == "new" and tenant not in self.new_tenants and len(self.new_tenants) >= self.max_new:
            return False
        variant = ctx.approved(it.get("family", ""))
        force_a = ctx.variants_on and not variant
        held = force_a and self.forced >= self.tier_a
        if held and not self.take_held:
            self.held.setdefault(it["job_key"], it)  # waits for the re-approval; may fill a free slot last
            return False
        cn = jobkey.norm_company(it.get("company", ""))
        unit = caps.hiring_unit(it.get("title", ""), it.get("text") or "", cn) if caps.unit_mode(ctx.rules, cn) else ""
        v = caps.caps_verdict(ctx.index, cn, it.get("family", ""), it["job_key"], set(it.get("req_ids") or []),
                              netfetch.host_or_blank(it.get("apply_url") or it.get("url") or ""), ctx.today, ctx.rules,
                              simhash=it.get("simhash") or None, unit=unit)
        if v[0] == "DEFER":
            self.deferred[it["job_key"]] = (it, v)
            return False
        self.deferred.pop(it["job_key"], None)
        ctx.index.append(caps.queue_entry({"company": it.get("company", ""), "family": it.get("family", ""),
                                           "job_key": it["job_key"], "host": tenant, "ats": it.get("ats", "")},
                                          ctx.today, unit=unit))
        it["_variant"], it["_force_a"], it["_held"] = variant, force_a and not held, held
        self.forced += 1 if force_a and not held else 0
        self.usa += 1 if usajobs else 0
        if account != "none":
            self.acct += 1
        if account == "new" and tenant not in self.new_tenants:
            self.new_tenants.append(tenant)
        self.picked.append(it)
        return True

    def run(self, cands, fill_held=True):
        """Jobs handed over on an earlier day first, min_share floors (by
        family priority), then everything by score; then, with slots left,
        the items waiting for a variant approval (queued as prep_failed)."""
        n = self.size
        for it in cands:
            if it.get("_carried") and len(self.picked) < n:
                self.admit(it)
        fams = [f for f in self.ctx.families.values() if str(f.get("status", "active")) == "active"
                and float(f.get("min_share") or 0) > 0]
        for fam in sorted(fams, key=lambda f: (int(f.get("priority") or 2), str(f.get("id")))):
            floor = math.ceil(float(fam["min_share"]) * n)
            got = sum(1 for p in self.picked if p.get("family") == fam["id"])
            for it in cands:
                if got >= floor or len(self.picked) >= n:
                    break
                if it.get("family") == fam["id"] and self.admit(it):
                    got += 1
        for it in cands:
            if len(self.picked) >= n:
                break
            self.admit(it)
        if fill_held:
            self.take_held = True
            for it in [c for c in cands if c["job_key"] in self.held]:
                if len(self.picked) >= n:
                    break
                self.admit(it)
            self.take_held = False
        return self.picked

    def waiting(self, cands):
        """{family: jobs waiting for its variant's approval}: every eligible job
        of a family with no usable variant that is not one of the day's Tier A."""
        ctx = self.ctx
        if not ctx.variants_on:
            return {}
        out = {}
        tier_a = {p["job_key"] for p in self.picked if p.get("_force_a")}
        for it in cands:
            fam = it.get("family") or ""
            if fam and not ctx.approved(fam) and it["job_key"] not in tier_a:
                out[fam] = out.get(fam, 0) + 1
        return dict(sorted(out.items()))


def mark_tiers(picked, ctx, tier_a):
    """Tier A: forced (no approved variant) and USAJOBS first, then a tier-1
    referral, band 0-3 with fit >= 0.7, then the highest fit."""
    def reason(it):
        if it.get("ats") == "usajobs" or it.get("lane") == "usajobs":
            return 0, "usajobs"
        if it.get("_force_a"):
            return 0, "no_variant"
        if it["_parts"]["referral"]:
            return 1, "referral"
        if it.get("band") == "0-3" and float(it.get("fit") or 0) >= 0.7:
            return 2, "fresh_fit"
        return 3, "fit"
    ranked = sorted((it for it in picked if not it.get("_held")),
                    key=lambda it: (reason(it)[0], late_rank(it), -float(it.get("fit") or 0), -it["_score"],
                                    it["job_key"]))
    chosen = {it["job_key"] for it in ranked[:tier_a]}
    for it in picked:
        it["_tier"] = "A" if it["job_key"] in chosen or it.get("_force_a") else "B"
        it["_tier_reason"] = reason(it)[1] if it["_tier"] == "A" else ""


# ---------- session order ----------

def fits_after(out, it):
    if out and out[-1]["_cn"] == it["_cn"]:
        return False
    return not (len(out) >= 3 and all(o["_host"] == it["_host"] for o in out[-3:]))


def take(out, primary, fillers):
    """Place the first item of primary that fits; else a filler that fits; else
    the first of primary anyway (nothing fits)."""
    for lst in (primary, fillers):
        for i, it in enumerate(lst):
            if fits_after(out, it):
                out.append(lst.pop(i))
                return
    out.append(primary.pop(0))


def borderline(it):
    """The gates' seniority mark (R6): the posting asks more than the user has."""
    return it.get("seniority") == "borderline"


def low_rank(it):
    """The gates' low_rank mark: shift work the user ranks last ([gates]
    shift_work = "last") or a job in a relocation region ([[home.region]])."""
    return bool(it.get("low_rank"))


def late_rank(it):
    """0 for most jobs, 1 for borderline seniority, 2 for a low_rank job:
    the order they are taken and listed in, each group by fit."""
    return 2 if low_rank(it) else 1 if borderline(it) else 0


def sequence(picked, batch_size):
    """Jobs handed over on an earlier day with no `s` (to ask about), batch 1
    (no-account Tier B by fit, then score), the account block, the rest by
    fit, the items whose seniority is borderline (R6), the items marked
    low_rank (shift work ranked last, relocation), and last the items
    waiting for a variant approval (prep_failed). A new-account tenant's
    items come forward as the account block (grouped, one account for
    several jobs) only when its best item is among the day's best by fit: at
    or above the median fit of the day's other items. Any other tenant's
    items take their places by fit, so no account is made early for the
    day's weakest jobs. account_block names the block's tenants only (their
    accounts are made together at the block); a later tenant's account is
    made at its own job (apply.md, Accounts)."""
    for it in picked:
        it["_cn"] = caps.group({}, jobkey.norm_company(it.get("company", "")))
        it["_host"] = netfetch.host_or_blank(it.get("apply_url") or it.get("url") or "")

    def by_fit(items):  # best first (D6): the score picked them; fit (the TERMS overlap) orders them
        return sorted(items, key=lambda it: (-float(it.get("fit") or 0), -it["_score"], it["job_key"]))
    ordered = by_fit(picked)
    first = [it for it in ordered if it.get("_carried")]
    last = [it for it in ordered if it.get("_held") and not it.get("_carried")]
    late = [it for it in ordered if late_rank(it) and it not in first and it not in last]
    late.sort(key=late_rank)  # stable: borderline seniority by fit, then the low_rank jobs by fit
    ordered = [it for it in ordered if it not in first and it not in last and it not in late]
    new_items = [it for it in ordered if it["_account"] == "new"]
    tenants = list(dict.fromkeys(tenant_of(it) for it in new_items))
    forward = []
    for t in tenants:
        mine = [float(it.get("fit") or 0) for it in new_items if tenant_of(it) == t]
        others = [float(it.get("fit") or 0) for it in ordered if tenant_of(it) != t or it["_account"] != "new"]
        if not others or max(mine) >= statistics.median(others):
            forward.append(t)
    block_items = [it for it in new_items if tenant_of(it) in forward]
    later_new = [it for it in new_items if tenant_of(it) not in forward]  # never a filler in batch 1
    batch1 = [it for it in ordered if it["_account"] == "none" and it["_tier"] == "B"]
    rest = [it for it in ordered if it not in batch1 and it["_account"] != "new"]
    out = []
    while first:
        take(out, first, [])
    while len(out) < batch_size and batch1:
        take(out, batch1, rest)
    while len(out) < batch_size and rest:  # fewer than batch_size no-account items
        take(out, rest, [])
    rest = by_fit(batch1 + rest + later_new)
    after = len(out)
    groups = {t: [it for it in block_items if tenant_of(it) == t] for t in forward}
    block = []
    while any(groups.values()):
        for t in forward:
            if groups[t]:
                block.append(groups[t].pop(0))
    while block:
        take(out, block, rest)  # a filler from the rest only when no tenant item fits
    while rest:
        take(out, rest, [])
    while late:
        take(out, late, [])
    while last:
        take(out, last, [])
    # account_block.tenants are the accounts made at the block; a later tenant's account is made at its own turn
    return out, {"after_n": after, "tenants": forward}


# ---------- job folders ----------

def runner_names(folder):
    try:
        return {ln.strip() for ln in (folder / RUNNER_FILES).read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


def runner_write(folder, name, text):
    """A runner file (2.4): never in a claimed folder, never over a file the
    runner did not write; the name joins .runner_files. False when refused."""
    if (folder / ".claimed").exists():
        return False
    names = runner_names(folder)
    target = folder / name
    if target.exists() and name not in names:
        return False
    jobfs.atomic_write_text(target, text)
    if name not in names:
        jobfs.atomic_write_text(folder / RUNNER_FILES, "\n".join(sorted(names | {name})) + "\n")
    return True


def render_posting(it, today):
    head = [("URL", it.get("url")), ("Retrieved", it.get("first_seen") or today.isoformat()),
            ("Company", it.get("company")), ("Role", it.get("title")), ("Location", it.get("location")),
            ("Job key", it.get("job_key")), ("Requisition", ", ".join(it.get("req_ids") or [])),
            ("Posted", it.get("posted") or "undated"), ("Source", "runner")]  # R2: a date or 'undated'
    if it.get("employer_site"):
        head.append(("Employer site", it["employer_site"]))
    lines = [(k + ": " + " ".join(str(v or "").split())).rstrip() for k, v in head]
    return "\n".join(lines + ["", "## Description", "", it.get("text") or "", ""])


def make_folder(ctx, it, used):
    """applications/<date> <Company> - <Role> for one item (reused when the pool
    item already has one). Returns the relative folder path."""
    if it.get("folder") and (ctx.root / it["folder"]).is_dir():
        rel = it["folder"]
    else:
        name = jobfs.job_folder_name(ctx.today, it.get("company") or "Employer", it.get("title") or "Role")
        base, k = name, 2
        while name in used or (ctx.folder.applications / name).exists():
            name = f"{base} ({k})"
            k += 1
        rel = f"applications/{name}"
    used.add(Path(rel).name)
    folder = ctx.root / rel
    folder.mkdir(parents=True, exist_ok=True)
    md = it.get("markdown") or render_posting(it, ctx.today)
    runner_write(folder, "posting.md", md if md.endswith("\n") else md + "\n")
    rec = jobfs.Posting.from_dict(it).to_dict()
    runner_write(folder, "posting.json", json.dumps(rec, indent=1, ensure_ascii=True) + "\n")
    it["folder"] = rel
    return rel


def answers_status(folder):
    """complete | needs_you | sensitive_confirm from fill_plan.json when present."""
    plan = read_json(Path(folder) / "fill_plan.json", {})
    needs, sens = [], []
    for f in plan.get("fields") or []:
        if f.get("status") == "needs_you":
            needs.append(str(f.get("name") or f.get("label") or ""))
        elif f.get("status") == "sensitive_confirm":
            sens.append(str(f.get("name") or f.get("label") or ""))
    return ("needs_you" if needs else "sensitive_confirm" if sens else "complete"), needs, sens


def final_files(folder):
    """(resume, letter) Paths a strict Tier A build passed, from the job
    folder's final.json (shared decision 1: {"v": 1, "resume", "letter",
    "built_at", "verdict": "ok"}); None for a name that is not a plain file
    name in the folder or is missing. (None, None) when the build cites a
    library entry the user corrected since (shared decision 5): the item
    keeps its Tier B files."""
    import library
    folder = Path(folder)
    data = read_json(folder / "final.json", {})
    if data.get("v") != 1 or str(data.get("verdict", "")).lower() != "ok":
        return None, None
    if library.final_stale(folder, data):
        return None, None

    def one(name):
        name = name if isinstance(name, str) else ""
        if not name or "/" in name or "\\" in name or name in (".", "..") or Path(name).name != name:
            return None
        p = folder / name
        return p if p.is_file() else None
    return one(data.get("resume")), one(data.get("letter"))


def resume_status(ctx, it, folder):
    if it["_tier"] == "A":
        return "tier_a_ready" if final_files(folder)[0] else "tier_a_pending"
    if it.get("_held"):
        return "prep_failed"  # its family has no approved variant: an old resume_B.pdf is not sent
    if (folder / "resume_B.pdf").exists():
        return "built"
    if ctx.variants_on and not it.get("_variant"):
        return "prep_failed"
    return "fallback_base"


def queue_item(ctx, it, n, folder_rel):
    prof = profile_of(it.get("ats"))
    folder = ctx.root / folder_rel
    status, needs, sens = answers_status(folder)
    account = it["_account"]
    flags = [f for f in it.get("flags") or [] if f != "unsubmitted"] + (["unsubmitted"] if it.get("_carried") else [])
    return {
        "n": n, "job_key": it["job_key"], "company": it.get("company", ""), "role": it.get("title", ""),
        "location": it.get("location", ""), "ats": it.get("ats", "") or "other",
        "host": netfetch.host_or_blank(it.get("apply_url") or it.get("url") or ""),
        "apply_url": it.get("apply_url") or it.get("url") or "",
        "referral_url": it.get("referral_url") or ctx.referral_urls.get(it["job_key"], ""),
        "folder": folder_rel, "family": it.get("family", ""), "mode": it.get("mode") or "industry",
        "tier": it["_tier"], "tier_reason": it["_tier_reason"], "variant": it.get("_variant", ""),
        "posted": gates.posted_value(it.get("posted")), "age_days": it.get("age_days"), "band": it.get("band") or "",
        "repost_of": it.get("repost_of"), "score": it["_score"], "score_parts": it["_parts"],
        "account": account, "attach": prof["attach"], "captcha_risk": prof["captcha_risk"],
        "est_user_min": NEW_TENANT_MIN if account == "new" else prof["est_user_min"],
        "upload": f"upload/{plan_upload(n, ctx.last, it.get('company', ''))}",
        "resume_status": resume_status(ctx, it, folder), "answers_status": status, "needs": needs,
        "sensitive": sens,
        "referral": ctx.referral(it).get("referral") or {"state": "none", "contact_ref": "", "hold_until": ""},
        "posting_status": it.get("posting_status") or "read", "flags": flags,
    }


def check_queue(data):
    """Problems with a queue.json or addendum against the 2.5 schema ([] = ok)."""
    bad = []
    for k, t in QUEUE_KEYS.items():
        if not isinstance(data.get(k), t):
            bad.append(f"{k}: missing or not {t}")
    for it in data.get("items") or []:
        for k, t in ITEM_KEYS.items():
            if k not in it or not isinstance(it[k], t) or (t is int and isinstance(it[k], bool)):
                bad.append(f"#{it.get('n')} {k}: missing or wrong type")
        for k, allowed in ENUMS.items():
            if it.get(k) not in allowed:
                bad.append(f"#{it.get('n')} {k}={it.get(k)!r}")
        if set(it.get("score_parts") or {}) != set(SCORE_PARTS):
            bad.append(f"#{it.get('n')} score_parts keys")
        if set(it.get("referral") or {}) != {"state", "contact_ref", "hold_until"}:
            bad.append(f"#{it.get('n')} referral keys")
        name = Path(it.get("upload", "")).name
        if not UPLOAD_RE.match(name) or len(name) > jobfs.UPLOAD_MAX:
            bad.append(f"#{it.get('n')} upload {name!r}")
    ab = data.get("account_block") or {}
    if not isinstance(ab.get("after_n"), int) or not isinstance(ab.get("tenants"), list):
        bad.append("account_block shape")
    return bad


# ---------- build ----------

def usable_supply(ctx, cands, tier_a):
    """How many eligible pool items could be sent in this caps window: those
    the caps allow one after another (today's daily caps aside) and that have a
    resume to send, an approved variant or one of the day's Tier A places."""
    rules = dict(ctx.rules, per_company_per_day=0)
    index = [e for e in ctx.base_index if e["status"] != "Queued"]
    with_variant, without = 0, 0
    for it in cands:
        cn = jobkey.norm_company(it.get("company", ""))
        unit = caps.hiring_unit(it.get("title", ""), it.get("text") or "", cn) if caps.unit_mode(rules, cn) else ""
        v = caps.caps_verdict(index, cn, it.get("family", ""), it["job_key"], set(it.get("req_ids") or []),
                              netfetch.host_or_blank(it.get("apply_url") or it.get("url") or ""), ctx.today, rules,
                              simhash=it.get("simhash") or None, unit=unit)
        if v[0] != "ALLOW":
            continue
        index.append(caps.queue_entry({"company": it.get("company", ""), "family": it.get("family", ""),
                                       "job_key": it["job_key"], "host": tenant_of(it), "ats": it.get("ats", "")},
                                      ctx.today, unit=unit) | {"status": "Applied"})
        if ctx.approved(it.get("family", "")):
            with_variant += 1
        else:
            without += 1
    return with_variant + min(tier_a, without)


def widen_step(ctx, pool_count, target):
    """One widen step a day while the usable pool is under target (3.12)."""
    widen = ctx.profile.get("widen") or {}
    if pool_count >= target or not widen.get("enabled", True):
        return None
    data = read_json(ctx.folder.queue_state_json, {})
    w = data.get("widen") or {}
    on = list(w.get("on") or [])
    if w.get("last_on") == ctx.today.isoformat():
        return None
    nxt = next((s for s in widen.get("steps") or [] if s not in on), None)
    if not nxt:
        return None
    data["widen"] = {"on": on + [nxt], "last_on": ctx.today.isoformat()}
    data.setdefault("v", 1)
    data.setdefault("items", {})
    write_state(ctx.folder.queue_state_json, data)
    return nxt


def move_not_sent(ctx):
    """applications/<date> ... folders the runner made for jobs that got no
    tracker row within 30 days move to applications/_not_sent/<YYYY-MM>/."""
    apps = ctx.folder.applications
    if not apps.exists() or (ctx.store is None and ctx.folder.tracker.exists()):
        return 0
    tracked = set()
    if ctx.store is not None:
        for r in ctx.store.app_rows() + ctx.store.archived_rows():
            tracked.add(Path(str(r.get("Folder") or "").replace("\\", "/")).name)
    moved = 0
    for d in sorted(apps.iterdir()):
        m = re.match(r"(\d{4}-\d{2}-\d{2}) ", d.name)
        day = gates.to_date(m.group(1)) if m else None
        if not d.is_dir() or not day or (ctx.today - day).days <= NOT_SENT_DAYS:
            continue
        if not (d / RUNNER_FILES).exists() or (d / ".claimed").exists() or d.name in tracked:
            continue
        dest = ctx.folder.not_sent(day) / d.name
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(d, dest)
        moved += 1
    return moved


def load_items(root, pool_path):
    data = gates.load_pool(root, pool_path) if pool_path else gates.load_pool(root)
    return [dict(it) for it in data.get("items", []) if isinstance(it, dict) and it.get("job_key")], data


def save_pool_after(ctx, pool, drop, picked, deferred, data):
    gone = {it["job_key"] for it in drop}
    queued = {it["job_key"]: it for it in picked}
    out = []
    for it in pool:
        if it["job_key"] in gone:
            continue
        it = {k: v for k, v in it.items() if not k.startswith("_")}
        if it["job_key"] in queued:
            q = queued[it["job_key"]]
            it.update(queued_on=ctx.today.isoformat(), folder=q.get("folder", ""))
        if it["job_key"] in deferred:
            _, v = deferred[it["job_key"]]
            it.update(deferred_rule=v[1], deferred_until=v[2].isoformat() if v[2] else "")
        out.append(it)
    gates.save_pool(ctx.root, out, counts=data.get("counts", {}), needs_you=data.get("needs_you", []),
                    date_=ctx.today)


def note_seen(ctx, picked, deferred):
    seen = gates.Seen(ctx.root, ctx.today)
    for it in picked:
        seen.record(jobfs.Posting.from_dict(it), "queued")
    for it, v in deferred.values():
        seen.record(jobfs.Posting.from_dict(it), f"deferred:{v[1]}")


def build(root, today=None, pool_path=None, emit=print, prep=None):
    """Build queue/<date>/queue.json. prep(item_dict, folder) may update an
    item (resume_status, answers_status, needs, sensitive) before writing."""
    root = Path(root)
    today = today or jobfs.today(root)
    f = jobfs.Folder(root)
    if f.queue_json(today).exists() and events_for(root, today):
        emit(f"FROZEN {today.isoformat()}: events exist for this date; late finds go to an addendum")
        return None
    if f.queue_json(today).exists() and session_active(root):
        # only a rebuild waits: before today's queue exists there are no numbers to protect
        emit("WAIT a session is active (claude/session.json beat under 20 minutes); the build waits")
        return None
    fold_events(root, today, emit=lambda s: None)  # a session's last events, even with no tick after them
    ctx = Context(root, today)
    pool, data = load_items(root, pool_path)
    target = int(ctx.daily.get("target", 20))
    size = math.ceil(target * float(ctx.daily.get("queue_factor", 1.5)))
    # Numbers and jobs given already today (an addendum or a job from a link
    # before queue.json) are kept: this list numbers around them.
    other_ns, other_keys = given_elsewhere(root, today)
    cands, drop = eligible(pool, ctx, other_keys)
    ctx.load_referrals(cands)
    for it in cands:
        score(it, ctx)
    # borderline seniority (R6) is taken only after the rest, so it never holds a cap slot a better job could
    cands.sort(key=lambda it: (2 if low_rank(it) else int(ctx.borderline(it)), -it["_score"], it["job_key"]))
    asm = Assembly(ctx, size)
    picked = asm.run(cands)
    mark_tiers(picked, ctx, asm.tier_a)
    ordered, block = sequence(picked, int(ctx.daily.get("batch_size", 10)))
    used, items, skipped = set(), [], set()
    n = max(other_ns) if other_ns and not f.queue_json(today).exists() else 0
    after = block["after_n"]
    built_before_block = 0
    for i, it in enumerate(ordered):
        k = n + 1
        while k in other_ns:
            k += 1
        # One item whose folder or files fail (bad data, a held file) is one
        # ERROR line: it stays in the pool for another day and the list is built.
        try:
            rel = make_folder(ctx, it, used)
            q = queue_item(ctx, it, k, rel)
        except Exception as e:  # noqa: BLE001  one item never stops the day's list
            emit(f"ERROR queue item {it.get('job_key', '?')}: {type(e).__name__}: {str(e)[:160]}")
            skipped.add(it["job_key"])
            continue
        if prep:
            try:
                prep(q, root / rel)
            except Exception as e:  # noqa: BLE001  its Tier B files failed: it is listed, never sent as built
                emit(f"ERROR prep #{k} {it.get('job_key', '?')}: {type(e).__name__}: {str(e)[:160]}")
                if q["tier"] == "B":
                    q["resume_status"] = "prep_failed"
        n = k
        items.append(q)
        built_before_block += i < after
    if skipped:
        picked = [it for it in picked if it["job_key"] not in skipped]
    block["after_n"] = items[built_before_block - 1]["n"] if built_before_block else (items[0]["n"] - 1 if items else 0)
    waiting = asm.waiting(cands)
    counts = dict(data.get("counts") or {})
    counts = {"seen": int(counts.get("seen", 0)), "passed": int(counts.get("passed", len(pool))),
              "discarded": int(counts.get("discarded", 0)), "deferred": len(asm.deferred),
              "variant_wait": waiting}
    pool_left = len(cands) - len(items)
    q = {"v": 1, "date": today.isoformat(), "built_at": jobfs.now_iso(root), "runner_version": jobfs.VERSION,
         "target": target, "size": len(items), "pool_left": pool_left, "items": items,
         "account_block": block, "counts": counts}
    written = jobfs.atomic_write_json(f.queue_json(today), q)
    save_pool_after(ctx, pool, drop, picked, asm.deferred, data)
    note_seen(ctx, picked, asm.deferred)
    moved = move_not_sent(ctx)
    d = ctx.daily
    emit(f"QUEUE size={len(items)} pool_left={pool_left} new_account_tenants<={d.get('new_account_tenants_per_day', 3)} "
         f"account_required<={d.get('account_required_per_day', 6)} usajobs<={d.get('usajobs_per_day', 1)}")
    emit(f"MIX tier_a={sum(1 for i in items if i['tier'] == 'A')} new_account_tenants={len(asm.new_tenants)} "
         f"account_required={asm.acct} usajobs={asm.usa} deferred={len(asm.deferred)}")
    for fam, k in waiting.items():
        emit(f"WAIT_VARIANT {fam} {k} jobs")
    supply = usable_supply(ctx, cands, asm.tier_a) if len(cands) >= target else len(cands)
    if supply < target:
        step = widen_step(ctx, supply, target)
        if step:
            emit(f"WIDEN {step}")
    if moved:
        emit(f"NOT_SENT moved {moved} folders to applications/_not_sent/")
    emit(f"WROTE {f.queue_json(today).relative_to(root).as_posix()}")
    return written


# ---------- addendum and more ----------

def numbers_used(root, day):
    """(highest number given today, job keys given), jobs from links included."""
    top, keys = 0, set()
    for it in day_items(root, day):
        top = max(top, int(it.get("n") or 0))
        keys.add(it.get("job_key"))
    return top, keys


def given_elsewhere(root, day):
    """(numbers, job keys) the day's addenda and jobs from links hold: all but queue.json."""
    items = []
    for p in queue_files(root, day)[1:]:
        if p.exists():
            items += [it for it in read_json(p, {}).get("items") or [] if isinstance(it, dict)]
    keys = {it.get("job_key") for it in items}
    items += [it for it in events.user_items(root, day) if it["job_key"] not in keys]
    return {int(it.get("n") or 0) for it in items if it.get("n")}, {it.get("job_key") for it in items}


def addendum(root, today=None, pool_path=None, limit=None, emit=print):
    """New numbered items after the highest number already given; queue.json and
    upload/ are left as they are."""
    root = Path(root)
    today = today or jobfs.today(root)
    f = jobfs.Folder(root)
    if not f.queue_json(today).exists():
        # Numbers given now would collide with the build's; late finds are in the pool for it.
        emit(f"ADDENDUM none: no list for {today.isoformat()} yet (the daily builds it first)")
        return None
    top, keys = numbers_used(root, today)
    ctx = Context(root, today, include_queued=True)
    pool, data = load_items(root, pool_path)
    limit = int(limit or ctx.daily.get("more_size", 10))
    cands, _ = eligible(pool, ctx, keys)
    ctx.load_referrals(cands)
    for it in cands:
        score(it, ctx)
    cands.sort(key=lambda it: (2 if low_rank(it) else int(ctx.borderline(it)), -it["_score"], it["job_key"]))
    asm = Assembly(ctx, limit)
    asm.tier_a = 0  # Tier A prep happens in the morning; late items are Tier B only
    picked = asm.run(cands, fill_held=False)  # `more` is for jobs it can send today
    mark_tiers(picked, ctx, 0)
    ordered, _ = sequence(picked, 0)
    used, items, skipped = set(), [], set()
    for it in ordered:
        try:  # one item that fails is one ERROR line (as in build); it stays in the pool
            items.append(queue_item(ctx, it, top + len(items) + 1, make_folder(ctx, it, used)))
        except Exception as e:  # noqa: BLE001
            emit(f"ERROR queue item {it.get('job_key', '?')}: {type(e).__name__}: {str(e)[:160]}")
            skipped.add(it["job_key"])
    if skipped:
        picked = [it for it in picked if it["job_key"] not in skipped]
    # A job from a link may have taken a number meanwhile (an added event):
    # these items are not written yet, so they move up after it.
    top_now, keys_now = numbers_used(root, today)
    if top_now != top:
        items = [q for q in items if q["job_key"] not in keys_now]
        for i, q in enumerate(items, 1):
            q["n"] = top_now + i
            q["upload"] = f"upload/{plan_upload(q['n'], ctx.last, q.get('company', ''))}"
        top = top_now
    if not items:
        waiting = asm.waiting(cands)
        if waiting:
            emit("ADDENDUM none: " + "; ".join(f"{fam} needs a quick re-approval ({k} job{'s' if k != 1 else ''} wait"
                                               f"{'s' if k == 1 else ''})" for fam, k in waiting.items()))
        else:
            emit("ADDENDUM none: nothing new passes the caps")
        return None
    hhmm = jobfs.now(root).strftime("%H%M")
    path, k = f.addendum(today, hhmm), 2
    while path.exists():
        path = f.addendum(today, f"{hhmm}_{k}")
        k += 1
    data_out = {"v": 1, "date": today.isoformat(), "built_at": jobfs.now_iso(root), "runner_version": jobfs.VERSION,
                "target": int(ctx.daily.get("target", 20)), "size": len(items), "pool_left": len(cands) - len(items),
                "items": items, "account_block": {"after_n": top, "tenants": []},
                "counts": {"seen": 0, "passed": len(cands), "discarded": 0, "deferred": len(asm.deferred)}}
    jobfs.atomic_write_json(path, data_out)
    if not pool_path:
        save_pool_after(ctx, pool, [], picked, asm.deferred, data)
    note_seen(ctx, picked, asm.deferred)
    emit(f"ADDENDUM {path.name} items={top + 1}-{top + len(items)}")
    if session_active(root):
        emit("SESSION active: upload/ left as it is")
    return path


# ---------- jobs from links (addendum_user, spec 5.11) ----------

def posting_head(folder):
    """The `Key: value` lines at the top of a job folder's posting.md."""
    head = {}
    try:
        text = (Path(folder) / "posting.md").read_text(encoding="utf-8-sig")
    except OSError:
        return head
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            break
        k, sep, v = line.partition(":")
        if sep and k.strip() and k.strip() not in head:
            head[k.strip()] = v.strip()
    return head


def plan_fields(plan):
    """(answers_status, needs [Q- ids], sensitive [kinds]) from a fill plan, as
    the runner records them for its own items."""
    needs, sens = [], []
    for f in plan.get("fields") or []:
        if f.get("status") == "needs_you":
            needs.append(str(f.get("qid") or f.get("name") or f.get("label") or ""))
        elif f.get("status") == "sensitive_confirm":
            kind = sensitive_kind(f)
            if kind and kind not in sens:
                sens.append(kind)
    status = "needs_you" if needs else "sensitive_confirm" if sens else "complete"
    return status, needs, sens


def sensitive_kind(field):
    """salary, sponsorship, ...: the profile field a sensitive answer came from,
    else its label as a word."""
    src = str(field.get("answer_source") or "")
    kind = src.split(":", 1)[1] if src.startswith("profile:") else re.sub(r"[^a-z0-9]+", "_",
                                                                          str(field.get("label", "")).lower())
    return kind.strip("_")


def user_resume(folder):
    """The tailored resume a job from a link will send: the one final.json
    names (a strict build), else the single-job flow's "<Name> Resume.pdf"
    (the newest); None before one is built, and None when final.json's
    build cites a library entry the user corrected since (D5): the newest
    tailored PDF is then that same stale build, so it is never sent."""
    import library
    folder = Path(folder)
    data = read_json(folder / "final.json", {})
    if data.get("v") == 1 and str(data.get("verdict", "")).lower() == "ok" and library.final_stale(folder, data):
        return None
    final = final_files(folder)[0]
    if final:
        return final
    built = sorted((p for p in folder.glob("*Resume*.pdf") if p.name != "resume_B.pdf"),
                   key=lambda p: (p.stat().st_mtime, p.name))
    return built[-1] if built else None


def account_for(root, ats, url, store=None):
    """none, exists or new for one ATS host, from the tracker's Accounts sheet."""
    if profile_of(ats).get("account") == "none":
        return "none"
    if store is None:
        try:
            store = caps.open_store(root)
        except Exception:  # no tracker, or openpyxl missing here: count it as a new site
            store = None
    sites = set()
    for r in (store.accounts.rows() if store is not None and getattr(store, "accounts", None) is not None else []):
        s = str(r.get("Site") or "").strip().lower()
        s = (netfetch.host_or_blank(s) if "://" in s else s).removeprefix("www.")
        if s:
            sites.add(s)
    host = "usajobs.gov" if ats == "usajobs" else netfetch.host_or_blank(url or "")
    return "exists" if host and any(host == s or host.endswith("." + s) or s in host for s in sites) else "new"


def refresh_user_item(root, it):
    """A job from a link, its statuses read from its folder again: the session
    builds its resume and fill plan around the add."""
    folder = Path(root) / str(it.get("folder", ""))
    via = it.get("added_by", "")
    if via == "add" and (folder / "resume_B.pdf").exists():
        it["tier"] = "B"
    if it.get("tier") == "B":
        log = read_json(folder / "tune_log.json", {})
        it["resume_status"] = "fallback_base" if str(log.get("status", "")).startswith("fallback") else "built"
        it["variant"] = str(log.get("variant") or it.get("variant") or "")
        it["family"] = it.get("family") or str(log.get("family") or "")
    else:
        it["resume_status"] = "tier_a_ready" if user_resume(folder) else "tier_a_pending"
    plan = read_json(folder / "fill_plan.json", {})
    if plan.get("fields"):
        it["answers_status"], it["needs"], it["sensitive"] = plan_fields(plan)
    return it


def user_item(root, n, job_key, folder_rel, via, family="", store=None):
    """One job from a link as a 2.5 queue item: no score, Tier A for a pasted
    link (the single-job flow), Tier B for /job add with its resume_B files."""
    root = Path(root)
    head = posting_head(root / folder_rel)
    prefix = job_key.split(":", 1)[0]
    ats = prefix if prefix in ATS_PROFILES else "other"
    prof = profile_of(ats)
    url = head.get("URL", "")
    if url and events.url_refusal(url):
        url = ""  # a job-board or bad-url link is never recorded as the apply link (shared decision 1)
    company = head.get("Company", "") or "Employer"
    account = account_for(root, ats, url, store)
    it = {"n": n, "job_key": job_key, "company": company, "role": head.get("Role", ""),
          "location": head.get("Location", ""), "ats": ats, "host": netfetch.host_or_blank(url) if url else "",
          "apply_url": url, "referral_url": "", "folder": folder_rel, "family": family or "", "mode": "industry",
          "tier": "A", "tier_reason": "user", "variant": "",  # R2: posted is a YYYY-MM-DD date or None (undated)
          "posted": head.get("Posted") if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", head.get("Posted") or "") else None,
          "age_days": None, "band": "", "repost_of": None, "score": 0, "score_parts": {k: 0 for k in SCORE_PARTS},
          "account": account, "attach": prof["attach"], "captcha_risk": prof["captcha_risk"],
          "est_user_min": NEW_TENANT_MIN if account == "new" else prof["est_user_min"],
          "upload": f"upload/{plan_upload(n, last_name(root), company)}", "resume_status": "tier_a_pending",
          "answers_status": "complete", "needs": [], "sensitive": [],
          "referral": {"state": "none", "contact_ref": "", "hold_until": ""}, "posting_status": "read",
          "flags": [], "added_by": via}
    return refresh_user_item(root, it)


def user_addendum(root, today=None, emit=print):
    """queue/<date>/addendum_user.json from the day's added events; written only
    when an item changed. Returns the path written, or None."""
    root = Path(root)
    today = today or jobfs.today(root)
    f = jobfs.Folder(root)
    path = f.queue_dir(today) / "addendum_user.json"
    old = read_json(path, {})
    items = {it["job_key"]: it for it in old.get("items") or [] if isinstance(it, dict) and it.get("job_key")}
    # Numbers and keys the runner's own files gave today: an added event never takes one (SEC-14).
    given_n, given_keys = set(), set()
    for p in queue_files(root, today):
        if p.name != path.name and p.exists():
            for it in read_json(p, {}).get("items") or []:
                if isinstance(it, dict):
                    given_n.add(it.get("n"))
                    given_keys.add(it.get("job_key"))
    for ev_it in events.user_items(root, today):
        if ev_it["job_key"] in items or ev_it["job_key"] in given_keys or ev_it["n"] in given_n:
            continue
        # The full 2.5 item, with what the session recorded at the add kept as it
        # was; the link, its host and the upload name always come from the job
        # folder's posting.md and the item's own number, never from the event.
        full = user_item(root, ev_it["n"], ev_it["job_key"], ev_it.get("folder", ""), ev_it["added_by"],
                         ev_it.get("family", ""))
        full.update({k: ev_it[k] for k in ("company", "role", "location", "ats", "account", "attach")
                     if ev_it.get(k)})
        items[ev_it["job_key"]] = full
    if not items:
        return None
    new = sorted((refresh_user_item(root, dict(it)) for it in items.values()), key=lambda it: it["n"])
    if new == old.get("items"):
        return None
    data = {"v": 1, "date": today.isoformat(), "built_at": jobfs.now_iso(root), "runner_version": jobfs.VERSION,
            "target": int(jobfs.settings(root)["daily"].get("target", 20)), "size": len(new), "pool_left": 0,
            "items": new, "account_block": {"after_n": new[0]["n"] - 1, "tenants": []},
            "counts": {"seen": 0, "passed": 0, "discarded": 0, "deferred": 0}}
    jobfs.atomic_write_json(path, data)
    jobfs.prev_path(path).unlink(missing_ok=True)  # the readers skip *.prev.json; keep only the real file
    emit(f"ADDENDUM {path.name} items={','.join(str(it['n']) for it in new)}")
    return path


# ---------- uploads ----------

def unique_dest(folder, name):
    dest, k = folder / name, 2
    while dest.exists():
        stem, ext = os.path.splitext(name)
        dest, k = folder / f"{stem}_{k}{ext}", k + 1
    return dest


def move_to_done(root, path, day):
    done = jobfs.Folder(root).upload_done(day)
    done.mkdir(parents=True, exist_ok=True)
    os.replace(path, unique_dest(done, path.name))


def stage_uploads(root, today=None, emit=print):
    """Copy built files to upload/ under their NN names; move applied files and
    earlier days' leftovers to upload/_done/<date>/. Never deletes."""
    root = Path(root)
    today = today or jobfs.today(root)
    f = jobfs.Folder(root)
    f.upload.mkdir(parents=True, exist_ok=True)
    manifest_path = f.state / "upload_manifest.json"
    manifest = read_json(manifest_path, {})
    items = []
    for p in queue_files(root, today):
        if p.exists():
            items += read_json(p, {}).get("items") or []
    planned = {}
    for it in items:
        base = Path(it.get("upload", "")).name
        if base:
            planned[base] = it
            planned[re.sub(r"^(\d{2})_", r"\1c_", base).replace("_Resume_", "_Cover_")] = it
    active = session_active(root)
    moved, held = 0, set()
    if not active:
        old_day = manifest.get("date") or ""
        same_day = old_day == today.isoformat()
        zone = jobfs.now(root).tzinfo  # file times in the folder's [time] timezone, not the PC's
        for p in sorted(f.upload.iterdir()):
            if not p.is_file() or p.name.startswith(".") or (same_day and p.name in planned):
                continue
            made = datetime.fromtimestamp(p.stat().st_mtime, zone).date()
            # today's do-yourself files (E<n>_..., spec 5.8) sit beside the NN files
            if re.match(r"E\d+_", p.name) and made == today:
                continue
            # an earlier day's file (even one with today's name) goes to that day's _done folder
            day = old_day if old_day and not same_day else made.isoformat()
            try:
                move_to_done(root, p, day)
            except PermissionError:  # open in a PDF viewer: it moves on a later run
                held.add(p.name)
                emit(f"WARN upload/{p.name} is open in another program; it moves on a later run")
                continue
            moved += 1
    applied = {it["job_key"] for it in events.overlay(root, today)["items"].values() if it.get("state") == "applied"}
    staged, warn, files = 0, 0, {}
    for it in items:
        name = Path(it.get("upload", "")).name
        dest = f.upload / name
        if it.get("job_key") in applied:
            for target in (dest, f.upload / re.sub(r"^(\d{2})_", r"\1c_", name).replace("_Resume_", "_Cover_")):
                if target.exists():
                    try:
                        move_to_done(root, target, today)
                    except PermissionError:
                        emit(f"WARN upload/{target.name} is open in another program; it moves on a later run")
                        continue
                    moved += 1
            continue
        if name in held:
            # an earlier day's file under today's name could not move: never offer it as today's
            warn += 1
            emit(f"WARN #{it.get('n'):02d} upload/{name} is an earlier day's file still open; "
                 "attach from the job folder")
            continue
        if it.get("resume_status") == "prep_failed":
            continue  # waits for its family's variant approval; never an old file
        folder = root / it.get("folder", "")
        final, final_letter = final_files(folder) if it.get("tier") == "A" else (None, None)
        src = final or folder / "resume_B.pdf"  # a Tier A build that passed --strict, else its Tier B file
        if not dest.exists():
            if not src.exists():
                if it.get("added_by"):
                    continue  # a job from a link: its resume is the session's, attached from its folder
                warn += 1
                emit(f"WARN #{it.get('n'):02d} no built resume in {it.get('folder')}")
                continue
            shutil.copyfile(src, dest)
            staged += 1
        files[name] = it.get("job_key")
        cover = final_letter if final else folder / "cover_B.pdf"
        if cover is None:
            continue  # a Tier A build with no letter: no Tier B letter goes with its resume
        cname = re.sub(r"^(\d{2})_", r"\1c_", name).replace("_Resume_", "_Cover_")
        if cover.exists() and not (f.upload / cname).exists():
            shutil.copyfile(cover, f.upload / cname)
            staged += 1
            files[cname] = it.get("job_key")
    jobfs.atomic_write_text(manifest_path, json.dumps({"date": today.isoformat(), "files": files},
                                                      indent=1, sort_keys=True) + "\n")
    emit(f"STAGED {staged} files, moved {moved} to upload/_done/" + (", session active" if active else ""))
    return staged, moved, warn


# ---------- fold events ----------

def load_state(root):
    data = read_json(jobfs.Folder(root).queue_state_json, {})
    data.pop("_rev", None)
    data.pop("_sha256", None)
    data.setdefault("v", 1)
    data.setdefault("items", {})
    data.setdefault("folded", [])
    return data


def write_state(path, data):
    jobfs.atomic_write_json(path, {k: v for k, v in data.items() if k not in ("_rev", "_sha256")})


def day_tenant(root, day, job_key, cache):
    """The account tenant (host) of a job in that day's list, or ''."""
    if day not in cache:
        cache[day] = {it.get("job_key"): tenant_of(it) for it in day_items(root, day)}
    return cache[day].get(job_key, "")


def fold_events(root, today=None, emit=print):
    """Fold queue events into state/queue_state.json (per job key: the last state,
    reason, date, n, the id of that event; the days the user skipped it; the
    tenant an account_locked block was on). An event older than the one a job's
    record holds changes nothing, so a file that waits in runner/in/events for
    days never undoes a later state. Writes nothing when nothing changed."""
    root = Path(root)
    today = today or jobfs.today(root)
    path = jobfs.Folder(root).queue_state_json
    before = load_state(root)
    data = json.loads(json.dumps(before))
    done = set(data["folded"])
    new, tenants = 0, {}
    for ev in events.recent(root, today):
        if ev["type"] not in events.QUEUE_TYPES or ev["id"] in done or not ev.get("job_key"):
            continue
        done.add(ev["id"])
        it = data["items"].setdefault(ev["job_key"], {"history": []})
        if ev["id"] <= str(it.get("id") or ""):
            continue  # older than this job's last folded event
        p = ev.get("payload") or {}
        it.update(state=ev["type"], reason=p.get("reason", ""), date=ev.get("date", ""), n=ev.get("n"),
                  at=ev.get("at", ""), id=ev["id"])
        it["history"] = (it.get("history") or [])[-9:] + [ev["type"]]
        if ev["type"] == "skipped" and p.get("reason") == "user" and ev.get("date"):
            it["user_skips"] = sorted(set(it.get("user_skips") or []) | {ev["date"]})[-USER_SKIP_DROP - 3:]
        if ev["type"] == "blocked" and p.get("reason") == "account_locked":
            it["tenant"] = day_tenant(root, ev.get("date") or today.isoformat(), ev["job_key"], tenants) or \
                it.get("tenant", "")
        new += 1
    first = (today - timedelta(days=FOLDED_DAYS)).strftime("%Y%m%d")
    waiting = {events.NAME_RE.match(p.name).group(1) for p in events.pending_files(root) if events.NAME_RE.match(p.name)}
    data["folded"] = sorted(i for i in done if i[:8] >= first or i in waiting)
    if data != before or not path.exists():
        write_state(path, data)
    emit(f"FOLDED {new} events items={len(data['items'])}")
    return data


# ---------- CLI ----------

def parse_day(s, root):
    if not s:
        return jobfs.today(root)
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"--date must be YYYY-MM-DD, got '{s}'")


def cmd_build(a):
    build(a.root, parse_day(a.date, a.root), a.pool)
    return 0


def cmd_addendum(a):
    addendum(a.root, parse_day(a.date, a.root), a.pool, a.limit)
    return 0


def cmd_stage(a):
    stage_uploads(a.root, parse_day(a.date, a.root))
    return 0


def cmd_fold(a):
    fold_events(a.root, parse_day(a.date, a.root))
    return 0


def cmd_user(a):
    if user_addendum(a.root, parse_day(a.date, a.root)) is None:
        print("ADDENDUM addendum_user.json unchanged")
    return 0


def build_parser():
    ap = Parser(prog="jobqueue.py", description="Queue builder (spec 3.12).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, func, hlp in (("build", cmd_build, "Build queue/<date>/queue.json from the pool."),
                            ("addendum", cmd_addendum, "Append new items (late finds or more)."),
                            ("stage-uploads", cmd_stage, "Stage NN upload files; move done ones."),
                            ("fold-events", cmd_fold, "Fold queue events into state/queue_state.json."),
                            ("user-addendum", cmd_user, "Write addendum_user.json from the added events.")):
        p = sub.add_parser(name, help=hlp, allow_abbrev=False)
        p.add_argument("--root", required=True)
        p.add_argument("--date")
        if name in ("build", "addendum"):
            p.add_argument("--pool", help="a pool file (default state/pool.json)")
        if name == "addendum":
            p.add_argument("--limit", type=int, help="at most this many items (default more_size)")
        p.set_defaults(func=func)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if not Path(args.root).is_dir():
        print(f"ERROR --root {args.root} is not a folder")
        return 1
    try:
        return args.func(args)
    except caps.tracker.MissingPackage:
        print("MISSING openpyxl: run  pip install openpyxl  and run again")
        return 2
    except caps.tracker.TrackerError as e:
        print(f"ERROR {e}")
        return e.code
    except (jobfs.ConfigError, OSError, ValueError) as e:
        print(f"ERROR {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
