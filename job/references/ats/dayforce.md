# Dayforce

Links on `jobs.dayforcehcm.com/<locale>/<client>/<board>/jobs/<id>`.

**How the runner reads it.** For watch-listed Dayforce employers only
(`[sources] dayforce = true`): one GET of the board page, one GET of
`/api/auth/csrf`, then a POST search with that token and the session
cookie, 25 a page. The posted date is `postingStartTimestampUTC`. Dayforce's
robots.txt has no Disallow rules. No Dayforce employer has been tried live
by this skill yet.

**The form.**
- Usually one account per employer (the user creates it), then a resume
  upload that fills the profile. Check every parsed field.
- Several steps ending in a review page. Stop there, before Submit.
