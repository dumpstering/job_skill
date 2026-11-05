# Job application helper for Claude

Paste a job link. Claude checks you haven't already applied for it, reads the
posting, checks it against your resume, asks you a few questions, writes a
tailored resume (Word and PDF), and fills in the application in your browser.
You review it and click Submit yourself. Every job goes into a spreadsheet,
and Claude can check your Gmail for replies and update the spreadsheet for
you. Each application also adds to a file of your achievements, so the next
one starts with more material.

You need:
- The Claude desktop app for Windows, signed in on a paid plan (Pro or higher).
- Your most complete resume as a Word or PDF file.
- Gmail, if you want Claude to check for replies (optional).
- Google Chrome, if you want Claude to use your own browser (optional).

## One-time setup (about 10 minutes)

1. **Make the folder.** In File Explorer, open Documents and create a folder
   named `JobSearch`. Copy your most complete resume into it, with "resume" in
   the file name (for example `Resume - full.docx`). Use your full version,
   not one you cut down for a particular job.
2. **Turn on file creation.** In Claude, open Settings > Capabilities and
   switch on "Code execution and file creation" (newer versions call it
   "Create and edit files").
3. **Install the skill.** Open Customize > Skills, click +, then Create skill
   > Upload a skill, and choose `job.zip`. Check that "job" is switched on in
   the list.
4. **Connect Gmail (optional).** In Claude's settings, open Connectors and
   connect Gmail. Claude searches and reads your mail for job replies. It
   never sends, deletes or moves anything; it saves a draft follow-up only
   when you ask for one, for you to send yourself.
5. **Pick a browser (optional).** Claude uses Cowork's built-in browser unless
   you change it. To use your own Chrome, where you are already signed in to
   job sites, install the Claude in Chrome extension and choose Chrome under
   Settings > Cowork.

## Applying for a job

1. In the Claude app, switch to **Cowork** and start a new task. Use a new
   task for each job.
2. Connect your `Documents\JobSearch` folder to the task.
3. Type `/job`, pick "job" from the menu, paste the job link, and send. If
   /job is not in the menu, type "Use the job skill:" followed by the link.
4. **First time only:** Claude reads your resume, shows you what it found, and
   asks for the details every application wants (address, phone, work
   authorization, start date and so on). It also asks where you have applied
   recently, so reposts of those jobs get caught. Your details are saved in
   `profile.md` in your folder. Skip anything you like; Claude asks when an
   application needs it.
5. **Duplicate check:** if you already applied for this job, or it looks like
   a repost of one you did, Claude tells you when and asks whether to go on.
6. Claude shows how well you match the job and asks a few questions (up to
   6, plus some one-line quick checks). Short answers, rough numbers and
   "skip" are all fine.
7. Claude writes the resume and saves it as Word and PDF in
   `JobSearch\applications\<date> <company> - <role>`. Read it, and read the
   "Verify before sending" list: you are the one signing this resume. Ask for
   any changes in plain words.
8. Reply **audit** for a line-by-line check against what you told it. Claude
   also runs this itself before it fills in an application.
9. Reply **apply**. Claude opens the application and fills it in page by
   page. It stops and asks you to:
   - sign in or create an account (it never handles passwords or
     verification codes; it reminds you which email you used on that site
     before),
   - attach your resume file when it cannot attach it itself (it tells you
     which file),
   - answer anything your files do not cover,
   - fill in sensitive items yourself: SSN, date of birth, bank details,
     signatures and "I certify" boxes.
10. Claude stops on the last page, before Submit. Check each page, click
    Submit, then reply **submitted** so it is logged. Claude keeps a copy of
    exactly what you sent.

LinkedIn does not allow automated tools, so Claude never opens or fills in
LinkedIn pages. For a LinkedIn job, paste the posting text (or the company's
own application link, if the listing has one). For Easy Apply jobs, Claude
writes out your answers for you to paste.

When Claude pauses for you (to sign in, attach a file, or close the
spreadsheet), reply **done** when you have finished.

## Other things you can ask

Start a Cowork task with your JobSearch folder connected, then type:
- **/job inbox** - Claude checks Gmail for replies (confirmations,
  rejections, interview requests, offers), updates your spreadsheet, and
  warns you about anything that looks like a job scam.
- **/job status** - what is in progress, which follow-ups are due, and your
  response rate so far.
- **/job log** - tell Claude about a job you applied for on your own, so it
  goes in the spreadsheet too.
- **/job prep** - interview prep for a job, built from the resume you sent.

In a job task you can also reply **go** (skip the questions and write the
resume), **cover letter**, **library** (see your achievement library) or
**profile** (see or change your saved details).

## What is in your JobSearch folder

- `applications.xlsx` - the spreadsheet of every job you started or applied
  for: status, dates, follow-ups, and links. It also lists the job-site
  accounts you created (which email, never passwords) and every email Claude
  read for you. Close it in Excel while Claude is working, so Claude can
  update it.
- `profile.md` - your contact details and standard answers. Open it in
  Notepad and edit it whenever you like.
- `achievement_library.md` - everything you have done, role by role. It
  grows with each application, and every new resume draws on it.
- `applications\` - one folder per job: the posting, your tailored resume (and
  cover letter, if you asked for one), what was entered in the form, and,
  for applications Claude filled in, a `submitted` folder with exactly what
  you sent.
- `backups\` - dated copies of your profile, library and spreadsheet from
  before changes. Claude adds and updates files here but never deletes them.

## If something goes wrong

- **Claude says it cannot find your folder:** connect `Documents\JobSearch`
  to the Cowork task and send /job again.
- **Claude says the spreadsheet is locked:** close `applications.xlsx` in
  Excel and reply "done".
- **The job page will not load:** copy the posting text from your browser and
  paste it in.
- **/job does not appear in Cowork:** install `job-search-plugin.zip` from
  Customize > Plugins > Add > Upload plugin, then type / and pick
  "job-search:job".
- **The resume runs over one page:** say "make it fit on one page".
- Cowork on Windows is in beta, so menu names may differ a little from the
  ones above.

Privacy: your resume, profile, library and spreadsheet are files in your
JobSearch folder. Claude reads them during a task, the same as anything you
paste into a chat. It never asks for or saves your SSN, date of birth, bank
details or passwords.
