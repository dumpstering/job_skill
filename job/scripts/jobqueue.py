"""Queue builder for the runner (spec 3.12, 2.5 queue.json, 2.7.3, 5.2, 5.3).

Usage:
    python jobqueue.py build         --root R [--date YYYY-MM-DD] [--pool pool.json]
    python jobqueue.py addendum      --root R [--date YYYY-MM-DD] [--pool new.json] [--limit N]
    python jobqueue.py stage-uploads --root R [--date YYYY-MM-DD]
    python jobqueue.py fold-events   --root R [--date YYYY-MM-DD]

build scores the pool (state/pool.json, or --pool), walks it by score and
writes queue/<date>/queue.json with N = ceil(target x queue_factor) items:
    score = fresh[band] + 30*fit + 20*referral_tier1 + priority(p1=10,p2=5,p3=0)
            + family_w - cost(account: none 0, exists 3, new 8) - 10 if the company
            had an application in the last 30 days
taking items while caps.caps_verdict allows them (each pick is reserved, so
"apply to all" never breaks a cap; a DEFER keeps the item in the pool with
its eligible_on date), at most 3 new-account tenants, 6 account-required
items and 1 USAJOBS item, and each active family's min_share where the pool
allows. The top tier_a_per_day items are Tier A (a tier-1 referral, USAJOBS,
band 0-3 with fit >= 0.7, then the highest fit); until a family's variant is
approved its items can only be Tier A. The order: batch 1 is no-account Tier B
items by score, then the account block (new-account tenants grouped by
tenant), then the rest by score; never the same employer twice in a row and
at most 3 in a row on one ATS host. Job folders get posting.md, posting.json
and .runner_files. Folders of jobs that got no tracker row within 30 days
move to applications/_not_sent/<YYYY-MM>/ (never deleted). When the pool is
under target, one widen step a day is switched on for the next gates run.
    QUEUE size=<n> pool_left=<n> new_account_tenants<=3 account_required<=6 usajobs<=1
    MIX tier_a=<n> new_account_tenants=<n> account_required=<n> usajobs=<n> deferred=<n>
    WIDEN <step>
    WROTE queue/<date>/queue.json
queue.json is immutable once any event for that date exists (FROZEN), and the
build waits while claude/session.json has a beat under 20 minutes old (WAIT).

addendum appends new items after the highest number already given (late finds
from --pool, or `more`: the next more_size items from the pool) to
queue/<date>/addendum_<HHMM>.json; queue.json and upload/ are never touched
while a session is active.

stage-uploads copies each item's built resume (resume_B.pdf, or resume.pdf for
a ready Tier A item) to upload/NN_<Last>_Resume_<Employer12>.pdf (NNc_ for a
cover letter), moves files of applied items and earlier days' leftovers to
upload/_done/<date>/, and deletes nothing.

fold-events folds queue events (started ... blocked) into state/queue_state.json;
running it twice gives the same bytes.
Stdlib only (plus openpyxl when the tracker is read directly, mode M).

Exit codes: 0 ok (also FROZEN and WAIT), 1 bad input (ERROR ...).
"""
import argparse
import json
import math
import os
import re
import shutil
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
EXCLUDE_STATES = {"applied", "handed_over", "parked"}
PERMANENT_BLOCKS = {"sso_required", "unsafe_verification", "knockout_at_form", "closed"}

# Per ATS: account (none, or one per tenant), attach order (first everywhere
# until the day-1 supervised test, 5.4), CAPTCHA risk, his minutes (1.6) and
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
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


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
            index = [e for e in index if e["status"] != "Queued"]
        self.base_index = list(index)
        self.index = index
        self.rules = caps.load_rules(root, self.store)
        self.sites = self.account_sites()
        self.weights = (read_json(self.folder.weights_json, {}).get("family_w") or {})
        self.referrals = {}
        self.qstate = read_json(self.folder.queue_state_json, {}).get("items", {})
        self.acted = {e["job_key"] for e in self.base_index if e["job_key"]}
        self.recent_companies = {caps.group(self.rules, e["company_norm"]) for e in self.base_index
                                 if e["date"] and caps.in_window(e["date"], today, 30)}
        self.variants_on = self.folder.variants.exists()
        self._approved = {}
        self.last = last_name(root)

    def account_sites(self):
        rows = []
        if self.store is not None and getattr(self.store, "accounts", None) is not None:
            rows = self.store.accounts.rows()
        out = set()
        for r in rows:
            s = str(r.get("Site") or "").strip().lower()
            s = (netfetch.host_of(s) if "://" in s else s).removeprefix("www.")
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
    return netfetch.host_of(it.get("apply_url") or it.get("url") or "")


# ---------- scoring ----------

def age_now(it, today):
    """(age_days, band, points) at today, or (None, None, 0) when stale."""
    basis = it.get("date_basis") or ("posted" if it.get("posted") else "undated")
    if basis == "undated":
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

def eligible(pool, ctx, keys_taken=()):
    """Pool items that may be queued today, and the ones that leave the pool."""
    keep, drop = [], []
    taken = set(keys_taken)
    for it in pool:
        key = it.get("job_key", "")
        st = ctx.qstate.get(key) or {}
        age, band, _ = age_now(it, ctx.today)
        if band is None or it.get("closed") or st.get("state") == "applied" or \
                (st.get("state") == "blocked" and st.get("reason") in PERMANENT_BLOCKS) or key in ctx.acted:
            drop.append(it)
            continue
        if key in taken or it.get("parked") or it.get("diy") or st.get("state") in EXCLUDE_STATES:
            continue
        until = gates.to_date(it.get("deferred_until"))
        if until and until > ctx.today:
            continue
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
        if force_a and self.forced >= self.tier_a:
            return False
        cn = jobkey.norm_company(it.get("company", ""))
        v = caps.caps_verdict(ctx.index, cn, it.get("family", ""), it["job_key"], set(it.get("req_ids") or []),
                              netfetch.host_of(it.get("apply_url") or it.get("url") or ""), ctx.today, ctx.rules,
                              simhash=it.get("simhash") or None)
        if v[0] == "DEFER":
            self.deferred[it["job_key"]] = (it, v)
            return False
        self.deferred.pop(it["job_key"], None)
        ctx.index.append(caps.queue_entry({"company": it.get("company", ""), "family": it.get("family", ""),
                                           "job_key": it["job_key"], "host": tenant, "ats": it.get("ats", "")},
                                          ctx.today))
        it["_variant"], it["_force_a"] = variant, force_a
        self.forced += 1 if force_a else 0
        self.usa += 1 if usajobs else 0
        if account != "none":
            self.acct += 1
        if account == "new" and tenant not in self.new_tenants:
            self.new_tenants.append(tenant)
        self.picked.append(it)
        return True

    def run(self, cands):
        """min_share floors first (by family priority), then everything by score."""
        n = self.size
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
        return self.picked


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
    ranked = sorted(picked, key=lambda it: (reason(it)[0], -float(it.get("fit") or 0), -it["_score"], it["job_key"]))
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


def sequence(picked, batch_size):
    """Batch 1 (no-account Tier B by score), the account block (new-account
    tenants grouped by tenant), then the rest by score."""
    for it in picked:
        it["_cn"] = caps.group({}, jobkey.norm_company(it.get("company", "")))
        it["_host"] = netfetch.host_of(it.get("apply_url") or it.get("url") or "")
    by_score = sorted(picked, key=lambda it: (-it["_score"], it["job_key"]))
    batch1 = [it for it in by_score if it["_account"] == "none" and it["_tier"] == "B"]
    block_items = [it for it in by_score if it["_account"] == "new"]
    rest = [it for it in by_score if it not in batch1 and it not in block_items]
    out = []
    while len(out) < batch_size and batch1:
        take(out, batch1, rest)
    while len(out) < batch_size and rest:  # fewer than batch_size no-account items
        take(out, rest, [])
    rest = sorted(batch1 + rest, key=lambda it: (-it["_score"], it["job_key"]))
    after = len(out)
    tenants = list(dict.fromkeys(tenant_of(it) for it in block_items))
    groups = {t: [it for it in block_items if tenant_of(it) == t] for t in tenants}
    block = []
    while any(groups.values()):
        for t in tenants:
            if groups[t]:
                block.append(groups[t].pop(0))
    while block:
        take(out, block, rest)  # a filler from the rest only when no tenant item fits
    while rest:
        take(out, rest, [])
    return out, {"after_n": after, "tenants": tenants}


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
            ("Posted", it.get("posted") or ""), ("Source", "runner")]
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


def resume_status(ctx, it, folder):
    if it["_tier"] == "A":
        return "tier_a_ready" if (folder / "resume.pdf").exists() else "tier_a_pending"
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
    return {
        "n": n, "job_key": it["job_key"], "company": it.get("company", ""), "role": it.get("title", ""),
        "location": it.get("location", ""), "ats": it.get("ats", "") or "other",
        "host": netfetch.host_of(it.get("apply_url") or it.get("url") or ""),
        "apply_url": it.get("apply_url") or it.get("url") or "", "referral_url": it.get("referral_url", ""),
        "folder": folder_rel, "family": it.get("family", ""), "mode": it.get("mode") or "industry",
        "tier": it["_tier"], "tier_reason": it["_tier_reason"], "variant": it.get("_variant", ""),
        "posted": it.get("posted"), "age_days": it.get("age_days"), "band": it.get("band") or "",
        "repost_of": it.get("repost_of"), "score": it["_score"], "score_parts": it["_parts"],
        "account": account, "attach": prof["attach"], "captcha_risk": prof["captcha_risk"],
        "est_user_min": NEW_TENANT_MIN if account == "new" else prof["est_user_min"],
        "upload": f"upload/{plan_upload(n, ctx.last, it.get('company', ''))}",
        "resume_status": resume_status(ctx, it, folder), "answers_status": status, "needs": needs,
        "sensitive": sens,
        "referral": ctx.referral(it).get("referral") or {"state": "none", "contact_ref": "", "hold_until": ""},
        "posting_status": it.get("posting_status") or "read", "flags": list(it.get("flags") or []),
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

def widen_step(ctx, pool_count, target):
    """One widen step a day while the pool is under target (3.12)."""
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
    if session_active(root):
        emit("WAIT a session is active (claude/session.json beat under 20 minutes); the build waits")
        return None
    ctx = Context(root, today)
    pool, data = load_items(root, pool_path)
    target = int(ctx.daily.get("target", 20))
    size = math.ceil(target * float(ctx.daily.get("queue_factor", 1.5)))
    cands, drop = eligible(pool, ctx)
    ctx.load_referrals(cands)
    for it in cands:
        score(it, ctx)
    cands.sort(key=lambda it: (-it["_score"], it["job_key"]))
    asm = Assembly(ctx, size)
    picked = asm.run(cands)
    mark_tiers(picked, ctx, asm.tier_a)
    ordered, block = sequence(picked, int(ctx.daily.get("batch_size", 10)))
    used, items = set(), []
    for n, it in enumerate(ordered, 1):
        rel = make_folder(ctx, it, used)
        q = queue_item(ctx, it, n, rel)
        if prep:
            prep(q, root / rel)
        items.append(q)
    counts = dict(data.get("counts") or {})
    counts = {"seen": int(counts.get("seen", 0)), "passed": int(counts.get("passed", len(pool))),
              "discarded": int(counts.get("discarded", 0)), "deferred": len(asm.deferred)}
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
    if len(cands) < target:
        step = widen_step(ctx, len(cands), target)
        if step:
            emit(f"WIDEN {step}")
    if moved:
        emit(f"NOT_SENT moved {moved} folders to applications/_not_sent/")
    emit(f"WROTE {f.queue_json(today).relative_to(root).as_posix()}")
    return written


# ---------- addendum and more ----------

def numbers_used(root, day):
    top, keys = 0, set()
    for p in queue_files(root, day):
        if p.exists():
            data = read_json(p, {})
            for it in data.get("items") or []:
                top = max(top, int(it.get("n") or 0))
                keys.add(it.get("job_key"))
    return top, keys


def addendum(root, today=None, pool_path=None, limit=None, emit=print):
    """New numbered items after the highest number already given; queue.json and
    upload/ are left as they are."""
    root = Path(root)
    today = today or jobfs.today(root)
    f = jobfs.Folder(root)
    top, keys = numbers_used(root, today)
    ctx = Context(root, today, include_queued=True)
    pool, data = load_items(root, pool_path)
    limit = int(limit or ctx.daily.get("more_size", 10))
    cands, _ = eligible(pool, ctx, keys)
    ctx.load_referrals(cands)
    for it in cands:
        score(it, ctx)
    cands.sort(key=lambda it: (-it["_score"], it["job_key"]))
    asm = Assembly(ctx, limit)
    asm.tier_a = 0  # Tier A prep happens in the morning; late items are Tier B only
    picked = asm.run(cands)
    mark_tiers(picked, ctx, 0)
    ordered, _ = sequence(picked, 0)
    used = set()
    items = [queue_item(ctx, it, top + i, make_folder(ctx, it, used)) for i, it in enumerate(ordered, 1)]
    if not items:
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
    moved = 0
    if not active:
        old_day = manifest.get("date") or ""
        same_day = old_day == today.isoformat()
        for p in sorted(f.upload.iterdir()):
            if not p.is_file() or p.name.startswith(".") or (same_day and p.name in planned):
                continue
            # today's do-yourself files (E<n>_..., spec 5.8) sit beside the NN files
            if re.match(r"E\d+_", p.name) and datetime.fromtimestamp(p.stat().st_mtime).date() == today:
                continue
            # an earlier day's file (even one with today's name) goes to that day's _done folder
            day = old_day if old_day and not same_day else datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
            move_to_done(root, p, day)
            moved += 1
    applied = {it["job_key"] for it in events.overlay(root, today)["items"].values() if it.get("state") == "applied"}
    staged, warn, files = 0, 0, {}
    for it in items:
        name = Path(it.get("upload", "")).name
        dest = f.upload / name
        if it.get("job_key") in applied:
            for target in (dest, f.upload / re.sub(r"^(\d{2})_", r"\1c_", name).replace("_Resume_", "_Cover_")):
                if target.exists():
                    move_to_done(root, target, today)
                    moved += 1
            continue
        folder = root / it.get("folder", "")
        src = folder / ("resume.pdf" if it.get("tier") == "A" and (folder / "resume.pdf").exists() else "resume_B.pdf")
        if not dest.exists():
            if not src.exists():
                warn += 1
                emit(f"WARN #{it.get('n'):02d} no built resume in {it.get('folder')}")
                continue
            shutil.copyfile(src, dest)
            staged += 1
        files[name] = it.get("job_key")
        cover = folder / "cover_B.pdf"
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


def fold_events(root, today=None, emit=print):
    """Fold queue events into state/queue_state.json (per job key: the last state,
    reason, date, n). Writes nothing when nothing changed."""
    root = Path(root)
    today = today or jobfs.today(root)
    path = jobfs.Folder(root).queue_state_json
    before = load_state(root)
    data = json.loads(json.dumps(before))
    done = set(data["folded"])
    new = 0
    for ev in events.recent(root, today):
        if ev["type"] not in events.QUEUE_TYPES or ev["id"] in done or not ev.get("job_key"):
            continue
        p = ev.get("payload") or {}
        it = data["items"].setdefault(ev["job_key"], {"history": []})
        it.update(state=ev["type"], reason=p.get("reason", ""), date=ev.get("date", ""), n=ev.get("n"),
                  at=ev.get("at", ""))
        it["history"] = (it.get("history") or [])[-9:] + [ev["type"]]
        done.add(ev["id"])
        new += 1
    first = (today - timedelta(days=FOLDED_DAYS)).strftime("%Y%m%d")
    data["folded"] = sorted(i for i in done if i[:8] >= first)
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


def build_parser():
    ap = Parser(prog="jobqueue.py", description="Queue builder (spec 3.12).", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    for name, func, hlp in (("build", cmd_build, "Build queue/<date>/queue.json from the pool."),
                            ("addendum", cmd_addendum, "Append new items (late finds or more)."),
                            ("stage-uploads", cmd_stage, "Stage NN upload files; move done ones."),
                            ("fold-events", cmd_fold, "Fold queue events into state/queue_state.json.")):
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
