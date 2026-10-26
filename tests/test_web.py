"""網頁介面 API 測試：上傳 Excel、偵測網址、跑自動/手動比對。"""

import functools
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fslang.web import make_server

ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "data" / "FS_Version_137_EUR_Verified.xlsx"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    server = make_server("127.0.0.1", 0, tmp_path_factory.mktemp("web_runs"), ROOT / "configs")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def demo_game():
    handler = functools.partial(_QuietHandler, directory=str(ROOT / "examples" / "demo_game"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


def call(base, path, data=None, headers=None, raw=False):
    hdrs = {"X-Fslang": "1"}
    hdrs.update(headers or {})
    body = data if isinstance(data, (bytes, type(None))) else json.dumps(data).encode("utf-8")
    if body is not None and not isinstance(data, bytes):
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=hdrs, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as res:
        content = res.read()
        return content if raw else json.loads(content)


def upload(base):
    return call(base, "/api/upload", EXCEL.read_bytes(), {"X-Filename": "FS%20137.xlsx"})


def wait_job(base, jid, answers=(), timeout=180):
    answers = list(answers)
    end = time.time() + timeout
    while time.time() < end:
        st = call(base, f"/api/jobs/{jid}")
        if st["state"] in ("done", "error"):
            return st
        if st["prompt"] and answers:
            call(base, f"/api/jobs/{jid}/action", {"action": answers.pop(0)})
        time.sleep(0.3)
    raise AssertionError("比對逾時")


def test_index_page(base):
    html = call(base, "/", raw=True).decode("utf-8")
    assert "FS 多語系比對" in html and "/api/upload" in html


def test_upload_lists_sheets_and_preset(base):
    data = upload(base)
    assert data["filename"] == "FS 137.xlsx"
    names = [s["name"] for s in data["sheets"]]
    assert names == ["FS - Tree of Wild", "FS - Cat Gold Rush"]
    tow = data["sheets"][0]
    assert tow["rows"] == 23 and len(tow["languages"]) == 12
    assert tow["preset"]["from_config"] is True  # 套用 configs/tree_of_wild.json
    assert not data["sheets"][1]["preset"]["from_config"]


def test_upload_rejects_non_excel(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/upload", b"hello", {"X-Filename": "a.txt"})
    assert e.value.code == 400


def test_post_requires_custom_header_and_local_host(base):
    req = urllib.request.Request(base + "/api/jobs", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403
    req = urllib.request.Request(base + "/api/info", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_detect_url(base):
    d = call(base, "/api/detect-url", {"url": "https://h/g.jsp?id=1&lang=EN", "codes": ["en", "ja"]})
    assert d["found"] and d["template"] == "https://h/g.jsp?id=1&lang={game_lang}"
    assert d["game_codes"] == {"en": "EN", "ja": "JA"}


def test_job_validation_errors(base):
    up = upload(base)
    params = {"upload_id": up["upload_id"], "sheet": "FS - Tree of Wild", "mode": "auto",
              "url": "https://h/list.jsp", "langs": [{"code": "en"}], "placeholders": []}
    with pytest.raises(urllib.error.HTTPError) as e:
        call(base, "/api/jobs", params)
    assert "找不到語言參數" in json.loads(e.value.read())["error"]


def test_auto_job_end_to_end(base, demo_game):
    pytest.importorskip("playwright")
    up = upload(base)
    params = {"upload_id": up["upload_id"], "sheet": "FS - Tree of Wild", "mode": "auto",
              "url": demo_game + "?lang=en", "wait_seconds": 0.3, "help_selector": "#btnHelp",
              "langs": [{"code": c, "game_code": c} for c in ("en", "th", "ja", "bn")],
              "placeholders": [r"\[[^\]]*\]", r"(?<![A-Za-z])X(?![A-Za-z])"]}
    jid = call(base, "/api/jobs", params)["job_id"]
    st = wait_job(base, jid)
    if st["state"] == "error" and "Executable" in (st["error"] or ""):
        pytest.skip("沒有瀏覽器")
    assert st["state"] == "done", st["error"]
    res = st["result"]
    by = {(r["lang"], r["row"]): r for r in res["results"]}
    assert by[("en", 2)]["status"] == "PASS"
    assert by[("th", 3)]["status"] == "OUTDATED"
    assert by[("ja", 4)]["status"] == "MISMATCH" and by[("ja", 4)]["diff"]
    assert {l["code"]: l["counts"]["WRONG_LANG"] for l in res["languages"]}["bn"] == 23
    assert res["issues"]
    assert all(l["screenshots"] for l in res["languages"])
    report = call(base, res["report"], raw=True)
    assert report[:2] == b"PK"  # xlsx 是 zip
    shot = res["languages"][0]["screenshots"][0]
    assert call(base, f"/api/jobs/{jid}/files/{shot}", raw=True)[:4] == b"\x89PNG"


def test_manual_job_with_actions(base, demo_game):
    pytest.importorskip("playwright")
    up = upload(base)
    params = {"upload_id": up["upload_id"], "sheet": "FS - Tree of Wild", "mode": "manual",
              "url": demo_game + "?lang=th", "headless": True,
              "langs": [{"code": "th"}, {"code": "ja"}], "placeholders": [r"\[[^\]]*\]"]}
    jid = call(base, "/api/jobs", params)["job_id"]
    # th：擷取 -> 下一個語言；ja：跳過
    st = wait_job(base, jid, answers=["capture", "next", "skip"])
    if st["state"] == "error" and "Executable" in (st["error"] or ""):
        pytest.skip("沒有瀏覽器")
    assert st["state"] == "done", st["error"]
    counts = {l["code"]: l["counts"] for l in st["result"]["languages"]}
    assert counts["th"]["OUTDATED"] == 1 and counts["th"]["PASS"] == 22
    assert counts["ja"]["NO_CAPTURE"] == 23
