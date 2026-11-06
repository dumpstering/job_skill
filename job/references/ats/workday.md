# Workday

Links on `<tenant>.wd<N>.myworkdayjobs.com/<site>/job/...` (or
`wd<N>.myworkdaysite.com`).

Provisional: written from other projects' reports and the research, not
from a supervised application. After the first real Workday application,
replace anything here that turned out wrong.

**How the runner reads it.** A POST to `.../wday/cxs/<tenant>/<site>/jobs`,
20 a page, with the family's search words; the total comes from page 0, and
a capped tenant repeats page 0 past offset 2000, which the seen-ID guard
stops. The detail call gives the description and `startDate`, the date
used (never the "Posted 30+ days ago" text). The `site` must come from the
real careers URL; a guessed one returns 422.

**Accounts.**
- Every employer's Workday site is its own account. The account block
  (apply_batch.md) groups new tenants after the first batch, at most 3 a
  day. Run `tracker.py account --site <host> --reuse` so the user reuses the
  email they used there before (or the one they use most).
- The user creates the account and verifies the email. Never create one,
  never retry a sign-up: repeated automated sign-ups have locked accounts.
  Workday also cross-checks earlier applications to the same company.
- A locked account: `blocked` (`account_locked`); the tenant waits 7 days.

**The form.**
- It usually starts by asking for the resume to autofill: attach first,
  then check every parsed field, work history titles and dates especially.
  Workday's parsing is often wrong.
- A series of steps ending in a review step. On each step: fill, click
  "Save and Continue", then read that step's own error messages and fix
  only the fields they name, until the step advances.
- Fields carry stable `data-automation-id` attributes; use them over
  visible labels to tell fields apart.
- Dropdowns ignore typed text: open the dropdown, click the option by its
  text, and confirm it shows as selected. Option order differs from one
  question to the next, so never pick by position (a positional click has
  answered work authorization wrong).
- Stop at the review step, before Submit.
