# Workable

Links on `apply.workable.com/<account>/j/<id>` or `<account>.workable.com`.
No account for the applicant.

**How the runner reads it.** Per watched employer,
`apply.workable.com/api/v1/widget/accounts/<account>?details=true` gives
every job with its description; the posted date is `published_on`. Its
robots.txt carries Content-Signal `ai-input=yes, ai-train=no`, which is
recorded; nothing is trained on. Workable's cross-employer search
(`jobs.workable.com`) is off (`[sources] workable_search`): its terms are
unknown.

**The form.**
- One page: contact, resume, questions, sometimes a cover-letter box. Attach
  first; check the fields the parser filled.
- Workable tags applications sent with automated tools as "AI-assisted"
  from their metadata. Nothing is hidden about how the form was filled.
- A required cover letter comes from the family letter (Tier B) or a
  tailored one (Tier A), never a generic paragraph.
