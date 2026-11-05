# First-run setup

Do only the parts step 2 sent you here for, in two messages to the user:
1. The resume summary, any questions about scrambled dates or titles, the
   numbered profile questions, and (when the tracker is new) one more: "Have
   you applied anywhere in the last few months? List them (company, role,
   date, link if you have it) so reposts get caught." All together.
2. After the user answers: the filled profile for confirmation, a line saying
   the library was seeded, a line saying how many earlier applications were
   logged, and: "To have me check your email for replies, connect Gmail under
   Connectors in Claude's settings and say /job inbox."

If a Python package you need is missing, `pip install` it and carry on.

## Master resume

1. Find the resume: a .docx, .pdf, .txt or .md in the folder's top level whose
   name contains "resume" or "cv", other than `master_resume.md`. If there are
   several, ask which is the most complete. With none, ask the user to save
   their most complete resume into the folder and reply "done", or to attach
   it here so you can save a copy. It should be the full version, not one
   tailored for another job (those leave things out on purpose).
2. Extract its text to `master_resume.md`, starting with the line
   `Source: <file name>`. Keep every role, title, date, bullet, degree and
   certification exactly as written, in the original order. For a .docx,
   read the body, tables, headers, footers and text boxes (contact details
   often sit in a header or text box); for a .pdf use pdfplumber or pypdf.
   Drop PDF debris (stray page numbers, repeated headers, icon glyphs).
3. In message 1, give a short summary: name, each role (title, company,
   dates), education and certifications. Ask about anything whose dates,
   titles or order look scrambled, and fix `master_resume.md` from the
   answer.

The resume file named on the `Source:` line is the original that step 2 of
SKILL.md compares dates against.

## Profile

Copy [../templates/profile.md](../templates/profile.md) to `profile.md`. Fill
what the resume states (name, email, phone, city, links). Ask for the rest in
message 1 as one numbered list, and say that "skip" is fine and the file stays
in their folder on their PC. A skipped field stays blank and gets asked when
an application needs it. A blank self-identification field means "decline to
answer".

The profile holds no Social Security or other national ID number, date of
birth, bank or tax details, driver's license number or password, and you
never ask for them in chat: when a form wants one, the user types it into
the form (apply.md).

In message 2, show the filled profile and write it once the user confirms.

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

In message 2, say it was seeded from their resume and grows with every
application.

## Tracker and folders

Create `applications/` and `backups/` if missing, and the tracker with
`tracker.py init`. Log each earlier application the user lists with
`tracker.py add --source "By hand" --status Applied --applied <date>` plus
the company, role and link they gave. A loose date ("early October", "last
week") becomes your best single `YYYY-MM-DD`, with the user's words in
`--notes`. When that date is more than 14 days ago, clear the follow-up
(`tracker.py update <ID> follow_up=`) so old applications do not all show as
overdue on day one.
