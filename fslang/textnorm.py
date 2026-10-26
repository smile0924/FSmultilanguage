"""文字正規化工具。

比對分兩個層級：
- strict：只消除「看不出差別」的差異（各種空白、零寬字元、Unicode 組合方式）。
  strict 相同 => 判定「正確」。
- loose：再忽略全形/半形、大小寫、引號/破折號樣式、所有空白。
  只有 loose 相同 => 判定「警告」（內容對，但格式有小差異）。
"""

from __future__ import annotations

import functools
import re
import unicodedata

# 各種寬度的空白（NBSP、窄 NBSP、全形空白…）都視為一般空白
_SPACE_RE = re.compile(r"[\s\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")
# 完全看不見、可安全移除的字元（注意：ZWJ/ZWNJ 對印地語、孟加拉語有意義，strict 不移除）
_INVISIBLE_RE = re.compile(r"[\u200b\u2060\ufeff\u00ad]")
# loose 額外移除 ZWJ/ZWNJ
_JOINER_RE = re.compile(r"[\u200c\u200d]")

_LOOSE_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "`": "'", "\u00b4": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u00ab": '"', "\u00bb": '"', "\u300c": '"', "\u300d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2015": "-", "\u2212": "-",
})

_WS_ALL_RE = re.compile(r"\s+")


def strict(text: str | None) -> str:
    """嚴格正規化：NFC、空白統一並壓縮、移除不可見字元、去頭尾空白。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = _INVISIBLE_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


@functools.lru_cache(maxsize=65536)
def _loose_char(ch: str) -> str:
    ch = unicodedata.normalize("NFKC", ch)
    ch = _JOINER_RE.sub("", ch).translate(_LOOSE_TRANSLATE).casefold()
    return _WS_ALL_RE.sub("", ch)


def loose_with_map(strict_text: str) -> tuple[str, list[int]]:
    """對「已經 strict 過」的文字做 loose，並回傳每個輸出字元對應的原始位置。"""
    out, mapping = [], []
    for i, ch in enumerate(strict_text):
        lc = _loose_char(ch)
        out.append(lc)
        mapping.extend([i] * len(lc))
    return "".join(out), mapping


def loose(text: str | None) -> str:
    """寬鬆正規化：在 strict 之上再做 NFKC、大小寫、引號樣式統一並移除所有空白。"""
    return "".join(_loose_char(ch) for ch in strict(text))


# 數字：支援千分位（2,000 / 2.000 / 2 000 / 2 000），以及各語系的數字字元
_NUMBER_RE = re.compile(r"\d{1,3}(?:[,.\u00a0\u202f ]\d{3})+(?!\d)|\d+")


def numbers(text: str | None) -> set[int]:
    """取出文字中的所有數字（千分位視為同一個數字）。"""
    result = set()
    for m in _NUMBER_RE.finditer(text or ""):
        digits = "".join(ch for ch in m.group() if ch.isdigit())
        if digits:
            result.add(int(digits))
    return result
