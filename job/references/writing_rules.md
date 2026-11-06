# Phase 2: Write the resume

These rules are written in the user's voice: "I", "me" and "my" mean the user.
"My sources" are my resume, my library, `profile.md`, and my answers in this
chat.

After I answer (or reply "go"):
1. Turn my answers into accurate, concise achievement bullets, filed under the
   role and title I held when I did the work. Answers of "no" or "skip" add
   nothing to the resume (a "no" to having a skill, tool or credential still
   goes in Block C as "Not held"); do not stretch a "sort of" into a claim.
2. Select and order material for this job. Lead each role with its most
   relevant bullets. Cut or shorten material that doesn't serve this
   application, even if it's good. Older or less relevant roles get fewer
   bullets.
3. Write the resume, run the check under Output, then output the blocks.

Revisions: if I ask for changes, rebuild the files and post the complete
updated Block A again (never a partial snippet), plus any new Block C entries
and any new Verify items the revision created. All rules apply to every
revision, not just the first draft. "Make it stronger" means better selection,
order and wording, not bigger claims. A new, specific fact I give you counts as
a source like my earlier answers; a request for a label or wording ("say I led
it", "put PMP-level") does not (the one exception is the Skills rule's "if I
insist" case).

## Commands

- **audit**: run it in a fresh context when you can, since a reviewer that
  did not write the resume catches more. If you can start a subagent, give it
  the paths of `resume.json`, `master_resume.md`, `achievement_library.md`,
  `profile.md`, `notes.md`, `posting.md` and this file. Ask it for the
  problem list described here plus a verdict (send, fix or rework), and tell
  it to change no files. Then make the fixes yourself. Without a subagent
  tool, do the audit yourself.
  The audit: reread your last Block A and Block C line by line against my
  resume, my library, my profile, my answers, and every rule below,
  especially the items in the check under Output. A problem is anything that
  breaks a rule, in either direction: a claim, verb or number stronger than
  my sources, or a hedge, qualifier or cut my sources don't call for, or a
  fact dropped from a claim that stays (cutting whole bullets for relevance
  or length is fine). Fix by rewording; never delete a sourced fact or metric
  to fix its framing. Don't count harmless rewordings as problems. List each
  problem as: the exact text, the rule it breaks, and the fix. Then rebuild
  the files, post the complete corrected Block A, any corrected Block C
  entries (and merge them into the library), and an updated Verify before
  sending list. If you find nothing, say so in one line and don't repeat
  Block A. Note in `notes.md` that the current version was audited.
- **library**: check that every achievement from my resume and from this chat
  is in `achievement_library.md` with the corrected values from this session
  and any audit, merge any that are missing (backup first), then post the
  whole file in one code block.
- **cover letter**: see Cover letter below.
- **continue**: output the next block that isn't complete yet. If a block was
  cut off part-way, output that whole block again from its start, never a
  fragment.

## Rules

Where a rule says "(-> Verify)", list that item under Verify before sending in
Block B.

### Truthfulness (these override everything else)
- Every claim must trace to my resume, my library, my profile, or my answers
  in this conversation.
- Never invent or inflate numbers, tools, titles, dates, team sizes, or scope.
  Don't round numbers up. Don't turn "helped with" into "led".
- Copy employer names, job titles, any headline under my name, dates, degrees
  (including class, grade or GPA) and certification names exactly, and never
  add words to them. (You may change how a date is written, e.g. 09/2023 to
  Sep 2023, but never the date.) Never merge two possible values into one; if
  you're unsure which is right, ask in Quick checks.
- My answers in this chat take precedence over older wording in my resume or
  library when they are more specific, whether that makes a claim narrower or
  broader. It is a contradiction only when two sources state different facts
  and my answers didn't settle it.
- Keep every qualifier my sources give, everywhere the claim appears (bullets,
  Summary, library): scope (shared, reviewed, "one of 8 super users", "with
  the platform team"), hedges ("about", "up to", on staging, in a pilot, for
  one client, part-time) and level (basic, read-only, co-led, supported).
  Never swap in a stronger word, turn "most" or "near-zero" into a figure,
  annualize, or add up separate numbers. A number I gave exactly stays exact
  (no "about"); a floor I gave may be written as "200+". Map a vague verb in my
  answers ("set it up", "handled") to the specific thing I described doing,
  not its strongest reading. If others shared a result or another change
  landed at the same time, credit them or name the change in a few words;
  never claim sole cause. Keep qualifiers short: a few words or a parenthesis,
  not a clause wedged into the middle of the bullet.
- A derived figure (e.g. a percent change from my before/after numbers) is
  computed exactly, then rounded down to a whole number (-> Verify).
- A hedged answer ("a bit", "sort of", "did a tutorial") supports only the
  concrete facts it contains, never a bare skill keyword.
- Use the posting's terminology only where it names the same thing I did.
  Showing honestly related experience is fine, in my own words; claiming the
  exact requirement is not (supporting users of a system is not administering
  it). If a word for my work matches a requirement I don't fully meet (e.g.
  "build", "certified", "administered"), use a different word or qualify it
  (-> Verify).
- Don't place someone else's figure (a manager's budget, company revenue, a
  team's total) where a skim reader could take it as mine, especially next to
  a requirement I don't meet.
- A skill or tool goes in Skills or the Summary only if I used it hands-on
  (wrote, built, ran, configured, or analyzed with it) in a job, a project, or
  a completed course. A short tutorial, view-only access, or reviewing or
  watching someone else's work does not count. A skill already listed on my
  resume or library counts unless my answers or materials show it falls below
  this bar. If I partly meet a requirement, show the level I have (e.g. "SQL
  (basic queries, simple joins)"), not a bare term that implies the full
  requirement. In Skills, give only a level qualifier like that; scope and
  hedges belong in the bullet that shows the skill. If I ask you to list
  something below this bar, decline it under Declined requests and say why;
  if I insist in a later message, list it with its real extent stated.
- If my answers show that an item already on my resume is thinner than it
  reads, narrow it or remove it (-> Verify).
- If a requirement stays unmet, leave it as a gap and report it in Block B.
  Don't paper over it.
- If my sources contradict each other and Quick checks didn't settle it, use
  the more conservative version everywhere (resume, Block B, Block C)
  (-> Verify).

### ATS and AI-screening readability
- Modern ATS and AI screeners match on meaning, but many still filter on exact
  hard-skill terms and job titles. Use the posting's exact wording for skills I
  really have (if the posting says "Entra ID" and my resume says "Azure AD",
  use the posting's term, since it is the same product renamed). Spell out an
  acronym once where both forms are likely to be searched, e.g. "Security
  Information and Event Management (SIEM)", but only with an expansion you're
  sure of; otherwise leave the acronym as I wrote it.
- Work keywords into real achievement bullets and the skills section. No
  keyword lists dumped into the summary, no hidden or white text, no repeating
  a term just to raise its count.
- Standard section headings (Summary, Experience, Projects, Skills, Education,
  Certifications). The build script keeps the layout single-column, with
  contact details in the body as plain text.
- Each role shows Title, Company, Location (if my materials give it), and
  Dates, in one format throughout where possible (e.g. "Oct 2017 - Present").
  Never invent a month: if I can't supply months for a role, show years only
  for that role (-> Verify).
- If I confirm a title change within one employer, show each title with its
  own dates (the earlier title ends the month before the new one starts). This
  is not changing a date. Don't state a duration for a title or scope (e.g. "7
  years on all three shifts") unless my dates confirm it.

### Writing style
- Each bullet starts with a verb (no subject) and reads as one grammatical
  sentence: action + what I did + the result my sources report, with its
  metric if there is one. If no result is reported, end the bullet at what I
  did; don't add an outcome I didn't state. Keep each bullet to one or two
  lines on the page (about 200 characters at most). If an achievement runs
  longer, tighten it rather than splitting it across bullets.
- Plain, specific verbs: built, cut, migrated, automated, led, negotiated,
  trained. Vary verbs only among ones that are equally true, and don't start
  several bullets in a row with the same verb.
- Show, don't praise: no self-assessing adjectives (proven, strong, rigorous,
  highly skilled, expert, data-driven, results-driven, data-fluent, dynamic,
  passionate). Also avoid wording recruiters now read as AI-generated or
  filler: spearheaded, leveraged, utilized, orchestrated, robust, seamless,
  synergy, cutting-edge, high-velocity, "engine" as a metaphor (e.g. "growth
  engine"), "track record", "in today's fast-paced environment". Avoid em
  dashes in bullets.
- No first-person pronouns. Past roles: past tense. Current role: present
  tense for ongoing duties, past tense for finished work (a completed project
  or a result already achieved).
- Summary: two or three sentences, at most 60 words, no colon-led lists, no
  cliches. Name the target role or domain (as a direction, not a title I
  haven't held) and the two or three strengths the posting cares about most,
  leading with strengths rather than limitations. State years of relevant
  experience only if 1 or more: add up the relevant dated roles (count both
  end months, overlaps once, gaps left out, rounded down, so May 2007 to Apr
  2023 is 16 years; for "Present" use the current month from the system
  clock), and describe the years with the thread common to all of them (e.g.
  "6 years in logistics operations", not "6 years of production ML" if
  production work started later) (-> Verify, with the arithmetic). Every
  Summary claim must also appear lower in the resume, with the same
  qualifiers. Don't turn a single instance into an ongoing skill.
- Skills section: only skills that meet the bar under Truthfulness. Group them
  logically and put the posting's must-haves first.
- Education and certifications: include if present. Don't add expected
  graduation dates or certifications "in progress" unless I stated them. List
  a lapsed certification or license as "(earned YYYY)" or drop it; never imply
  it is current.

## Output

Before building Block A (every time, including revisions), reread your draft
and fix anything that fails: banned or self-praising words; em dashes in
bullets; every number, qualifier, title, date and degree detail matching its
source; the Summary's years claim; every Skills entry meeting the bar. The
build also lints for banned words, em dashes, first-person pronouns, long
bullets, repeated opening verbs and an overlong Summary; fix each warning or
keep it for a reason you can state. Don't describe these checks in your reply.

If you run out of room, stop at the end of a block and write: Reply
"continue".

Declined requests (only if there are any): before Block A, one short bullet for
each thing I asked for that a Truthfulness rule kept you from using: what I
asked, why not in one sentence, and the honest version you used instead. Do the
same in revisions.

### Block A: Tailored Resume

Write the resume to `resume.json` in the job folder. Write the posting's key
terms (the skills, tools, certifications and job titles from the Phase 1
requirements) to `terms.json` there, as a list of strings or
`{"term": "...", "aliases": ["..."]}` objects; aliases cover renamed products,
such as Entra ID and Azure AD. Then run:

`python "<skill>/scripts/build_resume.py" "<job folder>/resume.json" --out "<job folder>" --base "<Name> Resume" --sources "<folder>/master_resume.md" "<folder>/achievement_library.md" "<folder>/profile.md" "<job folder>/notes.md" --terms "<job folder>/terms.json"`

`<Name>` and the JSON `name` are my name exactly as the master resume shows
it. The build writes the .docx, .pdf and .txt (plain text, for "paste your
resume" boxes), prints the PDF's page count and lint warnings, then three
checks:
- `FACT`: a number, title, employer or date on the resume that is not in my
  sources. Correct it to match the source. A derived figure stays only if
  it is listed under Verify.
- `PARSE`: how the PDF reads back to an applicant tracking system. Anything
  but `PARSE ok` is a layout problem to fix.
- `KEYWORD`: the posting's terms that are on the resume (one marked "only
  as" appears under an older name: switch to the posting's term), that are
  in my sources but not on this resume (work them in where they are true and
  relevant), and that are not evidenced (gaps for Block B, never added
  without a source).

If it prints `ERROR`, fix `resume.json` and build again. If it reports a
missing package, run `pip install python-docx reportlab pypdf` and build
again; if that fails, tell me which file could not be built. After a clean
build, run `tracker.py update <ID> status="Resume ready"`.

Then post the .txt content in one code block, so I can read the resume
without opening a file, followed by the paths of the three files. Nothing
omitted or abbreviated.

`resume.json` format (keys in `page` are optional; sections print in the order
given, and a section may use any of `paragraph`, `lines`, `entries`,
`bullets`, printed in that order):

```json
{
  "name": "First Last",
  "contact": ["City, ST", "(555) 555-0100", "name@example.com", "linkedin.com/in/name"],
  "page": {"paper": "letter", "font_size": 10.5, "margin_in": 0.6},
  "sections": [
    {"heading": "Summary", "paragraph": "Two or three sentences."},
    {"heading": "Experience", "entries": [
      {"title": "Job Title", "dates": "Oct 2017 - Present", "org": "Company",
       "location": "City, ST", "bullets": ["Achievement bullet."]}
    ]},
    {"heading": "Skills", "lines": [{"label": "Category", "text": "Skill, Skill, Skill"}]},
    {"heading": "Education", "entries": [
      {"title": "B.S., Field", "dates": "May 2015", "org": "Institution", "location": "City, ST"}
    ]},
    {"heading": "Certifications", "bullets": ["Certification Name (earned 2019)"]}
  ]
}
```

- Contact: only the items `profile.md` gives, in this order: city and state,
  phone, email, LinkedIn, other links. Leave out any item I left blank.
- Paper and spelling come from `profile.md`; `paper` is `letter` or `a4`.
- Length: check the page count against my target length. To hit it you may
  set `font_size` between 10 and 11 and `margin_in` down to 0.5. If it still
  runs long at 10pt, cut the weakest bullets from older roles instead of
  cramming. Delete sections I have no material for.
- For students, recent graduates, or career changers whose strongest evidence
  is education or projects, put Education (and Projects) right after the
  Summary. A project is an entry with a title and dates and no org.
- v1.2 (daily batch, Tier A, variants): every bullet carries `ev` tags, the
  build runs with `--strict`, and publications are cited by ID. See "Tiers,
  evidence tags and strict builds (v1.2)" at the end of this file.

### Block B: Match Report

Post it, and save it to `notes.md`.
- Coverage: a table with columns Posting requirement | Status (Met / Partial /
  Not evidenced / Ask) | Where the resume addresses it (a few words copied
  exactly from Block A). One row per requirement from the Phase 1 Requirements
  check, plus any main duty of the role it left out. Mark Met only when the
  resume shows the requirement itself; related experience is Partial at most.
  If my answers changed a Phase 1 status, say so. Under the table, give the
  build's three `KEYWORD` lines as printed.
- Remaining gaps: every requirement not marked Met, must-haves first, each
  with one honest suggestion (e.g. mention it in the cover letter, a
  certification worth pursuing, or "not a fit"). A suggestion never recasts a
  gap as met (not "your Docker work demonstrates Kubernetes"), never coaches
  me to overstate in an interview, and never states facts about the employer
  that aren't in the posting. Once I've answered an Ask row, drop it from this
  list.
- Screening questions: only if the posting lists application or screening
  questions. For each, give the answer my sources support. If they don't
  settle it, or it's a personal fact `profile.md` doesn't answer (work
  authorization, relocation, salary), write "Your call:" and say what it
  depends on. Never supply a figure or a "Yes" my sources don't support.
- Length: the page count the build printed, against my target. If it runs
  over, name the bullets you'd cut first, not layout tweaks. If truthful
  content falls short, say so instead of padding; if you cut material to fit,
  say what.
- Verify before sending: at most 8 lines, riskiest first: the (-> Verify)
  items, any `FACT` line kept for a derived figure, and any wording stronger
  than a close paraphrase of my sources. If
  more qualify, put similar ones on one line (e.g. "Derived percents: 38% in
  bullet 2, 70% in bullet 5"); never drop a (-> Verify) item. Say nothing
  about checks that passed, changes I asked for, or facts I already
  confirmed. If there's nothing, say so.

### Block C: Achievement Library Additions

In one code block: the new or corrected achievements from this session,
grouped under `### Job Title | Company | Mon YYYY - Mon YYYY` headings that
match my library. Write entries as records, not resume bullets: keep every
number, qualifier and hedge I gave, and use month-year dates, never relative
ones ("Mar 2026", not "last March"); where a detail is unknown, leave it out
rather than noting it as unknown. The Truthfulness rules apply here as much as
in Block A: record what I did, not an overstated way I described it. Use the
same titles, dates and values as Block A. If you corrected an existing entry,
start it with "Replaces: <first few words of the old entry>". If a library
heading is wrong, say so above the code block and output that whole section
under the corrected heading. List things I said I don't have under `###
Skills, Certifications & Education` as "Not held: ...". If nothing new came
up, say so.

Then merge it into `achievement_library.md`, after copying the file to
`backups/`:
- Add each new entry under its role heading; a new role gets a new heading in
  date order.
- A "Replaces:" entry replaces the old entry it names; the "Replaces:" prefix
  never goes into the file.
- A corrected heading replaces the old heading, and the section's entries move
  with it.
- Tell me in one line how many entries were added and replaced.

Finish with one line offering: "audit" for a line-by-line check before I use
this; "apply" to fill in the application; and a cover letter or interview prep
for the gaps. Don't produce them unless I ask.

## Cover letter

When I ask for one: three or four short paragraphs, at most about 300 words,
in my voice (first person is fine here), naming the role and company from the
posting and leading with the two strongest matches from Block B. The
Truthfulness rules and the banned-word list apply; a gap is never recast as
met, and facts about the employer come only from the posting. Write it to
`cover_letter.json` in the job folder, dated from the system clock, and build
it with the same command, without `--terms`, and with
`--base "<Name> Cover Letter"`:

```json
{
  "name": "First Last",
  "contact": ["City, ST", "(555) 555-0100", "name@example.com"],
  "letter": {
    "date": "November 5, 2026",
    "recipient": ["Hiring Team", "Company"],
    "greeting": "Dear Hiring Team,",
    "paragraphs": ["...", "..."],
    "closing": "Sincerely,"
  }
}
```

Post the .txt content and the file paths.

## Tiers, evidence tags and strict builds (v1.2)

Everything above still applies. This part adds what the daily batch needs.

### Tiers

- **Tier A** (the day's best 3-5 jobs, `daily.tier_a_per_day`): this file in
  full, written for that one job by the job-tailor sub-agent (inline when the
  plugin is absent), then audited by job-auditor in a fresh context. At most
  2 questions per job go into the day's batch (fit_and_questions.md, "Daily
  batch mode"); if they stay unanswered, the "go" version stands. After 2
  failed audit rounds, or a "rework" verdict, the job gets its Tier B files
  for today.
- **Tier B** (every other job): nothing is written for the job.
  `variants.py tier-b` tunes the approved family variant
  ([variants.md](variants.md)) and builds it with `--strict`; on any failure
  it uses the untouched approved base, then parks the job.
- **Family variants** are written with every rule in this file, plus `ev`
  tags on every bullet, an approved pool and an alias table
  ([variants.md](variants.md)), and are audited before I approve them.
- A Tier B cover letter is only the approved family letter with its slots
  filled from posting.md; no new claims.

### Evidence tags

- In `resume.json` for Tier A jobs and variants, a bullet is an object:
  `{"text": "Validated 3 real-time PCR assays ...", "ev": ["L-0004"]}`.
  `ev` lists the achievement library IDs (`[L-0004]` in
  `achievement_library.md`) of every entry the bullet's facts rest on.
- Every number in the bullet must appear in a cited entry (the EVIDENCE
  check reads them the way FACT does, so "a dozen" covers 12). A fact that
  exists only in my answers in this chat goes into the library first (Block
  C), gets its ID from `library.py ids --root "<folder>"`, and is then cited.
- `"keep": true` marks a bullet Tier B tuning must never drop.
- Education, Certifications, Licenses, Awards, Honors, Memberships, Service,
  References, Languages, Grants and Fellowships, and citation sections carry
  no tags; FACT still checks their numbers, titles and dates.
- A corrected library entry keeps its ID: when you merge a "Replaces:" entry,
  the new text takes the replaced entry's `[L-...]`. New entries get new IDs;
  an ID is never reused or renumbered.
- A derived figure fails FACT under `--strict`. Record the derivation in the
  library entry the bullet cites (Block C, with the arithmetic, e.g.
  "410 to 120 is a 70% cut, derived"), so the figure has a source, and keep it
  under Verify before sending.

### Citations

- Publications, presentations and patents come only from `publications.md`,
  by ID: `{"heading": "Selected Publications", "citations": ["P-003"],
  "bold_name": true}`. Never type a citation; the build inserts its exact
  text, renders `*italics*`, bolds my name where `bold_name` is true, and the
  CITE check compares every printed citation with its line.
- Choose the citations by relevance; the text never changes. Industry
  resumes carry at most `publications_max` (3 by default), most relevant
  first, after Techniques. CV rules are in
  [cv_publications.md](cv_publications.md).
- Citations skip the bullet lint and FACT.

### Techniques

- Life-science resumes title the Skills section "Techniques" and draw it
  from `techniques.md`: hands-on techniques only, with a level qualifier
  where I partly meet a requirement (the Skills rule above). An alias goes on
  the resume only where it names the same thing (the renamed-product rule).

### Modes and the strict build

- `--mode industry` (2 pages at most), `cv` (no cap, CV section order) or
  `usajobs` (2 pages, hard): see [cv_publications.md](cv_publications.md).
  A variant's mode and page cap come from its `approved.json`.
- Tier A and variant builds add `--strict`, `--mode`, and
  `--publications "<folder>/publications.md"` when that file exists:

  `python "<skill>/scripts/build_resume.py" "<job folder>/resume.json" --out "<job folder>" --base "<Name> Resume" --sources "<folder>/master_resume.md" "<folder>/achievement_library.md" "<folder>/profile.md" "<job folder>/notes.md" --terms "<job folder>/terms.json" --mode industry --publications "<folder>/publications.md" --strict`

- The build then ends with `VERDICT ok` or, after one `STRICT-FAIL` line per
  problem, `VERDICT fail` and exit code 6. It fails on any FACT line, PARSE
  other than ok, an EVIDENCE or CITE problem, more pages than the cap, or a
  banned word. Fix each one and build again; a strict failure is never
  carried into Verify as it stands.
- Letters: build with `--posting "<job folder>/posting.md"` as well. The
  LETTER check needs the posting's company and role named exactly as
  posting.md's `Company:` and `Role:` lines give them, and no unfilled
  `{slot}`.
