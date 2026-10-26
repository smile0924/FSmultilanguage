"""用 Playwright 打開遊戲、擷取各語言實際顯示的文字。

兩種模式：
- 自動模式：依設定檔的 game_url（含 {lang}）與 steps，逐一語言開啟遊戲並擷取。
- 互動模式（--interactive）：開一個瀏覽器視窗，由你手動切換語言、打開說明頁，
  每個語言按 Enter 擷取一次。還不知道遊戲網址格式時，先用這個模式最方便。

每次擷取會收集：
- 每個頁面/iframe 顯示中的文字（innerText），以及隱藏中的文字區塊
- Cocos Creator / LayaAir 等 canvas 遊戲引擎裡 Label 的文字（如果抓得到）
- 網路載入的語系檔（JSON / JS / HTML / XML / 文字）裡的字串
- 每個頁面的截圖
存成 captures/<game_id>/<lang>.json，給 compare 指令使用。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from .config import GameConfig
from .corpus import CAPTURE_FORMAT
from .excel import TranslationSheet
from .extract import guess_kind, strings_from_text

DOM_JS = r"""
() => {
  const out = {visible: '', hidden: [], canvas: [], canvas_hidden: []};
  const body = document.body;
  if (body) {
    out.visible = body.innerText || '';
    const BLOCKS = 'p,li,td,th,h1,h2,h3,h4,h5,h6,dt,dd,label,button,span,div,a,figcaption,caption,font,b,strong';
    const CONTAINERS = 'p,li,td,th,h1,h2,h3,h4,h5,h6,dt,dd,div,ul,ol,table,section,article';
    const seen = new Set();
    for (const el of body.querySelectorAll(BLOCKS)) {
      if (el.querySelector(CONTAINERS)) continue;
      if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) continue;
      const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
      if (t && !seen.has(t)) { seen.add(t); out.hidden.push(t); }
    }
  }
  const push = (text, visible) => {
    if (typeof text !== 'string' || !text.trim()) return;
    (visible ? out.canvas : out.canvas_hidden).push(text);
  };
  // Cocos Creator 2.x / 3.x
  try {
    const cc = window.cc;
    if (cc && cc.director && cc.director.getScene) {
      const walk = (node, depth) => {
        if (!node || depth > 60) return;
        const comps = node._components || node.components || [];
        for (const c of comps) {
          if (c && typeof c.string === 'string') push(c.string, node.activeInHierarchy !== false);
        }
        for (const ch of (node.children || node._children || [])) walk(ch, depth + 1);
      };
      walk(cc.director.getScene(), 0);
    }
  } catch (e) {}
  // LayaAir
  try {
    const Laya = window.Laya;
    if (Laya && Laya.stage) {
      const walk = (node, visible, depth) => {
        if (!node || depth > 60) return;
        const vis = visible && node.visible !== false;
        if (typeof node.text === 'string') push(node.text, vis);
        const n = node.numChildren || 0;
        for (let i = 0; i < n; i++) walk(node.getChildAt(i), vis, depth + 1);
      };
      walk(Laya.stage, true, 0);
    }
  } catch (e) {}
  return out;
}
"""

_SKIP_RESOURCE_TYPES = {"image", "media", "font", "stylesheet", "websocket", "manifest"}


class NetworkRecorder:
    """記錄瀏覽器載入的文字類檔案，並取出其中的字串。"""

    def __init__(self, max_bytes: int = 15_000_000):
        self.max_bytes = max_bytes
        self.items: list[dict] = []
        self.pending: set[asyncio.Task] = set()

    def attach(self, context):
        context.on("response", self._on_response)

    def _on_response(self, response):
        task = asyncio.ensure_future(self._handle(response))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _handle(self, response):
        try:
            if response.status >= 300:
                return
            if response.request.resource_type in _SKIP_RESOURCE_TYPES:
                return
            ct = response.headers.get("content-type", "")
            body = await response.body()
            if not body or len(body) > self.max_bytes or b"\x00" in body[:2048]:
                return
            m = re.search(r"charset=([\w-]+)", ct, re.I)
            try:
                text = body.decode(m.group(1) if m else "utf-8", errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            kind = guess_kind(response.url, ct, text)
            if not kind:
                return
            strings = strings_from_text(text, kind)
            if strings:
                self.items.append({"url": response.url, "content_type": ct, "kind": kind, "strings": strings})
        except Exception:
            pass

    async def flush(self) -> list[dict]:
        if self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)
        items, self.items = self.items, []
        return items


def _fmt(value, lang: str, game_lang: str):
    if isinstance(value, str):
        return value.replace("{lang}", lang).replace("{game_lang}", game_lang)
    if isinstance(value, list):
        return [_fmt(v, lang, game_lang) for v in value]
    if isinstance(value, dict):
        return {k: _fmt(v, lang, game_lang) for k, v in value.items()}
    return value


async def take_snapshot(context, label: str, out_dir: Path, lang: str, index: int, screenshot: bool) -> dict:
    snap = {"label": label, "time": datetime.now().isoformat(timespec="seconds"),
            "pages": [], "dom": [], "screenshots": []}
    for p_idx, page in enumerate(context.pages):
        if page.is_closed():
            continue
        snap["pages"].append(page.url)
        for frame in page.frames:
            try:
                data = await frame.evaluate(DOM_JS)
            except Exception:
                continue
            data["url"] = frame.url
            snap["dom"].append(data)
        if screenshot:
            name = f"{lang}_{index}_{p_idx}.png"
            try:
                await page.screenshot(path=str(out_dir / name))
                snap["screenshots"].append(name)
            except Exception:
                pass
    return snap


def _save(out_dir: Path, cfg: GameConfig, lang: str, mode: str, snapshots: list[dict], network: list[dict]) -> Path:
    data = {
        "format": CAPTURE_FORMAT,
        "game_id": cfg.game_id,
        "lang": lang,
        "game_lang": cfg.game_lang(lang),
        "mode": mode,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "snapshots": snapshots,
        "network": network,
    }
    path = out_dir / f"{lang}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _count(snapshots: list[dict], network: list[dict]) -> str:
    dom_chars = sum(len(f.get("visible", "")) for s in snapshots for f in s["dom"])
    canvas = sum(len(f.get("canvas", [])) for s in snapshots for f in s["dom"])
    strings = sum(len(n["strings"]) for n in network)
    return f"畫面文字 {dom_chars} 字、引擎文字 {canvas} 筆、網路檔案 {len(network)} 個（{strings} 個字串）"


class _State:
    page = None


async def _run_steps(state: _State, steps: list[dict], cfg: GameConfig, lang: str, out_dir: Path,
                     snapshots: list[dict], context):
    game_lang = cfg.game_lang(lang)
    timeout = cfg.collect["timeout_ms"]
    for raw in steps:
        step = _fmt(raw, lang, game_lang)
        page = state.page
        target = page.frame_locator(step["frame"]) if step.get("frame") else page
        try:
            if "goto" in step:
                await page.goto(step["goto"], wait_until="domcontentloaded", timeout=timeout)
            elif "click" in step:
                await target.locator(step["click"]).first.click(timeout=step.get("timeout", timeout))
            elif "click_xy" in step:
                x, y = step["click_xy"]
                await page.mouse.click(x, y)
            elif "wait" in step:
                await page.wait_for_timeout(int(step["wait"]))
            elif "wait_for" in step:
                await target.locator(step["wait_for"]).first.wait_for(timeout=step.get("timeout", timeout))
            elif "press" in step:
                await page.keyboard.press(step["press"])
            elif "fill" in step:
                await target.locator(step["fill"]).first.fill(step.get("value", ""))
            elif "select" in step:
                await target.locator(step["select"]).first.select_option(step.get("value"))
            elif "eval" in step:
                await page.evaluate(step["eval"])
            elif "capture" in step:
                snapshots.append(await take_snapshot(context, str(step["capture"]), out_dir, lang,
                                                     len(snapshots), cfg.collect["screenshot"]))
            else:
                print(f"  ! 不認得的步驟：{raw}")
        except Exception as e:
            if step.get("optional"):
                continue
            raise RuntimeError(f"步驟失敗 {raw}：{e}") from e
        if "wait" not in step and "capture" not in step and step.get("after_ms", 300):
            await page.wait_for_timeout(int(step.get("after_ms", 300)))


async def _new_context(p, browser, cfg: GameConfig, lang: str | None):
    opts = {"ignore_https_errors": True}
    device = cfg.collect.get("device")
    if device:
        opts.update(p.devices[device])
    else:
        w, h = cfg.collect.get("viewport") or [1280, 800]
        opts["viewport"] = {"width": int(w), "height": int(h)}
    # 有些遊戲依瀏覽器語言決定顯示語言，所以順便把瀏覽器語言設成目標語言
    if lang and cfg.collect.get("set_browser_locale", True) and re.fullmatch(r"[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*",
                                                                             cfg.game_lang(lang)):
        opts["locale"] = cfg.game_lang(lang).replace("_", "-")
    return await browser.new_context(**opts)


def _start_url(cfg: GameConfig, lang: str) -> str:
    url = cfg.collect.get("game_url") or cfg.collect.get("list_url") or ""
    return _fmt(url, lang, cfg.game_lang(lang))


async def _collect_auto(p, browser, cfg: GameConfig, sheet: TranslationSheet, langs: list[str], out_dir: Path):
    if not cfg.collect.get("game_url") and not cfg.collect.get("steps"):
        print("! 設定檔沒有 game_url 也沒有 steps，只會擷取列表頁。建議先用 --interactive 模式。")
    saved = []
    for lang in langs:
        name = sheet.language(lang).name
        print(f"- [{lang}] {name} 擷取中…")
        context = await _new_context(p, browser, cfg, lang)
        recorder = NetworkRecorder()
        if cfg.collect.get("capture_network", True):
            recorder.attach(context)
        state = _State()
        context.on("page", lambda pg: setattr(state, "page", pg))
        state.page = await context.new_page()
        snapshots: list[dict] = []
        try:
            url = _start_url(cfg, lang)
            if url:
                await state.page.goto(url, wait_until="domcontentloaded", timeout=cfg.collect["timeout_ms"])
                try:
                    await state.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
            await state.page.wait_for_timeout(int(cfg.collect.get("wait_ms", 0)))
            await _run_steps(state, cfg.collect.get("steps", []), cfg, lang, out_dir, snapshots, context)
            if not snapshots:
                snapshots.append(await take_snapshot(context, "end", out_dir, lang, 0, cfg.collect["screenshot"]))
        except Exception as e:
            print(f"  ! {lang} 擷取失敗：{e}")
            await context.close()
            continue
        network = await recorder.flush()
        path = _save(out_dir, cfg, lang, "auto", snapshots, network)
        print(f"  已儲存 {path}（{_count(snapshots, network)}）")
        saved.append(path)
        await context.close()
    return saved


async def _ainput(prompt: str) -> str:
    try:
        return await asyncio.get_running_loop().run_in_executor(None, input, prompt)
    except EOFError:
        return "q"


async def _collect_interactive(p, browser, cfg: GameConfig, sheet: TranslationSheet, langs: list[str],
                               out_dir: Path):
    context = await _new_context(p, browser, cfg, None)
    recorder = NetworkRecorder()
    if cfg.collect.get("capture_network", True):
        recorder.attach(context)
    page = await context.new_page()
    url = _start_url(cfg, langs[0]) if langs else ""
    if url:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=cfg.collect["timeout_ms"])
        except Exception as e:
            print(f"! 開啟 {url} 失敗：{e}")
    print("\n瀏覽器已開啟。請在瀏覽器中打開「{}」，接著依照下面的提示操作。".format(cfg.game_name))
    print("提示：擷取的是「當下畫面」的文字，請先打開要檢查的畫面（例如遊戲說明/規則頁）。")
    saved = []
    for i, lang in enumerate(langs, start=1):
        name = sheet.language(lang).name
        ans = await _ainput(f"\n[{i}/{len(langs)}] 請把遊戲切換成「{name}」({lang}) 並打開要檢查的畫面，"
                            f"完成後按 Enter 擷取（s=跳過, q=結束）：")
        ans = ans.strip().lower()
        if ans == "q":
            break
        if ans == "s":
            continue
        snapshots = []
        while True:
            snap = await take_snapshot(context, f"page{len(snapshots) + 1}", out_dir, lang, len(snapshots),
                                       cfg.collect["screenshot"])
            snapshots.append(snap)
            for u in snap["pages"]:
                print(f"    頁面：{u}")
            more = await _ainput("  已擷取。還有下一頁說明就翻頁後按 Enter 再擷取；換下一個語言請輸入 n：")
            if more.strip().lower() in ("n", "q", "s"):
                break
        network = await recorder.flush()
        path = _save(out_dir, cfg, lang, "interactive", snapshots, network)
        print(f"  已儲存 {path}（{_count(snapshots, network)}）")
        saved.append(path)
    await context.close()
    return saved


async def collect_async(cfg: GameConfig, sheet: TranslationSheet, langs: list[str], out_dir: Path,
                        interactive: bool = False, headless: bool | None = None) -> list[Path]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise SystemExit("需要先安裝 Playwright：pip install playwright && python -m playwright install chromium") from e
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if headless is None:
        headless = False if interactive else bool(cfg.collect.get("headless", True))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            if interactive:
                return await _collect_interactive(p, browser, cfg, sheet, langs, out_dir)
            return await _collect_auto(p, browser, cfg, sheet, langs, out_dir)
        finally:
            await browser.close()


def collect(*args, **kwargs) -> list[Path]:
    return asyncio.run(collect_async(*args, **kwargs))
