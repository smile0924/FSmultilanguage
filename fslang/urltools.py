"""遊戲網址工具：找出網址中代表語言的部分，換成 {game_lang}，才能自動切換各語言。

例如 https://host/game.jsp?id=12&lang=en  ->  https://host/game.jsp?id=12&lang={game_lang}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

LANG_PARAM_NAMES = {
    "lang", "language", "locale", "lng", "lan", "langcode", "lang_code", "languagecode", "language_code",
    "l", "ln", "hl", "culture", "langid", "lang_id",
}
_LANG_VALUES = {
    "en", "th", "ja", "jp", "ko", "kr", "zh", "cn", "tw", "hk", "vi", "vn", "id", "ms", "my", "tr", "ru",
    "es", "fr", "pt", "br", "hi", "bn", "it", "de", "km", "lo", "fil", "tl", "ar", "fa", "he", "el", "nl",
    "pl", "sv", "fi", "da", "no", "nb", "uk", "bg", "ro", "cs", "hu", "sc", "tc", "zh-cn", "zh-tw", "pt-br",
}
_LOCALE_RE = re.compile(r"[a-z]{2,3}[-_][a-z]{2,4}", re.I)

# 語言代碼 -> 常見的「語言-地區」寫法
LOCALES = {
    "en": "en-US", "th": "th-TH", "ja": "ja-JP", "ko": "ko-KR", "tr": "tr-TR", "ru": "ru-RU", "es": "es-ES",
    "fr": "fr-FR", "pt": "pt-PT", "pt-br": "pt-BR", "hi": "hi-IN", "bn": "bn-BD", "it": "it-IT", "de": "de-DE",
    "vi": "vi-VN", "id": "id-ID", "ms": "ms-MY", "my": "my-MM", "km": "km-KH", "zh": "zh-CN", "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
}
# 有些遊戲用國家代碼表示語言
COUNTRY_STYLE = {"ja": "jp", "ko": "kr", "zh": "cn", "zh-cn": "cn", "zh-tw": "tw", "vi": "vn"}


@dataclass
class UrlLang:
    template: str        # 含 {game_lang} 的網址樣板
    found: str           # 找到的位置，例如 "lang=en"
    sample: str | None   # 原本網址中的語言值，例如 "en"


def looks_like_lang(value: str) -> bool:
    v = unquote(value).strip().lower()
    if v in _LANG_VALUES:
        return True
    return bool(_LOCALE_RE.fullmatch(v)) and v[:2] in _LANG_VALUES


def _replace_in_query(query: str) -> tuple[str, str, str] | None:
    pieces = query.split("&")
    pairs = [p.split("=", 1) if "=" in p else [p, ""] for p in pieces]

    def hit(i):
        key, value = pairs[i]
        pieces[i] = f"{key}={{game_lang}}"
        return "&".join(pieces), f"{unquote(key)}={unquote(value)}", unquote(value)

    # 1. 參數名稱明顯是語言（lang=、language=、locale=…），值是什麼都接受（有些遊戲用數字）
    for i, (key, value) in enumerate(pairs):
        name = unquote(key).strip().lower()
        if value and name in LANG_PARAM_NAMES and (len(name) > 2 or looks_like_lang(value)):
            return hit(i)
    # 2. 值看起來像語言代碼（例如 ?g=12&en）
    for i, (key, value) in enumerate(pairs):
        if value and looks_like_lang(value):
            return hit(i)
    return None


def detect_lang_in_url(url: str) -> UrlLang | None:
    url = (url or "").strip()
    if not url:
        return None
    if "{lang}" in url or "{game_lang}" in url:
        return UrlLang(url, "網址中已有 {lang}/{game_lang}", None)
    parts = urlsplit(url)
    hit = _replace_in_query(parts.query) if parts.query else None
    if hit:
        query, found, sample = hit
        return UrlLang(urlunsplit(parts._replace(query=query)), found, sample)
    # 單頁應用常把參數放在 # 後面，例如 #/game?lang=en
    if "?" in parts.fragment or "=" in parts.fragment:
        head, _, frag_query = parts.fragment.rpartition("?") if "?" in parts.fragment else ("", "", parts.fragment)
        hit = _replace_in_query(frag_query)
        if hit:
            query, found, sample = hit
            fragment = f"{head}?{query}" if "?" in parts.fragment else query
            return UrlLang(urlunsplit(parts._replace(fragment=fragment)), found, sample)
    # 路徑中的語言，例如 /games/en/index.html
    segments = parts.path.split("/")
    for i, seg in enumerate(segments):
        if seg and looks_like_lang(seg):
            new = segments[:i] + ["{game_lang}"] + segments[i + 1:]
            return UrlLang(urlunsplit(parts._replace(path="/".join(new))), f"路徑 /{seg}/", unquote(seg))
    return None


def suggest_game_codes(sample: str | None, codes: list[str]) -> dict[str, str]:
    """依原網址中語言值的寫法（en / EN / en-US / en_us / jp…），推測每個語言在遊戲網址中的代碼。"""
    if not sample:
        return {c: c for c in codes}
    s = sample.strip()
    if _LOCALE_RE.fullmatch(s):
        sep = s[2] if s[2] in "-_" else s[3]
        lang_part, region = re.split(r"[-_]", s, maxsplit=1)
        result = {}
        for c in codes:
            loc = LOCALES.get(c, c)
            if "-" not in loc:
                result[c] = loc
                continue
            lp, rg = loc.split("-", 1)
            lp = lp.upper() if lang_part.isupper() else lp.lower()
            rg = rg.upper() if region.isupper() else rg.lower()
            result[c] = f"{lp}{sep}{rg}"
        return result
    style = {}
    if s.lower() in set(COUNTRY_STYLE.values()):
        style = COUNTRY_STYLE
    result = {c: style.get(c, c) for c in codes}
    if s.isupper():
        result = {c: v.upper() for c, v in result.items()}
    return result
