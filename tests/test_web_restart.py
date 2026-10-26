"""再次啟動網頁時，要先關掉還在跑的舊比對網頁（沒關掉的舊視窗），避免開到舊版。"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fslang import web

ROOT = Path(__file__).resolve().parents[1]

# 模擬 0.2 以前的舊版：有 /api/info，但沒有 /api/shutdown
FAKE_OLD_SERVER = r"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"devices": [""]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, proc: subprocess.Popen):
    for _ in range(100):
        if web._listening(port):
            return
        assert proc.poll() is None, "伺服器沒有啟動"
        time.sleep(0.1)
    raise AssertionError("伺服器沒有啟動")


def test_stops_running_new_version_gracefully(tmp_path):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "fslang", "serve", "--no-browser", "--port", str(port),
                             "--workdir", str(tmp_path)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        _wait_listening(port, proc)
        assert web.stop_running_server(port)
        assert proc.wait(timeout=10) == 0  # 自己正常結束，不是被強制結束
        assert not web._listening(port)
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(os.name != "nt" and shutil.which("lsof") is None, reason="需要 lsof 找出占用連接埠的程式")
def test_stops_old_version_without_shutdown_api():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", FAKE_OLD_SERVER, str(port)])
    try:
        _wait_listening(port, proc)
        assert web._running_fslang(port) == {"devices": [""]}
        assert web.stop_running_server(port)
        assert proc.wait(timeout=10) is not None
        assert not web._listening(port)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_leaves_other_programs_alone():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_listening(port, proc)
        assert web.stop_running_server(port) is False   # 不是比對網頁：不動它，改用其他連接埠
        assert proc.poll() is None
    finally:
        proc.kill()
