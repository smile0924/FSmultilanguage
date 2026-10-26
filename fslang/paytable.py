"""直接下載遊戲的說明頁（paytable）HTML 來比對，不需要開瀏覽器。

只要給一個語言的說明頁網址，例如
    https://qa-static.object1.net/touch/fsnew/20260101P/games/treeofwild/assets/paytable_en_US.73fe25c7.html
程式會找出其他語言的說明頁：
1. 下載遊戲首頁、資源清單和它載入的 JS，找出裡面提到的 paytable_xx_XX.雜湊.html 檔名
2. 試「相同雜湊」與「沒有雜湊」的檔名
3. 使用者另外提供的網址
下載後存成擷取檔（captures/<game_id>/<lang>.json），再用一般的比對流程比對。
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from .corpus import CAPTURE_FORMAT
from .extract import html_to_text, strings_from_js
from .urltools import suggest_game_codes

_NAME_RE = re.compile(r"^(?P<stem>.*?[_-])(?P<locale>[a-z]{2,3}(?:[_-][a-z]{2,4})?)(?P<hash>\.[0-9a-z]{6,})?"
                      r"(?P<ext>\.html?)$", re.I)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36 fslang")
MAX_BYTES = 20 * 1024 * 1024
MAX_SCAN = 40  # 找其他語言時最多下載幾個檔案


@dataclass
class PaytableRef:
    url: str
    base: str        # 說明頁所在資料夾（.../assets/）
    stem: str        # paytable_
    locale: str      # en_US
    hash: str        # .73fe25c7（可能是空字串）
    ext: str         # .html

    def with_locale(self, locale: str, hash_: str | None = None) -> str:
        return self.base + self.stem + locale + (self.hash if hash_ is None else hash_) + self.ext


def parse_paytable_url(url: str) -> PaytableRef | None:
    """網址的檔名是否像「說明頁_語言.雜湊.html」。"""
    url = (url or "").strip()
    if not re.match(r"https?://", url):
        return None
    parts = urlsplit(url)
    path = parts.path
    name = path.rsplit("/", 1)[-1]
    m = _NAME_RE.match(name)
    if not m:
        return None
    base = urlunsplit(parts._replace(path=path[: len(path) - len(name)], query="", fragment=""))
    return PaytableRef(url, base, m.group("stem"), m.group("locale"), m.group("hash") or "", m.group("ext"))


def locale_to_code(locale: str, codes: list[str]) -> str | None:
    """en_US -> en、zh_TW -> zh-tw（依 Excel 有哪些語言代碼）。"""
    loc = locale.replace("-", "_").lower()
    lang, _, region = loc.partition("_")
    alias = {"jp": "ja", "kr": "ko", "cn": "zh-cn", "tw": "zh-tw", "vn": "vi"}
    candidates = []
    if lang == "zh":
        candidates += {"cn": ["zh-cn", "zh"], "tw": ["zh-tw", "zh"], "hk": ["zh-tw", "zh"]}.get(region, ["zh"])
    if lang == "pt" and region == "br":
        candidates.append("pt-br")
    candidates += [loc.replace("_", "-"), lang, alias.get(lang, lang)]
    for c in candidates:
        if c in codes:
            return c
    return None


def guess_locales(code: str, sample: str) -> list[str]:
    """依範例的寫法（en_US / en-us / en）推測某個語言在檔名中的寫法。"""
    guesses = [suggest_game_codes(sample, [code])[code], code]
    if "_" in sample or "-" in sample:
        sep = "_" if "_" in sample else "-"
        guesses.append(code.replace("-", sep))
    out = []
    for g in guesses:
        if g and g not in out:
            out.append(g)
    return out


class Fetcher:
    """下載網頁（用系統的 Proxy 設定；公司憑證驗證失敗時改用不驗證並提示）。"""

    def __init__(self, log=print):
        self.log = log
        self.insecure = False
        self.count = 0
        self._cache: dict[str, tuple[int, str | None]] = {}

    def get(self, url: str) -> tuple[int, str | None]:
        if url not in self._cache:
            self.count += 1
            self._cache[url] = self._get(url)
        return self._cache[url]

    def _get(self, url: str) -> tuple[int, str | None]:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        try:
            return self._open(req)
        except urllib.error.HTTPError as e:
            return e.code, None
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError) and not self.insecure:
                self.log("! 網站憑證驗證失敗（可能是公司網路的憑證），改用不驗證憑證的方式下載")
                self.insecure = True
                try:
                    return self._open(req)
                except urllib.error.HTTPError as e2:
                    return e2.code, None
                except urllib.error.URLError as e2:
                    self.log(f"! 無法下載 {url}：{e2.reason}")
                    return 0, None
            self.log(f"! 無法下載 {url}：{e.reason}")
            return 0, None
        except (OSError, ValueError) as e:
            self.log(f"! 無法下載 {url}：{e}")
            return 0, None

    def _open(self, req) -> tuple[int, str | None]:
        ctx = ssl._create_unverified_context() if self.insecure else None
        with urllib.request.urlopen(req, timeout=30, context=ctx) as res:
            body = res.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return res.status, None
            charset = res.headers.get_content_charset()
            if not charset:
                m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
                charset = m.group(1).decode("ascii") if m else "utf-8"
            try:
                return res.status, body.decode(charset, errors="replace")
            except LookupError:
                return res.status, body.decode("utf-8", errors="replace")


_SCRIPT_REF_RE = re.compile(r"""["'(]([^"'()\s<>]+?\.m?js)(?:\?[^"'()\s<>]*)?["')]""")


def discover(start_url: str, codes: list[str], fetcher: Fetcher, extra_urls: list[str] = (),
             log=print) -> dict[str, str]:
    """找出各語言說明頁的網址。回傳 {Excel 語言代碼: 網址}。

    順序：先看小檔案（說明頁本身、遊戲首頁、資源清單）-> 猜檔名 -> 最後才翻 JS（可能很大），找齊就停。
    """
    ref = parse_paytable_url(start_url)
    if ref is None:
        raise ValueError("網址看起來不是說明頁檔案（檔名要像 paytable_en_US.73fe25c7.html）")
    result: dict[str, str] = {}

    def add(locale: str, url: str):
        code = locale_to_code(locale, codes)
        if code and code not in result:
            result[code] = url

    add(ref.locale, ref.url)
    for u in extra_urls:
        r = parse_paytable_url(u)
        if r:
            add(r.locale, r.url)
        elif u.strip():
            log(f"! 看不出這個網址是哪個語言：{u}")

    name_re = re.compile(re.escape(ref.stem) + r"([a-z]{2,3}(?:[_-][a-z]{2,4})?)((?:\.[0-9a-z]{6,})?)"
                         + re.escape(ref.ext) + r"(?![\w.])", re.I)
    host = urlsplit(ref.url).netloc
    game_dir = urljoin(ref.base, "..")
    scripts: list[str] = []

    def done() -> bool:
        return all(c in result for c in codes)

    def scan(url: str) -> str | None:
        if urlsplit(url).netloc != host:
            return None
        status, text = fetcher.get(url)
        if not text:
            return None
        for m in name_re.finditer(text):
            add(m.group(1), ref.base + m.group(0))
        for js in _SCRIPT_REF_RE.findall(text):
            for cand in (urljoin(url, js), ref.base + js.rsplit("/", 1)[-1]):
                if cand not in scripts:
                    scripts.append(cand)
        return text

    # 1. 小檔案
    index_text = None
    for url in (ref.url, game_dir + "index.html", game_dir, ref.base, game_dir + "manifest.json",
                game_dir + "asset-manifest.json", game_dir + ".vite/manifest.json", ref.base + "manifest.json"):
        if done():
            break
        text = scan(url)
        if url == game_dir + "index.html" and text:
            index_text = text

    # 2. 猜檔名：相同雜湊、沒有雜湊（找不到檔案時回傳首頁的網站不算）
    for code in codes:
        if code in result:
            continue
        for locale in guess_locales(code, ref.locale):
            for hash_ in dict.fromkeys([ref.hash, ""]):
                cand = ref.with_locale(locale, hash_)
                status, text = fetcher.get(cand)
                if status == 200 and text and "<" in text and text != index_text and len(html_to_text(text)) >= 20:
                    result[code] = cand
                    break
            if code in result:
                break

    # 3. 翻 JS（打包後的 JS 通常會寫出每個語言說明頁的檔名）
    visited: set[str] = set()
    while scripts and not done() and len(visited) < MAX_SCAN:
        url = scripts.pop(0)
        if url in visited or urlsplit(url).netloc != host:
            continue
        visited.add(url)
        log(f"  搜尋 {url.rsplit('/', 1)[-1]} …")
        scan(url)
    return result


def _external_script_strings(html: str, page_url: str, fetcher: Fetcher) -> list[dict]:
    host = urlsplit(page_url).netloc
    out = []
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, re.I)[:10]:
        url = urljoin(page_url, src)
        if urlsplit(url).netloc != host:
            continue
        status, text = fetcher.get(url)
        if text:
            out.append({"url": url, "content_type": "", "kind": "js", "strings": strings_from_js(text)})
    return out


def capture_paytable(html: str, url: str, fetcher: Fetcher) -> dict:
    """把一份說明頁 HTML 轉成擷取資料（畫面文字 + 頁內 script 的字串）。"""
    visible = html_to_text(html)
    network = []
    inline = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.I | re.S))
    if inline.strip():
        network.append({"url": url + "#script", "content_type": "", "kind": "js", "strings": strings_from_js(inline)})
    if len(visible) < 50:
        # 說明頁的文字是由 JS 產生的：把它載入的 JS 裡的字串也收進來
        network += _external_script_strings(html, url, fetcher)
    return {"label": "paytable", "pages": [url],
            "dom": [{"url": url, "visible": visible, "hidden": [], "canvas": [], "canvas_hidden": []}],
            "network": network}


def collect_paytables(cfg, sheet, langs: list[str], out_dir: Path, start_url: str,
                      extra_urls: list[str] = (), hooks=None) -> list[Path]:
    log = hooks.log if hooks else print
    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(log)
    log("尋找各語言的說明頁…")
    urls = discover(start_url, langs, fetcher, list(extra_urls), log)
    names = {l.code: l.name for l in sheet.languages}
    missing = [c for c in langs if c not in urls]
    log(f"找到 {len([c for c in langs if c in urls])} / {len(langs)} 個語言的說明頁（下載了 {fetcher.count} 個檔案）")
    for code in langs:
        if code in urls:
            log(f"  {names.get(code, code)}：{urls[code]}")
    if missing:
        log("  找不到：" + "、".join(names.get(c, c) for c in missing)
            + "（可以在「其他語言的說明頁網址」補上）")

    saved = []
    todo = [c for c in langs if c in urls]
    for i, code in enumerate(todo):
        if hooks:
            if hooks.cancelled():
                log("已停止。")
                break
            hooks.progress(i, len(todo), code)
        status, html = fetcher.get(urls[code])
        if not html:
            log(f"! {names.get(code, code)} 下載失敗（HTTP {status}）")
            continue
        (out_dir / "raw" / f"{code}.html").write_text(html, encoding="utf-8")
        snap = capture_paytable(html, urls[code], fetcher)
        data = {"format": CAPTURE_FORMAT, "game_id": cfg.game_id, "lang": code, "mode": "paytable",
                "captured_at": datetime.now().isoformat(timespec="seconds"), "snapshots": [snap], "network": []}
        path = out_dir / f"{code}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"- {names.get(code, code)}：說明頁 {len(snap['dom'][0]['visible'])} 字")
        saved.append(path)
    if hooks:
        hooks.progress(len(todo), len(todo), None)
    return saved
