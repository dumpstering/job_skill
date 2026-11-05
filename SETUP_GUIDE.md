# Job application helper for Claude

Paste a job link. Claude reads the posting, checks it against your resume,
asks you a few questions, writes a tailored resume (Word and PDF), and fills in
the application in your browser. You review it and click Submit yourself.
Every application also adds to a file of your achievements, so the next one
starts with more material.

You need:
- The Claude desktop app for Windows, signed in on a paid plan (Pro or higher).
- Your most complete resume as a Word or PDF file.
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
4. **Pick a browser (optional).** Claude uses Cowork's built-in browser unless
   you change it. To use your own Chrome, where you are already signed in to
   LinkedIn and job sites, install the Claude in Chrome extension and choose
   Chrome under Settings > Cowork.

## Applying for a job

1. In the Claude app, switch to **Cowork** and start a new task. Use a new
   task for each job.
2. Connect your `Documents\JobSearch` folder to the task.
3. Type `/job`, pick "job" from the menu, paste the job link, and send. If
   /job is not in the menu, type "Use the job skill:" followed by the link.
4. **First time only:** Claude reads your resume, shows you what it found, and
   asks for the details every application wants (address, phone, work
   authorization, start date and so on). They are saved in `profile.md` in
   your folder. Skip anything you like; Claude asks when an application needs
   it.
5. Claude shows how well you match the job and asks a few questions (up to
   6, plus some one-line quick checks). Short answers, rough numbers and
   "skip" are all fine.
6. Claude writes the resume and saves it as Word and PDF in
   `JobSearch\applications\<date> <company> - <role>`. Read it, and read the
   "Verify before sending" list: you are the one signing this resume. Ask for
   any changes in plain words.
7. Reply **audit** for a line-by-line check against what you told it. Claude
   also runs this itself before it fills in an application.
8. Reply **apply**. Claude opens the application and fills it in page by
   page. It stops and asks you to:
   - sign in or create an account (it never handles passwords or
     verification codes),
   - attach your resume file when it cannot attach it itself (it tells you
     which file),
   - answer anything your files do not cover,
   - fill in sensitive items yourself: SSN, date of birth, bank details,
     signatures and "I certify" boxes.
9. Claude stops on the last page, before Submit. Check each page, click
   Submit, then reply **submitted** so it is logged.

Other replies you can use at any time: **go** (skip the questions and write
the resume), **cover letter**, **library** (see your achievement library),
**profile** (see or change your saved details).

## What is in your JobSearch folder

- `profile.md` - your contact details and standard answers. Open it in
  Notepad and edit it whenever you like.
- `achievement_library.md` - everything you have done, role by role. It
  grows with each application, and every new resume draws on it.
- `applications.md` - every job you started, its fit and its status.
- `applications\` - one folder per job: the posting, your tailored resume (and
  cover letter, if you asked for one), and a record of what was entered in
  the form.
- `backups\` - a dated copy of your profile and library from before each
  change. Claude adds and updates files here but never deletes them.

## If something goes wrong

- **Claude says it cannot find your folder:** connect `Documents\JobSearch`
  to the Cowork task and send /job again.
- **The job page will not load:** copy the posting text from your browser and
  paste it in.
- **/job does not appear in Cowork:** install `job-search-plugin.zip` from
  Customize > Plugins > Add > Upload plugin, then type / and pick
  "job-search:job".
- **The resume runs over one page:** say "make it fit on one page".
- Cowork on Windows is in beta, so menu names may differ a little from the
  ones above.

Privacy: your resume, profile and library are files in your JobSearch folder.
Claude reads them during a task, the same as anything you paste into a chat.
It never asks for or saves your SSN, date of birth, bank details or passwords.
