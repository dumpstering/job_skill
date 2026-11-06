# Lever

Links on `jobs.lever.co` or `jobs.eu.lever.co`. No account.

**How the runner reads it.** `api.lever.co/v0/postings/<site>?mode=json`
(EU: `api.eu.lever.co`), 100 a page, with the 1-second Crawl-delay its
robots.txt asks for. The posted date is `createdAt`.

**The form.**
- Lever's posting data has no form questions; read them on the application
  page (the posting's `/apply` link).
- Attaching the resume fills fields from it, so it is attach-first: the user
  attaches and types `ok`, then check every filled field against the
  tailored resume.
- A "Success!" message after the resume uploads means the file uploaded. It
  is not the application being sent.
- Consent and acknowledgment checkboxes are the user's to tick: clicks on
  them by a tool can bring up an hCaptcha. Name them in the handover's
  `Check:` line.
- An hCaptcha is the user's part (one retry at the end of the session, then
  `Blocked - CAPTCHA`).
