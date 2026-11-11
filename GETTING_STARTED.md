# Start here

This is a job-search helper that runs inside the Claude desktop app on a
Windows PC. Each morning it finds fresh jobs that fit you and gets a resume
ready for each one. You type `/job`, Claude fills in the applications one at
a time, and you click Submit yourself.

You do not need a GitHub account, Claude Code, or any coding. You download
one file, unzip it, and follow the steps below. Plan on about 45 minutes the
first day.

## What you need

- A Windows PC.
- The Claude desktop app, signed in on a paid plan (Pro or higher). Get the
  app at https://claude.ai/download.
- Your most complete resume, as a Word or PDF file.
- A Gmail account (Claude reads replies and job alerts there; it never
  sends, deletes or moves mail).

## Step 1: download and unzip

1. Click this link. The download starts right away:
   https://github.com/dumpstering/job_skill/releases/latest/download/job-skill-full.zip
   (On the release page itself, it is the file named `job-skill-full.zip`
   under "Assets". Ignore the two "Source code" files.)
2. Open your Downloads folder. Right-click `job-skill-full.zip` and choose
   **Extract All**, then **Extract**. You get a folder named `job-skill`.
   Do not skip this: files opened from inside the zip will not work.

What is in the folder:

| File | What it is | When you use it |
|---|---|---|
| `START_HERE.txt` | this page | now |
| `SETUP_GUIDE.txt` | the full guide: every step, daily use, problems | when a step here needs more detail |
| `job-search-plugin.zip` | the helper Claude loads | Step 3 |
| `job.zip` | the same helper in another form | only if the plugin will not install |
| `install_runner.cmd` | installs the morning job finder | Step 6 |
| `jobrunner-(version).zip` | the job finder itself; the installer uses it | never opened by hand |

## Step 2: make your job folder

1. In File Explorer, open **Documents** and create a folder named
   `JobSearch`.
2. Copy your resume into it. Put the word "resume" in the file name, for
   example `Resume - full.docx`.
3. If someone set this up with you and sent starting files
   (`achievement_library.md`, `techniques.md`, `publications.md`,
   `search_profile.toml`), copy the first three into `JobSearch`, and put
   `search_profile.toml` in a folder named `config` inside `JobSearch`.
   Claude then starts from them and only asks about what is missing.

## Step 3: add the helper to Claude

1. Open the Claude desktop app.
2. Open **Settings > Capabilities**. Switch on "Code execution and file
   creation" and check "Allow network egress".
3. Open the **Plugins** page, choose **Add > Upload plugin**, and pick
   `job-search-plugin.zip` from the `job-skill` folder. (Menu names in the
   app change now and then; this click path is UNVERIFIED. If you cannot
   find it, ask Claude: "How do I upload a plugin zip in this app?")
4. If the plugin will not install, upload `job.zip` as a skill instead.
   Everything still works.

## Step 4: connect your folder and Gmail

1. Start a new task in Claude. Connect your `Documents\JobSearch` folder and
   allow it. You do this once.
2. In Claude's settings, open **Connectors** and connect Gmail.

## Step 5: run setup

Type this in Claude and press Enter:

```
/job setup
```

Claude reads your resume, shows you what it found, and asks a short list of
questions (contact details, work authorization, lowest pay worth applying
for, and so on). "Skip" is a fine answer to any of them. Say yes to the two
scheduled tasks it offers.

## Step 6: install the morning job finder

1. In the `job-skill` folder, double-click `install_runner.cmd`.
2. If Windows shows a blue "Windows protected your PC" box, click
   **More info**, then **Run anyway**.
3. It asks for two optional free keys (Adzuna and USAJOBS). Press Enter to
   skip either one; you can add them later by running the installer again.
4. When it says Done, double-click the new desktop shortcut **Run job
   finder now**. The first run takes about 15-20 minutes.
5. Back in Claude, type `/job setup` once more to finish.

It needs no administrator rights. It runs by itself at 5:30 every morning.

## Step 7: approve your base resumes

Type `/job variants`. Claude builds one base resume for each kind of job you
are going after and shows it to you. Each takes 15-20 minutes to read and
approve. Do the two or three closest to your last jobs first; the rest can
wait a day.

## Every day after that

Open Claude and type:

```
/job
```

Claude shows a short card for the day and starts on job #1. For each job it
fills the form, tells you which file to attach, and stops. You look the page
over and click Submit, then type `s`. Type `short` any time for an easy day.

Other things you can type:

| Type | What happens |
|---|---|
| `/job paste` | paste several job pages you copied from any site; Claude prepares them all |
| `/job linkedin` | Claude works LinkedIn jobs in your own Chrome (only if you turned it on in setup; LinkedIn's rules do not allow automated tools and it can restrict an account) |
| `/job variants` | make or update a base resume |
| `/job inbox` | check Gmail for replies now |
| `/job status` | where every application stands |
| `/job week` | the weekly summary |
| `/job settings` | change a setting in plain words ("aim for 25 a day") |

Claude fills forms in its own built-in browser, so keep the Claude app open
and its browser pane visible while it works. Sign in to a job site once
inside that browser when it asks. Google Chrome with the Claude extension
is only needed for `/job linkedin`.

## What Claude will never do

- Click Submit. That is always you.
- Create accounts, type passwords or codes, or solve CAPTCHAs.
- Send, delete or move email.
- Put anything on a resume that your own history does not support.

## If something goes wrong

- **Claude does not know `/job`.** The plugin did not load. Repeat Step 3,
  or upload `job.zip` as a skill, then start a new task.
- **The card says the job finder needs an update.** Download the zip again
  (Step 1) and double-click the new `install_runner.cmd`.
- **No jobs today.** Double-click **Run job finder now**, wait for it to
  finish, then type `/job`.
- **Anything else.** Tell Claude what you saw, in your own words. It can
  read the job finder's log and say what to do. `SETUP_GUIDE.txt` has a
  longer list of problems and fixes.

## Updating

Download the zip again (Step 1), unzip it, upload the new
`job-search-plugin.zip` (Step 3), and double-click the new
`install_runner.cmd`. Your `JobSearch` folder and everything in it stay as
they are.
