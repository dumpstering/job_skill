# First-run setup

Do only the parts step 2 sent you here for. Ask for everything you need in one
message, not one question at a time.

## Master resume

1. Find the resume: a .docx, .pdf, .txt or .md in the folder's top level whose
   name contains "resume" or "cv". If there are several, ask which is the most
   complete. With none, ask the user to save their most complete resume into
   the folder and reply "done", or to attach it here so you can save a copy.
   It should be the full version, not one tailored for another job (those
   leave things out on purpose).
2. Extract its text to `master_resume.md`: every role, title, date, bullet,
   degree and certification exactly as written, in the original order. Drop
   PDF debris (stray page numbers, repeated headers, icon glyphs). For a .docx
   read paragraphs and tables in document order with python-docx; for a .pdf
   use pdfplumber or pypdf.
3. Show the user a short summary: name, each role (title, company, dates),
   education and certifications. Ask about anything whose dates, titles or
   order look scrambled, and fix `master_resume.md` from the answer.

## Profile

Copy [../templates/profile.md](../templates/profile.md) to `profile.md`. Fill
what the resume states (name, email, phone, city, links). Ask for the rest in
one numbered list, and say that "skip" is fine and the file stays in their
folder on their PC. A skipped field stays blank and gets asked when an
application needs it. A blank self-identification field means "decline to
answer".

Applications that want a Social Security or other national ID number, date of
birth, bank or tax details, a driver's license number or a password get them
from the user directly at apply time (apply.md); the profile holds none of
them.

Show the filled profile and write it once the user confirms.

## Achievement library

Create `achievement_library.md` from
[../templates/achievement_library.md](../templates/achievement_library.md) and
seed it from `master_resume.md`:
- One `### Job Title | Company | Mon YYYY - Mon YYYY` heading per role; when
  the resume shows two titles at one employer, each gets its own heading.
- One entry per resume bullet, written as a record: every number, qualifier
  and hedge kept, nothing added.
- `### Skills, Certifications & Education`, and `### Projects, Labs &
  Volunteer Work` if the resume has any.

Tell the user it was seeded from their resume and grows with every
application.

## Folders

Create `applications/`, `backups/` and `applications.md` if missing. The
tracker's header row:

`| Date | Company | Role | Fit | Status | Folder | URL |`
