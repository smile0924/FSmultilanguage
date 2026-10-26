@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 找 Python（python 或 py 都可以；Microsoft Store 的空殼 python 會被略過）
set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY goto :nopython

rem 第一次執行：安裝需要的套件與瀏覽器（之後就會跳過）
%PY% -c "import openpyxl, playwright" >nul 2>nul
if not errorlevel 1 goto :run
echo 第一次執行，正在安裝需要的套件，大約 1 到 5 分鐘，只需要一次...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail
%PY% -m playwright install chromium
if errorlevel 1 echo 瀏覽器下載失敗也沒關係，程式會改用這台電腦上的 Microsoft Edge。

:run
echo 啟動中，瀏覽器會自動打開 http://127.0.0.1:8000/ ，使用期間請不要關掉這個黑色視窗。
%PY% -m fslang serve
if errorlevel 1 pause
exit /b 0

:nopython
echo 找不到 Python。
echo 請到 https://www.python.org/downloads/ 下載安裝，安裝畫面最下面要勾選 "Add python.exe to PATH"，
echo 裝好之後再雙擊一次 start_web.bat。
pause
exit /b 1

:fail
echo 安裝失敗。請把上面的訊息截圖給負責的人（常見原因：公司網路擋住了下載）。
pause
exit /b 1
