# First-run setup: two short sittings

Do only the parts SKILL.md step 2 (or `/job setup`) sent you here for. The
user's time is about 30-45 minutes on day 1, and on day 2 about 20 minutes
for the job finder plus about 15-20 minutes per base resume (2-4 families,
so 30-80 minutes; one family per sitting is fine). A family's jobs start
once its base resume is approved. Say these times when setup starts. The
card sends the user here with `/job setup` when no job finder
runs: with sitting 1 done, start at Sitting 2 step 1 (no
`runner/out/status.json`: install it) or step 2 (it is installed but mode
is still M: switch it on). Commands are shortened (SKILL.md "Scripts"); each takes `--root
"<folder>"`. If a Python package is missing, `pip install` it and carry on.

## Sitting 1 (day 1, about 30-45 minutes)

Two messages to the user, with the file work between them.

**Message 1, all together:**
1. The resume summary (below) and any question about scrambled dates or
   titles.
2. The numbered profile questions, saying "skip" is fine and the file stays
   in their folder on their PC. Ask only what applications gate on (work
   authorization, sponsorship, salary range, earliest start, relocation),
   any contact detail the resume lacks, licenses or certificates held (with
   the year), whether they are claiming unemployment and in which state, and
   the field and region they are searching in. Leave the self-identification
   fields blank (each form then picks "decline to answer") and say once that
   they can be filled in later.
3. "Have you applied anywhere in the last few months? List them (company,
   role, date, link if you have it) so reposts get caught."
4. The publications list (from the resume) for a yes, with any character the
   PDF font cannot print and the plain rendering you propose
   ([cv_publications.md](cv_publications.md)).
5. The techniques list (from the resume and library): name, hands-on or
   familiar, for a yes or corrections.

**Message 2, after the answers:**
- The filled profile for confirmation; write it once confirmed.
- One line each: the library was seeded; N earlier applications were logged;
  the search is set up for `<field>` around `<city>` (the preset, if one was
  used).
- "Connect Gmail under Connectors in Claude's settings, so I can read job
  replies and alerts. I never send, delete or move mail."
- **LinkedIn alerts** (they feed the daily list): "On LinkedIn, create 5 job
  alerts now, daily, by email, one per broad title: <5 titles across the
  families>. I'll suggest 3 more each of the next days, up to 20." Claude
  never opens LinkedIn; the user creates them. (Click path UNVERIFIED:
  search Jobs for the title and place, then switch on "Set alert".)
- **LinkedIn data export** (for referrals; Connections can take up to 48
  hours): "On LinkedIn, Me > Settings & Privacy > Data Privacy > Download
  your data. When the email comes, drop the zip into JobSearch\network\raw."
- **Scheduled tasks:** offer to create them (below).

### Master resume

1. Find the resume: a .docx, .pdf, .txt or .md in the folder's top level whose
   name contains "resume" or "cv", other than `master_resume.md`. If there are
   several, ask which is the most complete. With none, ask the user to save
   their most complete resume into the folder and reply "done", or to attach
   it here so you can save a copy. It should be the full version, not one
   tailored for another job.
2. Extract its text to `master_resume.md`, starting with the line
   `Source: <file name>`. Keep every role, title, date, bullet, degree and
   certification exactly as written, in the original order. For a .docx,
   read the body, tables, headers, footers and text boxes; for a .pdf use
   pdfplumber or pypdf. Drop PDF debris (stray page numbers, repeated
   headers, icon glyphs).
3. In message 1, give a short summary: name, each role (title, company,
   dates), education and certifications.

### Profile, answers, publications, techniques

- Copy [../templates/profile.md](../templates/profile.md) to `profile.md`.
  Fill what the resume states; ask for the rest. A skipped field stays blank
  and is asked when an application needs it. A blank self-identification
  field means "decline to answer". `Licenses held:` lists each license or
  certificate by name or knockout id (for example `CA CLS`): the runner skips
  jobs that require one only when it is not listed there. `Unemployment
  claim:` and `Claim state:` take yes or no and two letters.
- The profile never holds an SSN, date of birth, bank or tax details,
  driver's license number or password, and you never ask for them.
- Copy [../templates/answers.md](../templates/answers.md) to `answers.md`.
  An older profile with a "Standard answers" section: back it up, then
  `answers.py migrate`.
- `publications.md` from [../templates/publications.md](../templates/publications.md)
  and `techniques.md` from [../templates/techniques.md](../templates/techniques.md),
  filled from the resume and confirmed in message 1 (formats in
  cv_publications.md). Leave them as templates when there is nothing to list.

### Achievement library

Create `achievement_library.md` from
[../templates/achievement_library.md](../templates/achievement_library.md) and
seed it from `master_resume.md`:
- One `### Job Title | Company | Mon YYYY - Mon YYYY` heading per role; two
  titles at one employer get two headings.
- One entry per resume bullet, written as a record: every number, qualifier
  and hedge kept, nothing added.
- `### Skills, Certifications & Education`, and `### Projects, Labs &
  Volunteer Work` if the resume has any.
Then `library.py ids` gives every entry its stable `[L-0001]` ID.

### Config

Create `config/` and write, backing up anything already there:
1. `config/settings.toml` from [../templates/settings.toml](../templates/settings.toml),
   with **`[runner] mode = "M"`**: the runner is not installed yet, and in
   mode L every tracker write would wait for a runner that is not there.
2. **The search profile, from a preset when one fits.** Presets live in
   `templates/presets/`; `lifesci_bayarea` holds life-science title
   families, license knockouts and San Francisco Bay Area places, plus a
   watch list of about 280 employer boards and a list of life-science job boards.
   - When the user's field and region match a preset, say so in one line and
     offer it. On yes: copy
     [../templates/presets/lifesci_bayarea/families.toml](../templates/presets/lifesci_bayarea/families.toml)
     to `config/search_profile.toml`, set `[home] label` ("City, ST") and
     `zip` from profile.md, and copy the preset's `watchlist.csv` and
     `boards.csv` into `config/`.
   - Otherwise copy [../templates/search_profile.toml](../templates/search_profile.toml),
     fill `[home]` from profile.md, ask once for the cities within commuting
     distance (`places`), set `[home] states` to those cities' US state
     code(s) (`["CA"]`; a place name beside another state, such as "Newark,
     NJ", then does not count), and propose 2-4 `[[family]]` tables from the master
     resume (ids lowercase ASCII, `core_titles`, `title_any`, `terms`). Copy
     the header-only [../templates/watchlist.csv](../templates/watchlist.csv)
     and [../templates/boards.csv](../templates/boards.csv); employers come
     in through `/job watch` and resolved leads.
3. The unemployment answer: with a claim, set `[worksearch] claiming = true`
   and `state`, and ask the one follow-up [worksearch.md](worksearch.md)
   "Setup" names (in California, the DE 8405 date).

### Tracker and folders

Create `applications/` and `backups/` if missing, and the tracker with
`tracker.py init` (mode M, so it writes the workbook directly). Log each
earlier application with `tracker.py add --source "By hand" --tier "By hand"
--status Applied --applied <date> --args-file claude/args.json`, the file
holding the `company`, `role` and `url` given (SKILL.md "Scripts"). A
loose date becomes your best single `YYYY-MM-DD`, with the user's words as
`notes` in the file. When that date is more than 14 days ago, clear the
follow-up (`tracker.py update <ID> follow_up=`). A tracker from an earlier version is
upgraded in place on its first write (a backup is made first).

### The scheduled tasks

Offer to create two Claude scheduled tasks (with a scheduling tool, create
them on yes; otherwise give the steps, marked UNVERIFIED). Both use the
JobSearch folder and default to Auto approval:

| Name | When | Prompt | Model |
|---|---|---|---|
| job-morning | daily 06:30 | `Use the job skill: morning` | default |
| job-inbox | daily 13:00 | `Use the job skill: inbox-watch` | the cheapest one offered |

They write the folder only while the desktop app is open; a missed run is
caught up by the first `/job`. Free plans have no scheduled tasks; then
`/job` does the morning steps itself.

## Sitting 2 (day 2, about 20 minutes, plus 15-20 minutes per base resume)

1. **The job finder.** The user downloads `install_runner.cmd` from the
   release page, https://github.com/dumpstering/job_skill/releases/latest
   (give this link), and double-clicks it (SmartScreen: More info > Run
   anyway).
   It installs Python for this user, checks the download's SHA-256, sets up
   the two Windows tasks and a desktop shortcut "Run job finder now", and
   asks for optional API keys (Adzuna, USAJOBS) in its own window. **Keys
   are typed only there, never in chat**; if one is pasted into chat, do not
   store or repeat it, and suggest making a new key. It ends with
   `SELFTEST PASS` or `FAIL` lines; `WARN` lines (a job site that did not
   answer, Recruitee) are fine.
2. **Switch to mode L** after `SELFTEST PASS` (`runner/out/status.json`
   shows `selftest` `net` PASS): back up `config/settings.toml`, set `[runner]
   mode = "L"`, and, for each key the user entered, `[sources] adzuna =
   true` or `usajobs = true`. From now on tracker writes are events
   ([runner.md](runner.md)). On `SELFTEST FAIL`, ask which line failed and
   stay in mode M: `python`, `packages`, `tzdata` or `build` means "double-click
   install_runner.cmd again"; `folder` means the JobSearch folder is not
   writable (OneDrive paused or out of space); `net` means nothing on the PC
   reached a job site (a VPN, proxy or firewall). Run setup step 2 again after
   the fix. An install that stopped with `ERROR the job finder cannot start`
   registered nothing: run the installer again.
3. **The sandbox check:** run `jobrunner.py selftest --net --mode S` here
   (it records `selftest_s` and leaves the PC's result alone). `SELFTEST
   PASS` means mode S can stand in when the PC runner has not run for 36
   hours (daily.md step 3); a FAIL changes nothing.
4. **The first pull:** the user double-clicks "Run job finder now" (or waits
   for 05:30). Then `today_view.py card`.
5. **Places and families, one "ok":** list each `[[family]]` (label, status)
   and the `places`, and the widen ladder (`[widen] steps`). Apply any change
   the user names (backup first).
6. **Alert senders, one "ok":** run one read-only Gmail search,
   `newer_than:14d (alert OR "jobs for you" OR "new jobs")`, and list the
   distinct From addresses of job-alert digests other than LinkedIn's three
   (which are built in). Add the ones the user confirms to `[alerts]
   senders`; a `boards.csv` row of kind `alert_email` counts only after this
   yes. Indeed, Glassdoor, SimplyHired and ZipRecruiter stay off unless the
   user turns them on ([settings.md](settings.md)).
7. **Variants:** build and approve 2-4 role-family base resumes
   ([variants.md](variants.md)), about 15-20 minutes per family for the
   user to read; offer to stop after any family and do the rest another
   day. Until a family is approved its jobs wait (the card names the family
   and how many jobs wait). The list from step 4 was built before any
   approval, so after the last `approve` of this sitting ask the user to
   double-click "Run job finder now" once more: it builds those jobs'
   resumes in a minute or two (`runner/out/status.json` then shows
   `last_daily.counts.prep_failed` 0). Do this before the first `/job`: a
   running session holds that run until it ends.
8. **The export:** when the LinkedIn zip has arrived, the user drops it into
   `network\raw\` ([referrals.md](referrals.md)).
9. **The usage check** on days 1-2 ([usage.md](usage.md)).
10. Then the first `/job`.

## Without the runner

Everything still works in mode M: `/job <link>` runs the single-job flow,
and the tracker is written directly. Only the daily list needs the runner.
