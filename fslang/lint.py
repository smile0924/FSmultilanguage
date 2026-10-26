"""Excel 翻譯檔本身的檢查（不需要連到遊戲）。

以 EN 原文為基準，檢查每個語言的翻譯：
- empty        有原文但沒有翻譯
- whitespace   開頭/結尾有空白、連續多個空白
- placeholder  佔位符（[WILD]、X…）數量和原文不同
- keyword      原文的大寫關鍵字（WILD、SCATTER…）在翻譯中不見了、大小寫不同、或黏了其他字母
- spacing      印地語/孟加拉語/俄語等用空白分詞的語言，關鍵字和前後文字黏在一起
- number       原文中的數字在翻譯中不見了
- repeat       相鄰重複的字（例如 "the the"、泰文 "จะจะ"）
- untranslated 翻譯和英文原文一模一樣
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import textnorm
from .excel import TranslationSheet

ERROR = "ERROR"
WARNING = "WARN"

# 以空白分詞、且關鍵字前後通常要留空白的「非拉丁字母」語言
_SPACED_NON_LATIN = {"hi", "bn", "ru", "uk", "bg", "el", "mr", "ne", "ta", "te", "kn", "ml", "gu", "pa"}
# 用「字元重複」檢查錯字的語言（沒有空白分詞）
_CHAR_REPEAT_LANGS = {"th", "lo", "km", "my"}

_KEYWORD_RE = re.compile(r"(?<![A-Za-z])[A-Z]{3,}(?![A-Za-z])")
_LATIN = "A-Za-z\u00c0-\u024f"


@dataclass
class Issue:
    row: int
    key: str
    lang: str
    lang_name: str
    cell: str
    severity: str
    rule: str
    message: str
    text: str


def _keywords(source: str, configured: list[str] | None) -> list[str]:
    if configured is not None:
        return [k for k in configured if k in source]
    return sorted(set(_KEYWORD_RE.findall(source)))


def _repeats_words(text: str) -> list[str]:
    tokens = [re.sub(r"^\W+|\W+$", "", t) for t in text.split()]
    found = []
    for a, b in zip(tokens, tokens[1:]):
        if a and a.casefold() == b.casefold() and re.search(r"[^\W\d_]", a):
            found.append(f"{a} {b}")
    return found


def _repeats_chars(text: str) -> list[str]:
    return [m.group() for m in re.finditer(r"([^\W\d_]{2,4})\1", text)]


def lint_sheet(sheet: TranslationSheet, placeholders: list[str] | None = None,
               keywords: list[str] | None = None, langs: list[str] | None = None) -> list[Issue]:
    ph = [re.compile(p) for p in (placeholders or [])]
    issues: list[Issue] = []
    src = sheet.source_lang
    langs = langs or sheet.codes

    for entry in sheet.entries:
        source_tr = entry.translations.get(src)
        source = source_tr.text if source_tr else None

        def texts_for(lang):
            tr = entry.translations[lang]
            out = []
            if tr.text:
                out.append((tr.text, tr.cell))
            if tr.old_text:
                out.append((tr.old_text, tr.old_cell))
            return out

        for lang in langs:
            if lang not in entry.translations:
                continue
            name = sheet.language(lang).name

            def add(cell, severity, rule, message, text):
                issues.append(Issue(entry.row, entry.key, lang, name, cell, severity, rule, message, text))

            tr = entry.translations[lang]
            if not tr.text:
                if source and lang != src:
                    add(tr.cell, ERROR, "empty", "有英文原文但沒有翻譯", "")
                continue

            for text, cell in texts_for(lang):
                # 空白
                if text != text.strip():
                    add(cell, WARNING, "whitespace", "開頭或結尾有多餘空白", text)
                if re.search(r"[ \u3000]{2,}", text.strip()):
                    add(cell, WARNING, "whitespace", "有連續多個空白", text)

                if lang == src or not source:
                    continue
                if cell == tr.old_cell:
                    continue  # 舊翻譯只檢查空白，其他規則看修訂後的版本

                # 佔位符
                for p in ph:
                    want = [m.group() for m in p.finditer(source)]
                    got = [m.group() for m in p.finditer(text)]
                    if len(want) != len(got):
                        add(cell, ERROR, "placeholder",
                            f"佔位符數量不同：原文 {want}，翻譯 {got}", text)

                # 關鍵字
                for kw in _keywords(source, keywords):
                    if re.search(rf"(?<![{_LATIN}]){re.escape(kw)}(?![{_LATIN}])", text):
                        continue
                    if kw in text:
                        m = re.search(rf"[{_LATIN}]*{re.escape(kw)}[{_LATIN}]*", text)
                        add(cell, ERROR, "keyword", f"關鍵字 {kw} 黏了其他字母：「{m.group()}」", text)
                    elif kw.casefold() in text.casefold():
                        add(cell, WARNING, "keyword", f"關鍵字 {kw} 大小寫不同", text)
                    else:
                        add(cell, ERROR, "keyword", f"翻譯中沒有關鍵字 {kw}", text)

                # 印地語/孟加拉語/俄語：關鍵字前後要有空白
                if lang in _SPACED_NON_LATIN:
                    for kw in _keywords(source, keywords):
                        for m in re.finditer(re.escape(kw), text):
                            before = text[m.start() - 1] if m.start() > 0 else " "
                            after = text[m.end()] if m.end() < len(text) else " "
                            glued = [c for c in (before, after) if re.match(r"[^\W\d_]", c) or
                                     (c.strip() and c not in "[](){}'\".,!?:;-\u2013\u2014।॥")]
                            if glued:
                                ctx = text[max(0, m.start() - 6):m.end() + 6]
                                add(cell, WARNING, "spacing", f"關鍵字 {kw} 和前後文字之間沒有空白：「{ctx}」", text)

                # 數字
                missing = sorted(textnorm.numbers(source) - textnorm.numbers(text))
                if missing:
                    add(cell, WARNING, "number", f"原文的數字 {missing} 在翻譯中不見了，請確認", text)

                # 重複字
                reps = _repeats_chars(text) if lang in _CHAR_REPEAT_LANGS else _repeats_words(text)
                for rep in reps:
                    add(cell, WARNING, "repeat", f"可能重複字：「{rep}」", text)

                # 未翻譯
                if (textnorm.strict(text) == textnorm.strict(source)
                        and len(re.findall(r"[a-z]{2,}", source)) >= 1):
                    add(cell, WARNING, "untranslated", "翻譯和英文原文完全相同，可能沒有翻譯", text)
    return issues
