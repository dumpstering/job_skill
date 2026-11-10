# job_skill

A Claude skill and plugin, `job`, for a daily job search run from one folder
on a Windows PC, plus a small local job finder (the runner) that does the
zero-token work. Version 1.2.

- **The runner** (Python, Task Scheduler, on the user's PC) reads employer
  job boards every morning (Greenhouse, Lever, Ashby, Workday, BambooHR,
  Workable, Oracle, Dayforce and more), resolves alert-email leads, filters
  everything through cheap gates, applies per-company caps, ranks a pool,
  and writes a numbered list of about 30 jobs. Each job gets a folder with
  the posting, a fill plan, and a resume tuned from an approved role-family
  base and checked so it can never say anything the base did not.
- **The skill** shows the day's card, fills the applications one at a time
  in the browser, and stops before Submit, which is always the user's click.
  The best 4 jobs a day get a fully tailored resume from a sub-agent and a
  fresh-context audit. Gmail is read for replies and alert emails; nothing
  is ever sent.
- **A pasted link** still runs the v1.1 single-job flow: duplicate check,
  fit, questions, a truthful tailored resume, the fill.

## Where the daily volume comes from

- **The watch list** (`config/watchlist.csv`; the life-science preset has
  about 280 Bay Area employer boards) gives a backlog in the first week and
  then a few applications a day: on the live runs of 2026-11-06 and
  2026-11-10 about 5 eligible postings a day passed the per-company caps,
  about 3.7 of them jobs the user would want.
- **Job-alert emails** (LinkedIn job alerts in Gmail, read for company,
  title and place only, then resolved to the employer's own posting) add a
  few more. In a 2026-11-10 test, 8 of 31 alert leads at employers off the
  watch list resolved to the employer's posting on the day; most of the rest
  go to the do-it-yourself list. The **free Adzuna and USAJOBS keys** add
  postings too; their yield was not measured. SETUP_GUIDE.md puts both near
  the top, about 5 minutes each; running `install_runner.cmd` again asks for
  a key that was skipped.
- **20 a day takes more than the queue.** The rest comes from the
  do-it-yourself list, LinkedIn Easy Apply done by hand, or looser
  per-company caps ("more to one company" in `/job settings`; with no caps
  the 2026-11-10 pool gave about 7 a day).
- The card adds one line when no alert lead has arrived for 3 days, and
  `/job alerts` gets them coming again in one short exchange.

Nothing personal is in this repository. The fixtures are synthetic
(`Jordan Q. Avery`, `example.com`, Springfield), and `build_zip.py` refuses
to package personal files.

## Architecture

```
config/watchlist.csv + aggregators (USAJOBS, Adzuna) + alert leads (Claude)
        |
runner daily 05:30 (PC): boards.py, aggregators.py, resolve.py -> gates.py -> caps.py -> jobqueue.py
        |                 variants.py tier-b and answers.py plan per item, upload staging
queue/<date>/queue.json, job folders, upload/NN_<Last>_Resume_<Employer>.pdf
        |
job-morning 06:30 (Claude scheduled task): inbox, alert intake, Tier A sub-agents, questions, drafts
        |
/job session: today_view.py card / next -> browser fill -> the user's Submit -> events
        |
runner tick every 15 min: tracker.py apply-events -> applications.xlsx -> runner/out/tracker.json
```

- **One writer per file.** In mode L the runner is the only writer of
  `applications.xlsx`; Claude's tracker writes become create-only event
  files, applied by the same `tracker.py` code. Mode M (no runner) writes
  directly, as v1.1 did.
- **No outside text on a command line.** A company, title, link, email
  subject or form question reaches a script only through a JSON file Claude
  writes under `claude/` (`--args-file`, `argfile.py`; `jobrunner.py request
  --json-file`), read once and removed, so no shell parses it.
- **Code boundary.** The runner runs only its own hash-checked install in
  `%LOCALAPPDATA%\JobRunner\app\<version>`, with `python -I`, and never
  imports or runs anything from the JobSearch folder.
- **Network policy** (`netfetch.py`): public addresses only, connecting to
  the checked address; https and a host allowlist for unattended runs;
  robots.txt for every unattended fetch; a hard deny of LinkedIn, Indeed,
  Glassdoor, SimplyHired and ZipRecruiter, and of the list endpoints of
  SmartRecruiters, iCIMS, UKG, Paycom, Paycor and ClearCompany.
- **Truthfulness.** FACT (numbers and names against the sources), EVIDENCE
  (every bullet cites library IDs), VARIANT (Tier B text equals approved
  text), CITE (citations equal publications.md), page caps and lint, all
  failing closed under `--strict`.

## Layout

| Path | What |
|---|---|
| `job/SKILL.md` | The router: global rules, entry points, folder contract, scripts, the single-job steps |
| `job/references/` | daily.md, apply_batch.md, apply.md, setup.md, settings.md, files.md, usage.md, runner.md, discovery.md, sources.md, variants.md, inbox.md, referrals.md, weekly.md, worksearch.md, interview_prep.md, fit_and_questions.md, writing_rules.md, cv_publications.md |
| `job/references/ats/` | Per-site notes: Greenhouse, Lever, Ashby, Workday, Workable, BambooHR, JazzHR, SmartRecruiters, iCIMS, Oracle, USAJOBS, Dayforce, LinkedIn and job boards |
| `job/templates/` | Starting profile, library, answers, publications, techniques, settings, search profile, header-only watch list and boards, work-search state presets |
| `job/templates/presets/lifesci_bayarea/` | An opt-in preset: life-science title families, license knockouts, San Francisco Bay Area places, employer boards and job boards |
| `job/scripts/` | jobfs, netfetch, posting, jobkey, argfile (foundation); tracker, events, caps (tracker and transport); boards, aggregators (readers); leads, resolve, gates, jobqueue (discovery and queue); build_resume, checks, library, variants, answers (preparation); network, worksearch, report (after applying); jobrunner, today_view (runner and card) |
| `plugin/agents/` | job-tailor (one Tier A job) and job-auditor (read-only, fresh context) |
| `runner/` | install_runner.cmd (template), install_runner.ps1, uninstall_runner.ps1, requirements.lock (hash-pinned), README.md |
| `tests/` | One script per module, fixtures, `run_all.py`, `walkthrough_v12.md` |
| `build_zip.py` | Checks and packages into `dist/v<version>/` |
| `SETUP_GUIDE.md` | The install and use guide for the person receiving it |

## Commands

```
python -m venv .venv
.venv\Scripts\python -m pip install python-docx reportlab pypdf openpyxl tzdata
.venv\Scripts\python tests\run_all.py            # every tests\test_*.py; PASS <script> each, then ALL PASS
python build_zip.py                              # writes dist\v1.2.0\ (refuses on a FAIL line)
```

One at a time (each prints `ok <name>` lines and `PASS`):

```
.venv\Scripts\python tests\test_jobfs.py
.venv\Scripts\python tests\test_netfetch.py
.venv\Scripts\python tests\test_jobkey.py
.venv\Scripts\python tests\test_posting.py       # JOB_SKILL_LIVE=1 also hits live Greenhouse and Lever
.venv\Scripts\python tests\test_tracker.py
.venv\Scripts\python tests\test_events.py
.venv\Scripts\python tests\test_caps.py
.venv\Scripts\python tests\test_boards.py
.venv\Scripts\python tests\test_aggregators.py
.venv\Scripts\python tests\test_leads.py
.venv\Scripts\python tests\test_resolve.py
.venv\Scripts\python tests\test_gates.py
.venv\Scripts\python tests\test_jobqueue.py
.venv\Scripts\python tests\test_build.py
.venv\Scripts\python tests\test_checks.py
.venv\Scripts\python tests\test_library.py
.venv\Scripts\python tests\test_variants.py
.venv\Scripts\python tests\test_answers.py
.venv\Scripts\python tests\test_argfile.py      # outside text through --args-file, never a shell
.venv\Scripts\python tests\test_network.py
.venv\Scripts\python tests\test_worksearch.py
.venv\Scripts\python tests\test_report.py
.venv\Scripts\python tests\test_runner.py
.venv\Scripts\python tests\test_today_view.py
.venv\Scripts\python tests\test_daily_e2e.py     # the offline end-to-end day
.venv\Scripts\python tests\test_package.py       # skill text, links, routing, build_zip and its guard
```

The offline day by hand:

```
python job\scripts\jobrunner.py daily --root <copy of tests\fixtures\folder> --offline tests\fixtures\boards --date 2026-11-06
python job\scripts\today_view.py card --root <that copy> --date 2026-11-06
```

It prints `DAILY ok queue=30 pool_left=41 discards=141 tier_b_built=30
prep_failed=0`. `runner/README.md` has the installer commands.

Release: `gh release create v1.2.0 dist/v1.2.0/* -R dumpstering/job_skill`.
The release page is https://github.com/dumpstering/job_skill/releases/latest
(setup.md and SETUP_GUIDE.md send the user there for `install_runner.cmd`).
`install_runner.cmd` downloads `jobrunner-1.2.0.zip` from that release and
checks it against the SHA-256 build_zip.py wrote into it. Send
`job-search-plugin.zip`, `install_runner.cmd` and `SETUP_GUIDE.md`;
`job.zip` is the fallback.

## Platform facts this relies on (read 2026-11-05)

- Since 2026-10-06 skill scripts run in Anthropic's cloud sandbox, whose
  outbound network is documented as package registries only; hence the
  runner on the user's PC.
- Cowork and chat are one app ("start a new task"). Plugins install from
  the Plugins page (Add > Upload plugin; one plugin.json, at most 5,000
  files and 200 MB); plugin commands run as `/job-search:job`. A plugin's
  `agents/*.md` load in Cowork and are ignored in chat. The exact upload
  click path is UNVERIFIED until checked on a real account.
- A skill description may be up to 200 characters in the current UI;
  build_zip.py fails above that.
- Scheduled tasks write a connected folder only while the desktop app is
  open; missed-run behavior is undocumented, so every step is idempotent
  and caught up by the next `/job`.
- The built-in browser cannot attach local files, and Claude in Chrome's
  file upload is unreliable, so the user attaches from one `upload\` folder.

UNVERIFIED and measured in week 1: Claude's fill time per form, usage per
application (`/job usage`), whether a hidden browser pane stalls, which
ATSs keep fields on attach (attach-last), and per-tenant permission prompts.

## Where the rules came from

v1.2 research (2026-11-05; each finding verified, and the verifier's
correction used where one was given):
- Board endpoints, live-tested: Greenhouse Job Board API, Lever Postings API
  (github.com/lever/postings-api), Ashby posting API, Workday CXS (page size
  20, tenant-specific 2000 cap and wraparound, 422 on a guessed site),
  BambooHR, Breezy, Workable widget, Rippling, Pinpoint, Teamtailor JSON
  Feed, Personio XML, ADP Workforce Now (short pages), Oracle Recruiting
  Cloud, Paylocity, Dayforce (CSRF plus cookie), Jobvite and JazzHR
  JSON-LD, Phenom sitemaps, Comeet, Manatal, Hireology. Round 4
  (2026-11-06): SAP SuccessFactors career-site search pages (Bayer,
  Boehringer Ingelheim, Grifols), a Radancy TalentBrew sitemap over Oracle
  Taleo (Kaiser Permanente) and Oracle Taleo Social Sourcing landing pages
  (Lawrence Berkeley National Laboratory).
- robots.txt positions read live for api.smartrecruiters.com,
  recruiting.ultipro.com, *.icims.com, paycomonline.net,
  recruitingbypaycor.com and careers-page.clearcompany.com.
- The MIT-licensed jobseek boards directory (colophon-group/jobseek) as an
  optional seed.
- Aggregators: USAJOBS developer guides (headers, limits, terms), Adzuna
  terms (personal job search), The Muse and Workable search (terms unknown).
- Volume and caps: Google Careers (3 in 30 days, 90 days per job),
  Greenhouse application limits and invisible reCAPTCHA (support articles),
  LinkedIn Easy Apply limits (help a8068422), OPM's two-page USAJOBS resume
  rule.
- Open-source pipelines, ideas only (no code copied): ApplyPilot issues
  (Workday login walls, lockouts; AGPL-3.0), career-ops (Ashby spam
  rejection, ATS quirks, repost precheck; MIT), ai-job-pilot (schema-first
  token-cheap fills, never resubmit; MIT), AIHawk (per-ATS results),
  christian-macedo/job-candidacy (evidence atoms; MIT).
- Work-search rules: state unemployment agency pages, with each preset
  value marked confirmed or not in `job/templates/worksearch_states.toml`.

v1.1 sources:
- Apply loop: upload first, read back, fix only flagged fields
  (ChamPro/ats-autofill; abhinav-reddy-k/workday-copilot); type-ahead clicks
  and Lever's "Success!" (AIHawk plugins); required-field sweep and knockout
  pre-scan (santifer/career-ops).
- Answer scope and sensitive flag (HarshadLambate/workday-autofill;
  neonwatty/job-apply-plugin); never storing legal answers (ApplyPilot #108).
- LinkedIn automation ban: linkedin.com/legal/user-agreement section 8.2.
- Fresh-context audit (job-candidacy application-verify;
  MadsLorentzen/ai-job-search); fact, read-back and keyword checks
  (srbhr/Resume-Matcher); Greenhouse's 2.5 MB parse limit.

## Design notes

- The PDF uses Helvetica and the .docx Arial, which share metrics, so the
  PDF page count stands for both.
- Job keys: Greenhouse, Lever and Ashby IDs are global; Workday, iCIMS,
  SmartRecruiters and the other tenant ATSs carry the tenant. Reposts get
  caught by requisition ID, company plus title plus city, and a 64-bit
  SimHash of the description.
- The tracker refuses backward status moves without `--force`, forces every
  text cell to a string (no formula injection) and hyperlinks http(s) URLs
  only.
- Dates are the user's local dates (`[time] timezone`, tzdata pinned), never
  the sandbox's UTC clock.
- Zips are deterministic (sorted names, a fixed timestamp, LF text), so the
  runner zip's SHA-256 is the same from any checkout.
