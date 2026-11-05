# job_skill

A Claude Agent Skill, `job`, built for Claude Desktop (Cowork). The user pastes
a job posting URL, and the skill:
- checks it against every job already in the tracker spreadsheet (reposts
  included);
- assesses fit against a master resume and asks targeted questions;
- writes a truthful, ATS-friendly resume as .docx, .pdf and .txt, with fact,
  read-back and keyword checks;
- maintains an achievement library and an application profile;
- fills in the application in the browser and stops before the final Submit.

Separate entry points check Gmail for replies and update the tracker
(`/job inbox`), log jobs applied for by hand (`/job log`), report status
(`/job status`), and prepare for interviews (`/job prep`).

It is adapted from `Prompt_Resume_Tailoring_v4.txt`. Phase 1 and the rules
carry over nearly word for word, still in the user's voice. Changes from v4:
- Inputs and outputs are files in a fixed folder, not pasted tags.
- Personal facts (work authorization and the like) come from `profile.md`.
- Block C merges into the library file automatically.
- The output is Word, PDF and plain text, built by a script. LaTeX is gone.
  v4 used it because chat AIs break formatting when they write Word or PDF
  directly; the agent now writes plain JSON and the script does the layout,
  which solves the same problem and gives the application step a file to
  upload. The LaTeX rules and template are in commit 3f51373
  (`job/references/latex.md`) if they are ever wanted back.

## Layout

| Path | What |
|---|---|
| `job/SKILL.md` | The workflow: 9 steps, entry points, file map, commands |
| `job/references/setup.md` | First run: master resume, profile, library seed, tracker with earlier applications |
| `job/references/fit_and_questions.md` | Phase 1 from v4 (fit table, verdict, questions) |
| `job/references/writing_rules.md` | Phase 2 from v4: truthfulness, ATS, style, Blocks A-C, fresh-context audit, library merge, cover letter |
| `job/references/apply.md` | Browser fill loop, accounts, the user's part, handover, after submit |
| `job/references/ats/*.md` | Per-site notes: Greenhouse, Lever, Workday (provisional), LinkedIn and job boards |
| `job/references/inbox.md` | Gmail check: search, match, classify, update tracker, scam flags |
| `job/references/interview_prep.md` | Interview prep from what was submitted |
| `job/templates/` | Starting `profile.md` and `achievement_library.md` |
| `job/scripts/tracker.py` | Every read and write of `applications.xlsx`: duplicate check, add, update, due, accounts, email log, stats |
| `job/scripts/posting.py` | Posting URL to `posting.md` via public ATS JSON, JSON-LD, or page text; flags hidden and AI-addressed text |
| `job/scripts/build_resume.py` | Resume or letter JSON to .docx + .pdf + .txt, with lint |
| `job/scripts/checks.py` | Fact check against sources, PDF read-back, keyword coverage (called by build_resume.py) |
| `job/scripts/jobkey.py` | Shared job-key, requisition-ID and name-normalization helpers |
| `tests/` | Samples, fixtures and one test script per script |
| `build_zip.py` | Validates and packages into `dist/v<version>/` |
| `SETUP_GUIDE.md` | Install and use guide for the person receiving the skill |

## Commands

```
python -m venv .venv
.venv\Scripts\python -m pip install python-docx reportlab pypdf openpyxl
.venv\Scripts\python tests\test_build.py
.venv\Scripts\python tests\test_checks.py
.venv\Scripts\python tests\test_tracker.py
.venv\Scripts\python tests\test_posting.py      # set JOB_SKILL_LIVE=1 to also hit live Greenhouse and Lever
python build_zip.py                             # writes dist\v<version>\
```

Release: `gh release create v<version> dist/v<version>/* -R dumpstering/job_skill`.
Send `job.zip` and `SETUP_GUIDE.md`; `job-search-plugin.zip` is the fallback.

## Platform facts this relies on (read 2026-11-05)

From claude.com/docs/skills and support.claude.com:
- Upload is at Customize > Skills > + > Create skill > Upload a skill.
- The ZIP must contain the skill folder at its top level, and the folder name
  must equal `name`.
- `description` can be up to 1,024 characters.
- Skills need "Code execution and file creation" switched on.
- Typing `/` in the message box picks a skill.
- Cowork is in beta on Windows for paid plans. It reads and writes connected
  folders. It drives a built-in browser, or Chrome through Claude in Chrome.
  Connectors (Gmail) are available to it.

UNVERIFIED:
- Whether a standalone uploaded skill shows in Cowork's `/` menu. The plugin
  zip is the fallback.
- Whether the connected folder persists across Cowork tasks.
- Whether the browser can attach a local file to a form. The skill asks the
  user to attach it.
- Which packages the consumer sandbox has. Each script names any missing
  package, and the skill installs it and retries.
- Whether the sandbox has outbound network. `posting.py` exits 3 with the URL
  to fetch, and the agent fetches it with its own tool and passes the file
  back in.
- Whether Cowork offers subagents. The audit falls back to running in the
  same context.

## Where the v1.1 rules came from (research 2026-11-05)

- Public posting endpoints: Greenhouse, Lever, Ashby, Workday and
  SmartRecruiters were each fetched live.
  - Greenhouse `?questions=true` returns the application form.
  - Greenhouse docs: docs.greenhouse.io/job-board.html.
  - Lever: github.com/lever/postings-api.
- Apply loop:
  - Upload first, read back, and fix only flagged fields: ChamPro/ats-autofill
    ARCHITECTURE.md and skills/fill-app/workday.md;
    abhinav-reddy-k/workday-copilot.
  - Type-ahead fields need a click on the suggestion, and Lever's "Success!"
    after an upload is not the send: AIHawk plugins/greenhouse.md and
    plugins/lever.md.
  - Required-field sweep and knockout pre-scan: santifer/career-ops
    modes/apply.md.
- Answer scope and sensitive flag: HarshadLambate/workday-autofill
  answer_bank.py; neonwatty/job-apply-plugin. Never storing legal answers:
  Pickle-Pixel/ApplyPilot issue #108.
- Workday accounts per tenant, and lockouts: ApplyPilot issues #22 and #108.
- LinkedIn automation ban: linkedin.com/legal/user-agreement section 8.2 and
  LinkedIn Help a1341387.
- Fresh-context audit: christian-macedo/job-candidacy application-verify;
  MadsLorentzen/ai-job-search application-reviewer.
- Fact, read-back and keyword checks: srbhr/Resume-Matcher ats_parse/ and
  verify tests; job-candidacy checks.md.
- Greenhouse 2.5 MB parse limit and parse-failure causes: Greenhouse support
  article 200989175.
- Ideas only. No code was copied. ApplyPilot is AGPL-3.0, and AIHawk's
  license reads differently in different places.

## Design notes

- The PDF uses Helvetica and the .docx uses Arial, which share metrics, so the
  PDF page count stands for both. Word renders the .docx at the same length
  (checked 2026-11-05 on the sample).
- The .docx sets author and last-modified-by to the candidate's name. The
  python-docx default template otherwise says "python-docx".
- The lint list in `build_resume.py` mirrors the Writing style list in
  `writing_rules.md`. Change both together.
- Job keys: Greenhouse, Lever and Ashby IDs are global, so the key has no
  company. Workday, iCIMS and SmartRecruiters keys carry the tenant. Reposts
  usually get a new ID, which is why the duplicate check also compares
  requisition IDs and posting text.
- The tracker refuses backward status moves without `--force`, so a late
  confirmation email cannot undo an interview.
