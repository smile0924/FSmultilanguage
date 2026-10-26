"""找出這台電腦可以給 Playwright 用的瀏覽器。

嘗試順序（browser 設定為 auto 時）：
1. Playwright 下載的 Chromium
2. Microsoft Edge：先用 Playwright 的 channel 找，找不到再從 Windows 登錄檔 / 常見安裝路徑找
3. Google Chrome：同上
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

NAMES = {None: "Chromium（Playwright）", "msedge": "Microsoft Edge", "chrome": "Google Chrome"}
ORDERS = {
    "auto": [None, "msedge", "chrome"],
    "chromium": [None],
    "msedge": ["msedge"],
    "chrome": ["chrome"],
}
_WIN_EXE = {"msedge": "msedge.exe", "chrome": "chrome.exe"}
_WIN_DIRS = {"msedge": ("Microsoft", "Edge", "Application"), "chrome": ("Google", "Chrome", "Application")}
_OTHER = {
    "msedge": ["microsoft-edge", "microsoft-edge-stable", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    "chrome": ["google-chrome", "google-chrome-stable", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
}


def _from_registry(exe: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}",
                    rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe}"):
            try:
                with winreg.OpenKey(root, sub) as key:
                    path = (winreg.QueryValue(key, None) or "").strip().strip('"')
            except OSError:
                continue
            if path and Path(path).is_file():
                return path
    return None


def find_installed(channel: str) -> str | None:
    """回傳已安裝的 Edge / Chrome 執行檔路徑。"""
    if os.name == "nt":
        exe = _WIN_EXE[channel]
        found = _from_registry(exe)
        if found:
            return found
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                path = Path(base).joinpath(*_WIN_DIRS[channel], exe)
                if path.is_file():
                    return str(path)
        return None
    for candidate in _OTHER[channel]:
        if candidate.startswith("/"):
            if Path(candidate).is_file():
                return candidate
        elif shutil.which(candidate):
            return shutil.which(candidate)
    return None


def launch_attempts(choice: str | None = "auto") -> list[tuple[str, dict]]:
    """[(說明, 傳給 chromium.launch 的參數)]，依序嘗試。"""
    attempts: list[tuple[str, dict]] = []
    for channel in ORDERS.get(choice or "auto", ORDERS["auto"]):
        if channel is None:
            attempts.append((NAMES[None], {}))
            continue
        attempts.append((NAMES[channel], {"channel": channel}))
        path = find_installed(channel)
        if path:
            attempts.append((f"{NAMES[channel]}（{path}）", {"executable_path": path}))
    return attempts
