@echo off
REM Project Chronos - daily log snapshot for Windows Task Scheduler.
REM
REM Register this once in Task Scheduler:
REM   Program:  C:\path\to\chronos-trading-system\snapshot_and_push.bat
REM   Trigger:  Daily, 00:20, time zone Eastern Time (New York)
REM   Settings: "Run task as soon as possible after a scheduled start is missed"
REM             (so a sleeping machine still pushes, late, the same night)
REM
REM The script picks the previous completed NY day for itself, so it does not
REM matter that a missed trigger fires an hour or a day late: it will push the
REM day that is finished, not today's half-written one. It never needs a human:
REM a failed push is logged and retried on the next run.
REM
REM DIAGNOSING IT FROM THE SCHEDULED CONTEXT
REM A scheduled task inherits the machine PATH, which is not the PATH an
REM interactive Git Bash session builds for itself. To see what the task can
REM actually reach, edit the task's "Add arguments" to: --doctor
REM (or run:  snapshot_and_push.bat --doctor  from a command prompt).
REM Any argument given here is passed straight through.
setlocal
cd /d "%~dp0"
python bot\live\snapshot_and_push.py %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo snapshot_and_push exited %RC% >> logs\SNAPSHOT_PUSH.log
exit /b %RC%
