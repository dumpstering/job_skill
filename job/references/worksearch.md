# Work-search record (unemployment)

For `/job worksearch`, and for job-morning in a certification week. The
record is built from what the tracker already holds; the user types nothing twice.
Only when `[worksearch] claiming = true` in `config\settings.toml`.

**The user's part, always:** their name, SSN or claimant ID, and signature. The export
leaves those cells blank and flags them; you never ask for them in chat and
never write them anywhere.

## Setup (once)

- "I'm claiming unemployment in <state>": set `[worksearch] claiming = true`,
  `state = "<code>"`, `claim_effective = "<date>"` (backup first), and ask one
  follow-up: the date of the DE 8405 notice (California) or the state's
  job-bank notice, into `de8405_received`.
- Say the state's job-bank registration rule once, from `worksearch.py
  states` and the preset's `registration` value (California: CalJOBS within
  21 days of the DE 8405). The skill never creates that account; it needs the
  user's SSN.
- `job/templates/worksearch_states.toml` holds presets for CA, TX, FL, NY,
  GA, NC, OH, PA, AZ and IL. Each value names its research finding and says
  whether an official page confirmed it. Any other state gets the generic
  preset and a note to check the state's rules.

## The export

1. California certifies every two weeks. In a certification week (the
   Monday after the second claim week), offer it in one line.
2. Run `worksearch.py export --root "<folder>"` (defaults: the configured
   state and its period ending yesterday; or `--state CA --from <date> --to
   <date>`). It writes `worksearch\CA_<from>_<to>.csv`, `.xlsx` and `.md`:
   - every application with an Applied date, by-hand logs included, every
     referral ask the user sent, and every Activities row (job fair, workshop,
     RESEA);
   - the columns of spec 6.6, one row per contact, grouped by claim week;
   - employer address, phone and main website from the Employers sheet;
     `posting_url` and `employer_main_website` are separate columns (North
     Carolina accepts only the main website);
   - a `.md` block of the strongest rows per week, ready to type into the
     state portal or onto the form.
3. Show the user the `.md` and the `FLAG` lines. The files are their record; nothing
   is sent anywhere.
4. Earlier exports are never rewritten or deleted. A second export of the
   same period is written as `_v2`, `_v3`; `KEPT` lines name the earlier
   files. Rows and proof are kept at least 2 years.
5. When the user says they reported the weeks, `--mark` on the export records the
   date in each exported row's Work search column.

## Missing employer details

`worksearch.py employers --root "<folder>"` lists `MISSING <employer> |
street, city, zip, phone` lines. Ask the user once per employer, in one message
for all of them. A value you looked up (the company's own site) is shown to
the user to confirm before it is saved. Then `tracker.py employer-set --employer
"<name>" --street ... --city ... --state ... --zip ... --phone ...
--main-website ... --source user --confirmed-on <today>`.

## Weekly checks

`worksearch.py check --root "<folder>" --week <any day of the week>` prints
one `WEEK` line, then a `WARN` line for each rule the week does not meet:
- Georgia: 3 new employers, on 3 or more days
  (`WARN GA needs contacts on 3 or more days`); applying for a job the user is not
  qualified for does not count, and every queued job passed the fit gates.
- Arizona: contacts on 4 or more days.
- Florida: the same employer at the same location 3 weeks running does not
  count.
- Pennsylvania: 2 applications plus 1 activity; a repeat application to the
  same job does not count.
- Other states: the weekly minimum, where the preset has one.
A `WARN` is information for the user, said plainly in one line. Twenty
applications on one day can still leave a Georgia or Arizona week short of
days; a referral ask or an activity on another day fixes that.
