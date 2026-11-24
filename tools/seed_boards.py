"""Grow a preset's watchlist.csv from the public StoreSignal ATS job boards directory.

Usage:
  python tools/seed_boards.py --preset lifesci_bayarea [--append]
  python tools/seed_boards.py --preset remote_tech --append --max-minutes 90

A maintainer tool: it is not packaged in the skill zip (build_zip.py packages
job/, the agents and the runner only). What it does, in order:

1. Downloads boards.csv once from the storesignalhq/ats-job-boards repository
   to the cache folder (default %LOCALAPPDATA%\\JobSkillTools; --cache moves
   it), through netfetch. The file is never copied into this repository.
2. Filters its rows by the preset's rules file, tools/seed/<preset>.toml
   (allowed ATS, minimum open jobs, company-name include and exclude regexes),
   and skips every (ats, slug) already in the preset's watchlist.csv, and
   every employer name already in it.
3. Reads each candidate's job list the way `boards.py fetch --list-only` does
   (boards.read_board, no detail calls; every request goes through netfetch:
   allowlist, robots.txt, one request a second per host). One thread per ATS,
   so the four hosts run side by side. Each answer is cached, so an
   interrupted run resumes and a rerun reads nothing it already has.
4. Keeps a board only when its list answered and at least min_relevant
   postings are relevant: located in one of the preset's home places and
   titled like one of its families (kind = "places", read from families.toml
   through gates.py; a strong life-science name also counts on location
   alone), or remote in the US (kind = "remote_us") with a title that names a
   wanted word.
5. Prints a summary (candidates, answered, kept, per ATS) and, with --append,
   adds the new rows to watchlist.csv in its exact column format (creating
   the file with the header when it is missing).

Credit: the directory is the "StoreSignal ATS job boards directory"
(github.com/storesignalhq/ats-job-boards), CC BY 4.0; rows carry ref
storesignal-2026-11.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import threading
import time
import tomllib
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "job" / "scripts"))

import boards  # noqa: E402
import gates  # noqa: E402
import jobfs  # noqa: E402
import jobkey  # noqa: E402
import netfetch  # noqa: E402
import posting  # noqa: E402

SOURCE_URL = "https://raw.githubusercontent.com/storesignalhq/ats-job-boards/main/boards.csv"
HEADER = ["employer", "employer_norm", "parent", "ats", "board", "host", "site", "extra", "careers_url",
          "main_website", "segment", "city", "families", "priority", "status", "added_on", "added_by", "ref",
          "notes"]
SEEDABLE = {"greenhouse", "lever", "ashby", "workable"}  # readers that poll today; recruitee waits for a selftest
GENERIC_PLACES = {"bay area", "san francisco bay area", "sf bay area", "peninsula", "silicon valley"}
# Words that, beside a remote location, name a region outside the US.
FOREIGN_REGIONS = re.compile(r"\b(europe|emea|apac|asia|latam|latin america|eu|uk|dach|nordics|benelux|anz|"
                             r"oceania|africa|middle east)\b", re.I)
US_WORDS = re.compile(r"\b(us|usa|u\.s\.|united states|north america|americas|anywhere|nationwide)\b", re.I)
REMOTE_WORD = re.compile(r"\bremote(?:ly)?\b", re.I)
PROBE_FILE = "probes.jsonl"
CONSECUTIVE_ERRORS = 10   # in a row: the thread pauses (the network or the host is the problem)
PAUSE_SECONDS = 30
MAX_PAUSES = 3


# ---------- the rules file ----------

def load_rules(preset):
    path = ROOT / "tools" / "seed" / f"{preset}.toml"
    if not path.is_file():
        raise SystemExit(f"ERROR no rules file {path}")
    with open(path, "rb") as fh:
        r = tomllib.load(fh)
    r.setdefault("ats", sorted(SEEDABLE))
    bad = set(r["ats"]) - SEEDABLE
    if bad:
        raise SystemExit(f"ERROR ats {sorted(bad)} cannot be seeded (no reader polls them today)")
    r.setdefault("min_open_jobs", 1)
    r.setdefault("min_relevant", 1)
    r.setdefault("cap", 0)
    r.setdefault("priority", 3)
    r.setdefault("segment", "biotech")
    r.setdefault("ref", "storesignal-2026-11")
    r["_include"] = [re.compile(x, re.I) for x in r.get("name_include", [])]
    r["_exclude"] = [re.compile(x, re.I) for x in r.get("name_exclude", [])]
    r["_strong"] = [re.compile(x, re.I) for x in r.get("strong_name", [])]
    r["_segments"] = [(re.compile(x["match"], re.I), x["segment"]) for x in r.get("segment_rule", [])]
    rel = r.get("relevant") or {}
    if rel.get("kind") not in ("places", "remote_us"):
        raise SystemExit("ERROR [relevant] kind must be 'places' or 'remote_us'")
    r["_title_any"] = [re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in rel.get("title_any", [])]
    r["_title_caps"] = [re.compile(r"\b" + re.escape(t) + r"\b") for t in rel.get("title_caps", [])]
    return r


# ---------- directory download ----------

def fetch_directory(cache, refresh):
    path = cache / "boards.csv"
    if path.is_file() and not refresh:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    f = netfetch.Fetcher(unattended=False, user_agent=netfetch.USER_AGENT)  # attended: public hosts, robots obeyed
    r = f.get(SOURCE_URL, accept="text/csv,text/plain,*/*;q=0.5").check()
    if not r.text().startswith("ats,slug,"):
        raise SystemExit("ERROR the directory did not look like boards.csv")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(r.body)
    os.replace(tmp, path)
    print(f"DOWNLOADED {path} ({len(r.body)} bytes)")
    return path


def read_directory(path):
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                row["open_jobs"] = int(row.get("open_jobs") or 0)
            except ValueError:
                row["open_jobs"] = 0
            yield row


# ---------- the watch list ----------

def read_watchlist(path):
    if not path.is_file():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def candidates(rows, rules, existing):
    """(candidates, counts of what was dropped) from the directory rows."""
    have = {(r["ats"].strip().lower(), r["board"].strip().lower()) for r in existing if r.get("ats") and r.get("board")}
    names = {r["employer_norm"] for r in existing if r.get("employer_norm")}
    seen, out, drop = set(), [], Counter()
    for row in rows:
        ats, slug = row["ats"].strip().lower(), row["slug"].strip()
        name = (row.get("company_name") or "").strip() or slug
        if ats not in rules["ats"]:
            drop["ats"] += 1
        elif row["open_jobs"] < rules["min_open_jobs"]:
            drop["open_jobs"] += 1
        elif rules["_include"] and not any(rx.search(name) for rx in rules["_include"]):
            drop["name"] += 1
        elif any(rx.search(name) for rx in rules["_exclude"]):
            drop["excluded"] += 1
        elif (ats, slug.lower()) in have:
            drop["on watch list"] += 1
        elif jobkey.norm_company(name) in names:
            drop["employer on watch list"] += 1
        elif (ats, slug.lower()) in seen:
            drop["repeat"] += 1
        else:
            seen.add((ats, slug.lower()))
            out.append(dict(row, ats=ats, slug=slug, name=name))
    out.sort(key=lambda r: -r["open_jobs"])
    return out, drop


# ---------- the probe cache ----------

class Probes:
    """key 'ats:slug' -> the cached answer of one list call: status, why, and
    (title, location, remote) of each posting. Appended to a JSONL file, so a
    killed run loses at most the board in flight."""

    def __init__(self, path, max_age_days):
        self.path, self.lock, self.data = path, threading.Lock(), {}
        cutoff = time.time() - max_age_days * 86400
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("at", 0) >= cutoff and e.get("status") != "ERROR":  # an error is read again
                    self.data[e["key"]] = e

    def get(self, key):
        return self.data.get(key)

    def put(self, entry):
        with self.lock:
            self.data[entry["key"]] = entry
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")


def probe(fetcher, ats, slug, name):
    """One list call through boards.read_board, as `boards.py fetch --list-only` does it."""
    spec = boards.BoardSpec(ats, slug, employer=name)
    res = boards.read_board(spec, fetcher, details=False, first_seen=jobfs.today().isoformat())
    return {"key": f"{ats}:{slug}", "at": time.time(), "status": res.status, "why": res.why[:200],
            "posts": [[rd.posting.title, rd.posting.location, rd.posting.remote] for rd in res.readings]}


class LockedThrottle(netfetch.Throttle):
    """netfetch's one-request-a-second-per-host throttle, safe for several threads: the lock
    spaces the starts of requests, so a slow download overlaps the next one but two requests
    to a host never start within a second of each other."""

    def __init__(self, stamp_dir=None):
        super().__init__()
        self.lock = threading.Lock()
        self.stamp_dir = Path(stamp_dir) if stamp_dir else None

    def wait(self, host, crawl_delay=None):
        with self.lock:
            if self.stamp_dir is None:
                return super().wait(host, crawl_delay)
            return self.wait_shared(host, crawl_delay)

    def wait_shared(self, host, crawl_delay):
        """The same spacing across processes (--shard runs several on one ATS): a stamp file per
        host holds the wall-clock time of the last request start, behind a lock file."""
        gap = max(netfetch.MIN_INTERVAL, min(crawl_delay or 0, netfetch.MAX_CRAWL_DELAY))
        safe = re.sub(r"[^a-z0-9.-]", "_", host.lower())
        stamp, lock = self.stamp_dir / f"throttle_{safe}.stamp", self.stamp_dir / f"throttle_{safe}.lock"
        waited = 0.0
        for _ in range(3000):  # the lock is held for a few milliseconds
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 30:  # a killed process left it
                        lock.unlink()
                except OSError:
                    pass
                time.sleep(0.02)
        try:
            try:
                last = float(stamp.read_text(encoding="ascii").strip() or 0)
            except (OSError, ValueError):
                last = 0.0
            waited = max(0.0, last + gap - time.time())
            if waited:
                time.sleep(waited)
            stamp.write_text(f"{time.time():.3f}", encoding="ascii")
        finally:
            try:
                lock.unlink()
            except OSError:
                pass
        return waited


def run_probes(todo, probes, deadline, progress, workers=3, stamp_dir=None):
    """Per ATS: one fetcher (so one throttle per host) shared by `workers` threads that take
    the next board off a queue; the ATS hosts run side by side. Returns the count read this run."""
    by_ats = defaultdict(list)
    for c in todo:
        by_ats[c["ats"]].append(c)
    done, stop = Counter(), threading.Event()

    def worker(ats, items, f, lock, total):
        streak = pauses = 0
        while True:
            with lock:
                if not items:
                    return
                c = items.pop(0)
            if stop.is_set() or time.time() >= deadline:
                return
            try:
                e = probe(f, ats, c["slug"], c["name"])
            except Exception as ex:  # noqa: BLE001  one board never stops the run
                e = {"key": f"{ats}:{c['slug']}", "at": time.time(), "status": "ERROR",
                     "why": f"{type(ex).__name__}: {str(ex)[:120]}", "posts": []}
            probes.put(e)
            done[ats] += 1
            streak = streak + 1 if e["status"] == "ERROR" else 0
            if streak >= CONSECUTIVE_ERRORS:
                pauses += 1
                print(f"PAUSE {ats}: {streak} errors in a row (last: {e['why']}); pause {pauses}/{MAX_PAUSES}",
                      flush=True)
                if pauses >= MAX_PAUSES:
                    return
                time.sleep(PAUSE_SECONDS)
                streak = 0
            if done[ats] % 50 == 0:
                progress(ats, done[ats], total)

    threads = []
    for ats, items in by_ats.items():
        f = netfetch.Fetcher(root=None, throttle=LockedThrottle(stamp_dir))
        lock, total = threading.Lock(), len(items)
        threads += [threading.Thread(target=worker, args=(ats, items, f, lock, total), daemon=True)
                    for _ in range(workers)]
    try:
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        print("STOPPING: finishing the requests in flight", flush=True)
        for t in threads:
            t.join(timeout=70)
    return sum(done.values())


# ---------- relevance ----------

def home_rules(preset_dir):
    """The [home] places, the title families and the global title excludes of the preset's
    families.toml, as gates.home_place_verdict and gates.family_score read them."""
    with open(preset_dir / "families.toml", "rb") as fh:
        data = tomllib.load(fh)
    home = data.get("home") or {}
    fams = [f for f in data.get("family") or [] if str(f.get("mode", "industry")).lower() != "usajobs"]
    return SimpleNamespace(places=list(home.get("places") or []), second_ring=list(home.get("second_ring") or []),
                           home_states=frozenset(home.get("states") or []), employers={}, regions=None,
                           families=fams, exclude=list((data.get("gates") or {}).get("exclude_title_words") or []))


def family_title(title, home):
    """True when a title is one the preset's families look for: it passes the global
    title excludes and matches a core, adjacent or title_any term of any family
    (paused ones too: setup turns them on from the user's own roles)."""
    if not (title or "").strip():
        return False
    tw = gates.words(title)
    if any(gates.exclude_hit(w, title, tw) for w in home.exclude):
        return False
    return any(gates.family_score(f, title)[0] for f in home.families)


def bay_place(location, rules):
    """The longest home place named by a location field, or ''. Only a place that is
    in the home area counts: 'Dublin, Ireland' and 'Newark, NJ' are out."""
    best = ""
    for part in posting.place_parts(location):
        kind, _ = gates.home_place_verdict(part, rules)
        if kind != "in":
            continue
        words = gates.words(part)
        for pl in rules.places:
            if gates.term_hits(pl, words) and len(pl) > len(best):
                best = pl
    return best


def remote_us(title, location, remote):
    """True when the posting is remote in the US: the location says Remote with a US
    word, a US state, or no country at all (never a foreign country or region
    unless the US is named too); or the board flags it remote and names the US."""
    parts = posting.place_parts(location)
    flagged = remote == "remote"
    if not parts:
        return flagged
    for part in parts:
        said = bool(REMOTE_WORD.search(part))
        if not (said or flagged):
            continue
        states, foreign, us = posting.region_of(part)
        us = us or bool(US_WORDS.search(part))
        if foreign and not us and not states:
            continue
        if FOREIGN_REGIONS.search(part) and not us and not states:
            continue
        rest = REMOTE_WORD.sub("", part)
        rest = re.sub(r"[\W_]+", " ", rest).strip()
        if us or states:
            return True
        if not rest:  # a bare "Remote": the title may still name the country ("... - Aarhus, Denmark")
            t_states, t_foreign, t_us = posting.region_of(title or "")
            if not (t_foreign and not t_us and not t_states):
                return True
    return False


def title_wanted(title, rules):
    return any(rx.search(title) for rx in rules["_title_any"]) or any(rx.search(title) for rx in rules["_title_caps"])


def judge(entry, rules, home, name=""):
    """(relevant count, best city, note) for one cached probe; (0, '', '') when it did not answer
    or too little is relevant. kind "places": a posting in a home place whose title is one of the
    preset's families counts; a board whose name has a strong_name word (a biotech, a pharma) also
    counts on any posting in a home place, since such an employer posts titles the families will
    meet later. kind "remote_us": a US-remote posting with a wanted title."""
    if entry["status"] != "OK":
        return 0, "", ""
    rel = rules["relevant"]
    if rel["kind"] == "remote_us":
        n = sum(1 for title, location, remote in entry["posts"]
                if remote_us(title, location, remote) and title_wanted(title, rules))
        return (n, "", rel["note"]) if n >= rules["min_relevant"] else (0, "", "")
    fam, anyp = Counter(), Counter()
    nf = na = 0
    for title, location, remote in entry["posts"]:
        pl = bay_place(location, home)
        if not pl:
            continue
        na += 1
        if pl.lower() not in GENERIC_PLACES:
            anyp[pl] += 1
        if family_title(title, home):
            nf += 1
            if pl.lower() not in GENERIC_PLACES:
                fam[pl] += 1
    if nf >= rules["min_relevant"]:
        return nf, (fam.most_common(1)[0][0] if fam else ""), rel["note"]
    if na >= rules["min_relevant"] and any(rx.search(name) for rx in rules["_strong"]):
        return na, (anyp.most_common(1)[0][0] if anyp else ""), rel["note_strong"]
    return 0, "", ""


# ---------- rows ----------

def segment_of(name, rules):
    for rx, seg in rules["_segments"]:
        if rx.search(name):
            return seg
    return rules["segment"]


def board_urls(c):
    return {"greenhouse": f"https://job-boards.greenhouse.io/{c['slug']}",
            "lever": f"https://jobs.lever.co/{c['slug']}",
            "ashby": f"https://jobs.ashbyhq.com/{c['slug']}",
            "workable": f"https://apply.workable.com/{c['slug']}/"}[c["ats"]]


def make_row(c, entry, n, city, note, rules, today):
    note = f"StoreSignal list; answered {today} with {len(entry['posts'])} postings, {n} {note}"
    return {"employer": c["name"], "employer_norm": jobkey.norm_company(c["name"]), "parent": "", "ats": c["ats"],
            "board": c["slug"], "host": "", "site": "", "extra": "", "careers_url": board_urls(c),
            "main_website": "", "segment": segment_of(c["name"], rules), "city": city, "families": "",
            "priority": rules["priority"], "status": "active", "added_on": today, "added_by": "research",
            "ref": rules["ref"], "notes": note}


def rows_text(rows, with_header):
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\r\n")
    if with_header:
        w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def append_rows(path, rows):
    text = rows_text(rows, not path.is_file() or path.stat().st_size == 0)
    if path.is_file() and path.stat().st_size:
        tail = path.read_bytes()[-2:]
        if tail != b"\r\n":
            text = "\r\n" + text
    with open(path, "a", encoding="ascii", newline="") as fh:  # ASCII: a name that is not raises, never mojibake
        fh.write(text)


def ascii_name(name):
    """The name with accents folded and anything else non-ASCII dropped (the preset files are ASCII)."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", plain).strip()


# ---------- main ----------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Seed a preset's watch list from the StoreSignal ATS directory.")
    ap.add_argument("--preset", required=True, help="a folder under job/templates/presets/ with tools/seed/<preset>.toml")
    ap.add_argument("--cache", help="cache folder (default %%LOCALAPPDATA%%\\JobSkillTools)")
    ap.add_argument("--refresh", action="store_true", help="download boards.csv again")
    ap.add_argument("--max-minutes", type=float, default=90.0, help="stop starting list calls after this long")
    ap.add_argument("--max-age-days", type=float, default=7.0, help="reuse a cached answer younger than this")
    ap.add_argument("--limit", type=int, default=0, help="probe at most this many candidates per ATS (a trial run)")
    ap.add_argument("--append", action="store_true", help="add the kept rows to the preset's watchlist.csv")
    ap.add_argument("--ats", help="read only these ATSs (comma list); one process per ATS uses a core each")
    ap.add_argument("--shard", help="I/N: read every Nth unread board starting at I (0-based), so N processes split "
                                    "one ATS; they share one request spacing per host through stamp files in the cache")
    ap.add_argument("--no-probe", action="store_true", help="judge the cached answers only; no network")
    ap.add_argument("--keep-name-dupes", action="store_true", help="do not skip a name already on the watch list")
    args = ap.parse_args(argv)

    rules = load_rules(args.preset)
    preset_dir = ROOT / "job" / "templates" / "presets" / args.preset
    if not preset_dir.is_dir() and rules["relevant"]["kind"] == "places":
        raise SystemExit(f"ERROR no preset folder {preset_dir}")  # its families.toml names the home places
    cache = Path(args.cache) if args.cache else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "JobSkillTools"
    cache.mkdir(parents=True, exist_ok=True)
    watch = preset_dir / "watchlist.csv"
    existing = read_watchlist(watch)
    mine = sum(1 for r in existing if r.get("ref") == rules["ref"])
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

    if args.no_probe and not (cache / "boards.csv").is_file():
        raise SystemExit("ERROR --no-probe needs the cached boards.csv")
    directory = fetch_directory(cache, args.refresh)
    rows = list(read_directory(directory))
    existing_for_skip = existing if not args.keep_name_dupes else [dict(r, employer_norm="") for r in existing]
    cands, drop = candidates(rows, rules, existing_for_skip)
    print(f"DIRECTORY {len(rows)} rows; candidates {len(cands)}; dropped "
          + ", ".join(f"{k} {v}" for k, v in drop.most_common()))

    probes = Probes(cache / PROBE_FILE, args.max_age_days)
    only = {a.strip().lower() for a in (args.ats or "").split(",") if a.strip()}
    todo = [c for c in cands if probes.get(f"{c['ats']}:{c['slug']}") is None and (not only or c["ats"] in only)]
    if args.shard:
        i, _, n = args.shard.partition("/")
        if not (i.isdigit() and n.isdigit() and 0 <= int(i) < int(n)):
            raise SystemExit("ERROR --shard wants I/N with 0 <= I < N")
        todo = todo[int(i)::int(n)]
    cached = len(cands) - len(todo)
    if args.limit:
        keep = Counter()
        trimmed = []
        for c in todo:
            if keep[c["ats"]] < args.limit:
                keep[c["ats"]] += 1
                trimmed.append(c)
        todo = trimmed
    print(f"TO READ {len(todo)} list calls (cached {cached}); " + ", ".join(
        f"{a} {n}" for a, n in sorted(Counter(c['ats'] for c in todo).items())), flush=True)
    started = time.time()
    if todo and not args.no_probe:
        def progress(ats, n, total):
            print(f"PROGRESS {ats} {n}/{total} ({(time.time() - started) / 60:.1f} min)", flush=True)
        n = run_probes(todo, probes, started + args.max_minutes * 60, progress, stamp_dir=cache)
        print(f"READ {n} boards this run in {(time.time() - started) / 60:.1f} min")

    home = home_rules(preset_dir) if rules["relevant"]["kind"] == "places" else None
    per = defaultdict(Counter)
    kept = []
    for c in cands:
        e = probes.get(f"{c['ats']}:{c['slug']}")
        per[c["ats"]]["candidates"] += 1
        if e is None:
            per[c["ats"]]["unread"] += 1
            continue
        if e["status"] in ("OK", "EMPTY"):
            per[c["ats"]]["answered"] += 1
        else:
            per[c["ats"]][e["status"].lower()] += 1
        n, city, note = judge(e, rules, home, c["name"])
        if n and ascii_name(c["name"]):
            c["name"] = ascii_name(c["name"])
            kept.append((n, c, e, city, note))
    kept.sort(key=lambda k: (-k[0], -k[1]["open_jobs"], k[1]["slug"]))
    room = None
    print(f"QUALIFIED {len(kept)} boards (per ATS: " + ", ".join(
        f"{a} {n}" for a, n in sorted(Counter(k[1]["ats"] for k in kept).items())) + ")")
    if rules["cap"]:
        room = max(0, rules["cap"] - mine)
        kept = kept[:room]
    for _, c, _, _, _ in kept:
        per[c["ats"]]["kept"] += 1

    print("SUMMARY ats candidates answered kept (other answers)")
    total = Counter()
    for ats in sorted(per):
        p = per[ats]
        other = ", ".join(f"{k} {v}" for k, v in sorted(p.items()) if k not in ("candidates", "answered", "kept"))
        print(f"  {ats:10} {p['candidates']:6} {p['answered']:6} {p['kept']:6}  {other}")
        total.update(p)
    print(f"  {'total':10} {total['candidates']:6} {total['answered']:6} {total['kept']:6}  unread {total['unread']}")
    if room is not None:
        print(f"CAP {rules['cap']} (already on the list from this source: {mine}; room {room})")

    new_rows = [make_row(c, e, n, city, note, rules, today) for n, c, e, city, note in kept]
    out = cache / f"new_rows_{args.preset}.csv"
    out.write_text(rows_text(new_rows, True), encoding="ascii", newline="")
    print(f"KEPT {len(new_rows)} rows; written to {out}")
    if args.append and new_rows:
        preset_dir.mkdir(parents=True, exist_ok=True)
        append_rows(watch, new_rows)
        print(f"APPENDED {len(new_rows)} rows to {watch}")
    elif new_rows:
        print("DRY RUN: add --append to write them to the watch list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
