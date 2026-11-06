# Discovery: alert intake, the lead resolver, gates and the queue

The runner finds postings on the user's PC (boards, aggregators), Claude adds leads from alert emails, and the runner turns everything into one gated pool and a daily queue. This file covers Claude's part and how to read what the runner wrote. Board readers are in `sources.md`.

## Alert intake (job-morning; spec 3.5)

1. Search Gmail read-only with `newer_than:2d` (or SINCE from `tracker.py inbox-plan` after a gap):
   - LinkedIn: `from:(jobalerts-noreply@linkedin.com OR jobs-noreply@linkedin.com OR jobs-listings@linkedin.com)`.
   - The exact senders in `config/settings.toml` `[alerts] senders`.
   - Indeed, Glassdoor and SimplyHired only when `read_indeed_family = true`; ZipRecruiter only when `read_ziprecruiter = true`.
2. Skip messages already logged (`tracker.py seen`, Kind alert).
3. For each card keep company, title and location only. Never open, store, echo or hyperlink a link, and never keep the job id, work mode, salary or labels. Write them to a scratch file in the sandbox:
   ```json
   {"messages": [{"id": "<gmail message id>", "cards": [{"company": "...", "title": "...", "location": "..."}]}]}
   ```
4. Validate and write the lead file (create-only; a new HHMM for each run):
   ```
   python scripts/leads.py validate --source linkedin_alert --in <scratch> --root <JobSearch> --out <JobSearch>/runner/in/leads/<YYYY-MM-DD>_<HHMM>_linkedin.jsonl
   ```
   - `--source` is `linkedin_alert`, or `<board>_alert` for a confirmed board sender.
   - It prints `DROP <reason> msg=<ref> card=<i>` per dropped card, then `LEADS kept=<n> dropped=<n>` and `WROTE <file>`.
   - `REFUSED linkedin.com in input` (exit 1): a link slipped into the scratch file. Rebuild it from the three fields only.
   - `PARSE_FAILURE <msg_ref>` (exit 9): that email gave zero cards; the layout may have changed. Mention it once; the watch list keeps the queue full.
5. Log each email with a `tracker` event (`log-email`, class `Alert`, `--kind alert`).
6. Send one `resolve_leads` request (`runner/in/requests/<id>_resolve_leads.json`, `"args": {}`).

## The resolver (runner; spec 3.6)

`resolve.py run` reads every lead file it has not finished. Repeated cards collapse on company + title + city. Per lead:
1. Known board (watch lists, earlier resolutions in `state/company_ats.json`): it matches today's list, or reads that board once. A match is a title at least 85% alike and a compatible location (same city, a `[home]` place, or remote-US). Each match is its own posting.
2. Unknown company: it probes slug guesses on Greenhouse, Lever, Ashby, Workable, BambooHR, Breezy, JazzHR, Pinpoint, Personio, Teamtailor, Rippling, Recruitee (after its selftest) and Jobvite, in that order. Misses are cached 30 days; an empty board is unknown. A hit joins `state/watchlist_auto.csv`.
3. Still unknown, or an R3 lead-only employer: listed in `runner/out/needs_search.json`.
4. Unresolved on 2 different days: LinkedIn leads go to the do-yourself list (`queue/<date>/diy.md`, at most `easy_apply_list_max`, company, title and location only); other leads are discarded with reason `unresolved`.

### needs_search.json (job-morning, or the session at a batch break)

`{"v":1,"date":"...","companies":[{"company","company_norm","why":"unknown|lead_only","titles":[...],"leads":n}]}`, at most 10 a day.
- Run one web search per company: `"<company> careers"` (for `lead_only`, `"<company> <title> careers"` to find the single posting).
- Keep only a known ATS host or the company's own domain. Drop results on linkedin.com, indeed.*, glassdoor.*, simplyhired.* or ziprecruiter.com without opening them.
- Send a `probe_url` request: `"args": {"url": "<careers page or posting URL>", "company": "...", "title": "...", "location": "..."}`.

The runner then runs `resolve.py probe-url`:
- `DENY job-board <domain>` (exit 7): refused unopened.
- `DETECT <ats>:<board>[/<site>][@<host>]`: found on the careers page or its iframe; the board joins `watchlist_auto.csv`. Workday sites always come from the real URL.
- `READ <job key>`: a posting URL on an ATS host, read directly.
- `BROWSER_READ <url>`: robots.txt disallows that page (iCIMS hosts always do). The item is queued with `posting_status = browser_read`; read the page text in the tab already open for applying and run `posting.py --html-file`.
- `DETECT none <host>`: nothing found; try a better search result tomorrow.

## The gates (runner; spec 3.8)

Cheapest first; a list record that fails G1-G5 never gets a detail call.

| Gate | Fails when | Discard reason |
|---|---|---|
| G1 host | job-board URL; robots.txt disallows the page (R3 hosts go lead-only) | `host:<domain>`, `robots:<host>` |
| G2 seen | already pooled, queued, deferred, applied, closed, or the same job by another key | (skipped, no log) |
| G3 location | not in `[home] places` (plus `second_ring` when widened), not remote-US | `location` |
| G4 title | no family title match, a `title_none` term, or an `exclude_title_words` word | `title` |
| G5 age | over 30 days by its source date, not a repost | `stale` |
| G6 closed | the board's closed marker | `closed` |
| G7 knockout | a `[[knockout]]` title or required pattern not held in profile.md `Licenses held:` | `knockout:<id>` |
| G8 knockout | no sponsorship (or US citizenship) against profile.md | `knockout:sponsorship`, `knockout:citizenship` |
| G9 title | no `require_any` word in the description | `title` |
| G10 location | remote but not open to the US | `location` |
| G11 AI text | text addressed to an AI | parked as needs-you (never queued) |
| G12 apply | no apply URL or a job-board apply URL; chat apply goes to do-yourself | `apply_route` |

- A `preferred_patterns` match only flags the item (`license_preferred:<id>`) and lowers fit.
- Every discard is one line in `queue/<date>/discards.csv`: `date,lane,company,role,location,host,job_key,gate,reason,profile_hash`.
- Location, title, stale and knockout discards carry `profile_hash`; after search_profile.toml, the licenses held or the widen steps change, they are judged again on the next run.
- `python scripts/gates.py explain --root <JobSearch> --job-key <key>` prints one posting's trail, `GATE G1 ok ...` through the gate that stopped it, for example `GATE G7 knockout:ca_cls required "CLS license required"`. Use it when the user asks why a job was not queued.
- Parked AI-text postings are in `state/pool.json` `needs_you` (`job_key, company, role, quote`); show each once.

### Title terms in search_profile.toml

Whole words of the title, ignoring case and punctuation: `capa` never matches "Capacity". `microbiolog*` is a stem. `qc+micro*` needs both parts. `lecturer|instructor+biolog*` is either word plus a biology word. An exclude word in capitals of three letters or fewer (`IT`, `HR`, `VP`) matches only the title's own capitals. Families are tried in file order and the first that matches wins; for academic, institute and government employers a `cv` family goes first, and `usajobs` families take USAJOBS postings only. Status `proposed` families join when the widen step `adjacent_families` is on.

To loosen a gate the user agreed to change, edit search_profile.toml (backup first) and say which gate it was; the next run re-judges the affected discards.

## The queue (runner; spec 3.12)

`jobqueue.py build` writes `queue/<date>/queue.json` (2.5 schema; immutable once that day has events):
```
QUEUE size=30 pool_left=41 new_account_tenants<=3 account_required<=6 usajobs<=1
MIX tier_a=4 new_account_tenants=2 account_required=5 usajobs=1 deferred=6
WIDEN adjacent_families
```
- `score = fresh + 30*fit + 20*referral + priority + family_w - account cost - 10 for a company applied to in 30 days`; `score_parts` holds each term.
- Caps defer, never discard: a deferred item keeps `deferred_until` in the pool.
- Order: #1-#10 no-account items, then the account block (`account_block.after_n`, `tenants`), then the rest. Never the same employer twice in a row, at most 3 in a row on one ATS host.
- Until a family's variant is approved its items can only be Tier A.
- `FROZEN` means events exist for that date (late finds go to an addendum). `WAIT` means a session is active.
- `WIDEN <step>`: the pool was under target, so one widen step is now on (state/queue_state.json). Tell the user in the weekly summary, not the card.

Other commands (runner):
- `jobqueue.py addendum` appends new numbered items to `addendum_<HHMM>.json`; `more` is the same with the next `more_size` items from the pool.
- `jobqueue.py stage-uploads` copies built resumes to `upload/NN_<Last>_Resume_<Employer12>.pdf` and moves applied and earlier days' files to `upload/_done/<date>/`.
- `jobqueue.py fold-events` folds the day's queue events into `state/queue_state.json`.

## Files

| Path | Writer | Holds |
|---|---|---|
| `config/lead_salt.txt` | leads.py (first use) | the local salt for lead ids |
| `runner/in/leads/<date>_<HHMM>_<source>.jsonl` | Claude via leads.py | lead lines |
| `runner/out/needs_search.json` | runner | companies for one web search each |
| `state/leads_state.json`, `company_ats.json`, `probe_cache.csv`, `watchlist_auto.csv` | runner | lead progress, resolved boards, probe misses, new boards |
| `state/seen/<YYYY-MM>.jsonl`, `trails/<YYYY-MM>.jsonl`, `board_polls.json`, `pool.json` | runner | seen store, gate trails, undated-board polls, the pool |
| `state/queue_state.json`, `upload_manifest.json` | runner | folded queue events and widen steps; today's upload files |
| `queue/<date>/queue.json`, `addendum_*.json`, `discards.csv`, `diy.md` | runner | the day's queue, late items, discards, do-yourself list |
