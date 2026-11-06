@echo off
rem job_skill: install or update the job finder for this Windows user (no admin).
rem build_zip.py fills in @@VERSION@@ and @@ZIP_SHA256@@ for each release.
rem Steps (spec 7.5): download jobrunner-<version>.zip with curl, check its
rem SHA-256 with certutil (stop on a mismatch: nothing is installed), install
rem Python 3.13 for this user with winget, extract with tar to
rem %LOCALAPPDATA%\JobRunner\app\<version>, then run install_runner.ps1.
rem Every program is called by its full path, never by a bare name: a file
rem named certutil.bat (or curl, tar, py, powershell) saved beside this
rem installer in Downloads would otherwise run in its place. Python runs
rem with -I, so nothing is imported from the folder this starts in.
rem Testing: install_runner.cmd C:\path\jobrunner-<version>.zip uses that local
rem file instead of the download (it is still hash-checked).
setlocal EnableExtensions
set "NoDefaultCurrentDirectoryInExePath=1"
set "VER=@@VERSION@@"
set "SHA=@@ZIP_SHA256@@"
set "URL=https://github.com/dumpstering/job_skill/releases/download/v%VER%/jobrunner-%VER%.zip"
set "JR=%LOCALAPPDATA%\JobRunner"
set "APP=%JR%\app\%VER%"
set "ZIP=%TEMP%\jobrunner-%VER%.zip"
set "SYS=%SystemRoot%\System32"
set "CURL=%SYS%\curl.exe"
set "CERTUTIL=%SYS%\certutil.exe"
set "TAR=%SYS%\tar.exe"
set "PS=%SYS%\WindowsPowerShell\v1.0\powershell.exe"
set "WINGET=%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe"
set "PYUSER=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
set "PYALL=%SystemRoot%\py.exe"
set "PY313=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"

echo Job finder %VER%: installing for %USERNAME% (no admin needed).
if not "%~1"=="" (
    if not exist "%~f1" (
        echo ERROR %~1 not found.
        goto :fail
    )
    copy /y "%~f1" "%ZIP%" >nul || goto :fail
) else (
    "%CURL%" -fsSL --retry 2 -o "%ZIP%" "%URL%"
    if errorlevel 1 (
        echo ERROR could not download %URL%
        goto :fail
    )
)

set "GOT="
rem "call" lets the inner cmd expand the paths, so a ( or space in a path never breaks the for line.
for /f "skip=1 delims=" %%H in ('call "%%CERTUTIL%%" -hashfile "%%ZIP%%" SHA256') do if not defined GOT set "GOT=%%H"
set "GOT=%GOT: =%"
if /i not "%GOT%"=="%SHA%" (
    echo ERROR the download does not match its SHA-256. Nothing was installed.
    del /q "%ZIP%" >nul 2>nul
    goto :fail
)
echo SHA-256 ok.

set "PY="
call :findpy
if not defined PY (
    if not exist "%WINGET%" (
        echo ERROR Python 3.13 is not installed and winget is missing. Install Python 3.13 from python.org for this user, then run this again.
        goto :fail
    )
    "%WINGET%" install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    call :findpy
)
if not defined PY (
    echo ERROR Python 3.13 was not found after the winget install. Sign out and in, then run this again.
    goto :fail
)
echo Python: %PY%

if not exist "%APP%" mkdir "%APP%"
"%TAR%" -xf "%ZIP%" -C "%APP%"
if %errorlevel% neq 0 (
    echo ERROR could not extract %ZIP%
    goto :fail
)
del /q "%ZIP%" >nul 2>nul

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%APP%\install_runner.ps1" -AppSource "%APP%" -Python "%PY%" -Version %VER%
rem (not "if errorlevel 1": PowerShell gives a negative code when it cannot start the script)
if %errorlevel% neq 0 goto :fail
echo.
echo Done. The job finder runs every morning at 05:30; "Run job finder now" is on your desktop.
pause
exit /b 0

:findpy
rem The Python launcher by its full path (per-user, then all-users), then the usual per-user folder.
if not defined PY if exist "%PYUSER%" call :trypy "%PYUSER%"
if not defined PY if exist "%PYALL%" call :trypy "%PYALL%"
if not defined PY if exist "%PY313%" set "PY=%PY313%"
exit /b 0

:trypy
set "PYEXE=%~1"
for /f "delims=" %%P in ('call "%%PYEXE%%" -3.13 -I -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
exit /b 0

:fail
echo.
echo The install stopped. Nothing else was changed.
pause
exit /b 1
