# Greenhouse

Links on `boards.greenhouse.io`, `job-boards.greenhouse.io`, or an employer
site with `?gh_jid=` in the URL (the form is embedded there). No account.

**How the runner reads it.** One public GET per board,
`boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` (every job,
no paging), then `.../jobs/<id>?questions=true` for the survivors. The
posted date is the detail `first_published`; the list's value is
provisional. A `gh_src=` in a link is a source tag: it is kept as the
referral link, never as the job's identity.

**The form.**
- `posting.py` and the runner read the form's own questions from the
  posting data: `posting.md`'s "Application questions" lists them, required
  ones marked, with each dropdown's options, and `fill_plan.json` answers
  most of them before the page opens.
- An `AI policy:` line in `posting.md`, or a question about AI use, is the
  employer's rule about AI in applications. Show it before filling. If it
  asks applicants not to use AI, stop and ask the user how to proceed.
- Type-ahead fields (location, school, some dropdowns) need a click on the
  suggestion; text left unselected is not an answer.
- Attach first, until the day-1 supervised test shows that attaching here
  does not overwrite filled fields (Greenhouse is a candidate for
  attach-last).
- Greenhouse runs an invisible reCAPTCHA that scores typing and mouse
  activity. Depending on the employer's setting, the user may be asked to
  verify their email with a code before submitting: the user's part. Name
  the subject line to look for; never read or type the code.
- MyGreenhouse "Autofill my application" stays off (`[apply]
  mygreenhouse_autofill`) until a supervised test.
- Greenhouse cannot parse resumes over 2.5 MB; the built PDF is far
  smaller. When parsing fails, the file stays attached and a recruiter
  types the details in by hand.
- Employers can set application-limit rules that silently auto-reject extra
  applications; the caps (`caps.py`) keep within the usual ones.
