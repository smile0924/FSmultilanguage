"""從各種檔案內容（HTML / JSON / JS / 純文字）中取出「可能是畫面文字」的字串。"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    "option", "button", "label", "title",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # 壞掉的 HTML 也盡量取出文字
        pass
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    return "\n".join(line for line in lines if line)


_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_ASSET_RE = re.compile(r"^[\w./\\:@?=&%#-]+\.(png|jpe?g|gif|webp|svg|mp3|ogg|wav|m4a|json|js|css|atlas|plist|fnt|ttf|woff2?|html?)(\?.*)?$", re.I)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")
_LETTER_RE = re.compile(r"[^\W\d_]")


def is_textlike(s: str) -> bool:
    """過濾掉明顯不是畫面文字的字串（網址、檔名、base64…）。"""
    s = s.strip()
    if len(s) < 2 or not _LETTER_RE.search(s):
        return False
    if s.startswith(("http://", "https://", "data:", "//")):
        return False
    if _ASSET_RE.match(s) or _BASE64_RE.match(s):
        return False
    return True


def _expand(s: str) -> list[str]:
    """字串內若含 HTML 標籤，轉成純文字。"""
    if "<" in s and _TAG_RE.search(s):
        text = html_to_text(s)
        return [text] if text else []
    return [s]


def strings_from_json(obj) -> list[str]:
    out: list[str] = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            if is_textlike(cur):
                out.extend(_expand(cur))
        elif isinstance(cur, dict):
            stack.extend(reversed(list(cur.values())))
        elif isinstance(cur, list):
            stack.extend(reversed(cur))
    return out


_JS_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.){2,})"|\'((?:[^\'\\\n]|\\.){2,})\'|`((?:[^`\\]|\\.){2,})`', re.S)
_ESCAPE_RE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", "v": "", "0": ""}


def _unescape_js(s: str) -> str:
    def repl(m):
        e = m.group(1)
        if e.startswith("u{"):
            return chr(int(e[2:-1], 16))
        if e[0] == "u" and len(e) == 5:
            return chr(int(e[1:], 16))
        if e[0] == "x" and len(e) == 3:
            return chr(int(e[1:], 16))
        return _SIMPLE_ESCAPES.get(e, e)

    s = _ESCAPE_RE.sub(repl, s)
    # \uD83D\uDE00 這類 surrogate pair 合併回正常字元
    return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def strings_from_js(text: str) -> list[str]:
    out: list[str] = []
    for m in _JS_STRING_RE.finditer(text):
        raw = m.group(1) or m.group(2) or m.group(3) or ""
        s = _unescape_js(raw)
        if is_textlike(s):
            out.extend(_expand(s))
    return out


def strings_from_text(text: str, kind: str) -> list[str]:
    """依內容類型取出字串。kind: json / js / html / xml / text"""
    if kind == "json":
        try:
            return strings_from_json(json.loads(text))
        except ValueError:
            return strings_from_js(text)
    if kind == "js":
        return strings_from_js(text)
    if kind in ("html", "xml"):
        t = html_to_text(text)
        return [t] if t else []
    return [line for line in text.splitlines() if is_textlike(line)]


def guess_kind(url: str, content_type: str, body: str) -> str | None:
    ct = (content_type or "").lower()
    path = url.split("?", 1)[0].lower()
    head = body.lstrip()[:1]
    if "css" in ct or path.endswith(".css"):
        return None
    if "json" in ct or path.endswith(".json"):
        return "json"
    if "javascript" in ct or "ecmascript" in ct or path.endswith((".js", ".mjs")):
        return "js"
    if "html" in ct or path.endswith((".html", ".htm", ".jsp")):
        return "html"
    if "xml" in ct or path.endswith((".xml", ".plist")):
        return "xml"
    if ct.startswith("text/") or path.endswith((".txt", ".csv", ".properties", ".po", ".lang")):
        return "text"
    if head in ("{", "["):
        return "json"
    return None
