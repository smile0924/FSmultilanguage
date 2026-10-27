@echo off
rem Share mode: colleagues on the same network can open this computer's site.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files on Chinese Windows.
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed. Opening https://nodejs.org/ - install the LTS version, then run this file again.
  start "" https://nodejs.org/
  pause
  exit /b 1
)
set HOST=0.0.0.0
start "" http://localhost:5178
node server.js
pause
