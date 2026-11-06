# iCIMS (lead-only)

Links on `careers-<company>.icims.com/jobs/<id>/<slug>/job` or an employer
site that frames one. iCIMS tenant hosts disallow all crawling in
robots.txt, so the runner never fetches them: no list, no single posting.

**How postings get in.** As leads (alert cards, web search). The item is
queued with `posting_status = browser_read`: read the posting's text in the
tab already open for applying, save it, and run `posting.py --html-file`;
the Tier B packet is then built in the sandbox, or by a `build` request if
pip fails there. Many iCIMS hosts are gone (404) or redirect elsewhere; an
item whose page does not load is `closed`.

**The form.** One account per employer (the user creates it). The flow is
several steps: an email or sign-in step, a resume upload that parses into
the profile (check every parsed field), questions, and EEO pages. Fill one
step at a time and read each step's own error messages. Stop at the review
step, before Submit.
