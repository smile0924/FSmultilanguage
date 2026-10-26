"""start_web.bat 背後的 launcher：自動更新與套件安裝判斷。"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fslang import launcher

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")


def git(cwd, *args):
    subprocess.run([GIT, "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                   capture_output=True)


def run_launcher(folder: Path):
    return subprocess.run([sys.executable, "-m", "fslang.launcher", "--update-only"], cwd=folder,
                          capture_output=True, text=True, timeout=120)


def _seed(folder: Path):
    shutil.copytree(ROOT / "fslang", folder / "fslang", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "requirements.txt", folder / "requirements.txt")
    # 套件已經裝好、清單沒變：launcher 不應該執行 pip
    shutil.copy(ROOT / "requirements.txt", folder / ".installed_requirements.txt")
    (folder / ".gitignore").write_text(".installed_requirements.txt\n__pycache__/\n")


@pytest.mark.skipif(GIT is None, reason="沒有 git")
def test_pulls_new_version_from_github(tmp_path):
    origin, dev, user = tmp_path / "origin.git", tmp_path / "dev", tmp_path / "user"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    dev.mkdir()
    _seed(dev)
    git(dev, "init", "-q", "-b", "main")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "v1")
    git(dev, "remote", "add", "origin", str(origin))
    git(dev, "push", "-q", "-u", "origin", "main")
    git(tmp_path, "clone", "-q", str(origin), str(user))
    shutil.copy(ROOT / "requirements.txt", user / ".installed_requirements.txt")

    # GitHub 上有新版本
    (dev / "NEW_FEATURE.txt").write_text("v2")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "v2")
    git(dev, "push", "-q")

    out = run_launcher(user)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "檢查更新中" in out.stdout
    assert (user / "NEW_FEATURE.txt").read_text() == "v2"
    assert "正在安裝" not in out.stdout


def test_zip_folder_skips_update(tmp_path):
    _seed(tmp_path)
    out = run_launcher(tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "不是用 git 下載的" in out.stdout


def test_reinstalls_only_when_requirements_change(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "MARKER", tmp_path / ".installed_requirements.txt")
    monkeypatch.setattr(launcher, "_run", lambda cmd: calls.append(cmd[1:4]) or 0)
    (tmp_path / "requirements.txt").write_text("openpyxl>=3.1\nplaywright>=1.40\n")

    assert launcher.ensure_packages()           # 沒有紀錄：安裝
    assert calls == [["-m", "pip", "install"]]
    calls.clear()
    assert launcher.ensure_packages()           # 清單沒變：不安裝
    assert calls == []
    (tmp_path / "requirements.txt").write_text("openpyxl>=3.1\nplaywright>=1.40\nrequests\n")
    assert launcher.ensure_packages()           # 清單有變：重新安裝
    assert calls == [["-m", "pip", "install"]]
