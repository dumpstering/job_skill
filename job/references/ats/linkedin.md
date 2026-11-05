# LinkedIn and other job boards

Links on `linkedin.com`, `indeed.com` and similar job boards.

LinkedIn's User Agreement bans bots and other unauthorized automated methods,
and LinkedIn restricts accounts that use them. So the browser never drives
LinkedIn, and no script fetches LinkedIn pages:
- Ask the user to open the job and paste the posting text, or, when the
  listing says "Apply on company website", to paste that link instead. The
  employer's own application then goes through the normal steps; pasted
  text goes through `posting.py --text-file` with the LinkedIn link kept for
  the job key (SKILL.md step 3).
- For an Easy Apply-only job, write every answer into `filled.md` for the
  user to paste themselves, and set the tracker status to
  `Answers ready - fill by hand`.

For Indeed and other boards, prefer the employer's own application link the
same way.
