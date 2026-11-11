# LinkedIn, Indeed and other job boards

No script and no fetch ever opens `linkedin.com`, any `indeed.*` site,
`glassdoor.*`, `simplyhired.*` or `ziprecruiter.com`, under any setting:
`posting.py` refuses them (`JOB BOARD`, exit 4), and the runner's fetch
policy denies them, redirects included. LinkedIn's User Agreement bans bots
and other automated methods, and LinkedIn restricts accounts that use them;
Indeed's rules ban AI and agentic data mining and third-party bots for
applying.

What the browser may do on LinkedIn depends on `[linkedin] browser` in
`config/settings.toml` ([../settings.md](../settings.md)):
- `false` (the default): never automated. No browser action opens one of
  these sites either, and everything below applies as written.
- `true` (the user's own yes at setup): in a session the browser tool may
  work on LinkedIn's job pages in the user's own signed-in Chrome, only as
  [../linkedin_browser.md](../linkedin_browser.md) says and only after
  `today_view.py linkedin` prints `LINKEDIN GO`. Indeed, Glassdoor,
  SimplyHired and ZipRecruiter are never opened by any tool, whatever this
  says.

Under either setting a link from one of these sites is never stored, echoed
or hyperlinked, with one exception: a LinkedIn job's plain
`https://www.linkedin.com/jobs/view/<id>`, kept as the job key and never
fetched. `posting.py --text-file` and `tracker.py` keep a LinkedIn link in
that form only and leave out one that names no job. LinkedIn alert links
carry one-time sign-in tokens and are never opened.

**A job the user found there.** Ask the user to open it themselves and
paste the posting text, or, when the listing says "Apply on company
website", to paste that employer link instead. The employer's own
application then goes through the normal steps. Pasted text goes through
`posting.py --text-file` (SKILL.md step 3); several pasted pages at once are
`/job paste` ([../paste.md](../paste.md)). Pass the board link (as `"_"` in
`claude/args.json`) only when
the user pasted a plain `linkedin.com/jobs/view/<id>` link with nothing
after the id: it becomes the job key for the duplicate check and is never
fetched. Any other board link, or one with a query string, is left out.
With `browser = true` and `LINKEDIN GO`, a LinkedIn job the user names may
instead be read in the browser, from linkedin_browser.md step 5 on: only
its plain job link is ever opened, never the link as pasted.

**Easy Apply and Indeed Apply** stay the user's, in their own browser at
their own pace (LinkedIn limits how fast Easy Apply may be used). Indeed
Apply is never filled by any tool. LinkedIn Easy Apply is filled for the
user's own Submit only in `/job linkedin` with `browser = true`; otherwise
the job goes on the do-yourself list (apply_batch.md): company, title and
location only, the family base resume as `upload/E<n>_...pdf`, and an
answer sheet in `applications/_diy/<date>/`. `log e1` records it afterwards
(Source `By hand`, Found via `alert:linkedin`). A pasted Easy Apply job
gets its own resume and sheet the same way (`events.py diy-add`, numbered
from E51). In the single-job flow, write every answer into `filled.md` for
the user to paste and set the status to `Answers ready - fill by hand`.

**Alert emails** are read only for company, title and location, never
their links (discovery.md "Alert intake"); Indeed, Glassdoor and SimplyHired alerts
only when the user turned `[alerts] read_indeed_family` on, ZipRecruiter
only with `read_ziprecruiter`.
