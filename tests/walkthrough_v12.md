# v1.2 walk-through: one supervised day, from the skill text only

A manual checklist. Follow it with nothing but `job/SKILL.md` and the
references it routes to, on a copy of `tests/fixtures/folder` (a mode-L
JobSearch folder with approved variants, a carried pool and alert leads).
Every command below is the one the text names; the expected lines are what
the scripts printed on 2026-11-05 (dry run, offline). The browser steps are
marked MANUAL: in a dry run, say what you would do and go on. The last
section lists every gap the walk found.

Setup (PowerShell or Git Bash, from the repository root):

```
copy tests\fixtures\folder to <tmp>\JobSearch
set JOB_SKILL_NOW=2026-11-06T13:30:00Z      (pins the clock: 05:30 in Los Angeles)
python job\scripts\jobrunner.py daily --root <tmp>\JobSearch --offline tests\fixtures\boards --date 2026-11-06
```

Expected: `DAILY ok queue=30 pool_left=41 discards=141 tier_b_built=30
prep_failed=0`. Then set `JOB_SKILL_NOW=2026-11-06T17:00:00Z` (09:00 local),
the time the user types `/job`. Below, `P` is `python job\scripts` and `R`
is `--root <tmp>\JobSearch`. `A` is `--args-file claude\args.json`, after
writing that file (in the JobSearch folder) with the JSON shown: a company,
title, link or answer never goes on the command line (SKILL.md "Scripts"),
and the script removes the file once read.

## Part A: `/job` (SKILL.md routing -> daily.md -> apply_batch.md)

- [ ] **A1 date.** `P\events.py overlay R` -> `OVERLAY 2026-11-06` (the local
      date, not the UTC one).
- [ ] **A2 session.** Write `claude\session.json`:
      `{"task": "walk-1", "started": "2026-11-06T17:00:00Z", "beat": "2026-11-06T17:00:00Z", "current_n": 0}`.
- [ ] **A3 card.** `P\today_view.py card R`, shown exactly as printed:
      ```
      Friday, November 6
      Ready: 30. No account needed for any of them.
      Sensitive answers today: salary $95,000-$110,000 on all; "no sponsorship needed" on all. Reply yes, or name the ones to leave blank.
      This morning's prep did not run yet, so I'll read the inbox first and show what is urgent; full tailoring for the Tier A jobs comes at the first break.
      Starting #1 now. Say "short" any time for an easy day.
      ```
      Check: nothing is asked before #1. No `morning.done` marker exists,
      so daily.md step 4 runs the inbox and alert intake quietly (MANUAL:
      no Gmail in a dry run) and Tier A waits for the break.
- [ ] **A4 next.** `P\today_view.py next R`:
      ```
      NEXT #01 workable:ivybio:B00008C
      COMPANY Ivy Bio
      ROLE QC Microbiologist II
      ATS workable (attach first, account none)
      OPEN https://apply.workable.com/ivybio/j/B00008C/
      FOLDER applications/2026-11-06 Ivy Bio - QC Microbiologist II
      UPLOAD upload/01_Avery_Resume_IvyBio.pdf (Tier B file, sha256 ok)
      RESUME built tier B
      ANSWERS sensitive_confirm sensitive sponsorship,salary
      SENSITIVE leave blank sponsorship, salary (not confirmed today)
      ATTACH Attach the file starting with 01 in JobSearch\upload, then type ok.
      HANDOVER #01 Ivy Bio - QC Microbiologist II (Workable)
      HANDOVER Check: sponsorship, salary left blank (not confirmed today).
      HANDOVER Then click Submit and type s.
      ```
      Workable is attach-first, so the attach is the `ATTACH` line (A7) and
      the handover names no file.
- [ ] **A4b yes.** The user replies `yes` to the sensitive line (an `ok`
      never confirms it): write `claude\2026-11-06\sensitive_ok.json` as
      `{"v": 1, "date": "2026-11-06", "ok": "all", "blank": []}`. `next` again
      now prints `SENSITIVE enter sponsorship "no", salary "$95,000-$110,000" (confirmed today)`
      and no `Check:` line, and the card no longer shows the sensitive line.
      `P\today_view.py next R --short` gives `NEXT #02 greenhouse:7100052`
      (Greenhouse before #1's Workable) with or without the file: the
      profile's salary and sponsorship keep an item in a short day.
      `P\today_view.py card R --short` without the file prints
      `Short day: I'll do the quick ones.`, the sensitive line once, and
      `Starting #2 now. Say "stop" any time.`
- [ ] **A5 caps.** `{"company": "Ivy Bio"}`, then
      `P\caps.py check R --family qc_micro --job-key workable:ivybio:B00008C A` -> `CAP ok`.
- [ ] **A6 claim and start.** Create `<job folder>\.claimed`;
      `P\events.py emit R --type started --n 1 --job-key workable:ivybio:B00008C --date 2026-11-06 --session walk-1 --json "{}"`
      -> `EVENT 20261106T170000Z_...`.
- [ ] **A7 fill (MANUAL).** Open the OPEN URL, read the page as text, say
      the `ATTACH` line ("Attach the file starting with 01 in
      JobSearch\upload, then type ok."), fill from `fill_plan.json` with
      the `SENSITIVE` line's values, read back.
- [ ] **A8 handover.** Print the HANDOVER lines; save `filled.md`;
      `{"company": "Ivy Bio", "role": "QC Microbiologist II", "url": "https://apply.workable.com/ivybio/j/B00008C/", "folder": "applications/2026-11-06 Ivy Bio - QC Microbiologist II", "job-key": "workable:ivybio:B00008C"}`, then
      `P\tracker.py add R --status "Filled - awaiting submit" --source Runner --tier B --family qc_micro --variant qc_micro@v1 --ats workable --found-via watch --queue-date 2026-11-06 --account none A`
      -> `ADDED P-3b43e5e5 (pending)` (a tracker event, not an xlsx write);
      then `events.py emit ... --type handed_over`.
- [ ] **A9 `s`.** Save `submitted\confirmation.txt` and the sent file;
      `P\tracker.py update R P-3b43e5e5 status=Applied applied=2026-11-06 confirmed=2026-11-06 proof=submitted/confirmation.txt resume_sent=...`
      -> `UPDATED P-3b43e5e5 Status: Filled - awaiting submit -> Applied; ...`;
      `events.py emit ... --type applied --json "{\"confirmation\":\"page\",\"proof\":\"submitted/confirmation.txt\"}"`.
- [ ] **A10 state.** `P\events.py overlay R` -> `ITEM #1 workable:ivybio:B00008C applied confirmation=page`, `TRACKER PENDING 2`.
      `P\today_view.py progress R` -> `PROGRESS applied=1 ... left=29 of 30`
      and `Today: 1 application sent.`; `next` now gives `NEXT #02 greenhouse:7100052`.
- [ ] **A11 the runner applies it.** `P\jobrunner.py tick R --offline tests\fixtures\boards`
      -> `APPLIED 5 PENDING 0 REJECTED 0`, `STAGED 0 files, moved 1 to upload/_done/, session active`.
      `P\tracker.py show R --id P-3b43e5e5` resolves the P- ID to row 1,
      Applied, with no follow-up date (Tier B).

## Part B: `/job <link>`, a pasted Greenhouse fixture (SKILL.md steps 1-9)

Set `JOB_SKILL_NOW=2026-11-06T18:00:00Z`. The fixture is
`tests\fixtures\greenhouse.json`, the posting at
`https://job-boards.greenhouse.io/acme/jobs/1234567`.

- [ ] **B1 step 1.** `P\tracker.py due R` -> `NONE`.
- [ ] **B2 step 3, read.** No network in a dry run, so the `TRY`/`RERUN`
      path is taken with the saved API response:
      `{"_": "<link>"}`, then
      `P\posting.py R --json-file tests\fixtures\greenhouse.json --out <...>\applications\_incoming\posting.md A`
      -> `SOURCE greenhouse-api`, `COMPANY Acme Robotics`, `ROLE Senior IT Support Engineer`,
      `JOB KEY greenhouse:1234567`, `QUESTIONS 4`, `WROTE ...`, `POSTED 2026-10-01 (first_published)`,
      `EMPLOYER SITE none`, `FOLDER applications/2026-11-06 Acme Robotics - Senior IT Support Engineer`,
      `WARN short description (140 chars)` (the fixture is short; in real
      use the WARN sends you to the browser read).
- [ ] **B3 duplicate check.** `{"company": "Acme Robotics", "title": "Senior IT Support Engineer", "url": "<link>"}`, then
      `P\tracker.py check R --posting <...>\_incoming\posting.md A`
      -> `JOB KEY greenhouse:1234567`, `NO MATCH`.
- [ ] **B4 caps.** `{"company": "Acme Robotics", "url": "<link>"}`, then
      `P\caps.py check R --posting <...>\posting.md A` -> `CAP ok`.
- [ ] **B5 folder and row.** Move `_incoming` to the `FOLDER` path, exactly as printed;
      `{"company": "Acme Robotics", "role": "Senior IT Support Engineer", "location": "Austin, TX", "url": "<link>", "folder": "applications/2026-11-06 Acme Robotics - Senior IT Support Engineer"}`, then
      `P\tracker.py add R --status Started --tier A --found-via pasted --ats greenhouse --posted 2026-10-01 A`
      -> `ADDED P-fc153b11 (pending)`. Then
      `P\events.py add R --folder "applications/2026-11-06 Acme Robotics - Senior IT Support Engineer" --via pasted`
      -> `ADDED #31 greenhouse:1234567 (addendum_user)`: `P\today_view.py progress R`
      now ends `left=30 of 31`, and `P\events.py overlay R` lists
      `ADDED #31 greenhouse:1234567 pasted` (no tick needed).
- [ ] **B6 step 4 (MANUAL fit and questions).** `P\tracker.py update R P-fc153b11 "fit=Good Fit"` -> `UPDATED P-fc153b11 Fit:  -> Good Fit`.
      (`fit=Good` is refused: the values are Strong Fit, Good Fit, Stretch, Long Shot.)
- [ ] **B7 step 5, build.** With `tests\sample_resume.json` as the written resume.json:
      `P\build_resume.py <job>\resume.json --out <job> --sources tests\sample_sources\master_resume.md tests\sample_sources\achievement_library.md --terms tests\sample_terms.json`
      (no `--base`: the files are named from the JSON's own name)
      -> three `wrote:` lines for `Jordan Avery Resume.txt`, `.docx` and `.pdf` (1 page), two `WARN`
      lint lines, `PARSE ok (18 checks)`, two `FACT` lines, three `KEYWORD` lines. This dry run
      is not `--strict`, so it shows them all; the real build (SKILL.md step 5) is the strict
      command, and each finding is fixed before Verify.
- [ ] **B8 audit (MANUAL).** "audit" in the same context (writing_rules.md):
      the banned word and the two FACT numbers are the findings; fix, rebuild.
      `P\tracker.py update R P-fc153b11 "status=Resume ready"`.
- [ ] **B9 step 8, before the browser.** `P\answers.py plan R --job <job> --dry-run`
      -> `PLAN fields=6 ok=5 needs_you=0 sensitive_confirm=1 check=0 (greenhouse_api)`
      and `SENSITIVE "Do you require visa sponsorship?" = "No" (profile:sponsorship)`: confirm it
      for this job. `P\tracker.py account R --site job-boards.greenhouse.io --reuse` -> `NONE`
      (Greenhouse needs no account).
- [ ] **B10 handover (MANUAL fill).** The apply.md handover text: what was
      entered with sources, the checklist (attach the PDF, any email code),
      and "Review each page, then click Submit. Type s (or "submitted") and
      I'll log it." `P\tracker.py update R P-fc153b11 "status=Filled - awaiting submit"`.
- [ ] **B11 step 9.** `P\tracker.py update R P-fc153b11 status=Applied applied=2026-11-06`
      -> `... Follow-up:  -> 2026-11-13` (Tier A gets the +7 follow-up).
      `P\events.py emit R --type applied --n 31 --job-key greenhouse:1234567 --date 2026-11-06 --session walk-1 --json "{\"confirmation\":\"page\"}"`;
      `P\today_view.py progress R` -> `PROGRESS applied=2 ... left=29 of 31`, `Today: 2 applications sent.`
      After `P\jobrunner.py tick R --offline tests\fixtures\boards` (it prints
      `ADDENDUM addendum_user.json items=31`),
      `{"company": "Acme Robotics"}`, then `tracker.py show R A` shows row 2, Good Fit, Applied, follow-up 2026-11-13.

## Part C: the IN QUEUE note

- [ ] **C1.** Paste item #2's link (`https://job-boards.greenhouse.io/cedartherapeutics/jobs/7100052`)
      as text: `{"_": "<link>", "company": "Cedar Therapeutics", "role": "QC Microbiologist I"}`, then
      `P\posting.py R --text-file "<job #2 folder>\posting.md" --out <scratch>\posting.md A`,
      then `{"company": "Cedar Therapeutics", "title": "QC Microbiologist I", "url": "<link>"}` and
      `P\tracker.py check R --posting <scratch>\posting.md A`
      -> `JOB KEY greenhouse:7100052`, `IN QUEUE 2026-11-06 #2`, `NO MATCH`.
      SKILL.md step 3: use item #2's prepared folder, do not make a new one.
- [ ] **C2.** `{"company": "Cedar Therapeutics", "url": "<link>"}`, then
      `P\caps.py check R --family qc_micro A` -> `CAP ok`;
      `P\variants.py check R --family qc_micro` -> `APPROVED qc_micro v1 (on 2026-11-05)` (offered as the start).

## Part D: the live supervised day (not dry; on the user's PC)

- [ ] 5 applications; the first two stopped before Submit for inspection.
- [ ] Usage readings before #1, after #3 and after #5 (`report.py usage --add`), and minutes per item.
- [ ] The attach-order test per ATS: attach after filling, then check whether any field changed.
- [ ] Record what the built-in browser does with a hidden pane.

## Gaps found (the text cannot close these; owners named)

Gaps 1-5 of the first walk were closed in v1.2 hardening round 1; each line
says what now covers it.

1. **`addendum_user` (closed).** `events.py add --folder <job folder> --via
   pasted|add` writes an `added` event: the job is in `today_view.py` card,
   next and progress at once (B5, B11), and the tick writes it into
   `queue/<date>/addendum_user.json`. Mode M has no list (`NO LIST`).
2. **Sensitive confirmations (closed).** `today_view.py next` reads
   `claude/<date>/sensitive_ok.json` for the `SENSITIVE` and `Check:`
   lines (A4, A4b). Round 2: the line is confirmed with `yes`, never `ok`.
3. **Draft text location (closed).** The card's draft line ends `(say
   "drafts" for the full text)`, and `today_view.py drafts` prints the full
   texts from morning.json. Round 2: a drafted field is not asked again,
   and `next` names it (`DRAFT`, and a `Check:` item at the handover).
4. **Short day (closed).** `today_view.py card --short` and `next --short`:
   no-account items, easiest first, none with an open question, a
   sensitive field beyond the profile's salary and sponsorship that is not
   settled today, or no resume; the rest get no event and stay.
5. **The card with no job finder (closed).** No `runner/out/status.json`,
   or mode M: one line saying to type `/job setup`, never the shortcut.
6. **Tick and liveness.** A tick without `--offline` re-reads the next items'
   postings live (in this walk it marked fixture items closed against the
   real Greenhouse API). Dry runs must pass `--offline`. Per-item liveness
   in the session relies on the tick, not a fetch by Claude.
7. **The clock for `session.json`.** No script prints the current time;
   the text uses `date -u` (UTC with Z), which `jobqueue.session_active`
   accepts.
8. **Audit is a manual step** in a dry run (job-auditor or the same-context
   audit); the walk cannot check its verdict.
