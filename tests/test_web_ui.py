"""用瀏覽器操作網頁介面的測試。"""

import threading
from pathlib import Path

import pytest

from fslang import web

ROOT = Path(__file__).resolve().parents[1]
EXCEL = ROOT / "data" / "FS_Version_137_EUR_Verified.xlsx"


def test_job_error_is_shown_instead_of_disconnect(tmp_path, monkeypatch):
    sync_api = pytest.importorskip("playwright.sync_api")

    def broken_collect(*args, **kwargs):
        raise RuntimeError("找不到可以用的瀏覽器（測試用錯誤）")

    monkeypatch.setattr(web, "collect", broken_collect)
    server = web.make_server("127.0.0.1", 0, tmp_path / "runs", ROOT / "configs")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:
                pytest.skip(f"無法啟動瀏覽器：{e}")
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.set_input_files("#file", str(EXCEL))
            page.wait_for_selector(".lc")
            page.check("input[name=mode][value=manual]")
            page.click("#start")
            page.wait_for_function("document.querySelector('#runTitle').textContent === '發生錯誤'", timeout=20000)
            status = page.inner_text("#runStatus")
            assert "測試用錯誤" in status
            assert "連線中斷" not in status
            assert not page.is_visible("#stop")
            assert not page.is_disabled("#start")  # 可以修正後再按一次
            browser.close()
    finally:
        server.shutdown()
