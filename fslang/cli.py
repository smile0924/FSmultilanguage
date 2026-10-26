"""命令列介面。

    python -m fslang inspect  data/xxx.xlsx
    python -m fslang lint     -c configs/tree_of_wild.json
    python -m fslang collect  -c configs/tree_of_wild.json --interactive
    python -m fslang compare  -c configs/tree_of_wild.json
    python -m fslang run      -c configs/tree_of_wild.json      (collect + compare)
    python -m fslang serve                                       (網頁介面)
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from .config import GameConfig, config_from_args, load_config
from .excel import TranslationSheet, list_sheets, load_sheet
from .lint import ERROR, lint_sheet
from .matcher import FAIL_STATUSES, STATUS_LABELS, summarize
from .pipeline import compare_captures, write_report
from .report import write_lint_report


def _setup_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, w: int) -> str:
    s = str(s)
    return s + " " * max(0, w - _width(s))


def _short(s: str | None, n: int = 70) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _load(args) -> tuple[GameConfig, TranslationSheet]:
    if args.config:
        cfg = load_config(args.config)
        if args.excel:
            cfg.excel = Path(args.excel).resolve()
        if args.sheet:
            cfg.sheet = args.sheet
    elif args.excel:
        cfg = config_from_args(args.excel, args.sheet)
    else:
        raise SystemExit("請用 -c 指定設定檔，或用 --excel 指定 Excel 檔")
    sheet = load_sheet(cfg)
    return cfg, sheet


def _langs(args, sheet: TranslationSheet) -> list[str]:
    if not getattr(args, "langs", None):
        return sheet.codes
    wanted = [x.strip() for x in args.langs.split(",") if x.strip()]
    unknown = [x for x in wanted if x not in sheet.codes]
    if unknown:
        raise SystemExit(f"不認得的語言代碼 {unknown}，可用：{sheet.codes}")
    return wanted


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _captures_dir(args, cfg: GameConfig) -> Path:
    return Path(args.captures) if getattr(args, "captures", None) else Path("captures") / cfg.game_id


# ---------------------------------------------------------------- commands

def cmd_inspect(args):
    if args.config or (args.excel and args.sheet):
        cfg, sheet = _load(args)
        sheets = [sheet]
    else:
        path = args.excel or args.path
        if not path:
            raise SystemExit("請指定 Excel 檔")
        sheets = [load_sheet(config_from_args(path, name)) for name in list_sheets(path)]
    for sheet in sheets:
        print(f"\n工作表「{sheet.sheet}」：{len(sheet.entries)} 句，原文語言 {sheet.source_lang}")
        for lang in sheet.languages:
            filled = sum(1 for e in sheet.entries if e.translations[lang.code].text)
            rev = f"（修訂欄：{lang.revision_name}）" if lang.revision_name else ""
            print(f"  {_pad(lang.code, 6)} {_pad(lang.name, 14)} 已翻譯 {filled}/{len(sheet.entries)} {rev}")
    return 0


def _print_issues(issues, limit: int):
    errors = sum(1 for i in issues if i.severity == ERROR)
    print(f"\nExcel 檢查：錯誤 {errors} 筆、警告 {len(issues) - errors} 筆")
    for it in issues[:limit]:
        print(f"  [{it.severity:5}] {_pad(it.cell, 5)} {_pad(it.lang_name, 11)} {it.message}")
    if len(issues) > limit:
        print(f"  …其餘 {len(issues) - limit} 筆請看報表")


def cmd_lint(args):
    cfg, sheet = _load(args)
    issues = lint_sheet(sheet, cfg.placeholders, cfg.keywords, _langs(args, sheet))
    _print_issues(issues, args.show)
    out = Path(args.out) if args.out else Path("reports") / f"{cfg.game_id}_lint_{_timestamp()}.xlsx"
    write_lint_report(out, sheet, issues)
    print(f"\n報表：{out}")
    return 1 if any(i.severity == ERROR for i in issues) else 0


def cmd_collect(args):
    from .collector import collect

    cfg, sheet = _load(args)
    out_dir = _captures_dir(args, cfg)
    headless = False if args.headed else None
    saved = collect(cfg, sheet, _langs(args, sheet), out_dir, interactive=args.interactive, headless=headless)
    print(f"\n共擷取 {len(saved)} 個語言，存放在 {out_dir}")
    return 0 if saved else 1


def cmd_compare(args):
    cfg, sheet = _load(args)
    langs = _langs(args, sheet)
    captures = _captures_dir(args, cfg)
    outcome = compare_captures(cfg, sheet, captures, langs, lint=not args.no_lint)
    if not outcome.captured_langs:
        print(f"! 在 {captures} 找不到任何擷取檔（檔名需以語言代碼開頭，例如 th.json、th.txt）")
    results, issues = outcome.results, outcome.issues

    # 主控台摘要
    summary = summarize(results)
    cols = ["PASS", "WARN", "OUTDATED", "WRONG_LANG", "MISMATCH", "MISSING", "NO_CAPTURE"]
    print(f"\n{cfg.game_name}（{sheet.sheet}）比對結果，擷取資料：{captures}")
    print("  " + _pad("語言", 16) + "".join(_pad(STATUS_LABELS[c], 15) for c in cols))
    for lang in sheet.languages:
        if lang.code in summary:
            row = summary[lang.code]
            print("  " + _pad(f"{lang.name} ({lang.code})", 16) + "".join(_pad(row[c], 15) for c in cols))

    problems = [r for r in results if r.status in FAIL_STATUSES or r.status == "WARN"]
    # 整個語言幾乎都顯示成別的語言時（例如沒有語系檔、語言沒切成功），合併成一行
    for lang in sheet.languages:
        row = summary.get(lang.code)
        checked = sum(row.values()) - row["NO_CAPTURE"] - row["NO_EXPECTED"] if row else 0
        if row and checked and row["WRONG_LANG"] >= max(3, checked * 0.5):
            print(f"\n! {lang.name} ({lang.code})：{row['WRONG_LANG']}/{checked} 句顯示成其他語言，"
                  f"可能遊戲沒有切換到 {lang.name} 或缺少語系檔")
            problems = [r for r in problems if not (r.lang == lang.code and r.status == "WRONG_LANG")]
    if problems:
        print(f"\n需要確認的句子（{len(problems)} 筆）：")
        for r in problems[: args.show]:
            print(f"  [{r.label}] {_pad(r.cell, 5)} {_pad(r.lang_name, 11)} {_short(r.expected, 50)}")
            if r.actual:
                print(f"      遊戲：{_short(r.actual)}")
            if r.note:
                print(f"      說明：{_short(r.note, 100)}")
        if len(problems) > args.show:
            print(f"  …其餘 {len(problems) - args.show} 筆請看報表")
    if issues is not None:
        _print_issues(issues, min(args.show, 10))

    out = Path(args.out) if args.out else Path("reports") / f"{cfg.game_id}_{_timestamp()}.xlsx"
    write_report(out, cfg, sheet, outcome, captures)
    print(f"\n報表：{out}")
    return 1 if any(r.status in FAIL_STATUSES for r in results) else 0


def cmd_serve(args):
    from .web import serve

    serve(args.host, args.port, open_browser=not args.no_browser, workdir=args.workdir)
    return 0


def cmd_run(args):
    rc = cmd_collect(args)
    if rc:
        return rc
    return cmd_compare(args)


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fslang", description="FS 遊戲多語系比對工具（以 Excel 翻譯檔為標準）")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("-c", "--config", help="遊戲設定檔，例如 configs/tree_of_wild.json")
        p.add_argument("--excel", help="翻譯 Excel（覆寫設定檔）")
        p.add_argument("--sheet", help="工作表名稱（覆寫設定檔）")
        p.add_argument("--langs", help="只處理這些語言，逗號分隔，例如 th,ja,ko")

    p = sub.add_parser("inspect", help="列出 Excel 的工作表與語言欄位")
    p.add_argument("path", nargs="?", help="Excel 檔")
    common(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("lint", help="檢查 Excel 翻譯本身（佔位符、關鍵字、數字、空白…）")
    common(p)
    p.add_argument("--out", help="報表路徑（.xlsx）")
    p.add_argument("--show", type=int, default=50, help="主控台最多顯示幾筆")
    p.set_defaults(func=cmd_lint)

    def collect_args(p):
        p.add_argument("--captures", help="擷取檔資料夾（預設 captures/<game_id>）")
        p.add_argument("--interactive", "-i", action="store_true", help="互動模式：手動操作瀏覽器，按 Enter 擷取")
        p.add_argument("--headed", action="store_true", help="自動模式也顯示瀏覽器視窗")

    def compare_args(p):
        if not any(a.dest == "captures" for a in p._actions):
            p.add_argument("--captures", help="擷取檔資料夾（預設 captures/<game_id>）")
        p.add_argument("--out", help="報表路徑（.xlsx）")
        p.add_argument("--no-lint", action="store_true", help="報表不附 Excel 檢查")
        p.add_argument("--show", type=int, default=30, help="主控台最多顯示幾筆")

    p = sub.add_parser("collect", help="打開遊戲擷取各語言文字")
    common(p)
    collect_args(p)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("compare", help="拿擷取到的文字和 Excel 比對並輸出報表")
    common(p)
    compare_args(p)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("run", help="collect + compare")
    common(p)
    collect_args(p)
    compare_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("serve", help="開啟網頁介面：上傳 Excel、貼上遊戲網址就開始比對")
    p.add_argument("--host", default="127.0.0.1", help="預設只開放本機（127.0.0.1）")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="不要自動打開瀏覽器")
    p.add_argument("--workdir", default="web_runs", help="上傳檔、擷取檔、報表的存放資料夾")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_stdout()
    args = build_parser().parse_args(argv)
    return args.func(args)
