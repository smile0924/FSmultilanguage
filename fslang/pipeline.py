"""命令列與網頁共用的流程：讀擷取檔 -> 比對 -> Excel 檢查 -> 報表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import GameConfig
from .corpus import load_captures
from .excel import TranslationSheet
from .lint import Issue, lint_sheet
from .matcher import Matcher, Result, compare
from .report import write_compare_report


@dataclass
class CompareOutcome:
    results: list[Result]
    issues: list[Issue] | None
    captured_langs: list[str]


def compare_captures(cfg: GameConfig, sheet: TranslationSheet, captures_dir: Path, langs: list[str],
                     lint: bool = True) -> CompareOutcome:
    corpora = load_captures(Path(captures_dir), langs)
    matcher = Matcher(cfg.placeholders, cfg.match["wildcard_max_chars"], cfg.match["similarity_threshold"])
    results = compare(sheet, corpora, matcher, langs)
    issues = lint_sheet(sheet, cfg.placeholders, cfg.keywords, langs) if lint else None
    return CompareOutcome(results, issues, sorted(corpora))


def write_report(path: Path, cfg: GameConfig, sheet: TranslationSheet, outcome: CompareOutcome,
                 captures_dir: Path) -> Path:
    return write_compare_report(path, sheet, outcome.results, outcome.issues,
                                {"game_name": cfg.game_name, "captures": str(captures_dir)})
