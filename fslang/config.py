"""遊戲設定檔（JSON）讀取。

設定檔中的相對路徑（excel 等）以「設定檔所在資料夾」為基準。
所有以底線開頭的鍵（例如 "_說明"）都是註解，程式會忽略。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MATCH = {
    # 相似度 >= 此值視為「內容不符」(找到相近文字但不同)；低於此值視為「找不到」
    "similarity_threshold": 0.6,
    # 佔位符（例如 [WILD]、X）在遊戲中可對應的最多字元數（圖片則為 0 個字）
    "wildcard_max_chars": 40,
}

DEFAULT_COLLECT = {
    "list_url": "",
    # 直接開啟遊戲的網址樣板，可用 {lang}（Excel 語言代碼）或 {game_lang}（遊戲語言代碼）
    "game_url": "",
    # Excel 語言代碼 -> 遊戲網址使用的語言代碼，例如 {"ja": "jp", "ko": "kr"}
    "game_lang_codes": {},
    # 開啟遊戲後要執行的動作（點擊說明按鈕、翻頁、擷取…），見 README
    "steps": [],
    "wait_ms": 5000,
    "timeout_ms": 60000,
    "device": None,
    "viewport": [1280, 800],
    "headless": True,
    # 自動模式把瀏覽器語言設成目標語言（給依瀏覽器語言切換的遊戲用）
    "set_browser_locale": True,
    "capture_network": True,
    "screenshot": True,
}


@dataclass
class GameConfig:
    game_id: str
    game_name: str
    excel: Path
    sheet: str | None
    header_row: int = 1
    source_language: str = "en"
    languages: dict[str, str] = field(default_factory=dict)
    revisions: dict[str, str] = field(default_factory=dict)
    placeholders: list[str] = field(default_factory=list)
    keywords: list[str] | None = None
    match: dict = field(default_factory=lambda: dict(DEFAULT_MATCH))
    collect: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_COLLECT))
    path: Path | None = None

    def game_lang(self, lang: str) -> str:
        return self.collect.get("game_lang_codes", {}).get(lang, lang)


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def load_config(path: str | Path) -> GameConfig:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = _strip_comments(json.load(f))
    base = path.parent
    excel = Path(raw["excel"])
    if not excel.is_absolute():
        excel = (base / excel).resolve()
    match = dict(DEFAULT_MATCH)
    match.update(raw.get("match", {}))
    collect = copy.deepcopy(DEFAULT_COLLECT)
    collect.update(raw.get("collect", {}))
    return GameConfig(
        game_id=raw["game_id"],
        game_name=raw.get("game_name", raw["game_id"]),
        excel=excel,
        sheet=raw.get("sheet"),
        header_row=int(raw.get("header_row", 1)),
        source_language=raw.get("source_language", "en"),
        languages=raw.get("languages", {}),
        revisions=raw.get("revisions", {}),
        placeholders=raw.get("placeholders", []),
        keywords=raw.get("keywords"),
        match=match,
        collect=collect,
        path=path,
    )


def config_from_args(excel: str, sheet: str | None, game_id: str | None = None) -> GameConfig:
    """沒有設定檔時，用預設值 + 命令列參數建立設定。"""
    excel_path = Path(excel).resolve()
    gid = game_id or (sheet or excel_path.stem).lower().replace(" ", "_").replace("-", "_")
    return GameConfig(
        game_id=gid,
        game_name=sheet or excel_path.stem,
        excel=excel_path,
        sheet=sheet,
        placeholders=[r"\[[^\]]*\]"],
    )
