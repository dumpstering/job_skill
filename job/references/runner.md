# The job finder (runner): modes, events, requests, heartbeat, selftest

The runner is `jobrunner.py`, the same code as the skill's scripts, run on the
user's PC by Task Scheduler. It does all the zero-token work: board and aggregator
reads, lead resolution, gates, the queue, Tier B resumes, fill plans, upload
staging, the tracker writes. You talk to it only through files in the
JobSearch folder. You never run code from the folder, never move upload
files, and never write the runner's files.

## Modes (`config/settings.toml [runner] mode`)

| Mode | What runs | When |
|---|---|---|
| L | the runner on the user's PC (installed by `install_runner.cmd`) | default once installed and `selftest` passed |
| S | the same `jobrunner.py` in your sandbox, `--mode S` | only if the sandbox check passed (`status.json` `selftest_s` `net` PASS, setup.md) and the PC runner has not run for over 36 hours (daily.md step 3) |
| W | you fetch board URLs with web fetch (`posting.py --json-file`) | experiment, off (`experiment_mode_w = false`) |
| M | no runner: the v1.1 flow, tracker writes go straight to the xlsx | before the runner is installed, or when nothing else works |

- Setup writes `mode = "M"` until the runner is installed; switch to `"L"` after its selftest PASS lines.
- Every pasted link (`/job <link>`) works in every mode.
- In mode S run, in the sandbox: `python "<skill>/scripts/jobrunner.py" daily --root "<folder>" --mode S`
  (a root-local lock, `state/run.lock`), then `tick --mode S` after writing events or requests.
  A tick that prints `TICK the daily is due (...)` asks you to run that daily now (mode S has no
  Daily task).

## What the runner writes (read these; never edit them)

| File | What |
|---|---|
| `queue/<date>/queue.json`, `addendum_<HHMM>.json` | the day's numbered items (2.5); immutable once an event exists for the date |
| `queue/<date>/addendum_user.json` | the jobs from links (`added` events, below) with the numbers you gave them; written by the tick, never prepped by it |
| `queue/<date>/today.md`, `review.html`, `discards.csv`, `diy.md` | the user's optional reading; the do-yourself list |
| `applications/<job>/posting.md`, `posting.json`, `fill_plan.json`, `resume_B.*`, `terms.json`, `tune_log.json`, `.runner_files` | per job; the runner never writes a file missing from `.runner_files`, and never in a folder holding `.claimed` |
| `upload/NN_<Last>_Resume_<Employer12>.pdf`, `upload/E<n>_...pdf` | the files the user attaches; only the runner moves them. The runner's own do-yourself items are E1 and up; E51 and up are a session's pasted or Easy Apply jobs (`events.py diy-add`), which the runner leaves alone that day and moves the next |
| `applications/_diy/<date>/E<n>_<Company>.md` | answer sheet for a do-yourself item (no link), one per item, with the `n` of its `upload/E<n>_` file |
| `runner/out/status.json` | health: `version`, `features` (what this runner's tracker takes beyond v1.2.0: `linkedin_source`, a row with Source LinkedIn; `today_view.py linkedin` prints `LINKEDIN WAIT runner` without it), `mode`, `last_daily {at, date, ok, counts}`, `last_tick`, `tracker_locked_since`, `sources`, `boards_paused`, `selftest {at, net, checks, recruitee}` (the PC's), `selftest_s` (the sandbox check), `min_skill`, `errors`, `daily_pending`, `daily_starts {date, count, at, why, how}` (dailies a tick started), `daily_running {at, pid}` (a daily is building the list now; null when none, and one over 2 hours old has stopped), `discovery_offline` (the date of a morning when no job board answered, until a run reaches them) |
| `runner/out/tracker.json` | the tracker snapshot (tracker.py reads it in mode L) |
| `runner/out/results/<id>.json` | one per request: `{id, op, ok, at, lines, result}` |
| `runner/out/staged.json` | Tier A files staged by `stage_tier_a`, with their sha256 |
| `runner/out/liveness.json` | the next items' postings re-read by the tick: `{items: {job_key: {checked, closed, why}}}`; only through the URL discovery read (the posting's own url), and only when the unattended policy allows it or its ATS API endpoint; a board's apply link is never a fallback (`why` "skip ..." otherwise); written after each item, and an item starts only while the tick has room for its reads inside its 10 minutes (`LIVENESS budget: <n> items left for the next tick`) |
| `state/request_starts/<id>.json` | the attempt marker of a request being answered (removed when it ends; a tick removes one whose request was answered or that is over 30 days old, never one whose request file still waits in `runner/in/requests`) |
| `runner/in/bad/` | event and request files that cannot be read as JSON (over 64 KB, nested deeper than 64, or not JSON after 3 tries), each with a `.reason.txt`; never deleted |
| `reports/funnel.csv`, `logs/runner_<date>.log` | metrics and the log (keys redacted) |

Every runner JSON carries `_rev` and `_sha256`. Read it with the verified
reader (`jobfs.read_json_verified`): it retries once after 5 s, then uses
`<name>.prev.json`.

## Session rules: claude/session.json (the heartbeat)

- Write `claude/session.json` `{task, started, beat, current_n}` when `/job` starts and rewrite it after every item.
- While `beat` is under 20 minutes old the runner only appends addenda with new numbers. It does not rebuild or renumber the queue, does not change or move the files already in `upload/` (except moving the files of applied items), and a `rebuild_queue` request waits.
- Late finds during a session land in `addendum_<HHMM>.json` (numbers after the last one) with their job folders and Tier B files; their upload copies are staged on the next tick (a `more` request's at once), under their new numbers. Until then `today_view.py next` names the file in the job folder.
- A daily that has not built today's queue never waits for a session (no numbers exist yet), so a
  session opened before the list still gets it. Only a rebuild of a queue that exists waits
  (`DAILY WAIT`); the first tick after the beat goes stale starts it.

## Events (what you hand the runner)

Write events only with `events.py emit` (or `events.emit` in Python), never by hand
(`emit` writes every type but `added`, which only `events.py add` writes):
`runner/in/events/<YYYYMMDDTHHMMSSZ>_<8hex>_<type>.json`, create-only, at most 64 KB, idempotent by id.
- `tracker`: an exact tracker.py write; tracker.py itself emits these in mode L (`--transport auto`).
- `started`, `handed_over`, `applied`, `skipped`, `later`, `parked`, `unparked`, `blocked`: the queue states, with `{reason, question_ids, confirmation, proof}` (reason codes from 5.10, or `user`).
- `answer_used`, `usage`.
- `added`: a job from a link joins today's list (spec 5.11, addendum_user).
  Write it only with `events.py add --folder "<job folder>" --via pasted|add`:
  it numbers the job after the highest number given today, prints `ADDED
  #<n> <key> (addendum_user)`, and holds the whole queue item, so
  `today_view.py`, `tracker.py check` (`IN QUEUE`) and `events.py overlay`
  (`ADDED #<n> <key> <via>`) see it at once. The runner numbers its own late
  addenda after these. Mode M has no list: `add` prints `NO LIST` and writes
  nothing, and the tracker row is the record. A posting.md whose URL is a
  job board or not a plain public web address is refused (`ERROR`). The
  tick takes an added job's link, host and upload name from its posting.md
  and its own number, never from the event, and ignores an event whose
  number or job key was already given today.
The tick applies them in name order (`tracker.py apply-events`), moves them to
`runner/out/events_done/<date>/`, folds queue states into `state/queue_state.json`,
writes the `added` jobs into `queue/<date>/addendum_user.json` (it never
writes in their folders, which are yours),
moves the upload files of applied items to `upload/_done/<date>/` and updates
`reports/funnel.csv`. A file that will not parse is retried on 3 ticks; one
that cannot be read as JSON then (or at once when over 64 KB or nested
deeper than 64) moves to `runner/in/bad/` with a `.reason.txt` and one
`ERROR bad event file` line, and one that parses but breaks the rules moves
to `runner/out/events_rejected/`; both count in `TRACKER REJECTED`. A request
file that cannot be read as JSON is answered `ok` false and moves to
`runner/in/bad/` too. No bad file stops a run, the card or `next`. `events.py overlay`
(and `today_view.py`) read pending and applied events together, so you never wait for a tick.

## Requests (what you ask the runner to do)

Write the arguments as one JSON object to `R/claude/args.json` with your file
tool, then `python "<skill>/scripts/jobrunner.py" request <op> --root "<folder>" --json-file
claude/args.json` (it reads the file once, validates, and creates
`runner/in/requests/<id>_<op>.json`, printing `REQUEST <id> <op>`). A company
name or a URL never goes on the command line (SKILL.md "Scripts"); `--json`
inline is only for codes and numbers.
Arguments are company names, URLs and folders inside the root; anything else
is refused before the file is written, and the runner checks again. A URL is
judged by `netfetch.web_refusal`, the one check every link goes through: a
job board in any spelling or reading (lnkd.in and licdn.com too), a link that
is not a plain public web address (`DENY bad-url`: a backslash, space or
control character, user info, a port other than 80 or 443, an IP address, a
one-label or local name), a name that leads to a private address (`DENY
non-public`) or an R3 list endpoint is refused, and never opened by any tool,
the browser included. A request's own URL is
allowed for that request only, never for the rest of the run. The next tick
(at most 15 minutes) writes `runner/out/results/<id>.json`. The long ops
(`resolve_leads`, `probe_url`, `rebuild_queue`) never run inside a tick: the
tick starts the daily, which answers them, so their result can take longer
while that run polls the boards. A request whose run was stopped twice before
it finished (a time limit, a page that hangs the reader) is answered `ok`
false with `REJECTED this request was started 2 times and never finished
...` and never run again: tell the user in one line that the runner could
not finish it, and ask again only for a different link.

| op | args | what the runner does |
|---|---|---|
| `resolve_leads` | none | resolves new `runner/in/leads/*.jsonl`, gates the postings, appends an addendum |
| `probe_url` | `url`, `company`, `title`, `location` | reads one careers page or posting URL from your web search, detects the ATS, gates what it finds, appends an addendum |
| `fetch_posting` | `url`, or `n` / `job_key` of a queue item | re-reads one posting (liveness); `result.closed`. A queue item is read through the URL discovery read, as liveness (`result.deny` "skip ..." otherwise) |
| `build` | `folder` (a job folder), `json` (default `resume.json`) | builds a Tier A JSON you wrote when pip failed in the sandbox (`--strict`) |
| `stage_tier_a` | `folder`, or `n` / `job_key` | replaces `upload/NN_*` (and `NNc_*`) with the Tier A files the job's `final.json` names (a build that passed `--strict`; none without it); `result.sha256` |
| `more` | `limit` (default `more_size`) | the next items from the pool as an addendum; before today's `queue.json` exists none (`ADDENDUM none: no list for <date> yet`), and late finds wait in the pool for the build |
| `import_network` | none | `network.py import` now |
| `rebuild_queue` | none | rebuilds today's queue (waits while a session is active; FROZEN once events exist) |

## The card and the next item

- `python "<skill>/scripts/today_view.py" card --root "<folder>"` prints the card (1.2) word for word. Show it as printed.
- `python "<skill>/scripts/today_view.py" next --root "<folder>"` prints the next item's handover data (`NEXT`, `OPEN`, `UPLOAD`, `LETTER`, `ANSWERS`, `SENSITIVE`, `DRAFT`, `ATTACH`, `HANDOVER` lines), the same item the card's `Starting #N` names. The resume is the file the job folder's `final.json` names (a strict Tier A build that passed and cites no library entry corrected since), else `resume_B.pdf`; it compares the staged copy's sha256 with it and names the job-folder file when they differ. Items with no resume (prep_failed) are held in one `WAIT` line. A Tier B file whose version (`tune_log.json`) is no longer approved gets a `HOLD` line instead of `OPEN` and `UPLOAD`, after every other item. A Tier A build whose `final.json` cites a library entry corrected since gets a `REBUILD #<n>` line before the item served, never its Tier B file without a word (apply_batch.md step 1). `OPEN` is only ever a link `netfetch.web_refusal` passes (`SKIP #NN bad link` otherwise), and a job key, code or path that a command line must not carry gives `SKIP #NN unsafe value`. `ANSWERS ... needs` lists only the question ids still open. Its `SENSITIVE` and `Check:` lines come from `claude/<date>/sensitive_ok.json` (apply_batch.md). `card --short` and `next --short` are the easy day: no-account items, easiest ATS first, none with an open question, a sensitive field beyond the profile's salary and sponsorship not settled today, or no resume yet (daily.md).
- `progress` gives the counts and the closing line (applications sent today outside the list, on LinkedIn or by hand, are one `OFFLIST linkedin=<n> other=<n>` line and part of the closing count); `diy` gives the do-yourself list (no links; pasted jobs after the runner's, from E51); `drafts` prints the free-text drafts of `morning.json` in full (the card shows one line each).
- `linkedin` prints the one line that says whether LinkedIn may be worked on now (`LINKEDIN GO left=<n> ...`, or `off`, `STOPPED`, `DONE`, `WAIT`): linkedin_browser.md. The runner itself never opens LinkedIn, under any setting.
- The day's first card adds one line when no job-alert lead (source `<board>_alert`, in `runner/in/leads/` or `state/leads_state.json`) has arrived for 3 days in a folder older than that, unless alert reading is off (`[alerts]`): the user types `/job alerts` (setup.md "Alerts").
- The day's items are `queue.json`, its addenda and the `added` jobs not yet in `addendum_user.json`, so a job added from a link counts in `card`, `next` and `progress` at once.
- `claude/<date>/morning.json` (written by job-morning, create-only) feeds the card:
  `{"v":1, "date", "prepared":false (only when written before today's list), "urgent":[{"text"}], "away":[{"text"}], "questions":[{"id","text","jobs":[n...],"all_jobs","saved"}], "waiting":<jobs>, "drafts":[{"n","label","question_id","text"}], "referral_drafts":[{"n","contact_ref"}], "weekly":["<line>"]}`. The sensitive line always comes from the fill plans.
  Without it (or with one older than `queue.json`, or `prepared` false) the card says the morning prep did not run and takes the questions from the fill plans; until the `tier_a.done` marker exists, Tier A items whose Tier A build is not ready come after the other items, in both `card` and `next`.

## Selftest

`jobrunner.py selftest [--net] --root "<folder>"` prints one `PASS` or `FAIL` line per
check, then `SELFTEST PASS` or `SELFTEST FAIL <names>` (exit 1 on a FAIL):
python, packages (openpyxl, python-docx, reportlab, pypdf), tzdata, folder
(write in `runner/out/`), robots (a live robots.txt fetch with `--net`, else
the parser), build (a sample resume to PDF). `--net` adds reach checks for
Greenhouse, Lever, Ashby, one Workday tenant, USAJOBS and Recruitee. With
`--net` a site that does not answer is a `WARN` line, not a FAIL (its boards
wait; the rest run); only `FAIL net` (no site answered: a proxy or firewall
blocks Python) fails the net check. Recruitee is optional: a PASS turns on the
Recruitee boards (status.json `selftest.recruitee`), a WARN leaves them off.
On the user's PC the installer runs it; at setup you also run it in the sandbox
with `--mode S` (recorded as `selftest_s`): a PASS there lets mode S stand in
when the PC runner has not run for 36 hours (daily.md); a FAIL changes nothing.

## Reading status.json for the card

- No `runner/out/status.json` (nothing installed), or mode M: the card's one line says the job finder is not installed yet (or not switched on) and to type `/job setup` (setup.md sitting 2), never the desktop shortcut.
- Mode S, or `last_daily.at` over 26 hours old: the card's one line about the job finder. In mode L with no list for today while `daily_running.at` is under 2 hours old, the card's last line says the list is being built instead.
- `last_daily` for today with `ok` false: one line says this morning's run stopped early and starts again within 30 minutes (while a tick still may start it).
- `errors` holding today's `offline <date>` entry (mode L): one line says the job sites could not be reached this morning and the job finder tries again later today.
- `tracker_locked_since` over 2 hours: the card asks the user to close the spreadsheet.
- `version` older than the skill: ask the user to double-click the new `install_runner.cmd`; `min_skill` newer than the skill: ask for the new skill zip.
- `errors`: a config file that stopped parsing (the runner uses `state/last_good/`), an optional step that failed, or a job board still in error after the poll's one retry (`board <key>: <why>`; its jobs come on the next run, and 3 days of errors pause it). The card checks `settings.toml` and `search_profile.toml` itself, live, and gives one line per file that does not parse ("Your search_profile.toml has a typo ..., so I'm using the last copy that worked. Type /job settings to fix it.").

## Exit codes

0 ok (also WAIT and TICK idle), 1 bad input or a selftest FAIL, 2 a package is
missing (run the installer again), 8 the runner is LOCKED (another run holds
`run.lock`, an OS lock the OS frees when that run ends however it ends; a
daily waits up to 12 minutes for a tick, and a run that was stopped leaves
only a `STALE LOCK` line for the next one).

## When the PC slept, ran on battery, or a file was open

- Both tasks start and keep running on battery. A daily keeps the PC awake
  while it runs and stops itself after 2 hours; a tick stops after 10 minutes.
  On a slow network the daily keeps time for today's list: after 75 minutes
  it reads no more boards and skips the aggregators and leads, and the
  boards' one retry runs only in its first 45 minutes and only when 20
  boards or fewer failed (one `WARN` line names the boards left out).
- A missed daily (the PC asleep or off at 05:30, a daily stopped, or one that
  waited for a session) is started by the first tick after 05:30 (`TICK started
  the daily (missed|failed|waited|requests; ...)`), at most 3 times a day 30
  minutes apart; "Run job finder now" on the desktop does the same at once.
- A morning when no job board answered (the network was not up yet) still ends
  `DAILY ok` with the carried-over list, and status.json `discovery_offline` is
  that date. Every 30 minutes a tick reads one board's robots.txt; once it
  answers, it starts the daily (`TICK started the daily (offline; ...)`), which
  searches again and adds the new postings as an addendum (`REDISCOVER`).
- A file another program holds open (an upload PDF in a viewer, `funnel.csv` in
  Excel) is a `WARN` line, and it is retried on a later run; requests are still
  answered and today's files are still staged.
- One board, aggregator lane, lead, liveness item or network import that
  fails is one `ERROR <unit>: <type>: ...` line in the log (and in
  status.json `errors`); the rest of the run goes on, and the daily still
  builds today's list. A network export that cannot be read is not tried
  again until a new file is dropped in `network/raw/`.
- A job folder carried from an earlier day keeps its `resume_B` files only while
  the variant it came from is approved as it was; otherwise they are rebuilt, or
  moved to `<job>/_unapproved/` when the family has no approved variant
  (`NO_VARIANT <family>` in the log).
