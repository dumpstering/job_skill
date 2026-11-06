# The JobSearch folder: every file, its one writer

Root: `Documents\JobSearch\`. The runner's code, venv, keys and lock live
outside it, in `%LOCALAPPDATA%\JobRunner\`, and nothing in this folder is
ever run as code. Each file has one writer; the others only read it. "You"
is Claude (a session or a scheduled task).

## The user's and your files

| Path | Writer | Holds |
|---|---|---|
| `<resume file>` | the user | the most complete resume (.docx, .pdf, .txt, .md) |
| `master_resume.md` | you (backup first) | text copy of that file; first line `Source: <file name>` |
| `profile.md` | you (backup first) | contact, eligibility, `Licenses held:`, `Unemployment claim:`, `Claim state:`, self-identification, resume preferences |
| `achievement_library.md` | you (backup first) | every achievement as a record, each with a stable `[L-0001]` ID (`library.py ids`) |
| `answers.md` | you, through `answers.py add` | the screening-answer bank, one `- ANS-012` line per answer |
| `publications.md`, `techniques.md` | you, after the user confirms | citations (`- P-001` lines) and techniques, one line each |
| `config/settings.toml`, `search_profile.toml`, `watchlist.csv`, `boards.csv` | you (`/job settings`, `/job watch`, setup), or the user in Notepad | the switches, families and places, watched boards, job boards |
| `config/lead_salt.txt` | `leads.py` (first use) | the local salt for lead ids |
| `variants/<family>/current.txt`, `variants/<family>/v<N>/` | you (`variants.py`) | role-family base resumes; a rework is a new `v<N+1>` |
| `runner/in/events/<ts>_<8hex>_<type>.json` | you, create-only, through `events.py emit`, `events.py add` or `tracker.py` | queue events, jobs from links joining today's list (`added`), tracker writes |
| `runner/in/leads/<date>_<HHMM>_<source>.jsonl` | you, create-only, through `leads.py validate` | alert cards (company, title, location) |
| `runner/in/requests/<ts>_<8hex>_<op>.json` | you, create-only, through `jobrunner.py request` | work for the runner |
| `claude/session.json` | you (the session) | `{task, started, beat, current_n}`, rewritten after every item |
| `claude/day_<date>/<step>.done` | you, create-only | empty markers: morning, inbox, intake, tier_a, weekly |
| `claude/<date>/morning.json` | you, create-only | the card's questions, drafts, referral drafts, urgent and away lists |
| `claude/<date>/sensitive_ok.json` | you (the session) | `{v, date, ok, blank}`: the jobs whose sensitive answers were confirmed today; `today_view.py next` builds each handover `Check:` line from it |
| `reports/week_<date>.md`, `reports/usage.csv` | you (`report.py`) | the weekly summary; usage readings |
| `worksearch/<STATE>_<from>_<to>.csv/.xlsx/.md` | you (`worksearch.py`, create-only; `_v2` for a re-export) | the work-search record |
| `network/raw/` | the user (drops exports) | LinkedIn and Google exports, never opened by you |
| `backups/` | you and the runner | dated copies, never deleted |

## The runner's files (you only read them)

| Path | Holds |
|---|---|
| `applications.xlsx` | the tracker (mode L: only `tracker.py apply-events` writes it; mode M: `tracker.py` directly) |
| `applications_archive_<year>.xlsx` | rows moved by `tracker.py archive --before <date>` |
| `queue/<date>/queue.json`, `addendum_<HHMM>.json` | the day's numbered items; frozen once that day has events |
| `queue/<date>/addendum_user.json` | the jobs from links (`added` events), with the numbers `events.py add` gave them |
| `queue/<date>/today.md`, `review.html`, `discards.csv`, `diy.md` | optional reading; every discard with its gate; the do-yourself list |
| `state/` | `seen/<YYYY-MM>.jsonl`, `trails/<YYYY-MM>.jsonl`, `pool.json`, `company_ats.json`, `probe_cache.csv`, `robots_cache.json`, `board_health.csv`, `board_polls.json`, `watchlist_auto.csv`, `queue_state.json`, `weights.json`, `leads_state.json`, `upload_manifest.json`, `tracker_events.jsonl`, `event_retries.json`, `network_raw.json`, `diy_base/`, `last_good/`; `run.lock` in mode S |
| `runner/out/status.json`, `tracker.json`, `needs_search.json`, `staged.json`, `liveness.json` | health, the tracker snapshot, companies to search, staged Tier A files, re-read postings |
| `runner/out/results/<id>.json`, `requests_done/<date>/` | one result per request |
| `runner/out/events_done/<date>/`, `events_rejected/` (with `.reason.txt`) | applied and refused events |
| `upload/NN_<Last>_Resume_<Employer12>.pdf`, `NNc_..._Cover_...pdf`, `NNw_...docx`, `E<n>_...pdf`; `upload/_done/<date>/` | the files the user attaches; only the runner moves them |
| `applications/_diy/<date>/<Company>.md` | answer sheets for do-yourself items (no links) |
| `applications/_not_sent/<YYYY-MM>/` | folders of queued jobs that never got a row in 30 days (moved, never deleted) |
| `network/network.csv`, `matches_<date>.csv`, `past_applications.csv` | people from the export (IDs N-0001...), the day's matches, earlier LinkedIn applications |
| `reports/funnel.csv`, `logs/runner_<date>.log` | daily metrics; the log, keys redacted |

## A job folder: `applications/<YYYY-MM-DD> <Company> - <Role>/`

| Files | Writer |
|---|---|
| `posting.md`, `posting.json`, `fill_plan.json`, `terms.json`, `resume_B.json`/`.docx`/`.pdf`/`.txt`, `cover_B.*`, `tune_log.json`, `.runner_files` | the runner; `.runner_files` lists them, and the runner never writes a file missing from it, or anything in a folder holding `.claimed` |
| `.claimed`, `resume.json` and the Tier A resume files, `notes.md`, `audit.md`, `filled.md`, `questions.json`, `cover_letter.*`, `submitted/` | you (a job-tailor sub-agent writes only inside its one folder) |

A job from a pasted link (SKILL.md steps 1-9) has no `.runner_files`; every
file in it is yours. So is a `/job add <link>` folder: its Tier B files and
fill plan come from your own `variants.py tier-b` and `answers.py plan`
runs. When either joins today's list (`addendum_user.json`) the runner
reads it there but never writes in the folder.

## Rules

- Before changing `profile.md`, `achievement_library.md`, `answers.md` or a
  config file, copy it to `backups/<name>_<YYYY-MM-DD_HHMM>.<ext>`.
- Files are only added or updated. To retire one, move it to `backups/`.
- Every runner JSON carries `_rev` and `_sha256`; a reader that finds a bad
  copy retries once after 5 seconds, then reads `<name>.prev.json`.
- Nothing personal leaves this folder: the runner sends nothing personal
  over the network except the USAJOBS key email the user agreed to, and
  `network/` rows reach you only for the current job.

## Exit codes (every script)

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | bad input (`ERROR ...`), or a refusal (`REFUSED ...` from answers.py, variants.py, library.py) |
| 2 | a package is missing (`pip install` it) |
| 3 | posting.py: nothing usable (`TRY` / `RERUN`), or `ROBOTS <host>` (read it in the browser); boards.py `GONE` or `ERROR` |
| 4 | tracker `LOCKED` (the workbook is open in Excel), or posting.py `JOB BOARD` |
| 5 | tracker `REFUSED` (a backward status move), or posting.py `CLOSED` |
| 6 | `STRICT-FAIL` (build_resume.py `--strict`), `VARIANT-FAIL` (variants.py diff), `PREP_FAILED` (tier-b) |
| 7 | netfetch `DENY` (host policy, robots or a private address) |
| 8 | the runner is `LOCKED` (another run holds its lock) |
| 9 | leads.py `PARSE_FAILURE` (an alert email gave no cards) |
