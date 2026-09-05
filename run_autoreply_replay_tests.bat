@echo off
setlocal

rem -- Replay coverage for Simple Autoreply mode. Same safety contract as
rem    run_replay_tests.bat: never touch the real logbook, never run while a
rem    real WSJT-X is up. This suite additionally isolates the SETTINGS file
rem    (JIMMY_TEST_INI_PATH), because each scenario needs different Options
rem    values -- the operator's own Jimmy.ini is only read as a seed.
rem
rem    Unlike run_replay_tests.bat this script must own the Jimmy process: it
rem    restarts Jimmy once per scenario with a different settings file, so any
rem    Jimmy already running would be using the wrong configuration.

tasklist /FI "IMAGENAME eq wsjtx.exe" 2>NUL | find /I "wsjtx.exe" >NUL
if %ERRORLEVEL%==0 (
    echo ERROR: A real WSJT-X process is currently running.
    echo Replay testing must not run while real WSJT-X is up -- ask the user
    echo to close it first. This script will not close it for you.
    exit /b 1
)

tasklist /FI "IMAGENAME eq Jimmy.exe" 2>NUL | find /I "Jimmy.exe" >NUL
if %ERRORLEVEL%==0 (
    echo ERROR: Jimmy.exe is already running.
    echo This suite starts and stops its own Jimmy instances, one per scenario,
    echo each with a different settings file. Close Jimmy first and re-run.
    exit /b 1
)

set "JIMMY_TEST_DB_PATH=%TEMP%\JimmyAutoReplyTest_logbook.db"
echo Test mode: JIMMY_TEST_DB_PATH=%JIMMY_TEST_DB_PATH%
echo   (real logbook untouched; real settings file read as a seed but never written)
if exist "%JIMMY_TEST_DB_PATH%" del /q "%JIMMY_TEST_DB_PATH%"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.6 or later and ensure it is on your PATH.
    exit /b 1
)

set "JIMMY_EXE=%~dp0WSJTX_Controller\bin\Debug\Jimmy.exe"
if not exist "%JIMMY_EXE%" (
    echo ERROR: %JIMMY_EXE% not found. Build Jimmy first ^(build.bat^).
    exit /b 1
)

python "%~dp0JimmyReplayAutoReply.py"
exit /b %ERRORLEVEL%
