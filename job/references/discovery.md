# Discovery: alert intake, the lead resolver, gates and the queue

The runner finds postings on the user's PC (boards, aggregators), Claude adds leads from alert emails, and the runner turns everything into one gated pool and a daily queue. This file covers Claude's part and how to read what the runner wrote. Board readers are in `sources.md`.

Run every command as SKILL.md "Scripts" says: `python "<skill>/scripts/<name>.py" ... --root "<folder>"`, every path in double quotes (a JobSearch path can hold spaces), and outside text only in the args file.

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
   python "<skill>/scripts/leads.py" validate --source linkedin_alert --in "<scratch>" --root "<folder>" --out "<folder>/runner/in/leads/<YYYY-MM-DD>_<HHMM>_linkedin.jsonl"
   ```
   - `--source` is `linkedin_alert`, or `<board>_alert` for a confirmed board sender.
   - It prints `DROP <reason> msg=<ref> card=<i>` per dropped card, then `LEADS kept=<n> dropped=<n>` and `WROTE <file>`.
   - `REFUSED linkedin.com in input` (exit 1): a link slipped into the scratch file. Rebuild it from the three fields only.
   - `PARSE_FAILURE <msg_ref>` (exit 9): that email gave zero cards; the layout may have changed. Mention it once; the watch list keeps the queue full.
5. Log each email with a `tracker` event (`log-email`, class `Alert`, `--kind alert`), its sender and subject in the args file as in inbox.md step 4, never inline.
6. Send one `resolve_leads` request (`runner/in/requests/<id>_resolve_leads.json`, `"args": {}`).

## The resolver (runner; spec 3.6)

`resolve.py run` reads every lead file it has not finished. Repeated cards collapse on company + title + city. Per lead:
1. Known board (watch lists, earlier resolutions in `state/company_ats.json`): it matches today's list, or reads that board once. A match is a title at least 85% alike and a compatible location (same city, a `[home]` place, or remote-US). Each match is its own posting.
2. Unknown company: it probes slug guesses on Greenhouse, Lever, Ashby, Workable, BambooHR, Breezy, JazzHR, Pinpoint, Personio, Teamtailor, Rippling, Recruitee (after its selftest) and Jobvite, in that order. Misses are cached 30 days; an empty board is unknown. A hit joins `state/watchlist_auto.csv`.
3. Still unknown, or an R3 lead-only employer: listed in `runner/out/needs_search.json`.
4. Unresolved on 2 different days: LinkedIn leads go to the do-yourself list (company, title and location only). The list has one order everywhere (`today_view.py diy`, the runner's staging and E numbers, and `queue/<date>/diy.md`): leads with status diy that pass the title and place gates a read posting would (G3, G4, the G7 title knockouts), the better family match first and then the newest, then chat-apply pool items by fit; at most `easy_apply_list_max`, then `DIY MORE <n>` for the rest (`diy all` shows them: `today_view.py diy --all`) and `DIY SET-ASIDE <n>` for leads the gates would not take, which are never listed. Other leads are discarded with reason `unresolved`. A lead that step 3 lists waits until `needs_search.json` has listed it on 2 different days, so a job-morning (or a batch break) had it to search; a lead still pending 30 days after it was seen gives up the same way (`UNRESOLVED <id> <company> day=<n> expired`), never silently.

### needs_search.json (job-morning, or the session at a batch break)

`{"v":1,"date":"...","companies":[{"company","company_norm","why":"unknown|lead_only","titles":[...],"leads":n}]}`, at most 10 a day.
- Run one web search per company: `"<company> <title> careers"`, with one of its `titles`, and send a job page from the results (the posting itself, on the company's own domain or a known ATS host). A job page names the employer's board in its apply link; a careers landing page often names none.
- Keep only a known ATS host or the company's own domain. Drop results on linkedin.com, lnkd.in, licdn.com, indeed.*, glassdoor.*, simplyhired.*, ziprecruiter.* or remotive.com without opening them.
- Send a `probe_url` request: write `{"url": "<careers page or posting URL>", "company": "...", "title": "...", "location": "..."}` to `claude/args_morning.json` (a session: `claude/args.json`) and run `jobrunner.py request probe_url --json-file claude/args_morning.json`; a URL from a search result never goes on the command line.

The runner then runs `resolve.py probe-url`:
- `DENY job-board <domain>` (exit 7): refused unopened. Any other `DENY <reason>` (`bad-url ...`, `non-public <host>`) is the same: that link is never opened or fetched by any tool, the browser included; try a better search result tomorrow.
- `DETECT <ats>:<board>[/<site>][@<host>]`: found on the careers page or its iframe; the board joins `watchlist_auto.csv`. Workday sites always come from the real URL.
- `PHENOM <url>` before a `DETECT` line: the page was a Phenom career site with no board link (Sutter Health, Eli Lilly), so the runner read that site's own job search, whose apply links name the board (usually a Workday tenant).
- `READ <job key>`: a posting URL on an ATS host, read directly.
- `BROWSER_READ <url>`: robots.txt disallows that page (iCIMS hosts always do), or asks for more than 10 s between requests. The item is queued with `posting_status = browser_read`; read the page text in the tab already open for applying and run `posting.py --html-file`.
- `DETECT none <host>`: nothing found on that page. On the next search for that company, send a job page for one of its titles (`"<company> <title> careers"`), never the same landing page again.

## The gates (runner; spec 3.8)

Cheapest first; a list record that fails G1-G5 never gets a detail call. A list record with no location (Jobvite, JazzHR), "N Locations" or only a country (Workday, ADP, Oracle), no title (Phenom) or no date (Workday) passes G3-G5 as unknown, and G3, G4 and G5 judge it again on the detail record (`GATE G3 judged again on the detail` in its trail).

| Gate | Fails when | Discard reason |
|---|---|---|
| G1 host | a posting or apply link on a job board (any spelling of one), one the runner refuses (`netfetch.web_refusal`: a lookalike host, userinfo, a backslash, another port, an IP address, localhost and the like), or not http(s) (a `file:` or `smb:` link); robots.txt disallows the page, or its robots.txt leads to a job board or a refused host (R3 hosts go lead-only) | `host:<domain>`, `host:bad-url`, `host:non-public`, `host:not-web`, `robots:<host>` |
| G2 seen | already pooled, queued, deferred, applied, closed, or the same job by another key | (skipped, no log) |
| G3 location | no listed place is a `[home]` place or remote within the US. Each place of a list ('Jersey City, NJ; Millbrae, CA') counts; a place name with another country beside it ('Dublin, Ireland'), or a state not in `[home] states` ('Newark, NJ', 'Dublin OH' when states is `["CA"]`), does not; a remote place outside the US ('TWN - Remote') or in another state ('Remote, Florida, USA') does not, nor does a remote list naming only other states, even beside 'Remote United States' (a regional field job). A `second_ring` place is out until widened. An office or lab label ('MBC Lab', a street address, the employer's own name) of a watch-listed employer counts as that row's `city`, and as unknown when the row has none | `location`; `location:second_ring` when title and age pass |
| G4 title | no family title match, a `title_none` term, or an `exclude_title_words` word | `title` |
| G5 age | over 30 days by its source date, not a repost | `stale` |
| G6 closed | the board's closed marker | `closed` |
| G7 knockout | a `[[knockout]]` title or required pattern not held in profile.md `Licenses held:` | `knockout:<id>` |
| G8 knockout | no sponsorship (or US citizenship) against profile.md | `knockout:sponsorship`, `knockout:citizenship` |
| G9 title | no `require_any` word in the requirements sections (Requirements, Basic and Preferred Qualifications, What you bring; the whole description when they are missing or under 200 characters), or at least 2 `avoid_any` words and more of them than `require_any` words (the job's core training is another field's: chemistry, formulation, biophysics, LC-MS, machine learning; for a postdoc, physics, electrochemistry, or energy or earth systems) | `title` |
| G9 seniority | clearly beyond the user, judged against their years and titles (settings.md `[experience]`, else the achievement library's role headings): a principal-level title two steps over theirs, unless the text asks a modest bar they meet, or 5 or more years over theirs. A job that asks a bit more (a higher title, more years, 4+ years of a field the library never names) passes as `borderline` and is queued after the rest. Industry families only | `seniority` |
| G10 location | remote but not open to the US | `location` |
| G11 AI text | text addressed to an AI | parked as needs-you (never queued) |
| G12 apply | no apply URL, or one G1 would refuse; chat apply goes to do-yourself. An http apply link from a board is opened as https | `apply_route` |

- A `preferred_patterns` match only flags the item (`license_preferred:<id>`) and lowers fit.
- A posting that fails to gate is skipped with one `ERROR gate <job key>: <error type>` line; the run goes on and the next run judges it again.
- `location:second_ring` discards are jobs set aside for their place alone. The weekly summary counts them in one line and says "widen"; when the user says widen, follow [weekly.md](weekly.md).
- Every discard is one line in `queue/<date>/discards.csv`: `date,lane,company,role,location,host,job_key,gate,reason,profile_hash`.
- Location, title, stale, knockout and seniority discards carry `profile_hash`; after search_profile.toml, the licenses held, the widen steps or the user's whole years or titles change, they are judged again on the next run.
- `python "<skill>/scripts/gates.py" explain --root "<folder>" --job-key <key>` prints one posting's trail, `GATE G1 ok ...` through the gate that stopped it, for example `GATE G7 knockout:ca_cls required "CLS license required"`. Use it when the user asks why a job was not queued.
- Parked AI-text postings are in `state/pool.json` `needs_you` (`job_key, company, role, quote`); show each once.

### Title terms in search_profile.toml

Whole words of the title, ignoring case and punctuation: `capa` never matches "Capacity". `microbiolog*` and `formulation*` are stems (`formulation*` matches "Formulations"; a bare `formulation` would not). `avoid_any` uses the same syntax on the requirements text. `qc+micro*` needs both parts. `lecturer|instructor+biolog*` is either word plus a biology word. An exclude word in capitals of three letters or fewer (`IT`, `HR`, `VP`) matches only the title's own capitals, never right after a number ('08-HR' is a shift length). Of the families that match, the closest core title wins (an exact one first: a bare 'Research Associate' is the research associate family's), then file order; for academic, institute and government employers a `cv` family goes first, and `usajobs` families take USAJOBS postings only. Status `proposed` families join when the widen step `adjacent_families` is on.

To loosen a gate the user agreed to change, edit search_profile.toml (backup first) and say which gate it was; the next run re-judges the affected discards.

## The queue (runner; spec 3.12)

`jobqueue.py build` writes `queue/<date>/queue.json` (2.5 schema; immutable once that day has events):
```
QUEUE size=30 pool_left=41 new_account_tenants<=3 account_required<=6 usajobs<=1
MIX tier_a=4 new_account_tenants=2 account_required=5 usajobs=1 deferred=6
WIDEN adjacent_families
```
- `score = fresh + 30*fit + 20*referral + priority + family_w - account cost - 10 for a company applied to in 30 days`; `score_parts` holds each term. The score picks the day's items.
- `fit` counts the user's terms the posting names: each techniques.md technique (hands-on 1, familiar 0.5), each family term (1 when the achievement library names it, else 0.5), and half for a technique's general form named alone ('PCR' for 'real-time PCR'); 6 of them is a full overlap. Fit orders the day; it never decides what a resume may claim.
- Caps defer, never discard: a deferred item keeps `deferred_until` in the pool as a note, and every build checks it again (a slot only today's queue held is free tomorrow). Universities, institutes, hospitals and government labs count per lab or department (settings.md).
- Order, best first: a job handed over on an earlier day with no `s` first (`unsubmitted`), #1-#10 no-account items, then the account block (`account_block.after_n`, `tenants`: only a new-account employer whose best job is at or above the day's median fit; another one's jobs take their places by fit), then the rest, each by fit, then the `borderline` seniority jobs (G9), so a user who stops early has done the best matches. Never the same employer twice in a row, at most 3 in a row on one ATS host.
- Until a family's variant is approved its items can only be Tier A, at most `tier_a_per_day`; the rest fill free places last as `prep_failed` (`WAIT_VARIANT <family> <n> jobs`).
- `FROZEN` means events exist for that date (late finds go to an addendum). `WAIT` means a session is active.
- `WIDEN <step>`: fewer than `target` pool items pass the caps with a resume to send, so one widen step is now on (state/queue_state.json). Tell the user in the weekly summary, not the card.

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
| `queue/<date>/queue.json`, `addendum_*.json`, `discards.csv`, `diy.md` | runner | the day's queue, late items, discards, that day's new do-yourself give-ups |
