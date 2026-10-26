import openpyxl

from fslang.config import config_from_args
from fslang.excel import load_sheet


def test_tree_of_wild_languages(tow_sheet):
    assert tow_sheet.sheet == "FS - Tree of Wild"
    assert tow_sheet.codes == ["en", "it", "th", "ja", "ko", "tr", "ru", "es", "fr", "pt", "hi", "bn"]
    assert len(tow_sheet.entries) == 23
    assert tow_sheet.language("hi").name == "Hindi"  # 表頭 "Hindi " 的空白被去掉


def test_revised_column_overrides_original(tow_sheet):
    row3 = next(e for e in tow_sheet.entries if e.row == 3)
    th = row3.translations["th"]
    assert th.cell == "D3" and th.column_name == "TH (revised)"
    assert th.text.startswith("รับฟรีสปิน")
    assert th.old_cell == "C3" and th.old_text.startswith("รับหมุนฟรี")


def test_revised_column_empty_falls_back_to_original(tow_sheet):
    row2 = next(e for e in tow_sheet.entries if e.row == 2)
    th = row2.translations["th"]
    assert th.cell == "C2" and th.old_text is None


def test_revision_detected_without_config(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Game"
    ws.append(["EN", "Japan", "JP (revised)", "Korea"])
    ws.append(["Hello", "こんにちは", "こんにちは！", "안녕"])
    ws.append(["Bye", "さよなら", None, None])
    path = tmp_path / "t.xlsx"
    wb.save(path)

    sheet = load_sheet(config_from_args(str(path), "Game"))
    assert sheet.codes == ["en", "ja", "ko"]
    hello, bye = sheet.entries
    assert hello.translations["ja"].text == "こんにちは！"
    assert hello.translations["ja"].old_text == "こんにちは"
    assert bye.translations["ja"].text == "さよなら"
    assert bye.translations["ko"].text is None
