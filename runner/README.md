# runner: the job finder's Windows installer

The job finder is `job/scripts/jobrunner.py` (with the rest of `job/scripts/`)
running on his PC under Task Scheduler. This folder holds what installs it.
Claude's side of the runner (modes, events, requests, the heartbeat, the card)
is in [job/references/runner.md](../job/references/runner.md).

## Files

| File | What |
|---|---|
| `install_runner.cmd` | Template. `build_zip.py` replaces `@@VERSION@@` and `@@ZIP_SHA256@@` and ships it beside the release zip. It downloads `jobrunner-<version>.zip` with `curl.exe`, checks it with `certutil -hashfile ... SHA256` (a mismatch stops before anything is installed), installs Python 3.13 per user with `winget` if `py -3.13` is missing, extracts with `tar` to `%LOCALAPPDATA%\JobRunner\app\<version>`, then runs `install_runner.ps1`. `install_runner.cmd <local zip>` uses a local zip instead of the download (still hash-checked). |
| `install_runner.ps1` | The install (no admin): app files into `app\<version>`, `venv`, `pip install --require-hashes --only-binary=:all: -r requirements.lock`, `venv\Lib\site-packages\jobrunner_app.pth` (so `pythonw.exe -I -m jobrunner` finds the app), `config.toml` (`folder = '<Documents>\JobSearch'`), the optional keys prompt into `keys.toml` with a user-only ACL, the two scheduled tasks, the desktop shortcut "Run job finder now", and `jobrunner selftest --net`. A second run updates in place and keeps keys and config. |
| `uninstall_runner.ps1` | Removes the two tasks, `%LOCALAPPDATA%\JobRunner\app`, the `.pth` file and the shortcut. Never touches the JobSearch folder; keeps `config.toml` and `keys.toml`. |
| `requirements.lock` | openpyxl, python-docx, reportlab, pypdf and tzdata, plus every dependency, pinned to one Python 3.13 win_amd64 (or pure) wheel each by SHA-256. |

The release zip `jobrunner-<version>.zip` (built by `build_zip.py`) holds
`app/` (a copy of `job/scripts/*.py`), `install_runner.ps1`,
`uninstall_runner.ps1` and `requirements.lock`.

## The scheduled tasks

| Task | Trigger | Settings | Action |
|---|---|---|---|
| JobRunner-Daily | daily 05:30 | StartWhenAvailable, WakeToRun, 2 h limit, IgnoreNew | `venv\Scripts\pythonw.exe -I -m jobrunner daily` |
| JobRunner-Tick | daily 06:00, every 15 minutes for 16 hours (06:00-22:00) | IgnoreNew, 5 min limit | `venv\Scripts\pythonw.exe -I -m jobrunner tick` |

Both run as him, LogonType Interactive (only while he is logged on, no stored
password), RunLevel Limited. Nothing asks for elevation. `-I` keeps the
JobSearch folder, PYTHONPATH and user site-packages off `sys.path`; the app
folder comes from the `.pth` file.

## install_runner.ps1 switches

| Switch | Use |
|---|---|
| `-DryRun` | Print the plan (venv path, pip command, both task definitions, shortcut, selftest) and change nothing. |
| `-NoTasks` | Install everything but the tasks and the shortcut. |
| `-AppSource <zip or folder>` | A local `jobrunner-<version>.zip`, an extracted folder, or a repo checkout (default: the script's folder; from the repo it finds `job/scripts`). |
| `-InstallRoot <dir>` | Install somewhere other than `%LOCALAPPDATA%\JobRunner` (tests). |
| `-Folder <dir>` | The JobSearch folder (default `Documents\JobSearch`). |
| `-Python <exe>` | The base Python 3.13 (default `py -3.13`, then `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`). |
| `-NoKeys`, `-NoSelftest`, `-Version` | Skip the keys prompt; skip the selftest; force the version label. |

## Commands

```
powershell -NoProfile -File runner\install_runner.ps1 -DryRun -Folder C:\Temp\JobSearch
powershell -NoProfile -File runner\install_runner.ps1 -NoTasks -NoKeys -AppSource C:\Temp\jobrunner-1.2.0.zip -InstallRoot C:\Temp\JR -Folder C:\Temp\JobSearch
powershell -NoProfile -File runner\uninstall_runner.ps1 -DryRun
.venv\Scripts\python tests\test_runner.py        # lock, tick, requests, boundary, selftest, installer dry run
.venv\Scripts\python tests\test_today_view.py    # the card and next
.venv\Scripts\python tests\test_daily_e2e.py     # the offline end-to-end day
.venv\Scripts\python tests\test_daily_e2e.py --make   # rebuild tests\fixtures\folder
```

The runner by hand (offline, against the board fixtures):

```
python job\scripts\jobrunner.py daily --root <copy of tests\fixtures\folder> --offline tests\fixtures\boards --date 2026-11-06
python job\scripts\jobrunner.py tick --root <root>
python job\scripts\jobrunner.py selftest --root <root> [--net]
python job\scripts\jobrunner.py status --root <root>
python job\scripts\jobrunner.py request more --root <root> --json "{\"limit\": 5}"
python job\scripts\today_view.py card --root <root>
```

With `--offline` (and in mode S) the lock is `state\run.lock` in the root,
and keys come from `<offline dir>\keys.toml` (none: the aggregators say
NOKEY), so tests never touch `%LOCALAPPDATA%\JobRunner`. Set `JOBRUNNER_HOME`
to point the runner at another home folder.

Tests never install Python, never register tasks and never write under the
real `%LOCALAPPDATA%\JobRunner`. `JOB_SKILL_LIVE=1` adds a real install
(pip from PyPI) into a temp folder with `-NoTasks`. PSScriptAnalyzer runs when
it is installed, or when `JOB_SKILL_PSSA` names its module folder
(`Save-Module PSScriptAnalyzer -Path <dir>` fetches it without installing).

## Updating the lock

Pins change only on purpose. For each package, read
`https://pypi.org/pypi/<name>/<version>/json`, keep the `cp313-cp313-win_amd64`
and `py3-none-any` wheel SHA-256 digests, check `requires_dist` for new
dependencies, and confirm with
`pip download --require-hashes --only-binary=:all: --platform win_amd64 --python-version 3.13 -r runner\requirements.lock -d <tmp>`.

## Manual check (once per release, a Windows 11 user without admin)

1. Double-click the built `install_runner.cmd`: Python installs per user, the venv is created, both tasks show in `Get-ScheduledTask JobRunner-*`, and the selftest prints PASS lines. Run it again: nothing breaks, keys are kept.
2. Change one byte of the zip and run the .cmd on it: the SHA-256 check fails and nothing is installed.
3. Run `uninstall_runner.ps1`: the tasks and `%LOCALAPPDATA%\JobRunner\app` are gone; `Documents\JobSearch` is untouched.
