"""產生示範用的「假遊戲」語系檔（examples/demo_game/lang/*.json）。

從 Excel 的 Tree of Wild 工作表取出各語言文字，並故意放入幾種錯誤，
用來展示/測試比對工具能不能抓到：

    th  第 3 列用舊翻譯（Thai 欄）而不是 TH (revised)      -> 舊翻譯
    ja  第 4 列改錯字                                      -> 內容不符
    ko  第 18 列顯示英文 "Tier 1"                          -> 語言錯誤
    it  第 9 列缺少                                        -> 找不到
    es  第 2 列全部大寫                                    -> 警告
    fr  所有不換行空白 (NBSP) 換成一般空白                 -> 仍判定正確
    bn  沒有語系檔，遊戲退回顯示英文                       -> 語言錯誤

用法（在專案根目錄）：python examples/demo_game/make_demo.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fslang.config import load_config  # noqa: E402
from fslang.excel import load_sheet  # noqa: E402

OUT = Path(__file__).resolve().parent / "lang"


def main():
    sheet = load_sheet(load_config(ROOT / "configs" / "tree_of_wild.json"))
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for lang in sheet.codes:
        if lang == "bn":
            continue
        help_texts = {}
        for e in sheet.entries:
            tr = e.translations[lang]
            text = tr.text or ""
            if lang == "th" and e.row == 3 and tr.old_text:
                text = tr.old_text
            if lang == "ja" and e.row == 4:
                text = text.replace("すべての", "すべで")
            if lang == "ko" and e.row == 18:
                text = e.translations["en"].text
            if lang == "it" and e.row == 9:
                continue
            if lang == "es" and e.row == 2:
                text = text.upper()
            if lang == "fr":
                text = text.replace("\u00a0", " ")
            help_texts[f"row{e.row}"] = text
        data = {"lang": lang, "title": "Tree of Wild", "help": help_texts}
        (OUT / f"{lang}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", OUT / f"{lang}.json")


if __name__ == "__main__":
    main()
