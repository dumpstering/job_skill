# SmartRecruiters (lead-only)

Links on `jobs.smartrecruiters.com/<Company>/<id>-<slug>` or
`careers.smartrecruiters.com/<Company>`. Its posting API host
(`api.smartrecruiters.com`) disallows every crawler except one in
robots.txt, so the runner never calls it, not even for one posting, and
never lists a SmartRecruiters board.

**How postings get in.** One at a time, as leads: an alert card or a web
search ("<company> <title> careers") gives the posting URL on
`jobs.smartrecruiters.com`, and `posting.py` reads that page's JobPosting
data when that host's robots.txt allows it. Otherwise it prints `ROBOTS
<host>` (exit 3) and `NEXT`: read the page text in the tab already open for
applying, save it, and run `posting.py --html-file`.

**The form.** Usually one page with an "apply with" choice: pick the plain
form with a resume upload, never an "apply with" a job-board account.
Attach first and check every parsed field. Most employers need no account;
when one is asked for, it is the user's.
