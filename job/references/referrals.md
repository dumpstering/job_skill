# Referrals

People the user already knows, matched to the day's jobs, with short asks the
user sends (spec 6.3, R11). A referral is never a gate: no job waits for one
unless the user asks for that.

**Never automated.** You never open LinkedIn, never send a message, and never
fetch anything from the user's export. LinkedIn bans bots that send messages or
download contacts; the user downloads their own export and sends every ask.

**Privacy.** `network\` holds other people's data and never leaves the
user's folder. Read only the rows for the job in front of you: `network.py match
--n <item>`, or with `{"company": "<Company>"}` in `claude/args.json`,
`network.py match --args-file claude/args.json` (a name, a company or a
link never goes inline; SKILL.md "Scripts"). Never print or summarize the
whole network.

## Getting the export in (setup, then monthly)

1. At setup the user requests their LinkedIn data (desktop: Me > Settings & Privacy >
   Data Privacy > Download your data). Connections can take up to 48 hours.
   A Google Contacts export (Google CSV) is optional.
2. The user drops the zip (or the CSV files) into `network\raw\`. Nothing is
   unzipped by hand.
3. The runner imports new files on its next run (`import_network` request).
   In mode M run it yourself: `network.py import --root "<folder>"`.
   - It reads only Connections.csv, Recommendations_Received.csv,
     Jobs/Job Applications.csv and a Google contacts CSV, and only their
     name, company, position, date and email columns.
   - It prints `SKIP <file> (never opened)` for Profile.csv, phone numbers,
     saved answers, logins and the rest. That is expected.
   - It writes `network\network.csv` (IDs N-0001 and up, no profile links)
     and `network\past_applications.csv` (jobs the user applied to on LinkedIn,
     for the duplicate check; their links are kept as text, never fetched).
4. Suggest a fresh export once a month, in one line, never more often.

## Matches

`network.py match --root "<folder>" --date <today>` (the runner runs it
daily) writes `network\matches_<date>.csv` and prints IDs only:
- `REFERRAL #7 <job key> candidate N-0004`: a tier-1 contact for item 7.
- Tier 1: the same company after legal suffixes are stripped, or "@ Company"
  in a staffing-agency Position.
- `MAYBE` lines (an alias, a rebrand, a parent or subsidiary) are shown to
  the user for a yes before anything is drafted.
- Self-employed, Stealth, Freelance, Retired, Student, Open to work,
  Confidential and blank companies never match.
- At most 2 names per job, ranked recommendation writer, then someone the
  user emailed, then a connection.

## Asks (after #10, at the batch break)

1. Run `network.py asks --root "<folder>" --date <today>`. It prints at most
   `[referrals] asks_per_day` (3) `ASK` lines, each with a `TEXT` line:
   50-80 words, the exact title, the posting link and req ID, a two-line
   pitch taken from the job's fact-checked resume JSON, and a yes or no
   question.
2. It never asks one person about two jobs, never asks someone who has an
   open ask (a Referrals row at Drafted or Sent) for another job, and never
   sends two people at one company the same text on one day. A draft whose
   pitch holds a number missing from the user's sources is dropped (`DROP` and its
   `FACT` line); say nothing about drops unless the user asks.
3. Show the drafts, one per contact: name, company, role, channel, the text.
   Edits the user asks for follow the writing_rules.md Truthfulness rules.
4. The user sends each one: copy, open LinkedIn or their email, paste, send.
5. `sent 1 2` means asks 1 and 2 went out. For each, write `{"contact":
   "<name>", "company": "<Company>", "job-key": "<key>", "channel":
   "LinkedIn message|Email|Text|In person", "status": "Sent"}` and run
   `tracker.py referral-add --args-file claude/args.json`. Contact holds
   the name only: no email, link or phone. The tracker sets Nudge on to 5
   days later.
6. If the user has already applied, the draft says so and asks for a word with the
   hiring team instead; whether that counts is the company's policy.

**Hold.** `[referrals] hold_days` is 0 by default, so referral jobs keep
their place in the queue. If the user set 1-2, only the job whose ask they marked
sent waits, until the hold date or the contact's reply.

**Referral link.** `ref 7 <url>` stores the contact's link exactly as sent,
query string and all: for a row, `{"_": ["<ID>", "referral_url=<url>"]}`
and `tracker.py update --args-file claude/args.json`; for the contact,
`"referral-url": "<url>"` in the `referral-add` file. While item 7 is not yet applied,
the fill opens that link instead of the posting. A gh_src in a link shows a
source is attached, not that a referral happened. A link that is not a plain
public web address (a user name, an IP address, another port, a local name)
is refused (`ERROR --referral-url refused ...`, nothing written): store the
row without it and use the posting link.

**Nudge.** Once, when Nudge on arrives with no reply: offer one short
follow-up as text to paste. Never a second nudge. After that, set the row to
`No reply` only when the user says so.

**Replies.** A contact's reply is an Immediate item in the inbox check
(inbox.md): `referral-add` with `"status": "Replied"`, or `Referred`.
