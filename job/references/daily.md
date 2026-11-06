# The day: card, morning prep, short days, breaks and recovery

The runner built today's list at 05:30 (`queue/<date>/queue.json`, about 30
items, each with a job folder, a fill plan and a checked Tier B resume
staged in `upload/`). The scheduled job-morning task added the Tier A
resumes, the question batch and the drafts. A session shows the card and
fills the items one at a time ([apply_batch.md](apply_batch.md)). The user
reads one card and types short replies; never ask anything before #1 starts.

Commands below are shortened (SKILL.md "Scripts"); each takes
`--root "<folder>"`. `<date>` is today's local date (`events.py overlay`
prints `OVERLAY <date>`).

## /job (the normal day)

1. **Folder and mode.** Find the folder (SKILL.md step 1). Read `[runner]
   mode` from `config/settings.toml`.
2. **Session file.** Write `claude/session.json`:
   `{"task": "<a new id for this session>", "started": "<UTC ISO time>",
   "beat": "<UTC ISO time>", "current_n": 0}` (times from `date -u
   +%Y-%m-%dT%H:%M:%SZ`). Rewrite `beat` and `current_n` after every item.
   While the beat is under 20 minutes old the runner never renumbers the
   list or touches `upload/` order; late finds come as addenda. Pass the same
   id as `--session` on every `events.py emit` and `events.py add`.
3. **The card.** Run `today_view.py card` and show its lines exactly as
   printed, nothing added. It already holds the urgent mail, the questions
   (at most 6), the sensitive-answers line, one line per free-text draft
   (its start, then `(say "drafts" for the full text)`) and the referral
   line, and it ends `Starting #1 now.` With no job finder installed (mode
   M, or no `runner/out/status.json`) its one line says to type `/job setup`
   (setup.md sitting 2). With no list today its last line starts `No list
   today yet. Paste a job link with /job <link>`: show it and wait for a
   link.
4. **Catch-up, when job-morning did not run** (no
   `claude/day_<date>/morning.done`; the card then says the morning prep did
   not run). Before #1, run the inbox check ([inbox.md](inbox.md)) and alert
   intake ([discovery.md](discovery.md)) quietly, and show only their urgent
   part in one block. Tier A prep waits for the first batch break; until
   then the Tier A items fill with their Tier B files. Write the
   `inbox.done` and `intake.done` markers when each step finishes.
5. **Start #1 at once.** Run `today_view.py next` and follow
   [apply_batch.md](apply_batch.md). The user answers the card's questions
   whenever they like, in one reply; never wait for them.
6. **Answers to the card** (`1 yes, Stanford 2 years 2 2026-12-01 3 skip;
   ok`), any time:
   - A recurring answer: `answers.py add ... --source user:<date>` (backup
     first; the bank keeps its own backup). An achievement: a new library
     entry, then `library.py ids`. A job-specific answer: that job's
     `notes.md`, as a `- JOB-1 | <question> | <answer> | <kind> | any | no |
     user:<date> | <date>` line.
   - Then `answers.py unpark --date <date> --session <id>`: the parked jobs
     those answers settle get `unparked` events and move to the front of
     what is left.
   - `ok` on the sensitive line confirms those values for the jobs listed;
     a job named in "leave blank" gets the field left empty as the user's
     part. Record the reply in `claude/<date>/sensitive_ok.json`
     ([apply_batch.md](apply_batch.md) "Sensitive confirmations"); each
     item's handover `Check:` line comes from it.
   - `skip` on a question: the jobs waiting on it stay parked today.
   - `drafts`: run `today_view.py drafts` and show its lines as printed
     (`DRAFT #<n> (<label>)`, then the full text; `--n <n>` for one). An
     edit the user makes replaces that draft for that job's field.

## /job short (an easy day)

Say "Short day: I'll do the quick ones." and nothing else about it.
- Run `today_view.py next --short` wherever the loop says `next`. It gives
  only items with no account to make, easiest first: Greenhouse, Lever,
  Ashby, Workable, BambooHR, Breezy, Teamtailor, Rippling, Pinpoint, then
  the other no-account sites (an item already started or handed over comes
  before them). It passes over items with an open question, a sensitive
  field the user has not settled today (`sensitive_ok.json`), or no resume
  yet; emit nothing for those. They stay in today's list for a normal day
  and in the pool while they are fresh.
- No questions, no sensitive line, no referral drafts, no account block, no
  usage readings.
- `stop` ends it at once. The closing line comes from `today_view.py
  progress` ("Today: 8 applications sent."), never with a comparison.
- Even lower: `/job inbox` alone (about 2 minutes).
- "short" typed mid-session switches to these rules from the next item.

## The batch break (after every `batch_size` items, default 10)

1. Say exactly: "Good place for a break. Start a new task and type /job;
   I'll pick up at #11." (the next number). A new task keeps the context
   small.
2. If the user stays in this task instead, or at the start of the next one,
   do the break work before the next item, each in one short block:
   - Tier A catch-up if job-morning did not run (below, step 5 of the
     morning steps), then `stage_tier_a` requests for the ready ones.
   - Referral drafts: [referrals.md](referrals.md) "Asks" (skip on a short
     day or with `[referrals] enabled = false`).
   - Questions collected during the batch, held until now.
   - The account block when the next items need new accounts
     ([apply_batch.md](apply_batch.md) "Account block").
3. `/job` in the new task resumes at the first item with no final event.
   The card it prints is shorter: answered questions and done items drop
   out.

## After a break of days

The runner kept running (a missed 05:30 runs at the next logon), job-inbox
kept watching Gmail, and the queue is rebuilt every morning from what is
still eligible, never more than 30. The card adds one block, "While you were
away:", with only interviews, assessments with deadlines, offers and direct
questions (job-morning fills `away` in morning.json). Questions whose jobs
aged out are dropped. Never mention how many days passed.

## Interruptions

- **Anything** (lid closed, browser crash, a usage limit, a new task):
  `/job` resumes at the first item with no `applied`, `skipped`, `later` or
  `blocked` event. `events.py overlay --date <date>` shows each item's state.
- **An item at `handed_over` with no `applied` event:** search Gmail for that
  ATS's confirmation (inbox.md search, the company name, since the handover
  time).
  - Found: log it Applied with `confirmed=<date> proof=gmail:<message id>`,
    emit `applied` (`confirmation email`), and never reopen it.
  - Not found: say "#11 may not have gone through; I'll reopen it" and fill
    it again. A confirmed application is never resubmitted.
- **A usage limit:** stop cleanly (event `parked`, reason `usage_limit`, for
  the item in progress, if any). The card says when the window resets. The
  queue and the events hold all the state.

## /job add <link> (one posting into today's list)

The posting joins today's list at once, with the next number, and is filled
in its turn like the runner's items.
1. SKILL.md step 3 up to the job folder: `posting.py` into `_incoming/`,
   `tracker.py check` and `caps.py check`, then the job folder. A LinkedIn
   or Indeed link follows ats/linkedin.md. `IN QUEUE <date> #<n>`: say "It
   is already #<n> today." and stop.
2. Its resume and answers, as the runner makes them (zero tokens), with the
   family whose titles match (SKILL.md step 4): `variants.py tier-b --item
   "<job folder>" --family <family>`, then `answers.py plan --job "<job
   folder>" --family <family>`. With no family, no approved variant
   (`PREP_FAILED`) or a package that will not install, go on without them.
3. `events.py add --folder "<job folder>" --via add --family <family>`
   prints `ADDED #<n> <key> (addendum_user)`; say "Added as #<n>." in one
   line. `today_view.py next` and `progress` include it at once; the next
   tick copies it into `queue/<date>/addendum_user.json`. No tracker row
   yet: it gets one at its handover, like every item.
4. So that it can come back tomorrow if today runs out (`later`, or the day
   ends first), also send `jobrunner.py request probe_url --json '{"url":
   "<link>", "company": "<Company>", "title": "<Role>", "location":
   "<Location>"}'`. The runner reads and gates it into its pool; it adds no
   second item today, since that job key is taken.
5. When its turn comes with `UPLOAD none (no built resume yet)` (step 2 gave
   no Tier B file), write its resume first by SKILL.md steps 4-7 in that
   folder, then fill it.
- `NO LIST <date>` (mode M, or before the runner built today's list): run
  the single-job flow now (SKILL.md steps 1-9) instead.
- A careers page rather than one posting: only the `probe_url` request. The
  runner reads it within 15 minutes, gates what it finds and appends an
  addendum (`runner/out/results/<id>.json`; `DENY job-board ...` is a job
  board link, ats/linkedin.md; `gates.py explain --job-key <key>` names the
  gate that stopped one).

## /job status

- `tracker.py show --active`, `tracker.py due` and `tracker.py stats`: what
  is in flight, follow-ups ready (Tier A and referral rows only; "2
  follow-ups ready", never "overdue"), and replies so far as "x of n".
- `today_view.py progress`: today's counts and the closing line.
- When the user says they withdrew: `status=Withdrawn`. At 30 or more days
  with no reply the weekly summary offers `No response`; do not offer it
  here.

## The morning task (job-morning, 06:30, "Use the job skill: morning")

Runs with no one watching. Every step is idempotent: a step whose marker
`claude/day_<date>/<step>.done` exists is skipped, so a rerun, or the first
`/job` of the day, finishes what is missing. Never use the browser, never
send mail, never Submit. When the folder cannot be reached, write nothing
and end.

1. Find the folder and today's date. If `claude/day_<date>/morning.done`
   exists, end with one line: "Morning prep already done."
2. **Inbox** ([inbox.md](inbox.md)), snippet first. Collect the Immediate
   items as one-line `urgent` texts (company, what they ask, by when) and,
   after a gap of days, the `away` texts. Marker `inbox.done`.
3. **Alert intake** ([discovery.md](discovery.md)): the alert senders, the
   lead file through `leads.py validate`, one `resolve_leads` request. Then
   `runner/out/needs_search.json`: at most 10 web searches, `probe_url`
   requests for usable results. Marker `intake.done`.
4. **Weekly** on `[schedule] weekly_day`: `report.py weekly`
   ([weekly.md](weekly.md)); its `CARD` lines go into `weekly`. In a
   certification week with `[worksearch] claiming = true`, offer the export
   ([worksearch.md](worksearch.md)). Marker `weekly.done`.
5. **Tier A.** For each queue item with `tier` A (at most `tier_a_per_day`):
   with the plugin, start one job-tailor sub-agent per item in parallel,
   with paths only (the job folder, `<folder>`, `<skill>`, the family
   variant folder). Each writes `notes.md`, `resume.json` and
   `questions.json` (at most 2 questions) and builds with `--strict`. Then
   job-auditor in a fresh context: `fix` goes back to the tailor once more;
   `rework`, or 2 failed rounds, leaves the item on its Tier B files (note
   `audit rework`). For each ready one send `stage_tier_a` with its `n`.
   Without the plugin (the job.zip install) do the same in this context, one
   at a time, with the same-context audit (writing_rules.md). If pip fails,
   send a `build` request instead. Marker `tier_a.done`.
6. **Drafts.** For each item whose fill plan has a required free-text field
   with no `template:` answer, draft it under the writing_rules.md
   Truthfulness rules from the posting and the sources, at most one per job.
7. **Referral drafts:** `network.py asks --date <date>` ([referrals.md](referrals.md));
   keep each `ASK` as `{"n", "contact_ref"}`. The texts are shown at the
   batch break, not in the card.
8. **Questions:** `answers.py questions --date <date> --json`.
9. **Write `claude/<date>/morning.json`**, create-only (if it exists, leave
   it):
   ```json
   {"v": 1, "date": "<date>",
    "urgent": [{"text": "Acme Bio asked for phone-screen times by Fri 11/14 (say \"draft\" for a reply)"}],
    "away": [],
    "questions": [{"id": "Q-1a2b3c", "text": "Earliest start date?", "jobs": [3, 7], "all_jobs": false, "saved": true}],
    "waiting": 5,
    "drafts": [{"n": 7, "label": "Why this role", "text": "<the full draft>"}],
    "referral_drafts": [{"n": 7, "contact_ref": "N-0004"}],
    "weekly": ["Last week: 12 applications sent, 2 people contacted."]}
   ```
   `questions` and `waiting` come from step 8 (`saved` is true when the
   answer will go into answers.md). Leave out `sensitive`: the card builds
   that line from the fill plans. Then the marker `morning.done`.
10. **The final message** is the phone notification, so it holds only the
    urgent texts, one line each, or "Nothing needs you before /job." Never
    a count of anything else.

If job-morning has not run when the user types `/job`, the session does
steps 2-3 inline (urgent part only), starts #1, and does steps 5-8 at the
first batch break.
