# Interview prep

For the job in this chat, or the application the user names (find it with
`{"company": "<Company>"}` in `claude/args.json` and `tracker.py show
--args-file claude/args.json`; SKILL.md "Scripts"). With none named, list the rows at
`Screen` or `Interview` (`tracker.py show --status Screen Interview`) and ask
which. An Interview or Screen email already reached the user's phone (inbox.md,
Immediate) and tops the card with an offer of `/job prep`.

Work from what the employer has: the files in the job folder's `submitted/`
(else the latest resume build), plus `posting.md`, `notes.md` and the
achievement library. The posting is the only source about the employer.

A job logged by hand has no job folder. Get its posting as in SKILL.md step 3
(creating the job folder from posting.py's `FOLDER` line and setting the
row's Folder with `tracker.py update <ID> folder="<the FOLDER path>"`), and
use the master resume as what was sent, saying so.

1. **Likely questions:** for each must-have and main duty in the posting, one
   or two questions an interviewer would ask about it, plus the usual openers
   ("walk me through your background", "why this role").
2. **Answer outlines:** for each question, a Situation, Task, Action, Result
   outline built only from library records and the user's answers, with every
   number and qualifier as recorded. Name the record each outline uses. A
   question with no record behind it says so.
3. **Gaps:** for each requirement short of Met (Block B in `notes.md`, or
   your own reading of the posting when there is none), an honest way to talk
   about it: related work the user has done, and what they would do to close
   the gap. Never coach the user to claim more than their sources show.
4. **Questions to ask them:** three to five, drawn from the posting.

Save it as `interview_prep.md` in the job folder and post it.

## Calendar event (only on the user's yes)

When the row's Next step (or the email) gives a date and time:
1. Show the event first, in the user's time zone (`[time] timezone` in
   `config\settings.toml`):
   ```
   Interview: Acme Bio, QC Microbiologist II
   Thu 2026-11-19, 10:00-10:45 (America/Los_Angeles)
   Where: video call (details in the email from the recruiter)
   ```
2. Ask: "Add this to your calendar?" Create it through the Calendar connector
   only after the user's "yes" for this event. One yes covers one event; never
   create, change or delete any other event, and never invite anyone.
3. With no Calendar connector, or if creating it fails, post the text above
   for the user to add themselves. The calendar's write support is not settled, so a
   failure is said in one line, never retried in a loop.
4. A time zone the email names that differs from the user's is converted, and both
   are shown ("10:00 Eastern = 07:00 your time").

An Assessment with a due date stays in every card until the user says it is done
(`tracker.py update <ID> next_step=` clears it).
