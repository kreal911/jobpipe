@echo off
REM Nightly jobpipe run for Windows Task Scheduler.
REM The bundled com.jobpipe.nightly.plist is macOS/launchd only and does not
REM run here; this is the Windows equivalent.
REM
REM Registered with:
REM   schtasks /Create /TN "jobpipe nightly" /TR "<path>\run_nightly.cmd" /SC DAILY /ST 06:00 /RL LIMITED /F
REM Remove with:
REM   schtasks /Delete /TN "jobpipe nightly" /F

setlocal
set "JOBPIPE_DIR=%~dp0"
REM Full path only if python is not on PATH, e.g.
REM set "PYTHON=C:\Users\YOU\AppData\Local\Programs\Python\Python312\python.exe"
set "PYTHON=python"

cd /d "%JOBPIPE_DIR%" || exit /b 1
if not exist "logs" mkdir "logs"

REM Deliberately NOT `jobpipe run`. `run` scores only jobs that have no score
REM yet, so an edited weight or threshold in profile.toml would never reach the
REM digest. Running the four steps explicitly, with --rescore, is what makes a
REM config change take effect on the next nightly pass. `run` also does digest
REM and packets itself, so calling it here would do both of them twice.
echo ---- run started %DATE% %TIME% >> "logs\run.log"

"%PYTHON%" -m jobpipe fetch >> "logs\run.log" 2>> "logs\run.err"
set "RC=%ERRORLEVEL%"

"%PYTHON%" -m jobpipe score --rescore >> "logs\run.log" 2>> "logs\run.err"
"%PYTHON%" -m jobpipe digest >> "logs\run.log" 2>> "logs\run.err"

REM Application packets: cover letter, answer sheet and resume choice per
REM APPLY role. Regenerated nightly, and packets for roles you have marked
REM applied are pruned, so the queue stays current on its own.
"%PYTHON%" -m jobpipe packets >> "logs\run.log" 2>> "logs\run.err"

echo ---- run finished %DATE% %TIME% fetch_exit=%RC% >> "logs\run.log"
exit /b %RC%
