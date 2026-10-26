"""診斷工具：雙擊 diagnose.bat（或 python -m fslang.doctor），把結果截圖給負責的人。

列出：程式版本與資料夾、Python、套件、可用的瀏覽器、8000 連接埠被誰占用、能不能連到 QA 站。
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parents[1]
OK, BAD, INFO = "[ OK ]", "[失敗]", "[資訊]"


def _line(mark: str, title: str, detail: str = ""):
    print(f"{mark} {title}" + (f"：{detail}" if detail else ""))


def _pkg(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def check_program():
    _line(INFO, "程式版本", __version__)
    _line(INFO, "程式資料夾", str(ROOT))
    _line(INFO, "Python", f"{platform.python_version()}（{sys.executable}）")
    _line(INFO, "作業系統", platform.platform())
    if (ROOT / ".git").exists():
        from .launcher import find_git
        git = find_git()
        if git:
            out = subprocess.run([git, "log", "-1", "--format=%h %ci"], cwd=ROOT, capture_output=True, text=True,
                                 errors="replace").stdout.strip()
            _line(OK, "下載方式", f"git clone（會自動更新），目前版本 {out}")
        else:
            _line(BAD, "下載方式", "git clone，但找不到 git，無法自動更新")
    else:
        _line(INFO, "下載方式", "ZIP（不會自動更新）")


def check_packages():
    for name in ("openpyxl", "playwright"):
        ver = _pkg(name)
        _line(OK if ver else BAD, f"套件 {name}", ver or "沒有安裝")


def check_browsers():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _line(BAD, "瀏覽器", "Playwright 沒有安裝，無法檢查")
        return
    from .browsers import launch_attempts

    usable = []
    with sync_playwright() as p:
        try:
            path = p.chromium.executable_path
            _line(OK if Path(path).is_file() else INFO, "Playwright 的 Chromium",
                  "已下載" if Path(path).is_file() else f"沒有下載（{path}）")
        except Exception as e:
            _line(INFO, "Playwright 的 Chromium", str(e).splitlines()[0])
        for label, kwargs in launch_attempts("auto"):
            try:
                browser = p.chromium.launch(headless=True, timeout=30000, **kwargs)
                version = browser.version
                browser.close()
                _line(OK, f"瀏覽器 {label}", f"可以使用（版本 {version}）")
                usable.append(label)
            except Exception as e:
                _line(BAD, f"瀏覽器 {label}", str(e).strip().splitlines()[0][:200])
    if usable:
        _line(OK, "比對時會用", usable[0])
    else:
        _line(BAD, "比對時會用", "沒有可以用的瀏覽器")


def check_port(port: int = 8000):
    from .web import _listening, _listening_pid, _running_fslang

    if not _listening(port):
        _line(INFO, f"連接埠 {port}", "沒有程式在使用（比對網頁沒有在執行）")
        return
    info = _running_fslang(port)
    pid = _listening_pid(port)
    who = f"（程式編號 PID {pid}）" if pid else ""
    if info is None:
        _line(BAD, f"連接埠 {port}", f"被其他程式占用{who}")
    elif info.get("version") == __version__:
        _line(OK, f"連接埠 {port}", f"比對網頁 {__version__} 正在執行{who}")
    else:
        _line(BAD, f"連接埠 {port}", f"有舊版比對網頁在執行（版本 {info.get('version', '0.2 以前')}）{who}，"
                                    "請關掉所有黑色視窗後再雙擊 start_web.bat")


def check_site():
    try:
        cfg = json.loads((ROOT / "configs" / "tree_of_wild.json").read_text(encoding="utf-8"))
        url = cfg["collect"]["list_url"]
    except Exception:
        return
    if not url:
        return
    try:
        with urllib.request.urlopen(url, timeout=10) as res:
            _line(OK, "QA 站連線", f"{url}（HTTP {res.status}）")
    except Exception as e:
        _line(BAD, "QA 站連線", f"{url}：{str(e)[:200]}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    print("=" * 60)
    print(" FS 多語系比對 診斷結果（請把這個畫面截圖給負責的人）")
    print("=" * 60)
    for check in (check_program, check_packages, check_browsers, check_port, check_site):
        try:
            check()
        except Exception as e:  # 任何一項壞掉都不影響其他項目
            _line(BAD, check.__name__, f"檢查時發生錯誤：{e}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
