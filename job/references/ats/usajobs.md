# USAJOBS (federal jobs)

Links on `www.usajobs.gov/job/<id>` (the announcement) and the agency's
application system it hands off to. Always Tier A, at most
`[daily] usajobs_per_day` (1) a day.

**How the runner reads it.** The USAJOBS Search API, only once the user
entered a key at the installer prompt (`[sources] usajobs = true`). Every
request sends the key's email as its User-Agent, as USAJOBS requires; that
email is the only personal value the runner ever sends.

**The resume.** Mode `usajobs`: a hard 2-page cap. USAJOBS rejects resumes
over two pages for most competitive and excepted service announcements, and
an applicant whose only resume is longer is found ineligible. Build with
`--mode usajobs --strict`; a third page is a STRICT-FAIL, never sent. A CV
goes in only when the announcement asks for it as an "other document".

**The application.**
- Sign-in is through Login.gov with its own multi-factor step: the user's.
- Pick the 2-page resume in USAJOBS, then the agency's questionnaire
  (often self-ratings and short essays). Self-ratings are answered only from
  sources; an essay is drafted under the Truthfulness rules and shown for
  the user's ok. The essay count and length are UNVERIFIED.
- Stop at the agency system's final review, before Submit.
- Federal format fields beyond the 2-page resume (hours per week, salary per
  role, supervisor contact) are UNVERIFIED; ask when a form wants one.
