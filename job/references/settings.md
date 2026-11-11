# Settings and the watch list

## /job settings

List the switches below in plain words, each with its current value from
`config/settings.toml` (a missing key has the default shown). The user says
the change in their own words. Back up the file
(`backups/settings_<YYYY-MM-DD_HHMM>.toml`), edit only that key, keep the
file valid TOML, and confirm in one line ("Done: 25 a day from tomorrow.").
The runner reads the file at its next run; a file that stops parsing makes it
keep the last good copy and say so in `runner/out/status.json`.

| The user says | Setting | Default | Note |
|---|---|---|---|
| "aim for 25 a day" | `[daily] target` | 20 | the list holds 1.5 times this (`queue_factor`) |
| "fewer full tailorings" / "6 full tailorings" | `[daily] tier_a_per_day` | 4 | 3-5 |
| "breaks every 8" | `[daily] batch_size` | 10 | applications per task |
| "fewer new accounts" | `[daily] new_account_tenants_per_day` | 3 | |
| "fewer questions" | `[daily] questions_per_day` | 6 | |
| "read my Indeed alerts" | `[alerts] read_indeed_family` | false | first quote the notice below |
| "read my ZipRecruiter alerts" | `[alerts] read_ziprecruiter` | false | its links carry sign-in tokens; still never opened |
| "stop reading LinkedIn alerts" | `[alerts] read_linkedin` | true | with `senders` empty too, the card's alert line stops |
| "apply on LinkedIn for me" / "stop using LinkedIn" | `[linkedin] browser` | false | on: first quote the LinkedIn notice below, and only a clear yes counts; off: at once, no question |
| "15 LinkedIn applications a day" | `[linkedin] per_day` | 25 | never over 50 |
| "go slower on LinkedIn" | `[linkedin] min_seconds_between_actions` | 4 | seconds between two actions there; never under 4 |
| "ask me before stopping on a LinkedIn warning" | `[linkedin] stop_on_warning` | true | a CAPTCHA, sign-in check, unusual-activity or limit notice ends LinkedIn for the day whatever this says (below) |
| "use Adzuna" / "use USAJOBS" | `[sources] adzuna` / `usajobs` | false | keys only through the installer's prompt (rerun `install_runner.cmd`: it asks only for a key not entered yet), never in chat |
| "search Workable" / "use The Muse" | `[sources] workable_search` / `muse` | false | their terms are unknown; say so before turning one on |
| "stop reading Dayforce boards" | `[sources] dayforce` | true | |
| "stop referral drafts" / "5 asks a day" | `[referrals] enabled` / `asks_per_day` | true / 3 | 3-5 |
| "hold jobs I asked about for 2 days" | `[referrals] hold_days` | 0 | 1-2; only the job whose ask was sent waits |
| "use Chrome" / "use the built-in browser" | `[apply] browser` | auto | auto, builtin or chrome |
| "don't try uploading in Chrome" | `[apply] try_chrome_upload` | true | |
| "confirm salary once a day" | `[apply] sensitive_confirm` | per_job | `daily` is one ok for the day |
| "try the 5-tab mode" | `[apply] fill_mode` | sequential | `rounds` only after the supervised test passed |
| "I'm claiming unemployment in California" | `[worksearch] claiming`, `state`, `claim_effective`, `de8405_received` | false | one follow-up question ([worksearch.md](worksearch.md)) |
| "my time zone is Eastern" | `[time] timezone` | America/Los_Angeles | an IANA name, e.g. America/New_York |
| "weekly summary on Fridays" | `[schedule] weekly_day` | monday | |
| "I have 6 years of experience" / "too many senior jobs" | `[experience] years` / `industry_years` | blank | blank counts the library's role headings; jobs clearly beyond these (principal level, or 5+ more years) are dropped, and ones that ask a bit more come last |
| "no Workday today" | none | | a one-day skip of the account block, kept in the session (apply_batch.md) |
| "more to one company" / "fewer to one company" | `[caps] per_company_30d` | 5 | in any 30 days; no source gives a number, so this is the user's call |
| "more of one kind of role at a company" | `[caps] per_family_per_company_30d` | 2 | in any 30 days |
| "more to one lab" / "fewer to one university" | `[caps] per_unit_30d` / `per_institution_30d` | 2 / 15 | universities, institutes, hospitals and government labs (below) |

The other `[caps]` keys change only when the user asks by name: 90 days
after a rejection before the same job again, one open Ashby application per
employer, 2 a day per company, and `[[caps.employer]]` rules such as
Google's 3 in 30 days (its published rule). An employer whose watch-list
`segment` is one of `unit_segments` (academic, institute, hospital,
government) counts per hiring unit: the lab or department a posting names
(a lab in the title, else the lab, division, department or center its text
starts with), at most `per_unit_30d` per unit and `per_institution_30d` for
the whole place; a posting that names no unit counts against the
institution at the company caps. `[runner] mode` is set by setup (see
[runner.md](runner.md)).

**The Indeed notice** (quote it before turning `read_indeed_family` on):
"Indeed's terms cover its emails as part of its site and ban AI or agentic
data mining. With this on I read only company, title and location from
Indeed, Glassdoor and SimplyHired alerts, never their links; you decide
whether that is acceptable." Indeed Apply is never automated either way.

**The LinkedIn notice** (quote it before turning `[linkedin] browser` on):
"LinkedIn's rules do not allow automated tools, and it can restrict an
account that uses them. With this on I work there slowly, in your own
Chrome, on job pages only, and you click Submit. Turn it on?" Only a clear
yes turns it on; anything else leaves it off. In plain words, the four
settings:
- `browser = false` (the default): nothing ever opens LinkedIn. Its jobs
  come as pasted text (`/job paste`) or the do-yourself list, as before.
- `browser = true`: `/job linkedin` works in the user's own signed-in
  Chrome ([linkedin_browser.md](linkedin_browser.md)): it searches the
  approved families' titles around home, reads each posting, passes over
  what the gates, the duplicate check and the caps drop, and fills Easy
  Apply up to Submit. Scripts and the job finder still never touch
  LinkedIn. Only the boolean `true` in a file that parses counts: a word in
  quotes or a file with a typo leaves it off.
- `per_day`: how many LinkedIn applications a day, sent and waiting for
  Submit together. A number over 50 is read as 50.
- `min_seconds_between_actions`: the wait after every click, search or
  typed field there. A number under 4 is read as 4.
- `stop_on_warning`: a CAPTCHA, a sign-in check, an unusual-activity notice
  or a limit notice always ends LinkedIn for the day (the card says so, and
  it is never retried that day). This switch is for anything else that
  reads as a warning: `true` ends the day on that too; `false` stops and
  asks the user first.

**The search profile** (`config/search_profile.toml`: families, places,
knockouts, the widen ladder) changes the same way, backup first. After a
gate change say which gate it was; the next run re-judges the discards it
affected ([discovery.md](discovery.md)).

## /job watch <company or careers URL>

Adds an employer's board to `config/watchlist.csv` (columns in
[sources.md](sources.md)). Never a job board: a linkedin.com, lnkd.in, licdn.com,
indeed.*, glassdoor.*, simplyhired.*, ziprecruiter.* or remotive.com link is refused.

1. **Find the careers page.** With a URL, use it. With a company name, look
   first in `state/watchlist_auto.csv` (the runner adds every employer it
   resolves): a row there is copied to `config/watchlist.csv` with
   `added_by` = `user` and `status` = `active`, and you are done. Otherwise
   run one web search, "<company> careers", and keep only a result on a
   known ATS host or the company's own domain.
2. **Detect the board.** Mode L or S: write `{"url": "<careers URL>",
   "company": "<Company>"}` to `claude/args.json` and run `jobrunner.py
   request probe_url --json-file claude/args.json` (SKILL.md "Scripts"),
   then read
   `runner/out/results/<id>.json` in about 20 minutes (the next tick hands
   `probe_url` to the daily):
   - `DETECT <ats>:<board>[/<site>][@<host>]`: write that row.
   - `BROWSER_READ <url>` or a lead-only ATS (SmartRecruiters, iCIMS, UKG,
     Paycom, Paycor, ClearCompany): write the row with `status` = `lead_only`;
     its postings come one at a time.
   - `DETECT none <host>`: say the board was not found and ask for the link
     of one of their postings.
   - Mode M: read the ATS from the URL itself (`boards.greenhouse.io/<token>`,
     `jobs.lever.co/<site>`, `jobs.ashbyhq.com/<name>`,
     `<tenant>.wdN.myworkdayjobs.com/<site>`); the runner uses the row once
     installed.
3. **Write the row** (backup first): `employer`, `ats`, `board`, `host`,
   `site` and `extra` as detected (a Workday `site` only from the real URL),
   `careers_url`, `segment` (biotech, pharma, diagnostics, academic,
   institute, government, hospital, cro or other; ask if unclear),
   `priority` 2 unless the user says otherwise, `status` active, `added_on`
   today, `added_by` user. Leave `families` blank (all families).
4. Confirm in one line: "Watching <Company> (<ATS>) from tomorrow's run."

Rows are never deleted. To stop watching one, set `status` to `paused`.
