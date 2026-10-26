@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python -m fslang serve
) else (
  py -m fslang serve
)
if errorlevel 1 pause
