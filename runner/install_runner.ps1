<#
.SYNOPSIS
Installs or updates the job finder (job_skill runner) for the current user. No admin rights.

.DESCRIPTION
install_runner.cmd runs this after it has checked the release zip's SHA-256 and
extracted it to %LOCALAPPDATA%\JobRunner\app\<version>. The steps (spec 7.5):
  1. copy the app files (app\*.py) into %LOCALAPPDATA%\JobRunner\app\<version>;
  2. create %LOCALAPPDATA%\JobRunner\venv and pip install requirements.lock
     with --require-hashes (openpyxl, python-docx, reportlab, pypdf, tzdata);
  3. point the venv at the app folder (jobrunner_app.pth), so Task Scheduler
     can run "pythonw.exe -I -m jobrunner <cmd>";
  4. write %LOCALAPPDATA%\JobRunner\config.toml with the JobSearch folder;
  5. ask for the optional Adzuna and USAJOBS keys and store them in keys.toml,
     readable by this user only (kept on an update);
  6. check that Task Scheduler's command line can start the runner (the .pth
     and config.toml are UTF-8, so a non-ASCII user name works);
  7. register JobRunner-Daily (05:30, WakeToRun, StartWhenAvailable, 2 h limit)
     and JobRunner-Tick (every 15 minutes 06:00-22:00, IgnoreNew, 10 min limit),
     both run only while the user is logged on, with no stored password, and
     both start and keep running on battery;
  8. add the desktop shortcut "Run job finder now";
  9. run "jobrunner selftest --net", which prints PASS, WARN or FAIL lines and
     writes runner\out\status.json in the JobSearch folder.
A second run is an update: it keeps keys, config and tasks (re-registered in place).
Every Python call runs with -I, so nothing is imported from the folder the
installer was started from (Downloads).

.PARAMETER DryRun
Print every step (venv path, pip command, task definitions) and change nothing.

.PARAMETER NoTasks
Do every step except registering the scheduled tasks and the shortcut.

.PARAMETER AppSource
A local jobrunner-<version>.zip or an extracted folder holding app\*.py
(default: this script's own folder).

.PARAMETER InstallRoot
Where the runner lives (default %LOCALAPPDATA%\JobRunner). Tests use a temp folder.

.PARAMETER Folder
The JobSearch folder (default: Documents\JobSearch).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File install_runner.ps1 -DryRun -Folder C:\Temp\JobSearch
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoTasks,
    [switch]$NoKeys,
    [switch]$NoSelftest,
    [string]$AppSource = '',
    [string]$InstallRoot = '',
    [string]$Folder = '',
    [string]$Python = '',
    [string]$Version = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$DailyTask = 'JobRunner-Daily'
$TickTask = 'JobRunner-Tick'
$ShortcutName = 'Run job finder now.lnk'

function Write-Step([string]$Text) {
    Write-Output $Text
}

function Get-DefaultFolder {
    return (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'JobSearch')
}

function Find-Python([string]$Given) {
    if ($Given -and (Test-Path -LiteralPath $Given)) { return $Given }
    # The Python launcher by its full path (per-user, then all-users), never by a bare name.
    foreach ($py in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'),
                      (Join-Path $env:SystemRoot 'py.exe'))) {
        if (-not (Test-Path -LiteralPath $py)) { continue }
        $found = & $py -3.13 -I -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return ($found | Select-Object -First 1).Trim() }
    }
    $fallback = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path -LiteralPath $fallback) { return $fallback }
    return ''
}

function Get-AppFolder([string]$Source, [string]$Staging) {
    # A zip is expanded into an empty staging folder; a folder is used as it is.
    if ($Source -and (Test-Path -LiteralPath $Source -PathType Leaf)) {
        if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
        New-Item -ItemType Directory -Path $Staging -Force | Out-Null
        Expand-Archive -LiteralPath $Source -DestinationPath $Staging -Force
        $Source = $Staging
    }
    if (Test-Path -LiteralPath (Join-Path $Source 'app\jobrunner.py')) { return (Join-Path $Source 'app') }
    if (Test-Path -LiteralPath (Join-Path $Source 'jobrunner.py')) { return $Source }
    # Run from a clone of the repository: runner\ sits beside job\scripts\.
    $repo = Join-Path $Source '..\job\scripts'
    if (Test-Path -LiteralPath (Join-Path $repo 'jobrunner.py')) { return (Resolve-Path -LiteralPath $repo).Path }
    throw "no app\jobrunner.py in $Source"
}

function Get-AppVersion([string]$AppFolder) {
    $jobfs = Join-Path $AppFolder 'jobfs.py'
    if (Test-Path -LiteralPath $jobfs) {
        $m = Select-String -LiteralPath $jobfs -Pattern '^VERSION = "([0-9.]+)"' | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    return '1.2.0'
}

function Get-TomlString([string]$Text) {
    # A TOML basic string: backslashes and quotes escaped, any letter kept (the file is UTF-8).
    return '"' + $Text.Replace('\', '\\').Replace('"', '\"') + '"'
}

function Write-Utf8([string]$Path, [string[]]$Lines) {
    # UTF-8 without a BOM: Windows PowerShell 5.1's -Encoding Ascii turns a letter
    # such as an accented e in a user name into '?', and then the runner never starts.
    [IO.File]::WriteAllText($Path, (($Lines -join "`r`n") + "`r`n"), (New-Object Text.UTF8Encoding $false))
}

function Get-TaskPlan([string]$Pythonw, [string]$WorkDir) {
    $user = [Environment]::UserDomainName + '\' + [Environment]::UserName
    $daily = [ordered]@{
        Name = $DailyTask
        Trigger = 'daily 05:30'
        Action = "$Pythonw -I -m jobrunner daily"
        WorkingDirectory = $WorkDir
        Settings = ('StartWhenAvailable WakeToRun AllowStartIfOnBatteries DontStopIfGoingOnBatteries ' +
                    'ExecutionTimeLimit=2h MultipleInstances=IgnoreNew')
        Principal = "$user, Interactive (only while logged on, no stored password), RunLevel Limited"
    }
    $tick = [ordered]@{
        Name = $TickTask
        Trigger = 'daily 06:00, every 15 minutes for 16 hours (06:00-22:00)'
        Action = "$Pythonw -I -m jobrunner tick"
        WorkingDirectory = $WorkDir
        Settings = ('MultipleInstances=IgnoreNew ExecutionTimeLimit=10m StartWhenAvailable ' +
                    'AllowStartIfOnBatteries DontStopIfGoingOnBatteries')
        Principal = "$user, Interactive (only while logged on, no stored password), RunLevel Limited"
    }
    return @($daily, $tick)
}

function Show-Task($Def) {
    Write-Step ("TASK {0}: {1}; action {2}; in {3}; {4}; {5}" -f $Def.Name, $Def.Trigger, $Def.Action,
            $Def.WorkingDirectory, $Def.Settings, $Def.Principal)
}

function Register-RunnerTask([string]$Pythonw, [string]$WorkDir) {
    $user = [Environment]::UserDomainName + '\' + [Environment]::UserName
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-I -m jobrunner daily' -WorkingDirectory $WorkDir
    $trigger = New-ScheduledTaskTrigger -Daily -At '05:30'
    # Task Scheduler's defaults skip a run on battery and stop one when the cable is pulled.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName $DailyTask -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'job_skill: the 05:30 job finder run' -Force | Out-Null

    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-I -m jobrunner tick' -WorkingDirectory $WorkDir
    $trigger = New-ScheduledTaskTrigger -Daily -At '06:00'
    $repeat = New-ScheduledTaskTrigger -Once -At '06:00' -RepetitionInterval (New-TimeSpan -Minutes 15) `
        -RepetitionDuration (New-TimeSpan -Hours 16)
    $trigger.Repetition = $repeat.Repetition
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName $TickTask -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'job_skill: applies events and answers requests every 15 minutes' `
        -Force | Out-Null
}

function Add-RunnerShortcut([string]$Pythonw, [string]$WorkDir) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $desktop $ShortcutName))
    $lnk.TargetPath = $Pythonw
    $lnk.Arguments = '-I -m jobrunner daily'
    $lnk.WorkingDirectory = $WorkDir
    $lnk.Description = 'Run the job finder now (it skips work already done today)'
    $lnk.Save()
}

function Protect-UserOnly([string]$Path) {
    # Only the current user may read or change the file (no inherited entries).
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Read-Key([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Write-KeyFile([string]$Path) {
    Write-Step 'Optional keys (press Enter to skip each). They stay on this PC, in a file only you can read.'
    $lines = @('# job_skill runner keys: never shared, never printed.')
    $adzunaId = Read-Key 'Adzuna app_id'
    if ($adzunaId) {
        $adzunaKey = Read-Key 'Adzuna app_key'
        $lines += '[adzuna]', ('app_id = ' + (Get-TomlString $adzunaId)), ('app_key = ' + (Get-TomlString $adzunaKey))
    }
    $usaKey = Read-Key 'USAJOBS API key'
    if ($usaKey) {
        $usaEmail = Read-Host -Prompt 'The email you used to request the USAJOBS key'
        $lines += '[usajobs]', ('key = ' + (Get-TomlString $usaKey)), ('email = ' + (Get-TomlString $usaEmail.Trim()))
    }
    Write-Utf8 $Path $lines
    Protect-UserOnly $Path
}

# ---------- plan ----------

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'JobRunner' }
if (-not $Folder) { $Folder = Get-DefaultFolder }
if (-not $AppSource) { $AppSource = $PSScriptRoot }
$staging = Join-Path $InstallRoot 'staging'
if ($DryRun) { $staging = Join-Path ([IO.Path]::GetTempPath()) ('jobrunner_dryrun_' + $PID) }
$appFolder = Get-AppFolder $AppSource $staging
if (-not $Version) { $Version = Get-AppVersion $appFolder }
$appDir = Join-Path $InstallRoot ('app\' + $Version)
$venv = Join-Path $InstallRoot 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
$lock = ''
foreach ($dir in @($appFolder, (Split-Path $appFolder -Parent), $PSScriptRoot)) {
    if (-not $lock -and (Test-Path -LiteralPath (Join-Path $dir 'requirements.lock'))) { $lock = Join-Path $dir 'requirements.lock' }
}
if (-not $lock) { throw "requirements.lock not found beside $appFolder" }
$pth = Join-Path $venv 'Lib\site-packages\jobrunner_app.pth'
$config = Join-Path $InstallRoot 'config.toml'
$keys = Join-Path $InstallRoot 'keys.toml'
$basePython = Find-Python $Python
$pipArgs = @('-I', '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:', '--disable-pip-version-check',
    '-r', $lock)

Write-Step "JOB FINDER $Version"
Write-Step "APP $appDir (from $appFolder)"
Write-Step "VENV $venv"
Write-Step ("PYTHON " + $(if ($basePython) { $basePython } else { 'not found: run install_runner.cmd, which installs Python 3.13 with winget' }))
Write-Step ("CREATE " + $(if ($basePython) { $basePython } else { 'python' }) + " -I -m venv $venv")
Write-Step ("PIP $venvPython " + ($pipArgs -join ' '))
Write-Step "PTH $pth -> $appDir"
Write-Step "CONFIG $config (folder = $Folder)"
Write-Step ("KEYS $keys" + $(if (Test-Path -LiteralPath $keys) { ' (kept)' } else { ' (asked once, user-only ACL)' }))
foreach ($def in (Get-TaskPlan $pythonw $appDir)) { Show-Task $def }
Write-Step "SHORTCUT Desktop\$ShortcutName -> $pythonw -I -m jobrunner daily"
Write-Step "SELFTEST $venvPython -I -m jobrunner selftest --net --root $Folder"

if ($DryRun) {
    if ($staging -and (Test-Path -LiteralPath $staging)) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Write-Step 'DRY RUN nothing was changed (no venv, no packages, no tasks, no shortcut).'
    exit 0
}
if (-not $basePython) {
    Write-Step 'ERROR Python 3.13 was not found. Run install_runner.cmd, or install Python 3.13 for this user.'
    exit 2
}

# ---------- install ----------

New-Item -ItemType Directory -Path $appDir -Force | Out-Null
if ((Resolve-Path -LiteralPath $appFolder).Path -ne (Resolve-Path -LiteralPath $appDir).Path) {
    Copy-Item -Path (Join-Path $appFolder '*.py') -Destination $appDir -Force
}
Copy-Item -LiteralPath $lock -Destination (Join-Path $appDir 'requirements.lock') -Force
Write-Step "COPIED app files to $appDir"

if (-not (Test-Path -LiteralPath $venvPython)) {
    # -I: a venv.py (or any module) in the folder the installer started from is never imported.
    & $basePython -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Step "ERROR could not create $venv"; exit 1 }
    Write-Step "CREATED $venv"
}
& $venvPython @pipArgs
if ($LASTEXITCODE -ne 0) { Write-Step 'ERROR pip could not install the pinned packages (see above). Nothing was registered.'; exit 2 }
Write-Step 'INSTALLED packages (hash-checked)'

Write-Utf8 $pth @($appDir)
New-Item -ItemType Directory -Path $Folder -Force | Out-Null
Write-Utf8 $config @(
    '# job_skill runner: written by install_runner.ps1',
    ('folder = ' + (Get-TomlString $Folder)),
    ('version = ' + (Get-TomlString $Version)))
Write-Step "WROTE $config"

# The command line Task Scheduler will run, without --root: it must import
# jobrunner through the .pth and find the folder through config.toml.
$oldHome, $oldPref = $env:JOBRUNNER_HOME, $ErrorActionPreference
$env:JOBRUNNER_HOME = $InstallRoot
$ErrorActionPreference = 'Continue'  # Windows PowerShell 5.1 turns a native stderr line into a stop
try { $check = (& $venvPython -I -m jobrunner status 2>&1 | ForEach-Object { "$_" }) -join ' '; $checkCode = $LASTEXITCODE }
finally { $env:JOBRUNNER_HOME = $oldHome; $ErrorActionPreference = $oldPref }
if ($checkCode -ne 0) {
    Write-Step ('ERROR the job finder cannot start from Task Scheduler: ' + $check.Trim())
    Write-Step 'Nothing was registered. Run this installer again; if it stops here again, tell Claude this line.'
    exit 1
}
Write-Step 'CHECKED the job finder starts (config.toml and jobrunner_app.pth)'

if (-not $NoKeys -and -not (Test-Path -LiteralPath $keys)) { Write-KeyFile $keys; Write-Step "WROTE $keys (user-only)" }
elseif (Test-Path -LiteralPath $keys) { Protect-UserOnly $keys; Write-Step "KEPT $keys" }

if ($NoTasks) {
    Write-Step 'TASKS not registered (-NoTasks)'
} else {
    Register-RunnerTask $pythonw $appDir
    Add-RunnerShortcut $pythonw $appDir
    Write-Step "REGISTERED $DailyTask and $TickTask; shortcut on the desktop"
}

if (-not $NoSelftest) {
    & $venvPython -I -m jobrunner selftest --net --root $Folder
    if ($LASTEXITCODE -ne 0) { Write-Step 'SELFTEST had a FAIL line above. In /job setup, tell Claude which line; WARN lines are fine.' }
}
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
Write-Step 'DONE'
exit 0
