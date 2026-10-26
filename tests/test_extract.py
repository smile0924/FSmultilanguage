import json

from fslang.corpus import CAPTURE_FORMAT, Corpus, find_language_files, load_file
from fslang.extract import guess_kind, html_to_text, strings_from_js, strings_from_json


def test_html_to_text_splits_blocks_and_skips_scripts():
    html = "<div><p>Hello <b>WILD</b> world</p><script>var x='no';</script><p>Second</p></div>"
    assert html_to_text(html) == "Hello WILD world\nSecond"


def test_strings_from_json_skips_assets_and_expands_html():
    data = {"a": "Unlock Free Game", "img": "res/wild.png", "n": 3,
            "help": ["<p>Line one</p><p>Line two</p>"], "url": "https://x.y/z"}
    assert sorted(strings_from_json(data)) == ["Line one\nLine two", "Unlock Free Game"]


def test_strings_from_js_unescapes():
    js = r"""var t={th:"\u0e1b\u0e25\u0e14\u0e25\u0e47\u0e2d\u0e01",en:'Free Game',x:"a.png"};"""
    assert strings_from_js(js) == ["ปลดล็อก", "Free Game"]


def test_guess_kind():
    assert guess_kind("https://a/lang/th.json?v=1", "", "{}") == "json"
    assert guess_kind("https://a/main.js", "application/javascript", "") == "js"
    assert guess_kind("https://a/help", "text/html; charset=utf-8", "") == "html"
    assert guess_kind("https://a/x.css", "text/css", "") is None


def test_capture_file_and_language_file_lookup(tmp_path):
    capture = {
        "format": CAPTURE_FORMAT,
        "snapshots": [{"label": "help", "dom": [
            {"url": "https://g/index", "visible": "Title\nUnlock Free Game", "hidden": ["Hidden rule"],
             "canvas": ["Label text"], "canvas_hidden": []}]}],
        "network": [{"url": "https://g/lang/th.json", "strings": ["From network"]}],
    }
    (tmp_path / "th.json").write_text(json.dumps(capture), encoding="utf-8")
    (tmp_path / "th_manual.txt").write_text("Typed by QA", encoding="utf-8")
    (tmp_path / "tr.json").write_text("{}", encoding="utf-8")

    files = find_language_files(tmp_path, "th")
    assert [f.name for f in files] == ["th.json", "th_manual.txt"]
    records = load_file(tmp_path / "th.json")
    assert {r.text for r in records} == {"Title\nUnlock Free Game", "Hidden rule", "Label text", "From network"}
    assert any("(隱藏中)" in r.source for r in records)

    corpus = Corpus.from_files(files)
    texts = {c.text for c in corpus.candidates}
    assert {"Unlock Free Game", "Typed by QA", "Title"} <= texts
