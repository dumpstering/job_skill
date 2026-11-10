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
| `variants/<family>/current.txt`, `variants/<family>/v<N>/` | you (`variants.py`) | role-family base resumes; a rework is a new `v<N+1>`; `base_resume.pdf` from `variants.py rebuild` |
| `runner/in/events/<ts>_<8hex>_<type>.json` | you, create-only, through `events.py emit`, `events.py add` or `tracker.py` | queue events, jobs from links joining today's list (`added`), tracker writes |
| `runner/in/leads/<date>_<HHMM>_<source>.jsonl` | you, create-only, through `leads.py validate` | alert cards (company, title, location) |
| `runner/in/requests/<ts>_<8hex>_<op>.json` | you, create-only, through `jobrunner.py request` | work for the runner |
| `claude/session.json` | you (the session) | `{task, started, beat, current_n}`, rewritten after every item |
| `claude/args.json`, `claude/args_inbox.json`, `claude/args_morning.json` | you, before each command that takes outside text | one command's options as JSON (`--args-file`, or `jobrunner.py request --json-file`); the script reads it once and removes it (SKILL.md "Scripts") |
| `claude/wording.txt` | you | a card or closing line for `report.py check-wording --file` |
| `claude/day_<date>/<step>.done` | you, create-only | empty markers: morning, inbox, intake, tier_a, weekly |
| `claude/<date>/morning.json` | you, create-only | the card's questions (their saved flags), drafts (with the field's `question_id`), referral drafts, urgent and away lists; `"prepared": false` when job-morning ran before today's list existed |
| `claude/<date>/sensitive_ok.json` | you (the session) | `{v, date, ok, blank}`: the jobs whose sensitive answers the user confirmed with `yes` today; `today_view.py next` builds each `SENSITIVE` and handover `Check:` line from it |
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
| `runner/in/bad/` (with `.reason.txt`) | event and request files that could not be read as JSON (too big, nested too deep, not JSON); set aside, never deleted |
| `upload/NN_<Last>_Resume_<Employer12>.pdf`, `NNc_..._Cover_...pdf`, `NNw_...docx`, `E<n>_...pdf`; `upload/_done/<date>/` | the files the user attaches; only the runner moves them |
| `applications/_diy/<date>/E<n>_<Company>.md` | answer sheets for do-yourself items, one per item, numbered as its `upload/E<n>_` file (no links) |
| `applications/_not_sent/<YYYY-MM>/` | folders of queued jobs that never got a row in 30 days (moved, never deleted) |
| `network/network.csv`, `matches_<date>.csv`, `past_applications.csv` | people from the export (IDs N-0001...), the day's matches, earlier LinkedIn applications |
| `reports/funnel.csv`, `logs/runner_<date>.log` | daily metrics; the log, keys redacted |

## A job folder: `applications/<YYYY-MM-DD> <Company> - <Role>/`

| Files | Writer |
|---|---|
| `posting.md`, `posting.json`, `fill_plan.json`, `terms.json`, `resume_B.json`/`.docx`/`.pdf`/`.txt`, `cover_B.*`, `tune_log.json`, `.runner_files` | the runner; `.runner_files` lists them, and the runner never writes a file missing from it, or anything in a folder holding `.claimed` |
| `.claimed`, `resume.json` and the Tier A resume files (`<Name> Resume.pdf`, `<Name> Cover Letter.pdf`), `final.json` (written by `build_resume.py --strict` only when every check passed; it names those files, and `today_view.py next` and staging use only them; once a library entry it cites is corrected, `next` prints `REBUILD #<n>` until a new build passes, or until you move it to `backups/` when the rebuild cannot pass), `notes.md`, `audit.md`, `filled.md`, `questions.json`, `cover_letter.*`, `submitted/` | you (a job-tailor sub-agent writes only inside its one folder) |

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
| 1 | bad input (`ERROR ...`), or a refusal (`REFUSED ...` from answers.py, variants.py, library.py); `ERROR --args-file ...`: write the file again under `claude/` and rerun |
| 2 | a package is missing (`pip install` it) |
| 3 | posting.py: a link the checks passed did not load (`TRY` / `RERUN`: a timeout or an HTTP error), or `ROBOTS <host>` (read it in the browser); boards.py `GONE` or `ERROR` |
| 4 | tracker `LOCKED` (the workbook is open in Excel), or posting.py `JOB BOARD` or `REFUSED` (a job board, a link that is not a plain public web address, or one that leads to a private address, before the request, while connecting or in a redirect: never fetched or opened by any tool, the browser included, and no `TRY` line; ask for the employer's own link or the posting text) |
| 5 | tracker `REFUSED` (a backward status move, or a second row for a job already sent), or posting.py `CLOSED` |
| 6 | `STRICT-FAIL` (build_resume.py `--strict`), `VARIANT-FAIL` (variants.py diff), `PREP_FAILED` (tier-b), `PROBLEM` (variants.py rebuild: that family stays out of the plan) |
| 7 | netfetch `DENY` (host policy, robots or a private address) |
| 8 | the runner is `LOCKED` (another run holds its lock) |
| 9 | leads.py `PARSE_FAILURE` (an alert email gave no cards) |
