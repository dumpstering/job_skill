# Job sources: board readers and aggregator lanes

The runner reads employer boards (`scripts/boards.py`) and aggregators (`scripts/aggregators.py`). Both go through `netfetch` only: host allowlist, https, robots.txt, one request per second per host, 20 MB, 20 s per socket read and 60 s per fetch. Nothing here runs in the cloud sandbox unless the selftest passed (mode S).

## Watch-list rows

`config/watchlist.csv` and `state/watchlist_auto.csv` share the 2.5 columns. A row names one board through `ats`, `board`, `site`, `host` and `extra`:

| ats | board | site | host | extra | List call | Date used |
|---|---|---|---|---|---|---|
| greenhouse | board token | | | | GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true; detail .../jobs/{id}?questions=true | detail `first_published`; list value provisional, then `updated_at` |
| lever, lever_eu | site slug | | | | GET api(.eu).lever.co/v0/postings/{board}?mode=json&skip=&limit=100 | `createdAt` |
| ashby | board name (case-sensitive) | | | | GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true | `publishedAt` |
| workday | tenant | site from the real careers URL | `{tenant}.wdN.myworkdayjobs.com` (or `wdN`, or `wdN.myworkdaysite.com`) | `search=` text, `mode=sitemap` | POST .../wday/cxs/{tenant}/{site}/jobs, 20 a page; detail GET .../wday/cxs/{tenant}/{site}{externalPath} | detail `startDate`, never `postedOn` |
| bamboohr | tenant | | | | GET {board}.bamboohr.com/careers/list; detail /careers/{id}/detail | detail `datePosted` |
| breezy | slug | | | | GET {board}.breezy.hr/json; detail = position page JSON-LD | `published_date` |
| workable | account slug | | | | GET apply.workable.com/api/v1/widget/accounts/{board}?details=true | `published_on` |
| rippling | board slug | | | | GET api.rippling.com/platform/api/ats/v1/board/{board}/jobs; detail .../jobs/{uuid} | none (first_seen) |
| pinpoint | subdomain | | | | GET {board}.pinpointhq.com/postings.json | none (first_seen) |
| teamtailor | subdomain | | custom domain if any | | GET {host}/jobs.json, then ?offset=&per_page=100 | `datePosted` |
| personio | slug | | `{slug}.jobs.personio.com` if not .de | | GET {host}/xml?language=en | `createdAt` |
| recruitee | slug | | | | GET {board}.recruitee.com/api/offers/ (poll runs it only after the selftest passes) | `published_at` |
| adp | cid | ccId | | | GET workforcenow.adp.com/.../job-requisitions?cid=&ccId=&$top=20&$skip=; detail .../job-requisitions/{itemID} | `postDate` |
| oracle | host prefix (or a short name) | siteNumber | full host, e.g. `x.fa.us2.oraclecloud.com` | `facet=` location facet id | GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?...finder=findReqs;siteNumber=..,limit=200; detail recruitingCEJobRequisitionDetails | list `PostedDate` |
| paylocity | board guid | company slug | | | GET recruiting.paylocity.com/recruiting/jobs/All/{guid}/{site} (window.pageData.Jobs); detail page JSON-LD, one at a time | `PublishedDate` |
| dayforce | client | job board code | | | GET board page, GET /api/auth/csrf, POST /api/geo/{client}/jobposting/search (cookies, X-CSRF-TOKEN), 25 a page | `postingStartTimestampUTC` |
| jobvite | tenant | | | | GET jobs.jobvite.com/{board}/jobs (links); detail page JSON-LD | `datePosted` |
| jazzhr | subdomain | | | | GET {board}.applytojob.com/apply/jobs (links); detail page JSON-LD | `datePosted` |
| phenom | short name | | careers host | | GET {host}/sitemap.xml (index, then job sitemaps); at most 200 job URLs a day, newest first; detail page JSON-LD | `datePosted` (lastmod provisional) |
| comeet | company slug | board id (e.g. 1B.00E) | | | GET www.comeet.com/jobs/{board}/{site} (COMPANY_POSITIONS_DATA) | none (first_seen) |
| manatal | slug | | | | GET www.careers-page.com/api/v1.0/c/{board}/jobs/ (follows `next`) | none (first_seen) |
| hireology | slug | | | | GET api.hireology.com/v2/public/careers/{board}?page_size=500 | `created_at` |

Lead-only ATSs never get a list call, whatever the row's status: smartrecruiters, icims, ukg, paycom, clearcompany, paycor, neogov, and other. Their postings arrive as leads and are read one at a time (spec 3.3). Rows with no usable endpoint carry `status=paused` or `lead_only` and a note.

Rules every reader follows:
- IDs are deduped across pages. A walk stops on an empty page, a page with no new IDs (`seen-ids`), the reported total (`total`), or 10 pages per board per run (`page-cap`).
- Workday and Oracle are read whole and the gates filter titles; they are never searched by family terms (Workday ANDs every word of one searchText, so a family's terms joined found almost nothing).
- Workday: one unfiltered call gives the tenant's size and its location facets. A tenant of 20 jobs or fewer is done. Otherwise the walk runs inside the facet values in the home area: a value naming a `[home]` place with no other state or country beside it ('Brisbane - CA - US' yes, 'Brisbane, Australia' and 'Richmond - VA - US' no), else a `[home] states` value itself ('California'), plus remote values within the US when remote counts. Only the facet parameter with the most matches is used (Workday ANDs parameters). Such a walk may take 30 pages. Facets that name states or countries but none at home: no walk (`no-local`, the board shows EMPTY). No location facet at all: the whole tenant when it fits the 10-page cap, else one search per title term, at most 2 pages each, under the 10-page cap, starting at a different term each day (`search-cap`). A row's `search=` text is sent with every call. Workday stops at offset >= total; a capped tenant reports exactly 2000 and wraps to page 0 past it, which the seen-ID guard catches.
- Oracle: one walk newest first (`sortBy=POSTING_DATES_DESC`), no keyword, stopping at a page whose last posting is over 45 days old (`stale`).
- The resolver's title lookups on a known board (`searches`) are sent as given.
- ADP never stops on a short page; a page can hold 19 of 20 with more to come.
- An empty board is `EMPTY` (unknown), never gone. Gone markers: a 404/410 on the list, a final URL off the tenant host (BambooHR to www.bamboohr.com, JazzHR to jazzhr.com, Jobvite to its support page), or an inactive career page.
- A detail call is skipped for any record the runner's list gates drop. A posting counts as closed only on an explicit body marker: a Greenhouse error body, Workday `canApply` false, an empty Oracle detail.

## Commands

```
python boards.py fetch --board <ats>:<board>[/<site>][@<host>] [--extra k=v;k=v] [--root R] [--offline DIR] [--out F.jsonl] [--list-only] [--max-pages N]
python boards.py poll --root R [--offline DIR] [--out F.jsonl] [--list-only]
python aggregators.py run --root R [--offline DIR] [--keys PATH] [--out-postings F.jsonl] [--out-leads F.jsonl]
```

Output lines:
- `BOARD <ats>:<board> OK <n> postings pages=<p> stop=<why>`, `EMPTY pages=<p>`, `GONE <why>`, `ERROR <why>`, `DENY <reason>`, `SKIP <why>`.
- `WARN detail <url>: <why>` when one detail call failed (the list record is kept).
- `POLL ok=.. gone=.. skip=..` after a poll.
- `SOURCE <name> OK <n> postings|leads requests=<k>`, `OFF`, `NOKEY`, `ERROR <why>`, `DENY <reason>`.

Exit codes: 0 ok (fetch: OK or EMPTY), 1 bad input, 3 GONE or ERROR, 7 DENY.

JSONL lines are `jobfs.Posting` dicts plus `ai_text` and `hidden` lists (G11 reads them).

## Board health

`state/board_health.csv`: `key,status,last_ok,last_count,last_error,consecutive_errors,error_day,empty_runs`. The key is `ats:board/site@host`.
- OK or EMPTY: active, errors reset.
- 3 errors in a row: paused. Errors count once a day, so a rerun the same morning is not another strike. A run in which no board answered at all (the PC had no network yet) counts none and prints `WARN offline: no board answered (<n> tried); board errors not counted today`. A robots disallow: robots_blocked. A Crawl-delay that is not a number from 0 to 10 seconds: that run prints `BOARD <ats>:<board> SKIP crawl-delay <host>`, writes `skip: crawl-delay <host> ...` in `last_error`, and counts no strike.
- One odd answer (a null row, a number where a list belongs, JSON nested thousands deep) is that board's `ERROR` line naming the error type, or a skipped row; the poll always finishes.
- A Workday tenant that lists 0 jobs even unfiltered on 3 polls (3 days): check_site. Its `site` in the watch list is probably wrong; open the employer's careers page and copy the site from the real Workday URL. A tenant with jobs but none at home is healthy.
- A gone marker twice in a row: gone (never polled again until the row is edited).
- Paused, robots_blocked, check_site and dormant boards are retried on `[schedule] weekly_day`.

`poll` also skips Dayforce rows when `[sources] dayforce = false`, and Recruitee rows until `runner/out/status.json` has `selftest.recruitee = "PASS"`.

## Aggregator lanes

| Source | Setting | Output | Request |
|---|---|---|---|
| USAJOBS | `sources.usajobs` (on once keyed) | direct postings, lane `usajobs` | GET data.usajobs.gov/api/search?Keyword=&LocationName=&Radius=&DatePosted=3&ResultsPerPage=500, headers Host, User-Agent = the user's key email, Authorization-Key |
| Adzuna | `sources.adzuna` (on once keyed) | leads only | GET api.adzuna.com/v1/api/jobs/us/search/1?what_or=&where=&distance=&max_days_old=3&sort_by=date |
| Workable search | `sources.workable_search` (off) | postings, lane `workable_search` | GET jobs.workable.com/api/v1/jobs?query=&location= |
| The Muse | `sources.muse` (off) | leads only, 14 days at most | GET www.themuse.com/api/public/jobs?page=0&descending=true&location= |

- One query per active family (at most 4): USAJOBS its first core title (usajobs-mode families first), Adzuna `what_or` its words. (Workday and Oracle boards are walked, not searched; above.)
- Adzuna `redirect_url` is never requested. Lead lines carry company, title, location and seen only, and pass the 2.5 lead validation: no URLs or instruction-like text, length limits.
- Keys live only in `%LOCALAPPDATA%\JobRunner\keys.toml` (`[usajobs] email, key`; `[adzuna] app_id, app_key`; `[muse] api_key`). They are never printed.

## Presets

`templates/watchlist.csv` and `templates/boards.csv` hold the header only. `templates/presets/lifesci_bayarea/` holds the research-run-2 lists for San Francisco Bay Area life-science employers and boards. Setup offers it when the field and region match. `ref` points into the research files (`employers#<n>`, finding IDs).

A row's `city` is the employer's main site. G3 uses it when a posting names an office or lab instead of a place ('MBC Lab', a street address, the employer's own name): that posting counts as being in the row's city, and as unknown when the row has no city. Rows in `second_ring` places (Novato, San Rafael) are polled every day; their postings wait until the user widens (discovery.md).
