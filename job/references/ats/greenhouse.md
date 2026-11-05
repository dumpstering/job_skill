# Greenhouse

Links on `boards.greenhouse.io`, `job-boards.greenhouse.io`, or an employer
site with `?gh_jid=` in the URL.

- `posting.py` reads the form's own questions from Greenhouse's posting data:
  the "Application questions" section of `posting.md` lists them, required
  ones marked, with the options of each dropdown. Draft answers to all of
  them before opening the form (apply.md, step 2 of "Before opening the
  browser").
- An `AI policy:` line in `posting.md`, or a question about AI use, is the
  employer's rule about AI in applications. Show it to the user before
  filling anything. If it asks applicants not to use AI, stop and ask the
  user how they want to proceed.
- Type-ahead fields (location, school, some dropdowns) need a click on the
  suggestion; text left unselected is not an answer.
- Greenhouse cannot parse resumes over 2.5 MB; the built PDF is far smaller.
  When parsing fails, the file stays attached and a recruiter types the
  details in by hand.
