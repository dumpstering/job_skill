# job_skill

A Claude Agent Skill, `job`, built for Claude Desktop (Cowork). The user pastes
a job posting URL. The skill assesses fit against a master resume, asks
targeted questions, writes a truthful ATS-friendly resume as .docx, .pdf and
.txt, maintains an achievement library and an application profile, then fills
in the application in the browser and stops before the final Submit.

It is adapted from `Prompt_Resume_Tailoring_v4.txt`. Phase 1 and the rules
carry over nearly word for word, still in the user's voice. Changes from v4:
- Inputs and outputs are files in a fixed folder, not pasted tags.
- Word and PDF are the default output, built by a script. LaTeX is optional.
- Block C merges into the library file automatically.
- Personal facts (work authorization and the like) come from `profile.md`.
- There is a browser apply step.

## Layout

| Path | What |
|---|---|
| `job/SKILL.md` | The workflow: 9 steps, file map, commands |
| `job/references/setup.md` | First run: extract the master resume, build the profile, seed the library |
| `job/references/fit_and_questions.md` | Phase 1 from v4 (fit table, verdict, questions) |
| `job/references/writing_rules.md` | Phase 2 from v4: truthfulness, ATS, style, Blocks A-C, audit, library merge, cover letter |
| `job/references/latex.md` | v4's LaTeX rules and template, only when the profile asks for LaTeX |
| `job/references/apply.md` | Browser fill protocol and what is left to the user |
| `job/templates/` | Starting `profile.md` and `achievement_library.md` |
| `job/scripts/build_resume.py` | JSON to .docx (python-docx, Arial) + .pdf (reportlab, Helvetica) + .txt, with lint |
| `tests/` | Sample resume and letter, and `test_build.py` |
| `build_zip.py` | Validates and packages `dist/job.zip`, `dist/job-search-plugin.zip`, `dist/SETUP_GUIDE.md` |
| `SETUP_GUIDE.md` | Install and use guide for the person receiving the skill |

## Commands

```
python -m venv .venv
.venv\Scripts\python -m pip install python-docx reportlab pypdf
.venv\Scripts\python tests\test_build.py [out_dir]    # builds the samples, checks text order, headings, lint
python build_zip.py                                   # writes dist\
```

Send `dist\job.zip`, `dist\job-search-plugin.zip` and `dist\SETUP_GUIDE.md`.

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

UNVERIFIED:
- Whether a standalone uploaded skill shows in Cowork's `/` menu. The plugin
  zip is the fallback.
- Whether the connected folder persists across Cowork tasks.
- Whether the browser can attach a local file to a form. The skill asks the
  user to attach it.
- Which packages the consumer sandbox has. The script exits 2 with a pip
  hint, and the skill installs and retries.

## Design notes

- The PDF uses Helvetica and the .docx uses Arial, which share metrics, so the
  PDF page count stands for both. Word renders the .docx at the same length
  (checked 2026-11-05 on the sample).
- The .docx sets author and last-modified-by to the candidate's name. The
  python-docx default template otherwise says "python-docx".
- The lint list in `build_resume.py` mirrors the Writing style list in
  `writing_rules.md`. Change both together.
