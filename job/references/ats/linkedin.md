# LinkedIn, Indeed and other job boards

Links on `linkedin.com`, any `indeed.*` site, `glassdoor.*`,
`simplyhired.*` and `ziprecruiter.com` are never automated. LinkedIn's User Agreement bans bots and other
automated methods, and LinkedIn restricts accounts that use them; Indeed's
rules ban AI and agentic data mining and third-party bots for applying. So:
- No script and no browser action ever opens one of these sites. `posting.py`
  refuses them (`JOB BOARD`, exit 4), and the runner's fetch policy denies
  them, redirects included.
- A link from one of them is never opened, stored, echoed or hyperlinked.
  LinkedIn alert links carry one-time sign-in tokens.

**A job the user found there.** Ask the user to open it themselves and
paste the posting text, or, when the listing says "Apply on company
website", to paste that employer link instead. The employer's own
application then goes through the normal steps. Pasted text goes through
`posting.py --text-file` (SKILL.md step 3). Pass the board link (as `"_"` in
`claude/args.json`) only when
the user pasted a plain `linkedin.com/jobs/view/<id>` link with nothing
after the id: it becomes the job key for the duplicate check and is never
fetched. Any other board link, or one with a query string, is left out.

**Easy Apply and Indeed Apply** stay the user's, in their own browser at
their own pace (LinkedIn limits how fast Easy Apply may be used). The job
goes on the do-yourself list (apply_batch.md): company, title and location
only, the family base resume as `upload/E<n>_...pdf`, and an answer sheet in
`applications/_diy/<date>/`. `log e1` records it afterwards (Source `By
hand`, Found via `alert:linkedin`). In the single-job flow, write every
answer into `filled.md` for the user to paste and set the status to
`Answers ready - fill by hand`.

**Alert emails** are read only for company, title and location, never
their links (discovery.md "Alert intake"); Indeed, Glassdoor and SimplyHired alerts
only when the user turned `[alerts] read_indeed_family` on, ZipRecruiter
only with `read_ziprecruiter`.
