@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 診斷工具：把結果截圖給負責的人

rem 找 Python（python 或 py 都可以；Microsoft Store 的空殼 python 會被略過）
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython
%PY% -m fslang.doctor & pause & exit /b

:nopython
echo 找不到 Python。
echo 請到 https://www.python.org/downloads/ 下載安裝，安裝畫面最下面要勾選 "Add python.exe to PATH"，
echo 裝好之後再雙擊一次 start_web.bat。
pause
exit /b 1
