@echo off
rem job_skill: install or update the job finder for this Windows user (no admin).
rem build_zip.py fills in @@VERSION@@ and @@ZIP_SHA256@@ for each release.
rem Steps (spec 7.5): download jobrunner-<version>.zip with curl, check its
rem SHA-256 with certutil (stop on a mismatch: nothing is installed), install
rem Python 3.13 for this user with winget, extract with tar to
rem %LOCALAPPDATA%\JobRunner\app\<version>, then run install_runner.ps1.
rem Testing: install_runner.cmd C:\path\jobrunner-<version>.zip uses that local
rem file instead of the download (it is still hash-checked).
setlocal EnableExtensions
set "VER=@@VERSION@@"
set "SHA=@@ZIP_SHA256@@"
set "URL=https://github.com/dumpstering/job_skill/releases/download/v%VER%/jobrunner-%VER%.zip"
set "JR=%LOCALAPPDATA%\JobRunner"
set "APP=%JR%\app\%VER%"
set "ZIP=%TEMP%\jobrunner-%VER%.zip"

echo Job finder %VER%: installing for %USERNAME% (no admin needed).
if not "%~1"=="" (
    if not exist "%~1" (
        echo ERROR %~1 not found.
        goto :fail
    )
    copy /y "%~1" "%ZIP%" >nul || goto :fail
) else (
    curl.exe -fsSL --retry 2 -o "%ZIP%" "%URL%"
    if errorlevel 1 (
        echo ERROR could not download %URL%
        goto :fail
    )
)

set "GOT="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%ZIP%" SHA256') do if not defined GOT set "GOT=%%H"
set "GOT=%GOT: =%"
if /i not "%GOT%"=="%SHA%" (
    echo ERROR the download does not match its SHA-256. Nothing was installed.
    del /q "%ZIP%" >nul 2>nul
    goto :fail
)
echo SHA-256 ok.

set "PY="
for /f "delims=" %%P in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY (
    where winget >nul 2>nul
    if errorlevel 1 (
        echo ERROR Python 3.13 is not installed and winget is missing. Install Python 3.13 from python.org for this user, then run this again.
        goto :fail
    )
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    for /f "delims=" %%P in ('py -3.13 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)
if not defined PY (
    echo ERROR Python 3.13 was not found after the winget install. Sign out and in, then run this again.
    goto :fail
)
echo Python: %PY%

if not exist "%APP%" mkdir "%APP%"
tar -xf "%ZIP%" -C "%APP%"
if errorlevel 1 (
    echo ERROR could not extract %ZIP%
    goto :fail
)
del /q "%ZIP%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%\install_runner.ps1" -AppSource "%APP%" -Python "%PY%" -Version %VER%
if errorlevel 1 goto :fail
echo.
echo Done. The job finder runs every morning at 05:30; "Run job finder now" is on your desktop.
pause
exit /b 0

:fail
echo.
echo The install stopped. Nothing else was changed.
pause
exit /b 1
