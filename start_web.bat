@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 這個檔案盡量不要改：自動更新、安裝套件、啟動都在 fslang\launcher.py 裡做
rem （cmd 是邊執行邊讀 .bat，自動更新時如果這個檔案被換掉，後面會讀到錯的位置）

rem 找 Python（python 或 py 都可以；Microsoft Store 的空殼 python 會被略過）
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython
%PY% -m fslang.launcher %* & exit /b

:nopython
echo 找不到 Python。
echo 請到 https://www.python.org/downloads/ 下載安裝，安裝畫面最下面要勾選 "Add python.exe to PATH"，
echo 裝好之後再雙擊一次 start_web.bat。
pause
exit /b 1
