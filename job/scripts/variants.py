"""Role-family resume variants: scaffold, approve, check, tune, diff and the
Tier B pipeline (spec 4.2-4.3). Deterministic; no model calls.

Usage:
    python variants.py new     --root R --family F [--copy]
    python variants.py approve --root R --family F [--version N] [--mode M]
                               [--max-pages N] [--publications-max N]
    python variants.py check   --root R [--family F]
    python variants.py tune    --root R --item <job folder> [--family F]
    python variants.py diff    --root R --item <job folder>
    python variants.py tier-b  --root R --item <job folder> [--family F]
    python variants.py rebuild --root R [--approve PLAN]

A variant lives in variants/<family>/v<N>/: resume.json (every bullet with an
ev list of library IDs), pool.json (summary options, extra bullets per role,
keyed "<title> | <org>", or "<title> | <org> | <dates>" when two roles share
a title and employer; extra skills, the alias table, extra publications),
letter.json (a family letter with {company} {role} {focus} {date} slots;
{focus} is the hands-on techniques.md technique the posting names most),
review.md, and after approval approved.json (the sha256 of each file, and
under "entries" the sha256 of every library entry they cite, heading and
text). current.txt names the approved version. A rework goes to v<N+1>;
earlier versions stay.

Approval holds while those bytes and entries are unchanged (shared decision
2). A changed cited entry prints `UNAPPROVED <family> v<N>: L-0006 changed
since approval`; check then prints `CHANGED` lines (the entry's new text and
each bullet citing it) and a NEXT line, and approve re-approves the same
files on the user's word (`APPROVED <family> v<N> again`). A changed file is
never re-approved: it needs a new version.

rebuild (R8: recovery in one reply) takes every family whose current
approval a correction broke (its files intact, a cited library entry
changed) at once. For each it uses the newest unapproved version above the
current one when Claude made one (new --copy, the lines that now say more
fixed), else the same files again; it runs the approval checks and a strict
build of the base resume (base_resume.pdf in that version's folder), and
prints one combined diff with only the changed lines: CHANGED <id> (the
entry's new text, once), then per family "- "/"+ " lines (a new version
against the current one) or "= " lines (the same files: the lines citing a
changed entry), then PLAN <12 hex> (a hash of every file and entry that one
yes approves) and NEXT. rebuild --approve PLAN approves them all when
nothing changed since that plan (else REFUSED): a new version with the old
version's mode and caps, the same files as APPROVED ... again; then it
rebuilds today's Tier B files built from a version it replaced (tier-b on
each job folder not done and not claimed), so the held jobs are ready at
once. A family with PROBLEM lines stays out of the plan.

tune makes resume_B.json and tune_log.json in the job folder using only:
the summary option with the most posting-term overlap; bullets stable-sorted
by overlap within each role, pool bullets swapped in for lower-scoring ones,
non-keep bullets of the oldest roles dropped to fit the page cap; skills items
reordered (pool items only when the posting names them); variant terms
swapped for the posting's form through the alias table only; up to
publications_max citations chosen by overlap. Names, titles, employers,
dates, education, contact and numbers never change.

diff checks that: VARIANT ok, or one VARIANT-FAIL line per unit that is not
an approved unit (or an approved unit after the logged alias swaps), and
exit 6.

tier-b runs terms, tune, diff and build_resume.py --strict; on any failure it
builds the untouched approved resume.json instead (FALLBACK base), and if that
fails too prints PREP_FAILED (exit 6). A form that requires a cover letter
also gets cover_B from letter.json, with the letter check.

Exit codes: 0 ok, 1 bad input or a refusal (REFUSED, UNAPPROVED with no
approved version), 6 VARIANT-FAIL (diff), PREP_FAILED (tier-b) or PROBLEM
lines that keep a family out of a rebuild plan.
"""
import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answers  # noqa: E402
import build_resume  # noqa: E402
import checks  # noqa: E402
import jobfs  # noqa: E402
import library  # noqa: E402

FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}\Z")  # \Z: a trailing newline never passes
PLAN_RE = re.compile(r"[0-9a-f]{12}")
DIFF_TEXT_MAX = 400
VARIANT_FILES = ("resume.json", "pool.json", "letter.json")
POOL_KEYS = {"summaries", "bullets", "skills", "aliases", "publications"}
LETTER_SLOTS = {"{company}", "{role}", "{focus}", "{date}"}
RUNNER_FILES = ".runner_files"
B_EXTS = (".docx", ".pdf", ".txt")
MAX_DROPS = 40


class Refused(Exception):
    """A precondition failed: the message is the line to print."""


# ---------- files and approval ----------

def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    try:
        return jobfs.read_json(path, jobfs.MAX_FILE)
    except FileNotFoundError:
        raise Refused(f"ERROR {path} not found")
    except (OSError, ValueError) as e:
        raise Refused(f"ERROR cannot read {path}: {e}")


def versions(root, family):
    base = jobfs.Folder(root).variant(family)
    found = []
    for p in base.glob("v*"):
        if p.is_dir() and re.fullmatch(r"v\d+", p.name):
            found.append(int(p.name[1:]))
    return sorted(found)


def current_version(root, family):
    """The version current.txt names ("v2" or "2"), else the highest one."""
    try:
        text = jobfs.Folder(root).variant_current(family).read_text(encoding="utf-8-sig").strip()
        m = re.fullmatch(r"v?(\d+)", text)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    vs = versions(root, family)
    return vs[-1] if vs else None


def files_approval(vdir):
    """(ok, reason, approved dict or None): the approved files' exact bytes."""
    try:
        approved = jobfs.read_json(vdir / "approved.json", jobfs.MAX_FILE)
    except FileNotFoundError:
        return False, "not approved yet", None
    except (OSError, ValueError) as e:
        return False, f"approved.json unreadable ({e})", None
    if not isinstance(approved, dict):
        return False, "approved.json unreadable (not an object)", None
    hashes = approved.get("sha256") or {}
    for name in ("resume.json", "pool.json"):
        if name not in hashes:
            return False, f"approved.json has no hash for {name}", None
    for name in VARIANT_FILES:
        path = vdir / name
        if name in hashes:
            if not path.exists():
                return False, f"{name} missing since approval", None
            if sha256_file(path) != hashes[name]:
                return False, f"{name} changed since approval", None
        elif path.exists():
            return False, f"{name} added since approval", None
    return True, "", approved


def entry_hash(entry):
    """sha256 of one library entry as a variant relies on it: its role heading
    and text (library.entry_hash, the form a Tier A final.json uses too)."""
    return library.entry_hash(entry)


def cited_ids(vdir):
    """Every library ID the variant's resume.json and pool.json bullets cite, sorted."""
    ids = set()
    for name in ("resume.json", "pool.json"):
        try:
            data = jobfs.read_json(vdir / name, jobfs.MAX_FILE)
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if isinstance(x.get("ev"), list):
                    ids |= {i for i in x["ev"] if isinstance(i, str)}
                stack += list(x.values())
            elif isinstance(x, list):
                stack += x
    return sorted(ids)


def changed_entries(root, approved):
    """[(id, why)] for the cited library entries whose text or role heading
    changed since approval (shared decision 2). An approval written before
    per-entry hashes falls back to the whole library's sha256."""
    f = jobfs.Folder(root)
    entries = approved.get("entries")
    if not isinstance(entries, dict):
        current = sha256_file(f.library) if f.library.exists() else ""
        if approved.get("library_rev") and approved["library_rev"] == current:
            return []
        return [("library", "achievement_library.md changed since approval (approved before per-entry checks)")]
    lib = library.load(f.library).by_id
    out = []
    for eid, h in sorted(entries.items()):
        if eid not in lib:
            out.append((eid, f"{eid} is no longer in the library"))
        elif entry_hash(lib[eid]) != h:
            out.append((eid, f"{eid} changed since approval"))
    return out


def approval(root, family, version):
    """(ok, reason, approved dict or None) for one version: the approved files'
    exact bytes and the text of every library entry they cite."""
    vdir = jobfs.Folder(root).variant(family, version)
    ok, why, approved = files_approval(vdir)
    if not ok:
        return ok, why, approved
    changed = changed_entries(root, approved)
    if changed:
        return False, "; ".join(why for _, why in changed[:3]) + (" ..." if len(changed) > 3 else ""), None
    return True, "", approved


def usable(root, family):
    """(version, approved, notes): the current version if its approval holds,
    else the newest older approved version; version None when there is none."""
    cur = current_version(root, family)
    if cur is None:
        return None, None, [f"UNAPPROVED {family}: no variant yet (variants.py new)"]
    notes = []
    for v in [cur] + [x for x in reversed(versions(root, family)) if x < cur]:
        ok, why, approved = approval(root, family, v)
        if ok:
            return v, approved, notes
        notes.append(f"UNAPPROVED {family} v{v}: {why}")
    return None, None, notes


# ---------- pool and letter validation ----------

def entry_key(e):
    return f'{e["title"]} | {e.get("org", "")}'


def full_key(e):
    """"<title> | <org> | <dates>": one stint, even when two share title and employer."""
    return f'{e["title"]} | {e.get("org", "")} | {e.get("dates", "")}'


def resume_entries(data):
    return [e for s in data.get("sections", []) for e in s.get("entries", [])]


def pool_bullets(pool, e, entries):
    """The pool bullets for one role: keyed by full_key, or by entry_key when
    no other role in entries shares that title and employer (a shared key
    belongs to neither, so a bullet never moves to another stint)."""
    bullets = pool.get("bullets", {}) if isinstance(pool.get("bullets"), dict) else {}
    if full_key(e) in bullets:
        return list(bullets[full_key(e)])
    if entry_key(e) in bullets and sum(1 for x in entries if entry_key(x) == entry_key(e)) == 1:
        return list(bullets[entry_key(e)])
    return []


def check_pool(pool, resume):
    """Shape errors in pool.json, as lines."""
    if not isinstance(pool, dict):
        return ["pool.json must be an object"]
    errors = [f"pool.json has unknown key {k!r}" for k in sorted(set(pool) - POOL_KEYS)]
    if not all(isinstance(s, str) and s.strip() for s in pool.get("summaries", [])):
        errors.append("pool summaries must be non-empty strings")
    entries = resume_entries(resume)
    shared = Counter(full_key(e) for e in entries)
    for key, k in sorted(shared.items()):
        if k > 1:
            errors.append(f'resume.json has {k} roles keyed "{key}": give each its own dates')
    short = Counter(entry_key(e) for e in entries)
    keys = set(shared)
    bullets = pool.get("bullets", {})
    if not isinstance(bullets, dict):
        errors.append('pool "bullets" must map "<title> | <org>" to a list of bullets')
        bullets = {}
    for key, items in bullets.items():
        if key in short and short[key] > 1:
            errors.append(f'pool bullets for "{key}" match {short[key]} roles in resume.json: '
                          'key them "<title> | <org> | <dates>"')
        elif key not in keys and key not in short:
            errors.append(f'pool bullets for "{key}", which is not a role in resume.json')
        for b in items if isinstance(items, list) else [None]:
            if not isinstance(b, dict) or not isinstance(b.get("text"), str) or not b.get("ev"):
                errors.append(f'pool bullet under "{key}" needs "text" and an "ev" list')
    for line in pool.get("skills", []):
        if not (isinstance(line, dict) and isinstance(line.get("label"), str)
                and isinstance(line.get("items"), list) and all(isinstance(i, str) for i in line["items"])):
            errors.append('pool skills need {"label": ..., "items": [...]}')
    for a in pool.get("aliases", []):
        ok = (isinstance(a, dict) and isinstance(a.get("variant"), str) and a["variant"].strip()
              and isinstance(a.get("posting"), list) and a["posting"]
              and all(isinstance(f, str) and f.strip() for f in a["posting"]))
        if not ok:
            errors.append('aliases need {"variant": "<our term>", "posting": ["<same thing, posting form>"]}')
        elif any(f.lower() == a["variant"].lower() for f in a["posting"]):
            errors.append(f'alias "{a["variant"]}" lists itself')
    if not all(isinstance(p, str) and build_resume.CITATION_ID.match(p) for p in pool.get("publications", [])):
        errors.append("pool publications must be IDs like P-003")
    return errors


def merged_for_checks(resume, pool):
    """resume.json plus every pool unit, so FACT and EVIDENCE cover anything tune may pick."""
    data = copy.deepcopy(resume)
    entries = resume_entries(data)
    for e in entries:
        e["bullets"] = list(e.get("bullets", [])) + pool_bullets(pool, e, entries)
    for i, text in enumerate(pool.get("summaries", []), 2):
        data["sections"].append({"heading": f"Summary option {i}", "paragraph": text})
    lines = [{"label": s["label"], "text": ", ".join(s["items"])} for s in pool.get("skills", [])]
    if lines:
        data["sections"].append({"heading": "Pool skills", "lines": lines})
    return data


def letter_errors(letter_data):
    errors = []
    L = letter_data.get("letter") if isinstance(letter_data, dict) else None
    if not isinstance(L, dict):
        return ['letter.json needs a "letter" object']
    texts = [L.get("date", ""), L.get("greeting", ""), L.get("closing", "")] + L.get("recipient", []) + L.get("paragraphs", [])
    for t in texts:
        for slot in checks.SLOT_RE.findall(t):
            if slot not in LETTER_SLOTS:
                errors.append(f"letter.json slot {slot} is not one of {', '.join(sorted(LETTER_SLOTS))}")
    return errors


def approval_problems(root, vdir):
    """Lines that stop an approval: bad JSON, TODO text, EVIDENCE, FACT or banned words."""
    f = jobfs.Folder(root)
    problems = []
    for name in VARIANT_FILES:
        p = vdir / name
        if p.exists() and "TODO" in p.read_text(encoding="utf-8-sig"):
            problems.append(f"{name} still has TODO text")
    try:
        resume = build_resume.load(vdir / "resume.json")
    except build_resume.InputError as e:
        return problems + [f"resume.json: {e}"]
    pool = read_json(vdir / "pool.json")
    problems += check_pool(pool, resume)
    if (vdir / "letter.json").exists():
        try:
            letter = build_resume.load(vdir / "letter.json")
            problems += letter_errors(letter)
        except build_resume.InputError as e:
            problems.append(f"letter.json: {e}")
    if problems:
        return problems
    data = merged_for_checks(resume, pool)
    entries = checks.library_entries([f.library], headings=True)
    problems += [ln for ln in checks.evidence_check(data, entries, v12=True) if not ln.startswith("EVIDENCE ok")]
    sources = [p for p in (f.master_resume, f.library, f.profile) if p.exists()]
    text, _ = checks.read_sources(sources)
    techniques = library.load_techniques(f.techniques)
    if text is None:
        problems.append("FACT not run: no master_resume.md or achievement_library.md in the root")
    else:
        text = checks.strip_ids(text)
        problems += [ln for ln in checks.fact_check(data, text, v12=True) if not ln.startswith("FACT ok")]
        problems += [ln for ln in checks.technique_check(data, techniques, text) if not ln.startswith("TECHNIQUE ok")]
    problems += [f"lint {w}" for w in build_resume.lint(data) if "is on the banned-word list" in w]
    if (vdir / "letter.json").exists():
        problems += letter_problems(read_json(vdir / "letter.json"), techniques, text, jobfs.today(root))
    return problems


def letter_problems(letter, techniques, source_text, day):
    """The family letter filled with a sample posting: no new numbers, no
    banned word, and a {focus} the posting can fill from techniques.md."""
    L = letter.get("letter", {})
    parts = [L.get("greeting", ""), L.get("closing", "")] + list(L.get("paragraphs", []))
    out = []
    if any("{focus}" in p for p in parts) and not hands_on(techniques):
        out.append("letter.json uses {focus}, and techniques.md has no hands-on technique to fill it")
    focus = (hands_on(techniques) or [None])[0]
    sample = fill_letter(letter, "Company: Example Co\nRole: Example Role\n",
                         focus.name if focus else "", day)
    if source_text is not None:
        out += [f"letter.json {ln}" for ln in checks.fact_check(sample, source_text, v12=True)
                if not ln.startswith("FACT ok")]
    out += [f"lint {w}" for w in build_resume.lint(sample) if "is on the banned-word list" in w]
    return out


def hands_on(techniques):
    return [t for t in techniques or [] if t.level == "hands-on"]


# ---------- commands: new, approve, check ----------

def check_family(family):
    if not FAMILY_RE.match(family or ""):
        raise Refused(f"ERROR family id {family!r} must be lowercase ASCII letters, digits or _")


def scaffold_resume(root):
    """resume.json from master_resume.md: roles, education and certifications,
    with empty ev lists for Claude to fill. Never approved as is (TODO text)."""
    f = jobfs.Folder(root)
    try:
        text = f.master_resume.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise Refused(f"ERROR cannot read {f.master_resume}: {e}")
    name, contact, sections, section, entry = "", [], [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# ") and not name:
            name = line[2:].strip()
        elif not contact and name and " | " in line and not line.startswith("#"):
            contact = [c.strip() for c in line.split(" | ")]
        elif line.startswith("## "):
            section = {"heading": line[3:].strip()}
            sections.append(section)
            entry = None
        elif line.startswith("### ") and section is not None:
            parts = [p.strip() for p in line[4:].split("|")]
            entry = {"title": parts[0], "org": parts[1] if len(parts) > 1 else "",
                     "location": parts[2] if len(parts) > 3 else "", "dates": parts[-1] if len(parts) > 2 else "",
                     "bullets": []}
            section.setdefault("entries", []).append(entry)
        elif line.startswith("- ") and section is not None:
            if entry is not None:
                entry["bullets"].append({"text": line[2:].strip(), "ev": []})
            else:
                section.setdefault("bullets", []).append(line[2:].strip())
        elif line and section is not None and " | " in line and entry is None:
            parts = [p.strip() for p in line.split("|")]
            section.setdefault("entries", []).append(
                {"title": parts[0], "org": parts[1] if len(parts) > 1 else "",
                 "location": parts[2] if len(parts) > 3 else "", "dates": parts[-1] if len(parts) > 2 else ""})
        elif line and section is not None and "paragraph" not in section and not section.get("entries"):
            section["paragraph"] = line
    sections = [s for s in sections if len(s) > 1]
    sections.insert(0, {"heading": "Summary", "paragraph": "TODO: two or three sentences for this family."})
    return {"name": name or "TODO", "contact": contact, "sections": sections}


def cmd_new(root, family, copy_prev):
    check_family(family)
    f = jobfs.Folder(root)
    vs = versions(root, family)
    n = (vs[-1] + 1) if vs else 1
    vdir = f.variant(family, n)
    vdir.mkdir(parents=True, exist_ok=False)
    if copy_prev and vs:
        prev = f.variant(family, vs[-1])
        for name in VARIANT_FILES + ("review.md",):
            if (prev / name).exists():
                shutil.copy2(prev / name, vdir / name)
        print(f"NEW {family} v{n} (copied from v{vs[-1]}): {vdir}")
        return 0
    resume = scaffold_resume(root)
    pool = {"summaries": [], "bullets": {}, "skills": [], "aliases": [], "publications": []}
    letter = {"name": resume["name"], "contact": resume["contact"][:3], "letter": {
        "date": "{date}", "recipient": ["Hiring Team", "{company}"], "greeting": "Dear Hiring Team,",
        "paragraphs": ["TODO: I am applying for the {role} role at {company}. ...",
                       "TODO: one paragraph on {focus}, a phrase copied from the posting.",
                       "I would welcome the chance to talk about how this work fits your team."],
        "closing": "Sincerely,"}}
    for name, obj in (("resume.json", resume), ("pool.json", pool), ("letter.json", letter)):
        jobfs.atomic_write_text(vdir / name, json.dumps(obj, indent=1, ensure_ascii=True) + "\n")
    jobfs.atomic_write_text(vdir / "review.md", REVIEW_TEMPLATE.format(family=family, n=n))
    print(f"NEW {family} v{n}: {vdir}")
    print("NEXT fill resume.json (ev on every bullet), pool.json, letter.json and review.md; "
          "audit; build; then variants.py approve")
    return 0


REVIEW_TEMPLATE = """# Variant {family} v{n}

## Verify before sending

## Pool (what tuning may swap in)

## Audit
"""


def cmd_approve(root, family, version, mode, max_pages, pub_max, emit=print, show_changed=True):
    check_family(family)
    print = emit  # noqa: A001  every line below goes to emit (rebuild --approve collects them)
    f = jobfs.Folder(root)
    version = version or current_version(root, family)
    if version is None:
        raise Refused(f"ERROR no variant for {family} (variants.py new --family {family})")
    vdir = f.variant(family, version)
    for name in ("resume.json", "pool.json"):
        if not (vdir / name).exists():
            raise Refused(f"ERROR {vdir / name} not found")
    again = None
    if (vdir / "approved.json").exists():
        ok, why, old = files_approval(vdir)
        if not ok:
            raise Refused(f"REFUSED {family} v{version} was approved before and {why}; "
                          f"put the change in a new version (variants.py new --family {family} --copy)")
        changed = changed_entries(root, old)
        if not changed:
            print(f"APPROVED {family} v{version} (unchanged since {old.get('approved_on', '?')})")
            return 0
        # Only cited library entries changed: the user re-approves the same files
        # after reading the bullets that cite them (variants.md).
        again = changed
        for line in changed_lines(root, vdir, changed) if show_changed else []:
            print(line)
        mode = mode or old.get("mode")
        max_pages = max_pages or old.get("max_pages")
        pub_max = pub_max if pub_max is not None else old.get("publications_max")
    problems = approval_problems(root, vdir)
    if problems:
        for p in problems:
            print(f"PROBLEM {p}")
        raise Refused(f"REFUSED approve {family} v{version}: fix the lines above")
    fam = family_profile(root, family)
    mode = mode or fam.get("mode") or "industry"
    if mode not in build_resume.MODE_CAPS:
        raise Refused(f"ERROR mode {mode!r} is not industry, cv or usajobs")
    cap = build_resume.page_cap(mode, max_pages)
    hashes = {name: sha256_file(vdir / name) for name in VARIANT_FILES if (vdir / name).exists()}
    lib = library.load(f.library).by_id
    approved = {"family": family, "version": version, "approved_on": jobfs.today(root).isoformat(),
                "library_rev": sha256_file(f.library) if f.library.exists() else "",
                "sha256": hashes, "entries": {i: entry_hash(lib[i]) for i in cited_ids(vdir) if i in lib},
                "mode": mode, "max_pages": cap,
                "publications_max": pub_max if pub_max is not None else 3}
    jobfs.atomic_write_text(vdir / "approved.json", json.dumps(approved, indent=1) + "\n")
    jobfs.atomic_write_text(f.variant_current(family), f"v{version}\n")
    shown = ", ".join(f"{k} {v[:8]}" for k, v in hashes.items())
    if again:
        print(f"APPROVED {family} v{version} again ({len(approved['entries'])} cited entries; "
              f"changed: {', '.join(i for i, _ in again)})")
    else:
        print(f"APPROVED {family} v{version} ({shown})")
    return 0


def changed_lines(root, vdir, changed):
    """CHANGED lines: each changed entry's new text and the variant units citing it."""
    lib = library.load(jobfs.Folder(root).library).by_id
    units = []
    for name in ("resume.json", "pool.json"):
        try:
            data = jobfs.read_json(vdir / name, jobfs.MAX_FILE)
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if isinstance(x.get("ev"), list) and isinstance(x.get("text"), str):
                    units.append((name, x["text"], x["ev"]))
                stack += list(x.values())
            elif isinstance(x, list):
                stack += x
    out = []
    for eid, why in changed:
        if eid == "library":
            out.append(f"CHANGED {why}")
            continue
        now = lib[eid].text if eid in lib else "(gone)"
        out.append(f'CHANGED {why}; now: "{checks.norm(now)[:120]}"')
        for name, text, ev in units:
            if eid in ev:
                out.append(f'  cited in {name}: "{checks.norm(text)[:120]}"')
    return out


def family_profile(root, family):
    for fam in jobfs.search_profile(root).get("family") or []:
        if fam.get("id") == family:
            return fam
    return {}


def cmd_check(root, family):
    base = jobfs.Folder(root).variants
    for fam in jobfs.search_profile(root).get("family") or []:
        if fam.get("variant") and fam.get("variant") != fam.get("id") and (not family or fam.get("id") == family):
            # Variants are found by family id; a family never borrows another's variant.
            print(f'WARN family {fam.get("id")}: variant = "{fam.get("variant")}" is not read; '
                  f'its variant is variants/{fam.get("id")} (remove the variant line)')
    fams = [family] if family else sorted(p.name for p in base.glob("*") if p.is_dir())
    odd = [f for f in fams if not FAMILY_RE.match(f)]
    if odd:  # shared decision 2: a name printed for a command line is a family id, or not printed at all
        print(f"REFUSED {plural_folders(len(odd))} in variants/ whose name is not a family id (lowercase "
              "letters, digits and _); not read")
        fams = [f for f in fams if FAMILY_RE.match(f)]
    if not fams:
        print("VARIANTS none")
        return 0
    for fam in fams:
        cur = current_version(root, fam)
        v, approved, notes = usable(root, fam)
        for line in notes:
            print(line)
        if cur is not None and v != cur:
            vdir = jobfs.Folder(root).variant(fam, cur)
            ok, _, old = files_approval(vdir)
            changed = changed_entries(root, old) if ok else []
            if changed:
                for line in changed_lines(root, vdir, changed):
                    print(line)
                print(f"NEXT if every cited line above still holds against the new text, ask the user to "
                      f"re-approve {fam} (variants.py approve --family {fam}); if one says more, "
                      f"variants.py new --family {fam} --copy, fix it, and ask for approval. With more than one "
                      "family affected, variants.py rebuild shows them all for one yes")
        if v is not None and v == cur:
            print(f"APPROVED {fam} v{v} (on {approved.get('approved_on', '?')})")
        elif v is not None:
            print(f"FALLBACK {fam} v{v} (the newest approved version still intact)")
        elif not notes:
            print(f"UNAPPROVED {fam}")
        for d in versions(root, fam):
            if cur is not None and d > cur and not (jobfs.Folder(root).variant(fam, d) / "approved.json").exists():
                print(f"DRAFT {fam} v{d} (not approved)")
    return 0


def plural_folders(k):
    return f"{k} folder" + ("" if k == 1 else "s")


# ---------- rebuild: every family a correction broke, one diff, one yes (R8) ----------

def unit_lines(vdir):
    """[(file, label, text)] of every line a reader sees in a version's files,
    in document order: the resume's summary, role headers, bullets, skills
    lines and citations; the pool's summaries, bullets, skills, aliases and
    publications; the letter's lines. Raises ValueError on a file that is not
    a JSON object of the usual shape (the approval checks then say why)."""
    out = []

    def add(name, label, text):
        if isinstance(text, str) and text.strip():
            out.append((name, label, checks.norm(text)))
    for name in VARIANT_FILES:
        path = vdir / name
        if not path.exists():
            continue
        data = jobfs.read_json(path, jobfs.MAX_FILE)
        if not isinstance(data, dict):
            raise ValueError(f"{name} is not an object")
        if name == "resume.json":
            add(name, "name", data.get("name"))
            add(name, "contact", " | ".join(c for c in data.get("contact") or [] if isinstance(c, str)))
            for s in data.get("sections") or []:
                h = str(s.get("heading", ""))
                add(name, h, s.get("paragraph"))
                for ln in s.get("lines") or []:
                    add(name, f"{h} / {ln.get('label', '')}", ln.get("text"))
                for e in s.get("entries") or []:
                    role = f"{e.get('title', '')} | {e.get('org', '')}"
                    add(name, h, " | ".join(str(e.get(k, "")) for k in ("title", "org", "location", "dates")))
                    for b in e.get("bullets") or []:
                        add(name, role, checks.bullet_text(b))
                for b in s.get("bullets") or []:
                    add(name, h, checks.bullet_text(b))
                if s.get("citations"):
                    add(name, h, ", ".join(str(c) for c in s["citations"]))
        elif name == "pool.json":
            for t in data.get("summaries") or []:
                add(name, "summary option", t)
            for key, items in (data.get("bullets") or {}).items():
                for b in items or []:
                    add(name, f"extra bullet, {key}", checks.bullet_text(b))
            for ln in data.get("skills") or []:
                add(name, f"extra skills, {ln.get('label', '')}", ", ".join(str(i) for i in ln.get("items") or []))
            for a in data.get("aliases") or []:
                add(name, "alias", f"{a.get('variant', '')} = {', '.join(str(x) for x in a.get('posting') or [])}")
            if data.get("publications"):
                add(name, "extra publications", ", ".join(str(p) for p in data["publications"]))
        else:
            L = data.get("letter") or {}
            for k in ("greeting", "closing"):
                add(name, k, L.get(k))
            for t in L.get("paragraphs") or []:
                add(name, "paragraph", t)
    return out


def changed_unit_lines(old_dir, new_dir):
    """The "- " and "+ " lines between two versions: the changed lines only."""
    import difflib
    a = [f"{f} {label}: {text}" for f, label, text in unit_lines(old_dir)]
    b = [f"{f} {label}: {text}" for f, label, text in unit_lines(new_dir)]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            out += [f"- {x[:DIFF_TEXT_MAX]}" for x in a[i1:i2]]
        if op in ("replace", "insert"):
            out += [f"+ {x[:DIFF_TEXT_MAX]}" for x in b[j1:j2]]
    return out


def citing_lines(vdir, ids):
    """The "= " lines: each line of the version citing one of ids, with the ids it cites."""
    out = []
    for name in ("resume.json", "pool.json"):
        try:
            data = jobfs.read_json(vdir / name, jobfs.MAX_FILE)
        except (OSError, ValueError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                ev = x.get("ev")
                if isinstance(ev, list) and isinstance(x.get("text"), str) and set(ev) & set(ids):
                    hit = ", ".join(i for i in ev if i in ids)
                    out.append(f"= {name}: {checks.norm(x['text'])[:DIFF_TEXT_MAX]} (cites {hit})")
                stack += list(x.values())
            elif isinstance(x, list):
                stack += reversed(x)
    return out


def broken_families(root):
    """[(family, current version, its old approved dict, [(id, why)])] for every
    family whose current version's files are intact as approved and whose
    approval a changed cited library entry broke."""
    out = []
    base = jobfs.Folder(root).variants
    for fam in sorted(p.name for p in base.glob("*") if p.is_dir()) if base.exists() else []:
        if not FAMILY_RE.match(fam):
            continue
        cur = current_version(root, fam)
        if cur is None:
            continue
        ok, _, old = files_approval(jobfs.Folder(root).variant(fam, cur))
        if not ok:
            continue
        changed = changed_entries(root, old)
        if changed:
            out.append((fam, cur, old, changed))
    return out


def draft_above(root, family, cur):
    """The newest version above cur with no approved.json (Claude's fix), or None."""
    f = jobfs.Folder(root)
    drafts = [v for v in versions(root, family) if v > cur and not (f.variant(family, v) / "approved.json").exists()]
    return drafts[-1] if drafts else None


def plan_targets(root):
    """[(family, old version, target version, old approved, changed entries)]
    rebuild would approve, and the PLAN hash over every file and cited entry."""
    f = jobfs.Folder(root)
    targets = []
    for fam, cur, old, changed in broken_families(root):
        target = draft_above(root, fam, cur) or cur
        targets.append((fam, cur, target, old, changed))
    lib = library.hashes(f.library)
    record = []
    for fam, cur, target, _, _ in targets:
        vdir = f.variant(fam, target)
        files = {n: sha256_file(vdir / n) for n in VARIANT_FILES if (vdir / n).exists()}
        record.append([fam, cur, target, files, {i: lib.get(i, "") for i in cited_ids(vdir)}])
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return targets, digest


def base_build(root, vdir, old):
    """A strict build of the version's resume.json; base_resume.pdf joins its
    folder. (ok, problem lines)."""
    ok, lines, out = strict_build(root, vdir / "resume.json", "base_resume", old, vdir)
    try:
        if ok and (out / "base_resume.pdf").exists():
            shutil.copyfile(out / "base_resume.pdf", vdir / "base_resume.pdf")
        return ok, ([] if ok else problem_lines(lines))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def held_folders(root, family, keep_version):
    """Today's job folders whose Tier B files came from another version of
    family (tune_log.json), not done and not claimed: the jobs a new version
    must rebuild before they can go out."""
    import events
    day = jobfs.today(root)
    f = jobfs.Folder(root)
    qd = f.queue_dir(day)
    paths = ([qd / "queue.json"] + sorted(qd.glob("addendum_*.json"))) if qd.exists() else []
    try:
        states = {(it.get("n"), it.get("job_key")): it.get("state")
                  for it in events.overlay(root, day)["items"].values()}
    except Exception:  # no events readable: every item counts as not done
        states = {}
    out, seen = [], set()
    for p in paths:
        if p.name.endswith(".prev.json") or p.name == "addendum_user.json" or not p.exists():
            continue
        try:
            data = jobfs.read_json_verified(p, retries=0, wait=0)
        except Exception:  # a queue file that cannot be read: its jobs wait for the runner
            continue
        for it in data.get("items") or [] if isinstance(data, dict) else []:
            if not isinstance(it, dict) or it.get("added_by"):
                continue
            if states.get((it.get("n"), it.get("job_key"))) in ("applied", "skipped", "later", "blocked"):
                continue
            folder = f.root / str(it.get("folder", ""))
            if folder in seen or not folder.is_dir() or (folder / ".claimed").exists():
                continue
            try:
                log = jobfs.read_json(folder / "tune_log.json", jobfs.MAX_FILE)  # R3: the safe loader
            except (OSError, ValueError):
                continue
            if isinstance(log, dict) and log.get("family") == family and log.get("version") != keep_version:
                seen.add(folder)
                out.append(folder)
    return out


def cmd_rebuild(root, plan=None):
    """R8: every family a correction broke, at once (module docstring)."""
    targets, digest = plan_targets(root)
    f = jobfs.Folder(root)
    if not targets:
        print("REBUILD none: no approved base resume cites a corrected library entry")
        return 0
    if plan is not None:
        return approve_plan(root, targets, digest, plan)
    lib = library.load(f.library).by_id if f.library.exists() else {}
    shown = set()
    for _, _, _, _, changed in targets:
        for eid, why in changed:
            if eid in shown:
                continue
            shown.add(eid)
            now = lib[eid].text if eid in lib else ""
            print(f'CHANGED {eid}: "{checks.norm(now)[:DIFF_TEXT_MAX]}"' if now else f"CHANGED {why}")
    clean, problems = [], []
    for fam, cur, target, old, changed in targets:
        vdir = f.variant(fam, target)
        ids = [i for i, _ in changed]
        if target != cur:
            try:
                lines = changed_unit_lines(f.variant(fam, cur), vdir)
            except (OSError, ValueError, AttributeError, TypeError) as e:
                lines = [f"PROBLEM v{target} cannot be read ({type(e).__name__})"]
            print(f"REBUILD {fam} v{cur} -> v{target}: {len(lines)} changed line{'s' if len(lines) != 1 else ''}")
        else:
            lines = citing_lines(vdir, ids)
            print(f"REBUILD {fam} v{cur} again: no line changes; {len(lines)} line{'s' if len(lines) != 1 else ''} "
                  f"cite{'s' if len(lines) == 1 else ''} the corrected {'entry' if len(ids) == 1 else 'entries'}")
        for line in lines:
            print(f"  {line}")
        found = [ln for ln in lines if ln.startswith("PROBLEM")] + approval_problems(root, vdir)
        if not found:
            ok, built = base_build(root, vdir, old)
            found = [] if ok else built
        for p in found:
            print(f"PROBLEM {fam} v{target}: {p.removeprefix('PROBLEM ')}")
        (problems if found else clean).append(fam)
        if not found:
            jobs = len(held_folders(root, fam, target)) if target != cur else 0
            print(f"BUILD ok {fam} v{target} (base_resume.pdf)" + (f"; {jobs} of today's jobs are rebuilt on the "
                                                                    f"yes" if jobs else ""))
    if problems:
        print(f"NEXT fix {', '.join(problems)} first (a line that now says more than its entry: variants.py new "
              "--family <id> --copy, fix it there), then run variants.py rebuild again, so one yes covers every "
              "family")
        return 6
    print(f"PLAN {digest} ({len(clean)} famil{'y' if len(clean) == 1 else 'ies'})")
    print("NEXT read each = line against its CHANGED text; where one now says more, variants.py new --family <id> "
          "--copy, fix that line, and run rebuild again. Then show the user every line above in one message and, "
          f"on their one yes, run variants.py rebuild --approve {digest}")
    return 0


def approve_plan(root, targets, digest, plan):
    """rebuild --approve: every family of the plan, then today's held jobs rebuilt."""
    if not PLAN_RE.fullmatch(plan or "") or plan != digest:
        raise Refused("REFUSED the base resumes or the library changed since that plan: run variants.py rebuild "
                      "again and show the user its lines")
    approved, rebuilt, failed = 0, 0, 0
    for fam, cur, target, old, _ in targets:
        lines = []
        try:
            cmd_approve(root, fam, target, old.get("mode"), old.get("max_pages"), old.get("publications_max"),
                        emit=lines.append, show_changed=False)
        except Refused as e:
            for line in lines + [str(e)]:
                print(line)
            return 1
        for line in lines:
            print(line)
        approved += 1
        if target == cur:
            continue  # the same files approved again: their jobs are ready at once
        for folder in held_folders(root, fam, target):
            out = []
            try:
                status = tier_b(root, folder, family=fam, emit=out.append)
            except Refused as e:
                status = "prep_failed"
                out.append(str(e))
            if status in ("built", "fallback_base"):
                rebuilt += 1
            elif status != "claimed":
                failed += 1
                print(next((ln for ln in out if ln.startswith("PREP_FAILED")), f"PREP_FAILED {fam} {folder.name}"))
    print(f"DONE approved={approved} rebuilt={rebuilt} prep_failed={failed}")
    return 0


# ---------- terms ----------

def build_terms(root, family, posting_text):
    """terms.json entries: family terms, techniques (with aliases) and knockout
    names that posting.md uses. The posting's own form leads; the rest are aliases."""
    f = jobfs.Folder(root)
    cands = [[t] for t in family_profile(root, family).get("terms") or [] if isinstance(t, str)]
    cands += [t.forms for t in library.load_techniques(f.techniques)]
    cands += [[k["name"]] for k in jobfs.search_profile(root).get("knockout") or [] if k.get("name")]
    out, seen = [], set()
    for forms in cands:
        counts = [(len(checks.term_regex(x).findall(posting_text)), i, x) for i, x in enumerate(forms) if x.strip()]
        found = [c for c in counts if c[0]]
        if not found:
            continue
        lead = sorted(found, key=lambda c: (-c[0], c[1]))[0][2]
        if lead.lower() in seen:
            continue
        seen.add(lead.lower())
        aliases = [x for x in forms if x != lead]
        out.append({"term": lead, "aliases": aliases} if aliases else lead)
    return out


def terms_list(raw):
    """[(term, aliases)] from terms.json content."""
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append((item, []))
        elif isinstance(item, dict) and isinstance(item.get("term"), str):
            out.append((item["term"], list(item.get("aliases") or [])))
    return out


# ---------- alias swaps ----------

def swap_regex(term):
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.I)


def alias_swaps(pool, posting_text):
    """[{variant, to}]: our term becomes the posting's form when the posting
    names the same thing only in its own words."""
    swaps = []
    for a in pool.get("aliases", []):
        if checks.term_found(posting_text, a["variant"]):
            continue
        to = next((x for x in a["posting"] if checks.term_found(posting_text, x)), None)
        if to:
            swaps.append({"variant": a["variant"], "to": to})
    return swaps


def apply_swaps(text, swaps):
    for s in swaps:
        text = swap_regex(s["variant"]).sub(lambda m, to=s["to"]: to, text)
    return text


def swapped(bullet, swaps):
    if isinstance(bullet, dict):
        return {**bullet, "text": apply_swaps(bullet["text"], swaps)}
    return apply_swaps(bullet, swaps)


# ---------- tune ----------

split_items = checks.split_items  # comma-separated items at parenthesis depth 0


class Scorer:
    """Posting-term overlap of a text after the alias swaps."""

    def __init__(self, terms, swaps):
        self.terms, self.swaps = terms, swaps

    def __call__(self, text):
        t = apply_swaps(text, self.swaps)
        return sum(1 for term, aliases in self.terms if checks.term_found(t, term, aliases))


def is_summary(s):
    return s["heading"].strip().lower() == "summary" and "paragraph" in s


def tune_summary(s, pool, score, log):
    opts = [s["paragraph"]] + list(pool.get("summaries", []))
    scores = [score(o) for o in opts]
    best = max(range(len(opts)), key=lambda i: (scores[i], -i))
    s["paragraph"] = apply_swaps(opts[best], score.swaps)
    log["summary"] = {"chosen": best + 1, "scores": scores}


def tune_entry(e, pool, score, entries=()):
    """Stable-sort by overlap; pool bullets replace lower-scoring ones; keep bullets stay."""
    base = list(e.get("bullets", []))
    extra = pool_bullets(pool, e, entries or [e])
    cands = [(score(checks.bullet_text(b)), i, "variant", i, b) for i, b in enumerate(base)]
    cands += [(score(checks.bullet_text(b)), len(base) + i, "pool", i, b) for i, b in enumerate(extra)]
    keep = [c for c in cands if c[2] == "variant" and isinstance(c[4], dict) and c[4].get("keep")]
    ranked = sorted((c for c in cands if c not in keep), key=lambda c: (-c[0], c[1]))
    chosen = sorted(keep + ranked[:max(0, len(base) - len(keep))], key=lambda c: (-c[0], c[1]))
    e["bullets"] = [swapped(c[4], score.swaps) for c in chosen]
    return {"entry": entry_key(e), "picked": [{"from": c[2], "index": c[3] + 1, "score": c[0]} for c in chosen]}


def tune_lines(s, pool, score, add_new=True):
    pool_items = {}
    for pl in pool.get("skills", []):
        pool_items.setdefault(pl["label"], []).extend(pl["items"])
    out, log = [], []
    labels = [ln["label"] for ln in s["lines"]]
    rows = [(ln["label"], split_items(ln["text"])) for ln in s["lines"]]
    if add_new:
        rows += [(label, []) for label in pool_items if label not in labels]
    for k, (label, items) in enumerate(rows):
        extra = [i for i in pool_items.get(label, []) if i not in items]
        cands = [(score(i), n, i) for n, i in enumerate(items)]
        cands += [(score(i), len(items) + n, i) for n, i in enumerate(extra) if score(i) > 0]
        cands.sort(key=lambda c: (-c[0], c[1]))
        if not cands:
            continue
        matched = sum(1 for c in cands if c[0] > 0)
        out.append((-matched, k, {"label": label, "text": ", ".join(apply_swaps(c[2], score.swaps) for c in cands)}))
        log.append({"label": label, "matched": matched, "added": [c[2] for c in cands if c[1] >= len(items)]})
    out.sort(key=lambda r: (r[0], r[1]))
    s["lines"] = [r[2] for r in out]
    return log


def tune_citations(s, pool, score, pubs, limit):
    cands = list(s["citations"]) + [p for p in pool.get("publications", []) if p not in s["citations"]]
    if pubs is None:
        s["citations"] = s["citations"][:limit]
        return {"chosen": s["citations"], "note": "no publications.md"}
    scored = [(score(pubs[p]["text"]) if p in pubs else -1, k, p) for k, p in enumerate(cands)]
    top = {c[2] for c in sorted(scored, key=lambda c: (-c[0], c[1]))[:limit]}
    s["citations"] = [p for p in cands if p in top]
    return {"chosen": s["citations"]}


def experience_entries(data):
    """Entries whose bullets tune may drop, newest role first."""
    out = []
    for s in data["sections"]:
        if checks.norm(s["heading"]).lower() in checks.EV_EXEMPT or checks.is_citation(s):
            continue
        out += s.get("entries", [])
    return out


def fit_pages(data, cap, pubs, log):
    """Drop non-keep bullets of the oldest roles until the build fits the cap."""
    try:
        pages = page_count(data, pubs)
    except (ImportError, build_resume.InputError):
        log["pages"] = None  # the strict build reports it
        return
    dropped = []
    while cap and pages > cap and len(dropped) < MAX_DROPS:
        for e in reversed(experience_entries(data)):
            idx = [i for i, b in enumerate(e.get("bullets", [])) if not (isinstance(b, dict) and b.get("keep"))]
            if idx:
                b = e["bullets"].pop(idx[-1])
                dropped.append({"entry": entry_key(e), "text": checks.bullet_text(b)[:60]})
                break
        else:
            break
        pages = page_count(data, pubs)
    log["pages"], log["dropped_for_pages"] = pages, dropped


def page_count(data, pubs):
    probe = build_resume.prepare(copy.deepcopy(data), pubs, None)
    return build_resume.count_pages(probe)


def tune_data(resume, pool, terms, posting_text, approved, pubs=None, pages=True):
    """(tuned resume, log): the five allowed operations and nothing else."""
    swaps = alias_swaps(pool, posting_text)
    score = Scorer(terms, swaps)
    data = copy.deepcopy(resume)
    entries = resume_entries(data)
    mode = approved.get("mode") or "industry"
    log = {"aliases": swaps, "entries": [], "skills": [], "publications": None}
    first_lines = True
    for s in data["sections"]:
        if is_summary(s):
            tune_summary(s, pool, score, log)
        elif "citations" in s:
            if mode == "industry":
                log["publications"] = tune_citations(s, pool, score, pubs, int(approved.get("publications_max") or 3))
        else:
            if "paragraph" in s:
                s["paragraph"] = apply_swaps(s["paragraph"], swaps)
            if "lines" in s:
                log["skills"] += tune_lines(s, pool, score, add_new=first_lines)
                first_lines = False
            for e in s.get("entries", []):
                if e.get("bullets") or pool_bullets(pool, e, entries):
                    log["entries"].append(tune_entry(e, pool, score, entries))
            if "bullets" in s:
                s["bullets"] = [swapped(b, swaps) for b in s["bullets"]]
    if pages:
        fit_pages(data, approved.get("max_pages"), pubs, log)
    log["note"] = tune_note(log)
    return data, log


def tune_note(log):
    """The today.md line: "summary 2; bullets 3,5 up; qPCR (alias of real-time PCR)"."""
    parts = []
    if log.get("summary", {}).get("chosen", 1) != 1:
        parts.append(f"summary {log['summary']['chosen']}")
    up, pooled = [], 0
    for e in log["entries"]:
        for pos, p in enumerate(e["picked"], 1):
            if p["from"] == "pool":
                pooled += 1
            elif p["index"] > pos:
                up.append(str(p["index"]))
    if up:
        parts.append(f"bullets {','.join(up)} up")
    if pooled:
        parts.append(f"{pooled} pool bullet{'s' if pooled != 1 else ''}")
    parts += [f'{s["to"]} (alias of {s["variant"]})' for s in log["aliases"]]
    if log.get("dropped_for_pages"):
        parts.append(f"{len(log['dropped_for_pages'])} dropped for length")
    return "; ".join(parts) or "base order"


# ---------- diff ----------

def numbers_of(text):
    return Counter(k for _, k in checks.numbers(text, words=True))


class Diff:
    def __init__(self, swaps, entries=()):
        self.swaps, self.fails, self.units = swaps, [], 0
        self.entries = list(entries)  # the approved resume's roles, for pool_bullets

    def fail(self, line):
        self.fails.append(f"VARIANT-FAIL {line}")

    def match(self, text, approved_texts):
        """The approved text this unit equals (directly or after the swaps), or None."""
        self.units += 1
        for a in approved_texts:
            if text == a or text == apply_swaps(a, self.swaps):
                return a
        return None

    def unit(self, where, text, approved_texts):
        a = self.match(text, approved_texts)
        if a is None:
            self.fail(f'{where}: not an approved unit ("{checks.norm(text)[:70]}")')
        elif numbers_of(text) != numbers_of(a):
            self.fail(f"{where}: a number differs from the approved text")
        return a


def variant_diff(resume, pool, tuned, swaps, approved):
    """VARIANT-FAIL lines (empty when every unit is approved) and the unit count."""
    table = {(a["variant"].lower(), x.lower()) for a in pool.get("aliases", []) for x in a["posting"]}
    d = Diff([s for s in swaps if (s.get("variant", "").lower(), s.get("to", "").lower()) in table],
             resume_entries(resume))
    for s in swaps:
        if (s.get("variant", "").lower(), s.get("to", "").lower()) not in table:
            d.fail(f'alias swap "{s.get("variant")}" -> "{s.get("to")}" is not in the approved alias table')
    for key in ("name", "contact", "page"):
        d.units += 1
        if tuned.get(key) != resume.get(key):
            d.fail(f"{key} changed")
    if [s["heading"] for s in tuned.get("sections", [])] != [s["heading"] for s in resume["sections"]]:
        d.fail("sections differ from the approved resume.json (headings or order)")
        return d.fails, d.units
    for t, a in zip(tuned["sections"], resume["sections"]):
        diff_section(d, t, a, pool, approved)
    return d.fails, d.units


def diff_section(d, t, a, pool, approved):
    h = a["heading"]
    extra = set(t) - set(a)
    if extra:
        d.fail(f"{h}: keys {sorted(extra)} are not in the approved section")
    if "paragraph" in a:
        opts = [a["paragraph"]] + (list(pool.get("summaries", [])) if is_summary(a) else [])
        d.unit(f"{h} paragraph", t.get("paragraph", ""), opts)
    if "lines" in a:
        diff_lines(d, t, a, pool)
    if "entries" in a:
        diff_entries(d, t, a, pool)
    if "bullets" in a:
        diff_bullets(d, f"{h}", t.get("bullets", []), list(a["bullets"]), keep_all=False)
    if "citations" in a:
        allowed = set(a["citations"]) | set(pool.get("publications", []))
        cites = t.get("citations", [])
        d.units += len(cites)
        for c in cites:
            if c not in allowed:
                d.fail(f"{h}: citation {c} is not in the variant or pool")
        if len(set(cites)) != len(cites):
            d.fail(f"{h}: a citation appears twice")
        limit = approved.get("publications_max")
        if (approved.get("mode") or "industry") == "industry" and limit is not None and len(cites) > int(limit):
            d.fail(f"{h}: {len(cites)} citations > publications_max {limit}")
        if bool(t.get("bold_name")) != bool(a.get("bold_name")):
            d.fail(f"{h}: bold_name changed")


def diff_lines(d, t, a, pool):
    h = a["heading"]
    allowed = {}
    for ln in a["lines"]:
        allowed.setdefault(ln["label"], []).extend(split_items(ln["text"]))
    for pl in pool.get("skills", []):
        allowed.setdefault(pl["label"], []).extend(pl["items"])
    for ln in t.get("lines", []):
        d.units += 1
        if ln.get("label") not in allowed:
            d.fail(f'{h} line "{ln.get("label")}": not an approved label')
            continue
        items = split_items(ln.get("text", ""))
        if len(set(items)) != len(items):
            d.fail(f'{h} line "{ln["label"]}": an item appears twice')
        for item in items:
            d.unit(f'{h} line "{ln["label"]}" item', item, allowed[ln["label"]])


def diff_entries(d, t, a, pool):
    h = a["heading"]
    te, ae = t.get("entries", []), a["entries"]
    if len(te) != len(ae):
        d.fail(f"{h}: {len(te)} entries, approved {len(ae)}")
        return
    for x, y in zip(te, ae):
        for k in ("title", "org", "location", "dates"):
            d.units += 1
            if x.get(k) != y.get(k):
                d.fail(f'{h} / {y["title"]} {k} "{x.get(k)}" (approved "{y.get(k)}")')
        if set(x) - set(y) - {"bullets"}:
            d.fail(f'{h} / {y["title"]}: keys {sorted(set(x) - set(y))} are not approved')
        allowed = list(y.get("bullets", [])) + pool_bullets(pool, y, d.entries or ae)
        diff_bullets(d, f'{h} / {y["title"]}', x.get("bullets", []), allowed, keep_all=True)


def diff_bullets(d, where, tuned, allowed, keep_all):
    used = set()
    for i, b in enumerate(tuned, 1):
        texts = [checks.bullet_text(x) for x in allowed]
        a = d.unit(f"{where} bullet {i}", checks.bullet_text(b), texts)
        if a is None:
            continue
        k = next((j for j, x in enumerate(allowed) if checks.bullet_text(x) == a and j not in used), None)
        if k is None:
            d.fail(f"{where} bullet {i}: an approved bullet appears twice")
            continue
        used.add(k)
        src = allowed[k]
        src_ev = src.get("ev") if isinstance(src, dict) else None
        b_ev = b.get("ev") if isinstance(b, dict) else None
        if src_ev != b_ev:
            d.fail(f"{where} bullet {i}: ev {b_ev} differs from the approved {src_ev}")
        if isinstance(src, dict) and bool(src.get("keep")) != bool(isinstance(b, dict) and b.get("keep")):
            d.fail(f"{where} bullet {i}: keep flag changed")
    if keep_all:
        for j, x in enumerate(allowed):
            if isinstance(x, dict) and x.get("keep") and j not in used:
                d.fail(f'{where}: keep bullet "{checks.bullet_text(x)[:50]}" is missing')


# ---------- the job folder ----------

def item_folder(root, item):
    """The job folder as a Path, which must lie inside <root>/applications."""
    root = Path(root)
    path = Path(item) if Path(item).is_absolute() else root / item
    apps = (root / "applications").resolve()
    try:
        inside = path.resolve().relative_to(apps) != Path(".")
    except ValueError:
        inside = False
    if not inside:
        raise Refused(f"ERROR {item} is not a job folder under {root / 'applications'}")
    if not path.is_dir():
        raise Refused(f"ERROR {path} is not a folder")
    return path


def family_of(root, item, family):
    """--family, else the family the queue gave this folder."""
    if family:
        check_family(family)
        return family
    it = answers.queue_item(root, item)
    if it and it.get("family"):
        check_family(it["family"])
        return it["family"]
    raise Refused(f"ERROR family unknown for {item.name}: pass --family")


def runner_names(item):
    path = item / RUNNER_FILES
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return set()
    try:
        data = jobfs.loads_json(text)  # R3: the safe loader (BadJson is a ValueError)
        if isinstance(data, list):
            return {str(x) for x in data}
    except ValueError:
        pass
    return {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}


def runner_write(item, name, data):
    """Write one runner file: never over a file the runner did not create (2.4),
    never in a claimed folder; the name joins .runner_files."""
    if (item / ".claimed").exists():
        raise Refused(f"REFUSED {item.name} is claimed")
    names = runner_names(item)
    before = set(names)
    target = item / name
    if target.exists() and name not in names:
        raise Refused(f"REFUSED {name} in {item.name} was not written by the runner")
    if isinstance(data, bytes):
        tmp = target.with_name(f".{name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    elif isinstance(data, dict) and name.endswith(".json") and name not in ("resume_B.json", "cover_B.json"):
        jobfs.atomic_write_json(target, data)  # keeps <stem>.prev.json beside it
        names.add(jobfs.prev_path(target).name)
    elif isinstance(data, (dict, list)):
        jobfs.atomic_write_text(target, json.dumps(data, indent=1, ensure_ascii=True) + "\n")
    else:
        jobfs.atomic_write_text(target, data)
    names.add(name)
    if names != before:
        jobfs.atomic_write_text(item / RUNNER_FILES, "\n".join(sorted(names)) + "\n")


def load_variant(root, family):
    v, approved, notes = usable(root, family)
    if v is None:
        raise Refused("\n".join(notes + [f"REFUSED {family} has no approved version"]))
    vdir = jobfs.Folder(root).variant(family, v)
    return v, approved, read_json(vdir / "resume.json"), read_json(vdir / "pool.json"), vdir, notes


def load_pubs(root):
    path = jobfs.Folder(root).publications
    if not path.exists():
        return None
    try:
        return checks.load_publications(path)
    except ValueError as e:
        raise Refused(f"ERROR {e}")


def item_terms(root, item, family, posting_text):
    path = item / "terms.json"
    if path.exists():
        try:
            return terms_list(jobfs.read_json(path, jobfs.MAX_FILE))  # R3: the safe loader
        except (OSError, ValueError):
            pass
    return terms_list(build_terms(root, family, posting_text))


def tune(root, item, family, emit=print, write=True, variant=None):
    """Tune one item; returns (tuned, log). Raises Refused for an unapproved
    family. variant is load_variant()'s result when the caller has it."""
    item = item_folder(root, item)
    family = family_of(root, item, family)
    if variant is None:
        variant = load_variant(root, family)
        for line in variant[5]:
            emit(line)
    v, approved, resume, pool, vdir, _ = variant
    posting = read_posting(item)
    pubs = load_pubs(root)
    terms = item_terms(root, item, family, posting)
    tuned, log = tune_data(resume, pool, terms, posting, approved, pubs)
    log = {"family": family, "version": v, "variant": f"{family}@v{v}", "approved_sha256": approved["sha256"],
           "mode": approved.get("mode"), "max_pages": approved.get("max_pages"), "at": jobfs.now_iso(root),
           "terms": [t for t, _ in terms], **log, "status": "tuned"}
    if write:
        runner_write(item, "resume_B.json", tuned)
        runner_write(item, "tune_log.json", log)
    emit(f"TUNE {family}@v{v}: {log['note']}")
    return tuned, log


def read_posting(item):
    try:
        return (item / "posting.md").read_text(encoding="utf-8-sig")
    except OSError as e:
        raise Refused(f"ERROR cannot read {item / 'posting.md'}: {e}")


def diff_item(root, item, emit=print):
    """VARIANT ok | VARIANT-FAIL lines for an item's resume_B.json."""
    item = item_folder(root, item)
    log = read_json(item / "tune_log.json")
    tuned = read_json(item / "resume_B.json")
    family, v = log.get("family"), log.get("version")
    if not family or not isinstance(v, int):
        raise Refused("ERROR tune_log.json does not name the variant")
    ok, why, approved = approval(root, family, v)
    if not ok:
        emit(f"VARIANT-FAIL {family}@v{v} is no longer approved: {why}")
        return False
    vdir = jobfs.Folder(root).variant(family, v)
    resume, pool = read_json(vdir / "resume.json"), read_json(vdir / "pool.json")
    fails, units = variant_diff(resume, pool, tuned, log.get("aliases") or [], approved)
    for line in fails:
        emit(line)
    if not fails:
        emit(f"VARIANT ok ({units} units)")
    return not fails


# ---------- tier-b ----------

def strict_build(root, json_path, base, approved, item, letter=False):
    """build_resume.py --strict into a temp folder: (ok, lines, folder)."""
    f = jobfs.Folder(root)
    out = Path(tempfile.mkdtemp(prefix="tier_b_"))
    argv = [str(json_path), "--out", str(out), "--base", base, "--strict"]
    sources = [p for p in (f.master_resume, f.library, f.profile) if p.exists()]
    if sources:
        argv += ["--sources", *map(str, sources)]
    if f.library.exists():
        argv += ["--library", str(f.library)]
    if f.techniques.exists():
        argv += ["--techniques", str(f.techniques)]
    if letter:
        argv += ["--posting", str(item / "posting.md")]
    else:
        argv += ["--mode", approved.get("mode") or "industry"]
        if approved.get("max_pages"):
            argv += ["--max-pages", str(approved["max_pages"])]
        if f.publications.exists():
            argv += ["--publications", str(f.publications)]
        if (item / "terms.json").exists():
            argv += ["--terms", str(item / "terms.json")]
    lines = []
    try:
        code = build_resume.main(argv, emit=lines.append)
    except SystemExit as e:  # argparse
        code = int(e.code or 1)
    except Exception as e:  # a crash fails closed, like any other failure
        code = 1
        lines.append(f"ERROR build stopped: {type(e).__name__}: {e}")
    return code == 0 and "VERDICT ok" in lines, lines, out


def install(item, built, base):
    """Copy a passing build's files into the job folder as runner files."""
    for ext in B_EXTS:
        src = built / (base + ext)
        if src.exists():
            runner_write(item, base + ext, src.read_bytes())


def problem_lines(lines):
    bad = [ln for ln in lines if ln.startswith(("STRICT-FAIL", "ERROR", "MISSING"))]
    return bad or [ln for ln in lines if ln.startswith("VERDICT")] or ["build failed"]


def fill_letter(letter, posting_text, focus, day):
    """cover_B.json from letter.json: slots filled from posting.md, then checked."""
    head = checks.posting_header(posting_text)
    values = {"{company}": head.get("Company", ""), "{role}": head.get("Role", ""),
              "{focus}": focus or "", "{date}": f"{day:%B} {day.day}, {day.year}"}
    data = copy.deepcopy(letter)

    def fill(text):
        for slot, value in values.items():
            if value:
                text = text.replace(slot, value)
        return text

    L = data["letter"]
    for k in ("date", "greeting", "closing"):
        if k in L:
            L[k] = fill(L[k])
    L["recipient"] = [fill(x) for x in L.get("recipient", [])]
    L["paragraphs"] = [fill(x) for x in L["paragraphs"]]
    L["company"], L["role"] = values["{company}"], values["{role}"]
    if focus:
        L["focus"] = focus
    return data


def pick_focus(posting_text, techniques):
    """{focus}: the hands-on techniques.md technique (name or alias) the posting
    uses most, copied as the posting writes it; None when it names none.
    Never a knockout name or a bare family term: those are requirements and
    search words, not work the user has done."""
    best = None
    for k, t in enumerate(hands_on(techniques)):
        for form in t.forms:
            if not form.strip():
                continue
            hits = checks.term_regex(form).findall(posting_text)
            if hits and (best is None or len(hits) > best[0]):
                best = (len(hits), k, checks.term_regex(form).search(posting_text).group(0))
    return best[2] if best else None


def tier_b(root, item, family=None, emit=print):
    """terms, tune, diff, strict build; FALLBACK base; PREP_FAILED.
    Returns built | fallback_base | prep_failed | claimed (queue resume_status)."""
    item = item_folder(root, item)
    if (item / ".claimed").exists():
        emit(f"SKIP {item.name} is claimed")
        return "claimed"
    family = family_of(root, item, family)
    try:
        variant = load_variant(root, family)
    except Refused as e:
        for line in str(e).splitlines():
            emit(line)
        emit(f"PREP_FAILED {family}: no approved variant")
        return "prep_failed"
    v, approved, resume, pool, vdir, notes = variant
    for line in notes:
        emit(line)
    posting = read_posting(item)
    terms_raw = build_terms(root, family, posting)
    label = f"{family}@v{v}"
    status, failure, log = "built", None, None
    try:
        runner_write(item, "terms.json", terms_raw)
        emit(f"TERMS {len(terms_raw)}")
        tuned, log = tune(root, item, family, emit=emit, variant=variant)
        if not diff_item(root, item, emit=emit):
            failure = "VARIANT-FAIL"
        else:
            ok, lines, out = strict_build(root, item / "resume_B.json", "resume_B", approved, item)
            if ok:
                install(item, out, "resume_B")
                emit(f"BUILD ok resume_B ({label})")
            else:
                failure = problem_lines(lines)[0]
                for line in problem_lines(lines):
                    emit(line)
            shutil.rmtree(out, ignore_errors=True)
    except Refused as e:
        failure = str(e).splitlines()[-1]
        emit(failure)
    if failure:
        emit("FALLBACK base")
        status, failure = fallback_base(root, item, vdir, resume, approved, label, emit)
    if status != "prep_failed" and answers.letter_required(posting):
        status, failure = letter_step(root, item, vdir, approved, posting, terms_list(terms_raw), emit, status)
    finish_log(root, item, log, family, v, status, failure, emit, approved=approved)
    if status == "prep_failed":
        emit(f"PREP_FAILED {label} {item.name}: {failure}")
    else:
        note = (log or {}).get("note", "base order") if status == "built" else "FALLBACK base"
        emit(f"TIER-B {status} {label} {item.name}: {note}; checks ok")
    return status


def fallback_base(root, item, vdir, resume, approved, label, emit):
    """Build the untouched approved resume.json; (fallback_base | prep_failed, failure)."""
    try:
        runner_write(item, "resume_B.json", resume)
        ok, lines, out = strict_build(root, vdir / "resume.json", "resume_B", approved, item)
        try:
            if ok:
                install(item, out, "resume_B")
                emit(f"BUILD ok resume_B (base {label})")
                return "fallback_base", "tuned build failed"
            for line in problem_lines(lines):
                emit(line)
            return "prep_failed", problem_lines(lines)[0]
        finally:
            shutil.rmtree(out, ignore_errors=True)
    except Refused as e:
        emit(str(e))
        return "prep_failed", str(e)


def letter_step(root, item, vdir, approved, posting, terms, emit, status):
    path = vdir / "letter.json"
    if not path.exists():
        return "prep_failed", "the form requires a cover letter and the variant has none"
    letter = read_json(path)
    focus = pick_focus(posting, library.load_techniques(jobfs.Folder(root).techniques))
    if focus is None and "{focus}" in json.dumps(letter.get("letter", {})):
        return "prep_failed", "letter: the posting names none of your hands-on techniques to fill {focus}"
    data = fill_letter(letter, posting, focus, jobfs.today(root))
    try:
        runner_write(item, "cover_B.json", data)
        ok, lines, out = strict_build(root, item / "cover_B.json", "cover_B", approved, item, letter=True)
        if ok:
            install(item, out, "cover_B")
            emit("BUILD ok cover_B")
        else:
            for line in problem_lines(lines):
                emit(line)
        shutil.rmtree(out, ignore_errors=True)
    except Refused as e:
        return "prep_failed", f"letter {e}"
    return (status, None) if ok else ("prep_failed", "letter " + problem_lines(lines)[0])


def finish_log(root, item, log, family, v, status, failure, emit, approved=None):
    """tune_log.json for the item. A log tune() never wrote (it raised first)
    still names family, version and approved_sha256, so the runner keeps a
    carried folder's fallback file only while that approval is in force."""
    log = dict(log or {"family": family, "version": v, "variant": f"{family}@v{v}", "at": jobfs.now_iso(root)})
    log.setdefault("family", family)
    log.setdefault("version", v)
    if approved and approved.get("sha256"):
        log.setdefault("approved_sha256", approved["sha256"])
    log["status"] = status
    if failure:
        log["failure"] = failure
    try:
        runner_write(item, "tune_log.json", log)
    except Refused as e:
        emit(str(e))


# ---------- main ----------

class Parser(argparse.ArgumentParser):
    """A bad argument is bad input: ERROR and exit 1 (exit 2 is reserved)."""

    def error(self, message):
        print(f"ERROR {message}")
        sys.exit(1)


def main(argv=None):
    ap = Parser(description=__doc__.splitlines()[0], allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("new", "approve", "check", "tune", "diff", "tier-b", "rebuild"):
        p = sub.add_parser(name, allow_abbrev=False)
        p.add_argument("--root", required=True, help="the JobSearch folder")
        if name in ("new", "approve"):
            p.add_argument("--family", required=True)
        elif name not in ("diff", "rebuild"):
            p.add_argument("--family")
        if name in ("tune", "diff", "tier-b"):
            p.add_argument("--item", required=True, help="the job folder")
    sub.choices["new"].add_argument("--copy", action="store_true", help="start from the newest version's files")
    ap_approve = sub.choices["approve"]
    ap_approve.add_argument("--version", type=int)
    ap_approve.add_argument("--mode", choices=sorted(build_resume.MODE_CAPS))
    ap_approve.add_argument("--max-pages", type=int)
    ap_approve.add_argument("--publications-max", type=int)
    sub.choices["rebuild"].add_argument("--approve", metavar="PLAN",
                                        help="the PLAN a rebuild printed: approve every family in it (the user's yes)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        if args.cmd == "rebuild":
            return cmd_rebuild(root, args.approve)
        if args.cmd == "new":
            return cmd_new(root, args.family, args.copy)
        if args.cmd == "approve":
            return cmd_approve(root, args.family, args.version, args.mode, args.max_pages, args.publications_max)
        if args.cmd == "check":
            return cmd_check(root, args.family)
        if args.cmd == "tune":
            tune(root, args.item, args.family)
            return 0
        if args.cmd == "diff":
            return 0 if diff_item(root, args.item) else 6
        return 6 if tier_b(root, args.item, args.family) == "prep_failed" else 0
    except Refused as e:
        print(str(e))
        return 1
    except FileExistsError as e:
        print(f"ERROR {e} already exists")
        return 1


if __name__ == "__main__":
    sys.exit(main())
