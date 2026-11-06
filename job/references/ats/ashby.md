# Ashby

Links on `jobs.ashbyhq.com/<name>` (the board name is case-sensitive), or an
employer site embedding an Ashby form. No account.

**How the runner reads it.** One GET,
`api.ashbyhq.com/posting-api/job-board/<name>?includeCompensation=true`,
gives every job with its description. Its robots.txt answers 401, so no
robots policy is readable there. The posted date is `publishedAt`.

**The form.**
- Usually short: contact fields, the resume, a few questions. Attach first
  until the day-1 test (Ashby is a candidate for attach-last).
- **One open application per employer.** Ashby merges a second application
  to the same company by email, so the queue holds one Ashby item per
  employer while an earlier one is open (`CAP DEFER ashby-open <company>`).
- **Spam rejection.** Ashby can reject a submission as spam when the form
  was filled in an automated browser, even after the user's own click on
  Submit. If the page says the application could not be submitted or was
  flagged: emit `blocked` (reason `spam_rejected`), give the user a numbered
  copy-paste list of every answer for their normal browser, set the row to
  `Answers ready - fill by hand`, and put the item on the do-yourself list.
  Never retry it in the automated browser, and never hide the automation.
