# Oracle Recruiting Cloud (Candidate Experience)

Links on `<tenant>.fa.<region>.oraclecloud.com/hcmUI/CandidateExperience/...`,
often behind an employer careers domain. Taleo (`*.taleo.net`) is older and
different; treat it as an unknown ATS.

**How the runner reads it.** A public REST list,
`<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions` with
`finder=findReqs;siteNumber=<site>,limit=200,keyword=<family words>`, and a
detail resource with the description. The posted date is the list's
`PostedDate`. The detail field names have not been checked against a live
tenant (UNVERIFIED).

**The form.**
- Usually one account per employer, or an email plus a one-time code: the
  code is the user's, never read or typed by you.
- The resume upload parses into the profile; check every parsed field,
  dates especially.
- Steps end in a review page. Stop there, before Submit.
- Other tools route Oracle Cloud, Workday and Taleo to manual handling
  because sign-up automation locks accounts: never create or retry an
  account.
