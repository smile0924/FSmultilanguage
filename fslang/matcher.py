"""比對引擎：拿 Excel 的標準翻譯，到遊戲擷取到的文字裡逐句檢查。

判定順序（每一句、每個語言）：
1. 正確 PASS        strict 正規化後完全出現在遊戲文字中
2. 警告 WARN        只在 loose 正規化後才相同（大小寫、全半形、空白、引號差異）
3. 舊翻譯 OUTDATED  遊戲顯示的是被「(revised)」欄取代的舊翻譯
4. 語言錯誤 WRONG_LANG  遊戲顯示的是同一句的其他語言（例如還是英文）
5. 內容不符 MISMATCH 找到相似度 >= 門檻的文字，但內容不同（附差異）
6. 找不到 MISSING    遊戲文字中找不到這句
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from . import textnorm
from .corpus import SEP, Corpus
from .excel import TranslationSheet

PASS = "PASS"
WARN = "WARN"
OUTDATED = "OUTDATED"
WRONG_LANG = "WRONG_LANG"
MISMATCH = "MISMATCH"
MISSING = "MISSING"
NO_CAPTURE = "NO_CAPTURE"
NO_EXPECTED = "NO_EXPECTED"

STATUS_LABELS = {
    PASS: "正確",
    WARN: "警告(格式差異)",
    OUTDATED: "舊翻譯",
    WRONG_LANG: "語言錯誤",
    MISMATCH: "內容不符",
    MISSING: "找不到",
    NO_CAPTURE: "未擷取",
    NO_EXPECTED: "Excel無翻譯",
}
STATUS_ORDER = [PASS, WARN, OUTDATED, WRONG_LANG, MISMATCH, MISSING, NO_CAPTURE, NO_EXPECTED]
FAIL_STATUSES = {OUTDATED, WRONG_LANG, MISMATCH, MISSING}


@dataclass
class Result:
    row: int
    key: str
    lang: str
    lang_name: str
    cell: str
    column_name: str
    status: str
    expected: str
    actual: str | None = None
    similarity: float | None = None
    source: str | None = None
    note: str = ""
    # [(tag, Excel 片段, 遊戲片段)]，只有內容不符/警告/舊翻譯才有
    diff: list[tuple[str, str, str]] | None = None

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]


@dataclass
class _Hit:
    text: str
    source: str
    record: int


# ---------------------------------------------------------------- 差異

def _tokens(text: str, placeholders=()) -> list[str]:
    """拉丁/西里爾字母以「字」為單位，其他文字（泰、日、韓、印地…）以「字元 + 附加符號」為單位。
    佔位符（例如 [WILD]）整個當成一個單位。"""
    spans = sorted(m.span() for p in placeholders for m in p.finditer(text) if m.end() > m.start())
    out: list[str] = []
    pos = 0
    for start, end in spans + [(len(text), len(text))]:
        if start < pos:
            continue
        for m in re.finditer(r"[0-9A-Za-z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff'\u2019]+|\s+|.", text[pos:start], re.S):
            tok = m.group()
            if out and len(tok) == 1 and unicodedata.category(tok).startswith("M"):
                out[-1] += tok  # 附加符號（母音記號、聲調）黏在前一個字元上
            else:
                out.append(tok)
        if end > start:
            out.append(text[start:end])
        pos = end
    return out


def diff_segments(expected: str, actual: str, placeholders=()) -> list[tuple[str, str, str]]:
    """回傳 [(tag, Excel 片段, 遊戲片段)]，tag 為 equal / replace / delete / insert / placeholder。
    Excel 那邊只差在佔位符（[WILD] 對應到圖示、X 對應到 x500）的片段標成 placeholder，不算差異。"""
    a, b = _tokens(expected, placeholders), _tokens(actual)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        sa, sb = "".join(a[i1:i2]), "".join(b[j1:j2])
        if tag in ("replace", "delete") and placeholders:
            rest = sa
            for p in placeholders:
                rest = p.sub("", rest)
            if not rest.strip() and (tag == "delete" or len(sb) <= 40):
                tag = "placeholder"
        out.append((tag, sa, sb))
    return out


def diff_summary(segments: list[tuple[str, str, str]], max_items: int = 4) -> str:
    """把差異寫成文字：「Excel」→「遊戲」、缺少「Excel 有但遊戲沒有」、多了「遊戲多出來的」。"""
    items = []
    for tag, a, b in segments:
        if tag == "replace":
            items.append(f"「{a.strip()}」→「{b.strip()}」")
        elif tag == "delete" and a.strip():
            items.append(f"缺少「{a.strip()}」")
        elif tag == "insert" and b.strip():
            items.append(f"多了「{b.strip()}」")
        elif tag in ("delete", "insert"):
            items.append("空白不同")
    if len(items) > max_items:
        items = items[:max_items] + [f"…共 {len(items)} 處差異"]
    return "；".join(items)


def _describe_loose(expected: str, actual: str, segments) -> str:
    reasons = []
    if expected.casefold() == actual.casefold() and expected != actual:
        reasons.append("大小寫不同")
    if re.sub(r"\s", "", expected) == re.sub(r"\s", "", actual):
        reasons.append("空白不同")
    if unicodedata.normalize("NFKC", expected) == unicodedata.normalize("NFKC", actual):
        reasons.append("全形/半形不同")
    if not reasons:
        reasons.append("大小寫/空白/標點樣式不同")
    detail = diff_summary(segments)
    return "、".join(reasons) + (f"：{detail}" if detail else "")


# ---------------------------------------------------------------- 比對器

class Matcher:
    def __init__(self, placeholders: list[str] | None = None, wildcard_max_chars: int = 40,
                 similarity_threshold: float = 0.6):
        self.placeholders = [re.compile(p) for p in (placeholders or [])]
        self.wildcard = f"[^{SEP}]{{0,{int(wildcard_max_chars)}}}?"
        self.threshold = similarity_threshold

    # 佔位符 ----------------------------------------------------------
    def fragments(self, text: str) -> list[str]:
        """以佔位符切開文字，回傳中間的固定文字片段。"""
        spans = sorted(m.span() for p in self.placeholders for m in p.finditer(text) if m.end() > m.start())
        frags, pos = [], 0
        for s, e in spans:
            if s < pos:
                pos = max(pos, e)
                continue
            frags.append(text[pos:s])
            pos = e
        frags.append(text[pos:])
        return frags

    def diff(self, expected: str, actual: str) -> list[tuple[str, str, str]]:
        return diff_segments(expected, actual, self.placeholders)

    def without_placeholders(self, text: str) -> str:
        return textnorm.strict(" ".join(self.fragments(text)))

    def _find(self, corpus: Corpus, text: str, loose: bool) -> _Hit | None:
        norm = textnorm.loose if loose else textnorm.strict
        index = corpus.loose_index if loose else corpus.strict_index
        frags = [norm(f) for f in self.fragments(textnorm.strict(text))]
        frags = [f for f in frags if f]
        if not frags:
            return None
        if len(frags) == 1:
            pos = index.text.find(frags[0])
            if pos < 0:
                return None
            span = (pos, pos + len(frags[0]))
        else:
            m = re.search(self.wildcard.join(re.escape(f) for f in frags), index.text)
            if not m:
                return None
            span = m.span()
        rec = index.record_at(span[0])
        text = index.text[span[0]:span[1]]
        if loose:
            # 換回原本的寫法（loose 會去掉空白、轉小寫，不適合直接顯示）
            original = textnorm.strict(corpus.records[rec].text)
            _, mapping = textnorm.loose_with_map(original)
            s, e = span[0] - index.starts[rec], span[1] - index.starts[rec]
            text = original[mapping[s]:mapping[e - 1] + 1]
        return _Hit(text, corpus.records[rec].source, rec)

    # 模糊比對 --------------------------------------------------------
    def best_match(self, expected: str, corpus: Corpus, exclude: set[str] = frozenset(),
                   record: int | None = None) -> tuple[float, str | None, str | None]:
        target = self.without_placeholders(expected)
        lt = len(target)
        if not lt:
            return 0.0, None, None
        sm = SequenceMatcher(None, autojunk=False)
        sm.set_seq2(target)
        best_r, best_t, best_src = 0.0, None, None

        def consider(text: str, rec: int):
            nonlocal best_r, best_t, best_src
            sm.set_seq1(text)
            if sm.real_quick_ratio() <= best_r or sm.quick_ratio() <= best_r:
                return
            r = sm.ratio()
            if r > best_r:
                best_r, best_t, best_src = r, text, corpus.records[rec].source

        for cand in corpus.candidates:
            if record is not None and cand.record != record:
                continue
            t = cand.text
            if t in exclude:
                continue
            lc = len(t)
            if lc < lt * 0.4:
                continue
            if lc <= lt * 2.5:
                consider(t, cand.record)
            elif lc <= 5000:
                # 很長的段落：用滑動視窗找最像的一段
                win, step = lt + lt // 5, max(1, lt // 4)
                for i in range(0, lc - lt // 2, step):
                    consider(t[i:i + win], cand.record)
        return best_r, best_t, best_src

    # 單句判定 --------------------------------------------------------
    def check(self, sheet: TranslationSheet, entry, lang: str, corpus: Corpus | None,
              exclude: set[str]) -> Result:
        tr = entry.translations[lang]
        language = sheet.language(lang)
        base = dict(row=entry.row, key=entry.key, lang=lang, lang_name=language.name,
                    cell=tr.cell, column_name=tr.column_name)
        if not tr.text:
            return Result(**base, status=NO_EXPECTED, expected="", note="Excel 這格沒有翻譯")
        expected = textnorm.strict(tr.text)
        if corpus is None or not len(corpus):
            return Result(**base, status=NO_CAPTURE, expected=expected, note="沒有這個語言的擷取資料")
        if not self.without_placeholders(expected):
            return Result(**base, status=NO_EXPECTED, expected=expected, note="只有佔位符，無法比對")

        hit = self._find(corpus, expected, loose=False)
        if hit:
            return Result(**base, status=PASS, expected=expected, actual=hit.text, similarity=1.0, source=hit.source)

        hit = self._find(corpus, expected, loose=True)
        if hit:
            segs = self.diff(expected, hit.text)
            return Result(**base, status=WARN, expected=expected, actual=hit.text, similarity=1.0,
                          source=hit.source, diff=segs,
                          note=_describe_loose(self.without_placeholders(expected),
                                               self.without_placeholders(hit.text), segs))

        if tr.old_text:
            hit = self._find(corpus, tr.old_text, loose=False) or self._find(corpus, tr.old_text, loose=True)
            if hit:
                segs = self.diff(expected, hit.text)
                return Result(**base, status=OUTDATED, expected=expected, actual=hit.text, source=hit.source,
                              similarity=round(SequenceMatcher(None, expected, hit.text, autojunk=False).ratio(), 3),
                              diff=segs,
                              note=f"遊戲還是舊翻譯（{tr.old_cell}），應更新為 {tr.cell}：{diff_summary(segs)}")

        for other_code, other in entry.translations.items():
            if other_code == lang or not other.text or textnorm.strict(other.text) == expected:
                continue
            if not self.without_placeholders(textnorm.strict(other.text)):
                continue
            hit = self._find(corpus, other.text, loose=False)
            if hit:
                other_name = sheet.language(other_code).name
                return Result(**base, status=WRONG_LANG, expected=expected, actual=hit.text, source=hit.source,
                              note=f"遊戲顯示的是 {other_name} 的文字（{other.cell}），不是 {language.name}")

        ratio, text, source = self.best_match(expected, corpus, exclude - {expected})
        if text is not None and ratio >= self.threshold:
            segs = self.diff(expected, text)
            return Result(**base, status=MISMATCH, expected=expected, actual=text, similarity=round(ratio, 3),
                          source=source, diff=segs, note=diff_summary(segs))
        note = "遊戲文字中找不到這句"
        if text is not None and ratio >= 0.45:
            note += f"（最接近的文字相似度 {ratio:.0%}）"
            return Result(**base, status=MISSING, expected=expected, actual=text, similarity=round(ratio, 3),
                          source=source, note=note)
        return Result(**base, status=MISSING, expected=expected, note=note)


def compare(sheet: TranslationSheet, corpora: dict[str, Corpus], matcher: Matcher,
            langs: list[str] | None = None) -> list[Result]:
    langs = langs or sheet.codes
    results: list[Result] = []
    for lang in langs:
        corpus = corpora.get(lang)
        exclude = {textnorm.strict(e.translations[lang].text) for e in sheet.entries
                   if lang in e.translations and e.translations[lang].text}
        for entry in sheet.entries:
            if lang in entry.translations:
                results.append(matcher.check(sheet, entry, lang, corpus, exclude))
    return results


def summarize(results: list[Result]) -> dict[str, dict[str, int]]:
    """{lang: {status: count}}"""
    out: dict[str, dict[str, int]] = {}
    for r in results:
        out.setdefault(r.lang, {s: 0 for s in STATUS_ORDER})[r.status] += 1
    return out
