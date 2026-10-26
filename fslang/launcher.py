"""雙擊 start_web.bat 時執行：自動更新 -> 安裝/更新套件 -> 啟動網頁介面。

    python -m fslang.launcher [--update-only]

- 自動更新：資料夾是用 git 下載的（git clone）才會更新，執行 git pull。
  找 git 的順序：PATH 上的 git，再來是 GitHub Desktop 內附的 git。
- 套件：第一次執行，或 requirements.txt 有變動時，執行 pip install。
- 啟動：用新的 Python 程序執行 fslang serve，確保跑的是更新後的程式碼。

這個檔案只能用標準函式庫（執行時套件可能還沒裝）。
"""

from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".installed_requirements.txt"


def _run(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd, cwd=ROOT).returncode
    except OSError as e:
        print(f"無法執行 {cmd[0]}：{e}")
        return 1


def find_git() -> str | None:
    git = shutil.which("git")
    if git:
        return git
    # GitHub Desktop 內附的 git（沒有加到 PATH）
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found = sorted(glob.glob(os.path.join(local, "GitHubDesktop", "app-*", "resources", "app", "git", "cmd",
                                              "git.exe")))
        if found:
            return found[-1]
    return None


def update() -> None:
    if not (ROOT / ".git").exists():
        print("這個資料夾不是用 git 下載的，不會自動更新（設定方式見 README「自動更新」）。")
        return
    git = find_git()
    if not git:
        print("找不到 git，略過自動更新。")
        return
    print("檢查更新中…")
    if _run([git, "pull", "--ff-only"]) != 0:
        print("更新失敗（可能是網路問題，或資料夾裡的檔案被改過），先用目前的版本。")


def ensure_packages() -> bool:
    requirements = (ROOT / "requirements.txt").read_bytes()
    first_time = importlib.util.find_spec("playwright") is None or importlib.util.find_spec("openpyxl") is None
    if not first_time and MARKER.exists() and MARKER.read_bytes() == requirements:
        return True
    print("第一次執行，正在安裝需要的套件，大約 1 到 5 分鐘…" if first_time else "套件清單有更新，正在安裝…")
    if _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]) != 0:
        print("安裝失敗。請把上面的訊息截圖給負責的人（常見原因：公司網路擋住了下載）。")
        return False
    MARKER.write_bytes(requirements)
    if first_time:
        print("下載瀏覽器（約 160MB）…")
        if _run([sys.executable, "-m", "playwright", "install", "chromium"]) != 0:
            print("瀏覽器下載失敗也沒關係，程式會改用這台電腦上的 Microsoft Edge。")
    return True


def _pause():
    try:
        input("\n按 Enter 關閉視窗…")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    update_only = "--update-only" in argv
    argv = [a for a in argv if a != "--update-only"]
    update()
    if not ensure_packages():
        _pause()
        return 1
    if update_only:
        return 0
    print("啟動中，瀏覽器會自動打開 http://127.0.0.1:8000/ ，使用期間請不要關掉這個視窗。")
    try:
        code = _run([sys.executable, "-m", "fslang", "serve", *argv])
    except KeyboardInterrupt:
        return 0
    if code != 0:
        _pause()
    return code


if __name__ == "__main__":
    sys.exit(main())
