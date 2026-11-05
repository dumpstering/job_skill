# Workday

Links on `*.myworkdayjobs.com`.

Provisional: written from other projects' reports, not from a supervised
application. After the first real Workday application, replace anything here
that turned out wrong.

- Every employer's Workday site has its own account. Run `tracker.py account
  --site <host>` before the user signs in, so they reuse the email they used
  there before. The user creates the account and verifies the email; repeated
  automated sign-up attempts have locked accounts.
- An application often starts by asking for the resume to autofill the form.
  The user attaches it there first; then check every parsed field, work
  history titles and dates especially, against the tailored resume. Workday's
  parsing is often wrong.
- The application is a series of steps ending in a review step. On each step:
  fill, click "Save and Continue", then read that step's own error messages
  and fix only the fields they name, until the step advances.
- Fields carry stable `data-automation-id` attributes; use them over visible
  labels to tell fields apart.
- Dropdowns ignore typed text: open the dropdown, click the option, and
  confirm it shows as selected.
- Stop at the review step, before Submit.
