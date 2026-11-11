# Job search helper for Claude: setup and daily use

Each morning a small job finder on your PC reads employers' job boards and
makes a list of about 30 fresh jobs that fit you, each with a resume
already built from a version you approved. You open Claude, type `/job`,
and Claude fills in the applications one at a time in its browser. You
attach the file it names, look over the page, and click Submit yourself.
Every application goes into a spreadsheet, and Claude checks Gmail for
replies.

New to this? Start with the short guide (`START_HERE.txt` in the download,
GETTING_STARTED.md on GitHub). The one download with every file is
https://github.com/dumpstering/job_skill/releases/latest/download/job-skill-full.zip;
unzip it with Extract All first.

You need:
- The Claude desktop app for Windows, signed in on a paid plan (Pro or
  higher; see "Checking your usage" for which plan).
- Your most complete resume as a Word or PDF file.
- Gmail (for replies and job alerts).
- About 30-45 minutes on the first day. On the second, about 20 minutes
  for the job finder, then 15-20 minutes for each base resume you approve
  (2-4 of them; one a day is fine).

**You are responsible for what is sent in your name.** Claude never clicks
Submit, never creates accounts, never handles passwords or verification
codes, and never solves CAPTCHAs. Submit is always yours.

**Where your daily jobs come from.** The job finder's list of employers
finds a backlog of fitting jobs in the first week, then about 5 new ones a
day. Two things you set up once add more, so do both:
- **Job-alert emails** (Day 1 step 7): LinkedIn job alerts sent to your
  Gmail. About 5 minutes.
- **Two free keys, Adzuna and USAJOBS** (Day 2 step 2). About 5 minutes
  each.
If no alert email has come in for 3 days, the morning card says so; type
`/job alerts` and Claude fixes it with you. To reach 20 a day you need
LinkedIn too, and there are two ways:
- **Paste (always works).** Copy several job pages in your own browser,
  type `/job paste`, and paste them all. Claude prepares a resume and your
  answers for each and takes you through them one at a time; you apply in
  your own browser.
- **LinkedIn in your own browser (optional, off unless you say yes).**
  Claude finds and reads jobs on LinkedIn in your own Chrome and fills Easy
  Apply for you. LinkedIn's rules do not allow automated tools, and it can
  restrict an account; this mode works slowly in your own browser, and you
  click Submit. See Day 2 step 6.
You can also say "more to one company" to loosen the per-company limits.

## Day 1: install and set up (about 30-45 minutes)

1. **Make the folder.** In File Explorer, open Documents and create a folder
   named `JobSearch`. Copy your most complete resume into it, with "resume"
   in the file name (for example `Resume - full.docx`). Use your full
   version, not one cut down for a particular job.
2. **Turn on code and files.** In Claude, open Settings > Capabilities,
   switch on "Code execution and file creation", and check "Allow network
   egress".
3. **Install the plugin.** Open the Plugins page, choose Add > Upload
   plugin, and pick `job-search-plugin.zip`. (This click path is UNVERIFIED:
   menu names were changing in November 2026, and "Customize" is being
   renamed "Plugins".) If the plugin will not install, upload `job.zip` as a
   skill instead; everything works, a little slower. If you uploaded an
   earlier version before 2026-10-05, remove it and upload this one.
4. **Connect the folder.** Start a new task, connect your
   `Documents\JobSearch` folder, and allow it. It then stays under Trusted
   folders, so you connect it once. Do not put the folder inside a Project:
   Claude cannot change a project's files.
5. **Connect Gmail.** In Claude's settings, open Connectors and connect
   Gmail. Claude only searches and reads: it never sends, deletes, labels or
   moves mail.
6. **Type `/job setup`** (or `/job-search:job setup`). Claude reads your
   resume, shows what it found, and asks the details applications want
   (contact, work authorization, start date, licenses you hold, whether you
   are claiming unemployment), in one list. "Skip" is fine for anything. It
   also asks where you applied recently, so reposts get caught, and shows
   your publications and lab techniques for a yes.
7. **Create job alerts on LinkedIn: they add jobs the employer list
   misses.** When Claude suggests them: 5 now, daily, by email, one per broad
   job title; Claude suggests 3 more each of the next few days, up to 20.
   You create them yourself; Claude only reads the alert emails for the
   company, title and place, and never opens a link in one.
8. **Request your LinkedIn data export:** on LinkedIn, Me > Settings &
   Privacy > Data Privacy > Download your data. It can take up to 48 hours.
   Claude uses it to find people you know at the companies you apply to.
9. **Say yes to the two scheduled tasks** Claude offers: "job-morning" at
   6:30 (it reads your mail, prepares the day's best few applications in
   full, and gathers the day's questions) and "job-inbox" at 1:00 pm (it
   checks for replies). They run only while the Claude desktop app is open.
   Free plans have no scheduled tasks; then `/job` does the morning work
   itself.

## Day 2: the job finder (about 20 minutes, plus 15-20 per base resume)

1. **Install the job finder.** Double-click `install_runner.cmd` in the
   unzipped `job-skill` folder (or download it from the release page,
   https://github.com/dumpstering/job_skill/releases/latest). If Windows SmartScreen warns you,
   click More info > Run anyway. It needs no administrator rights. It
   installs Python for your user, checks that the download is the published
   one (it stops if not), and sets up two Windows tasks: one at 5:30 every
   morning and a short check every 15 minutes from 6 am to 10 pm, both
   without a window and also on battery. If the PC sleeps through 5:30,
   the morning run starts when it wakes. It adds a desktop shortcut, "Run
   job finder now".
2. **Two free keys: do this, they add jobs every day.** Get an Adzuna key
   at developer.adzuna.com (a free account, then its API key) and a USAJOBS
   key (federal jobs) at developer.usajobs.gov (a key request with your
   email); the sign-up steps are UNVERIFIED. Type them only into the
   installer window when it asks, never into a chat. Skipped one?
   Double-click `install_runner.cmd` again: it asks only for a key you have
   not entered yet.
3. **The first run.** Double-click "Run job finder now". Then in Claude say
   `/job setup` again to finish: Claude switches to the job finder, shows
   the job families and places it searches (reply "ok" or name a change),
   and lists the alert senders it found in your mail for a yes.
4. **Approve your base resumes.** For each of 2-4 job families, Claude
   builds one base resume and has a second reviewer check it. You read one
   PDF and one page of extra lines per family, then reply `approve
   <family>`. These bases are what most applications start from, so each is
   worth 15-20 minutes; you can stop after any family and do the rest
   another day. Jobs of a family wait until you approve its base. After
   your last approval, double-click "Run job finder now" once more so
   today's jobs get their resumes, then type `/job`. Claude only ever
   reorders and selects approved lines for a job; it never writes new
   claims into them.
5. **Drop in the export.** When LinkedIn's email arrives, put the zip into
   `JobSearch\network\raw`. Nothing else to do; it is read on the next run.
6. **LinkedIn in your own browser: your choice.** Claude asks once whether
   to turn this on. LinkedIn's rules do not allow automated tools, and it
   can restrict an account; this mode works slowly in your own browser, and
   you click Submit. Say "yes" only if you accept that; "no" changes
   nothing, and you can still use `/job paste`. If you say yes:
   - Install the Claude in Chrome extension and stay signed in to LinkedIn
     in that Chrome. (Whether the extension can work on LinkedIn is
     UNVERIFIED; if it cannot, use `/job paste`.)
   - Type `/job linkedin`. Claude searches your job titles near home, newest
     first, skips jobs that do not fit or that you already applied to, and
     fills Easy Apply one job at a time. You attach the file it names, look
     it over, click Submit, and type `s`.
   - It does at most 25 a day and waits a few seconds between every step.
     It never messages anyone, never sends a connection request and never
     opens a profile.
   - If LinkedIn shows a CAPTCHA, a sign-in check or any warning, Claude
     stops using LinkedIn for the rest of that day and tells you. It never
     tries again that day.
   - To turn it off, say "stop using LinkedIn".

The job finder can be removed with `uninstall_runner.ps1`; that never
touches your JobSearch folder.

## Your day

**A normal day.** Start a new task with the JobSearch folder, type `/job`.
You see one short card: what is ready, any urgent mail (an interview
request, an assessment with a deadline), up to 6 questions, and the salary
and sponsorship answers it will use: reply `yes` to use them (or "yes,
leave salary blank"). Claude starts on #1 right away; answer the questions
whenever you like, in one reply. For each job:
1. Attach the file starting with the job's number from `JobSearch\upload`
   when Claude asks (pin that folder in Quick access the first time), and
   type `ok`. `ok` only ever means "attached" or "done"; it never says yes
   to the salary and sponsorship answers.
2. Look over the page Claude filled, click Submit, and type `s`.
Type `k` to skip a job, `later` to move it to tomorrow, `stop` to end.
After 10 jobs Claude suggests a break and a new task: type `/job` there and
it picks up at #11. Sites that need an account (Workday and the like) come
after the first 10, grouped, at most 3 new sites a day; you create each
account and sign in yourself, and Claude tells you which email you used
there before.

**A short day.** Type `/job short`, or say "short" at any point. Claude does
only the quick jobs that need no account, asks at most the one salary and
sponsorship line (until you say `yes`, those fields stay blank), and stops
when you say `stop`. Even shorter: `/job inbox` just checks your mail.

**After a break.** Nothing piles up. The list is rebuilt every morning from
what is still open, never more than about 30, and the card shows only what
happened that needs you: interviews, assessments, offers, direct questions.

**Keep the Claude app open** while job-morning runs and while you apply, and
keep the browser pane visible while Claude fills a form.

**The browser.** Claude uses its built-in browser unless you choose Claude
in Chrome (install the extension, then pick Chrome under Settings > Cowork >
Preferred browser). In the built-in browser, sign in to job sites once
inside it; on Windows it can import cookies only from Firefox.

## Replies, rejections and follow-ups

- Interview requests, screens, assessments with deadlines, offers and
  direct questions reach you at once (a phone notification from the
  scheduled task, and the top of the card).
- **Rejections come weekly.** They update the spreadsheet right away but
  appear only in the Monday summary, as one plain list.
- Follow-ups are offered only for the jobs Claude tailored in full and the
  ones someone referred you to, as text for you to send.
- `/job week` shows the weekly summary any time: what you sent, the
  conversations started, and what is working.

## Checking your usage (days 1 and 2)

Usage limits are not published, so on your first two days Claude asks three
times (before the first application, after the 3rd and after the 5th): "What
does Settings > Usage show now?" Reply with the two percentages. `/job
usage` then says whether your plan fits 20 applications a day or what to
change (two sittings, a larger plan, or a lower daily number). A good start
is Max for two weeks, or Pro with usage credits on.

## The work-search record (if you claim unemployment)

Tell Claude "I'm claiming unemployment in <state>". In certification weeks
it offers `/job worksearch`, which writes your job-search activity (dates,
employers, how you applied, results) to a spreadsheet and a ready-to-type
list in `JobSearch\worksearch`. Boxes for your SSN, claimant ID and
signature are left blank for you. Check your state's own rules; the
spreadsheet is your record, not a filing.

## Other things you can type

- `/job <link>` - one job from a link, with full tailoring: duplicate check,
  fit, a few questions, a tailored resume, then the form.
- `/job add <link>` - put a job you found into today's list.
- `/job paste` - paste several job pages you copied; Claude prepares them
  all, then takes you through them one at a time.
- `/job linkedin` - LinkedIn in your own Chrome, if you turned it on (Day 2
  step 6).
- `/job log` - tell Claude about a job you applied for on your own.
- `/job status` - what is in flight and what is ready to follow up.
- `/job prep` - interview prep from what you sent.
- `/job settings` - the switches in plain words ("aim for 25 a day", "use
  Chrome", "confirm salary once a day").
- `/job watch <company>` - add an employer to the job finder's list.
- `/job network` - people you know at the companies on your list.
- `/job variants` - your base resumes (after you correct something they
  use, one yes approves every changed one).
- `/job alerts` - get job-alert emails coming again.
In Cowork the plugin form is `/job-search:job` followed by the same words.

LinkedIn and Indeed do not allow automated tools. Claude never opens or
fills Indeed. It opens LinkedIn only if you turned the LinkedIn mode on
(Day 2 step 6), only in your own Chrome, and you click Submit. Otherwise,
for a job you found there, paste the posting text (several at once with
`/job paste`) or the company's own application link. Easy Apply jobs go on
a short do-yourself list with your answers written out, for you to apply in
your own browser; type `log e1` afterwards (`log e51` for a pasted one).

## What never leaves your PC

Your resume, profile, achievements, answers, spreadsheet, contacts export,
job list and uploads are files in `Documents\JobSearch`. Claude reads them
during a task, the same as anything you paste into a chat, and reads only
the contacts that match the job in front of it. The job finder sends nothing
personal over the network, except the email you used for a USAJOBS key,
which USAJOBS requires. API keys stay in your Windows user profile, never in
the folder or a chat. Claude never asks for or saves your SSN, date of
birth, bank details or passwords.

## If something goes wrong

- **Claude cannot find your folder:** connect `Documents\JobSearch` to the
  task and type `/job` again.
- **The card says the job finder has not run:** double-click "Run job finder
  now" on the desktop.
- **The card asks you to close the spreadsheet:** close `applications.xlsx`
  in Excel; Claude never needs it open.
- **A job page will not load:** paste the posting text instead.
- **The card says no job-alert email has come in:** type `/job alerts`.
- **Few jobs a day after the first week:** check that the LinkedIn alerts
  are on and that you entered both free keys (Day 2 step 2).
- **`/job` does not appear:** type `/job-search:job`, or "Use the job
  skill:" followed by what you want.
- **A usage limit:** stop; type `/job` after the window resets. Nothing is
  lost.
