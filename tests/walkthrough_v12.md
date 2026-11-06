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
is `--root <tmp>\JobSearch`.

## Part A: `/job` (SKILL.md routing -> daily.md -> apply_batch.md)

- [ ] **A1 date.** `P\events.py overlay R` -> `OVERLAY 2026-11-06` (the local
      date, not the UTC one).
- [ ] **A2 session.** Write `claude\session.json`:
      `{"task": "walk-1", "started": "2026-11-06T17:00:00Z", "beat": "2026-11-06T17:00:00Z", "current_n": 0}`.
- [ ] **A3 card.** `P\today_view.py card R`, shown exactly as printed:
      ```
      Friday, November 6
      Ready: 30. No account needed for any of them.
      Sensitive answers today: salary $95,000-$110,000 on all; "no sponsorship needed" on all. Reply ok, or name the ones to leave blank.
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
      HANDOVER #01 Ivy Bio - QC Microbiologist II (Workable)
      HANDOVER Attach the file starting with 01 in JobSearch\upload.
      HANDOVER Check: sponsorship, salary as confirmed today (blank if not).
      HANDOVER Then click Submit and type s.
      ```
- [ ] **A5 caps.** `P\caps.py check R --company "Ivy Bio" --family qc_micro --job-key workable:ivybio:B00008C` -> `CAP ok`.
- [ ] **A6 claim and start.** Create `<job folder>\.claimed`;
      `P\events.py emit R --type started --n 1 --job-key workable:ivybio:B00008C --date 2026-11-06 --session walk-1 --json "{}"`
      -> `EVENT 20261106T170000Z_...`.
- [ ] **A7 fill (MANUAL).** Open the OPEN URL, read the page as text, ask
      for the attach ("Attach the file starting with 01 in JobSearch\upload,
      then type ok."), fill from `fill_plan.json`, read back.
- [ ] **A8 handover.** Print the four HANDOVER lines; save `filled.md`;
      `P\tracker.py add R --company "Ivy Bio" --role "QC Microbiologist II" --url https://apply.workable.com/ivybio/j/B00008C/ --folder "applications/2026-11-06 Ivy Bio - QC Microbiologist II" --job-key workable:ivybio:B00008C --status "Filled - awaiting submit" --source Runner --tier B --family qc_micro --variant qc_micro@v1 --ats workable --found-via watch --queue-date 2026-11-06 --account none`
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
      `P\posting.py <link> --json-file tests\fixtures\greenhouse.json --out <...>\applications\_incoming\posting.md`
      -> `SOURCE greenhouse-api`, `COMPANY Acme Robotics`, `ROLE Senior IT Support Engineer`,
      `JOB KEY greenhouse:1234567`, `QUESTIONS 4`, `WROTE ...`, `POSTED 2026-10-01 (first_published)`,
      `EMPLOYER SITE none`, `WARN short description (140 chars)` (the fixture is short; in real
      use the WARN sends you to the browser read).
- [ ] **B3 duplicate check.** `P\tracker.py check R --company "Acme Robotics" --title "Senior IT Support Engineer" --url <link> --posting <...>\_incoming\posting.md`
      -> `JOB KEY greenhouse:1234567`, `NO MATCH`.
- [ ] **B4 caps.** `P\caps.py check R --company "Acme Robotics" --url <link> --posting <...>\posting.md` -> `CAP ok`.
- [ ] **B5 folder and row.** Move `_incoming` to `applications\2026-11-06 Acme Robotics - Senior IT Support Engineer`;
      `P\tracker.py add R --company "Acme Robotics" --role "Senior IT Support Engineer" --location "Austin, TX" --url <link> --folder "applications/2026-11-06 Acme Robotics - Senior IT Support Engineer" --status Started --tier A --found-via pasted --ats greenhouse --posted 2026-10-01`
      -> `ADDED P-fc153b11 (pending)`.
- [ ] **B6 step 4 (MANUAL fit and questions).** `P\tracker.py update R P-fc153b11 "fit=Good Fit"` -> `UPDATED P-fc153b11 Fit:  -> Good Fit`.
      (`fit=Good` is refused: the values are Strong Fit, Good Fit, Stretch, Long Shot.)
- [ ] **B7 step 5, build.** With `tests\sample_resume.json` as the written resume.json:
      `P\build_resume.py <job>\resume.json --out <job> --base "Jordan Avery Resume" --sources tests\sample_sources\master_resume.md tests\sample_sources\achievement_library.md --terms tests\sample_terms.json`
      -> three `wrote:` lines (1 page), two `WARN` lint lines, `PARSE ok (18 checks)`, two `FACT`
      lines, three `KEYWORD` lines. Each is fixed or carried into Verify.
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
      After `P\jobrunner.py tick R --offline tests\fixtures\boards`,
      `tracker.py show R --company "Acme Robotics"` shows row 2, Good Fit, Applied, follow-up 2026-11-13.

## Part C: the IN QUEUE note

- [ ] **C1.** Paste item #2's link (`https://job-boards.greenhouse.io/cedartherapeutics/jobs/7100052`)
      as text: `P\posting.py <link> --text-file "<job #2 folder>\posting.md" --company "Cedar Therapeutics" --role "QC Microbiologist I" --out <scratch>\posting.md`,
      then `P\tracker.py check R --company "Cedar Therapeutics" --title "QC Microbiologist I" --url <link> --posting <scratch>\posting.md`
      -> `JOB KEY greenhouse:7100052`, `IN QUEUE 2026-11-06 #2`, `NO MATCH`.
      SKILL.md step 3: use item #2's prepared folder, do not make a new one.
- [ ] **C2.** `P\caps.py check R --company "Cedar Therapeutics" --family qc_micro --url <link>` -> `CAP ok`;
      `P\variants.py check R --family qc_micro` -> `APPROVED qc_micro v1 (on 2026-11-05)` (offered as the start).

## Part D: the live supervised day (not dry; on the user's PC)

- [ ] 5 applications; the first two stopped before Submit for inspection.
- [ ] Usage readings before #1, after #3 and after #5 (`report.py usage --add`), and minutes per item.
- [ ] The attach-order test per ATS: attach after filling, then check whether any field changed.
- [ ] Record what the built-in browser does with a hidden pane.

## Gaps found (the text cannot close these; owners named)

1. **No `addendum_user`.** Spec 5.11 appends a pasted job to today's queue so
   the day's counts include it. No script does this, so a job applied from
   `/job <link>` counts in the tracker but not in `today_view.py progress`
   or the closing line. (WP4/WP7: a `jobqueue.py addendum --user <folder>`
   or a request op.)
2. **Sensitive confirmations have no reader.** The text records the user's
   `ok` in `claude/<date>/sensitive_ok.json`, but `today_view.py next`
   prints the same "as confirmed today (blank if not)" check line for every
   item and does not read it. (WP7.)
3. **Draft text location.** The card says "(full text in today.md)", but
   `today.md` is the runner's file and Claude's drafts live in
   `morning.json`. The text says to show the full draft from morning.json
   when asked. (WP7: change the card wording.)
4. **Short day has no filter.** `today_view.py next` has no easy-day mode, so
   daily.md emits `later` for each account or sensitive item to get past it;
   those items then return tomorrow even if the user leaves short mode.
   (WP7: `next --short`.)
5. **The card in mode M** says "Your PC's job finder has not run yet.
   Double-click 'Run job finder now'" before the runner is installed, when
   there is no shortcut yet. (WP7.)
6. **Tick and liveness.** A tick without `--offline` re-reads the next items'
   postings live (in this walk it marked fixture items closed against the
   real Greenhouse API). Dry runs must pass `--offline`. Per-item liveness
   in the session relies on the tick, not a fetch by Claude.
7. **The clock for `session.json`.** No script prints the current time;
   the text uses `date -u` (UTC with Z), which `jobqueue.session_active`
   accepts.
8. **Audit is a manual step** in a dry run (job-auditor or the same-context
   audit); the walk cannot check its verdict.
