"""說明頁（paytable）模式：直接下載各語言的說明頁 HTML 比對。"""

import functools
import html
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fslang.config import load_config
from fslang.excel import load_sheet
from fslang.matcher import MISMATCH, NO_CAPTURE, OUTDATED, PASS, WRONG_LANG
from fslang.paytable import Fetcher, collect_paytables, discover, guess_locales, locale_to_code, parse_paytable_url
from fslang.pipeline import compare_captures

ROOT = Path(__file__).resolve().parents[1]
GAME = "touch/fsnew/20260101P/games/treeofwild"
CODES = ["en", "it", "th", "ja", "ko", "tr", "ru", "es", "fr", "pt", "hi", "bn"]
HASH = {"en_US": ".73fe25c7", "th_TH": ".1a2b3c4d", "ja_JP": ".5e6f7a8b", "ko_KR": ".9c0d1e2f",
        "tr_TR": ".aa11bb22", "ru_RU": ".cc33dd44", "pt_PT": ".ee55ff66", "hi_IN": ".a1b2c3d4",
        "fr_FR": ".73fe25c7",   # 和 en 同一個雜湊：要靠猜
        "es_ES": "",            # 沒有雜湊：要靠猜
        "it_IT": ".deadbeef"}   # 哪裡都沒寫：要使用者提供
LOCALE = {"en": "en_US", "th": "th_TH", "ja": "ja_JP", "ko": "ko_KR", "tr": "tr_TR", "ru": "ru_RU",
          "pt": "pt_PT", "hi": "hi_IN", "fr": "fr_FR", "es": "es_ES", "it": "it_IT"}


def test_parse_and_locale_helpers():
    r = parse_paytable_url(f"https://qa-static.object1.net/{GAME}/assets/paytable_en_US.73fe25c7.html")
    assert (r.stem, r.locale, r.hash, r.ext) == ("paytable_", "en_US", ".73fe25c7", ".html")
    assert r.base.endswith("/treeofwild/assets/")
    assert r.with_locale("th_TH", "") == r.base + "paytable_th_TH.html"
    assert parse_paytable_url("https://h/games/x/index.html") is None
    assert [locale_to_code(x, CODES) for x in ("en_US", "jp", "ko-KR", "zh_TW")] == ["en", "ja", "ko", None]
    assert guess_locales("th", "en_US") == ["th_TH", "th"]


def _page(sheet, code: str) -> str:
    rows = []
    for e in sheet.entries:
        tr = e.translations[code]
        text = tr.text
        if code == "th" and e.row == 3:
            text = tr.old_text                       # 舊翻譯
        if code == "ja" and e.row == 4:
            text = text.replace("すべての", "すべで")  # 錯字
        if code == "ko" and e.row == 18:
            text = e.translations["en"].text          # 還是英文
        body = html.escape(text).replace("[WILD]", '<img src="wild.png" alt="">')
        body = body.replace(" X ", ' <span class="m">x2000</span> ')
        rows.append(f"<p>{body}</p>")
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Paytable</title></head><body>'
            + "".join(rows) + "<script>window.cfg = {\"theme\": \"dark\"};</script></body></html>")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sheet = load_sheet(load_config(ROOT / "configs" / "tree_of_wild.json"))
    root = tmp_path_factory.mktemp("static")
    game = root / GAME
    (game / "assets").mkdir(parents=True)
    for code, locale in LOCALE.items():
        (game / "assets" / f"paytable_{locale}{HASH[locale]}.html").write_text(_page(sheet, code), encoding="utf-8")
    names = {loc: f"paytable_{loc}{HASH[loc]}.html" for loc in HASH}
    (game / "index.html").write_text(
        '<!doctype html><html><head><script type="module" src="./assets/index.0f1e2d3c.js"></script></head>'
        '<body><canvas></canvas></body></html>', encoding="utf-8")
    (game / "assets" / "index.0f1e2d3c.js").write_text(
        "const p={" + ",".join(f'"{l}":"assets/{names[l]}"' for l in ("th_TH", "ja_JP", "ru_RU", "pt_PT", "hi_IN"))
        + '};import("./chunk-help.9f8e7d6c.js");', encoding="utf-8")
    (game / "assets" / "chunk-help.9f8e7d6c.js").write_text(f'export const ko="./{names["ko_KR"]}";',
                                                           encoding="utf-8")
    (game / "manifest.json").write_text(json.dumps({"paytable_tr_TR.html": {"file": "assets/" + names["tr_TR"]}}),
                                        encoding="utf-8")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/{GAME}/assets/"
    yield {"base": base, "start": base + names["en_US"], "it": base + names["it_IT"], "sheet": sheet}
    server.shutdown()


def test_discover_finds_languages(site):
    logs = []
    found = discover(site["start"], CODES, Fetcher(logs.append), [site["it"]], logs.append)
    expected = {code: site["base"] + f"paytable_{loc}{HASH[loc]}.html" for code, loc in LOCALE.items()}
    assert found == expected  # bn 不存在，其他 11 個語言都找到


def test_collect_and_compare(site, tmp_path):
    cfg = load_config(ROOT / "configs" / "tree_of_wild.json")
    sheet = site["sheet"]
    saved = collect_paytables(cfg, sheet, CODES, tmp_path / "cap", site["start"], [site["it"]])
    assert len(saved) == 11
    outcome = compare_captures(cfg, sheet, tmp_path / "cap", CODES)
    by = {(r.lang, r.row): r for r in outcome.results}
    assert by[("th", 3)].status == OUTDATED
    assert by[("ja", 4)].status == MISMATCH and by[("ja", 4)].note == "「ての」→「で」"
    assert by[("ko", 18)].status == WRONG_LANG
    assert by[("en", 6)].status == PASS   # X -> x2000、[WILD] -> 圖片都算正確
    assert {r.status for r in outcome.results if r.lang == "bn"} == {NO_CAPTURE}
    planted = {("th", 3), ("ja", 4), ("ko", 18)}
    bad = [(r.lang, r.row, r.status) for r in outcome.results
           if r.lang != "bn" and (r.lang, r.row) not in planted and r.status != PASS]
    assert bad == []
    assert (tmp_path / "cap" / "raw" / "th.html").is_file()


class _FakeFetcher(Fetcher):
    """找不到檔案時一律回傳首頁（單頁應用常見的設定）。"""

    def __init__(self, pages):
        super().__init__(lambda m: None)
        self.pages = pages

    def _get(self, url):
        return 200, self.pages.get(url, self.pages["https://h/g/index.html"])


def test_guess_ignores_index_fallback():
    pages = {"https://h/g/index.html": "<html><body><canvas></canvas></body></html>",
             "https://h/g/assets/paytable_en_US.12345678.html": "<p>Unlock Free Game and more text here</p>"}
    found = discover("https://h/g/assets/paytable_en_US.12345678.html", ["en", "th"], _FakeFetcher(pages))
    assert found == {"en": "https://h/g/assets/paytable_en_US.12345678.html"}


def test_web_paytable_job(site, tmp_path):
    from tests.test_web import call, upload, wait_job  # 共用網頁測試的工具
    from fslang.web import make_server

    server = make_server("127.0.0.1", 0, tmp_path / "runs", ROOT / "configs")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        d = call(base, "/api/detect-url", {"url": site["start"], "codes": CODES})
        assert d["paytable"] == {"locale": "en_US", "stem": "paytable_"}
        up = upload(base)
        params = {"upload_id": up["upload_id"], "sheet": "FS - Tree of Wild", "mode": "paytable",
                  "url": site["start"], "extra_urls": [site["it"]], "langs": [{"code": c} for c in CODES],
                  "placeholders": [r"\[[^\]]*\]", r"(?<![A-Za-z])X(?![A-Za-z])"]}
        st = wait_job(base, call(base, "/api/jobs", params)["job_id"])
        assert st["state"] == "done", st["error"]
        counts = {l["code"]: l["counts"] for l in st["result"]["languages"]}
        assert counts["th"]["OUTDATED"] == 1 and counts["ko"]["WRONG_LANG"] == 1
        assert counts["bn"]["NO_CAPTURE"] == 23
    finally:
        server.shutdown()
