<#
.SYNOPSIS
Removes the job finder (job_skill runner) for the current user. No admin rights.

.DESCRIPTION
Removes the JobRunner-Daily and JobRunner-Tick scheduled tasks, the app folder
(%LOCALAPPDATA%\JobRunner\app), the venv's jobrunner_app.pth and the desktop
shortcut "Run job finder now". It never touches the JobSearch folder (resumes,
tracker, queue, everything personal), and it keeps config.toml and keys.toml
so a later install finds them; delete those two by hand to forget the keys.

.PARAMETER DryRun
Print what would be removed and remove nothing.

.PARAMETER InstallRoot
Where the runner lives (default %LOCALAPPDATA%\JobRunner).

.PARAMETER NoTasks
Leave the scheduled tasks alone (tests).
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$NoTasks,
    [string]$InstallRoot = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA 'JobRunner' }
$app = Join-Path $InstallRoot 'app'
$pth = Join-Path $InstallRoot 'venv\Lib\site-packages\jobrunner_app.pth'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Run job finder now.lnk'
$tasks = @('JobRunner-Daily', 'JobRunner-Tick')

foreach ($name in $tasks) {
    if ($NoTasks) { Write-Output "TASK $name left alone (-NoTasks)"; continue }
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { Write-Output "TASK $name not registered"; continue }
    if ($DryRun) { Write-Output "WOULD REMOVE task $name"; continue }
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "REMOVED task $name"
}
foreach ($path in @($app, $pth)) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    if ($DryRun) { Write-Output "WOULD REMOVE $path"; continue }
    Remove-Item -LiteralPath $path -Recurse -Force
    Write-Output "REMOVED $path"
}
if (-not $NoTasks -and (Test-Path -LiteralPath $shortcut)) {
    if ($DryRun) { Write-Output "WOULD REMOVE $shortcut" }
    else { Remove-Item -LiteralPath $shortcut -Force; Write-Output "REMOVED $shortcut" }
}
Write-Output 'KEPT the JobSearch folder, config.toml and keys.toml'
exit 0
