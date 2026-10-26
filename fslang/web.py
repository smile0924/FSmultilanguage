"""本機網頁介面。

    python -m fslang serve

啟動後瀏覽器會打開 http://127.0.0.1:8000/ ，在網頁上傳翻譯 Excel、貼上遊戲網址，按「開始比對」。
伺服器只在本機執行：由這台電腦的 Playwright 打開遊戲擷取文字，結果顯示在網頁上，並可下載 Excel 報表。
每次比對的檔案（上傳的 Excel、擷取檔、截圖、報表）存放在 web_runs/。
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import queue
import re
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .collector import collect
from .config import GameConfig, config_from_args, load_config
from .excel import TranslationSheet, list_sheets, load_sheet
from .matcher import FAIL_STATUSES, STATUS_LABELS, STATUS_ORDER, summarize
from .pipeline import CompareOutcome, compare_captures, write_report
from .urltools import detect_lang_in_url, suggest_game_codes

STATIC_DIR = Path(__file__).resolve().parent / "web"
MAX_UPLOAD = 50 * 1024 * 1024
DEFAULT_PLACEHOLDERS = [r"\[[^\]]*\]", r"(?<![A-Za-z])X(?![A-Za-z])"]
DEVICES = ["", "iPhone 13", "iPhone 13 landscape", "Pixel 7", "Pixel 7 landscape", "iPad Mini"]


class UserError(Exception):
    """要顯示給使用者看的錯誤訊息。"""


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", text.lower()).strip("_") or "game"


# ---------------------------------------------------------------- 設定預設值

def _find_preset(configs_dir: Path | None, sheet_name: str) -> GameConfig | None:
    """configs/ 裡如果有同一個工作表的設定檔（例如 tree_of_wild.json），沿用它的佔位符、步驟等設定。"""
    if not configs_dir or not configs_dir.is_dir():
        return None
    for path in sorted(configs_dir.glob("*.json")):
        if path.name.startswith("demo"):
            continue
        try:
            cfg = load_config(path)
        except Exception:
            continue
        if cfg.sheet == sheet_name:
            return cfg
    return None


def _make_config(configs_dir: Path | None, excel: Path, sheet_name: str) -> tuple[GameConfig, bool]:
    preset = _find_preset(configs_dir, sheet_name)
    if preset:
        cfg = dataclasses.replace(preset, excel=excel, collect=copy.deepcopy(preset.collect),
                                  match=dict(preset.match))
        return cfg, True
    cfg = config_from_args(str(excel), sheet_name)
    cfg.placeholders = list(DEFAULT_PLACEHOLDERS)
    return cfg, False


# ---------------------------------------------------------------- 工作

@dataclasses.dataclass
class Upload:
    id: str
    path: Path
    filename: str


class Job:
    def __init__(self, jid: str, folder: Path, cfg: GameConfig, sheet: TranslationSheet, langs: list[str],
                 mode: str, headless: bool):
        self.id, self.dir, self.cfg, self.sheet, self.langs = jid, folder, cfg, sheet, langs
        self.mode, self.headless = mode, headless
        self.lock = threading.Lock()
        self.state = "running"   # running / waiting / comparing / done / error
        self.logs: list[str] = []
        self.prompt: dict | None = None
        self.answers: queue.Queue[str] = queue.Queue()
        self.progress = {"done": 0, "total": len(langs), "lang": None}
        self.result: dict | None = None
        self.error: str | None = None
        self.cancel = False

    @property
    def captures(self) -> Path:
        return self.dir / "captures"

    @property
    def report(self) -> Path:
        return self.dir / "report.xlsx"

    def log(self, message: str):
        with self.lock:
            self.logs.append(message)

    def set_prompt(self, prompt: dict | None):
        with self.lock:
            self.prompt = prompt
            if self.state in ("running", "waiting"):
                self.state = "waiting" if prompt else "running"

    def status(self, since: int = 0) -> dict:
        with self.lock:
            return {
                "id": self.id, "state": self.state, "mode": self.mode,
                "log": self.logs[since:], "log_total": len(self.logs),
                "progress": dict(self.progress), "prompt": self.prompt,
                "error": self.error, "result": self.result,
            }

    def act(self, action: str):
        if action == "stop":
            self.cancel = True
            self.answers.put("quit")
            self.log("收到停止指令，擷取完目前的語言後就會開始比對。")
            return
        if self.prompt is None:
            raise UserError("目前沒有在等待操作")
        self.answers.put(action)

    def run(self):
        try:
            self.log(f"開始：{self.sheet.sheet}，{len(self.langs)} 個語言，"
                     f"{'自動切換語言' if self.mode == 'auto' else '手動切換語言'}")
            if self.mode == "auto":
                self.log(f"遊戲網址：{self.cfg.collect['game_url']}")
            collect(self.cfg, self.sheet, self.langs, self.captures, interactive=self.mode == "manual",
                    headless=self.headless, hooks=WebHooks(self))
            with self.lock:
                self.state = "comparing"
            self.log("比對中…")
            outcome = compare_captures(self.cfg, self.sheet, self.captures, self.langs)
            write_report(self.report, self.cfg, self.sheet, outcome, self.captures)
            result = _result_payload(self, outcome)
            with self.lock:
                self.result = result
                self.state = "done"
            bad = sum(1 for r in outcome.results if r.status in FAIL_STATUSES)
            self.log(f"完成：{len(outcome.captured_langs)} 個語言有擷取資料，{bad} 句有問題。")
        except BaseException as e:  # SystemExit（沒裝 Playwright）也要顯示在網頁上
            msg = str(e) or e.__class__.__name__
            self.log("錯誤：" + msg)
            self.log(traceback.format_exc())
            with self.lock:
                self.error = msg
                self.state = "error"


class WebHooks:
    """讓擷取程式透過網頁回報進度、等待使用者在網頁上按按鈕。"""

    def __init__(self, job: Job):
        self.job = job

    def log(self, message: str):
        self.job.log(message)

    def progress(self, done: int, total: int, lang: str | None):
        with self.job.lock:
            self.job.progress = {"done": done, "total": total, "lang": lang}

    def cancelled(self) -> bool:
        return self.job.cancel

    async def _ask(self, prompt: dict, allowed: tuple[str, ...], default: str) -> str:
        if self.job.cancel:
            return "quit"
        while not self.job.answers.empty():
            self.job.answers.get_nowait()
        self.job.set_prompt(prompt)
        try:
            answer = await asyncio.get_running_loop().run_in_executor(None, self.job.answers.get)
        finally:
            self.job.set_prompt(None)
        return answer if answer in allowed else default

    async def ask_language(self, lang: str, name: str, index: int, total: int) -> str:
        return await self._ask({"kind": "language", "lang": lang, "name": name, "index": index, "total": total},
                               ("capture", "skip", "quit"), "capture")

    async def ask_more(self, lang: str, name: str, count: int, pages: list[str]) -> str:
        return await self._ask({"kind": "more", "lang": lang, "name": name, "count": count, "pages": pages},
                               ("capture", "next", "quit"), "next")


def _result_payload(job: Job, outcome: CompareOutcome) -> dict:
    sheet = job.sheet
    summary = summarize(outcome.results)
    languages = []
    for lang in sheet.languages:
        if lang.code not in summary:
            continue
        counts = summary[lang.code]
        checked = sum(counts[s] for s in STATUS_ORDER if s not in ("NO_CAPTURE", "NO_EXPECTED"))
        languages.append({
            "code": lang.code, "name": lang.name, "counts": counts,
            "rate": (counts["PASS"] + counts["WARN"]) / checked if checked else None,
            "captured": lang.code in outcome.captured_langs,
            "screenshots": sorted(p.name for p in job.captures.glob(f"{lang.code}_*.png")),
        })
    results = []
    for r in outcome.results:
        d = dataclasses.asdict(r)
        d["label"] = r.label
        results.append(d)
    return {
        "sheet": sheet.sheet,
        "rows": [{"row": e.row, "key": e.key} for e in sheet.entries],
        "languages": languages,
        "results": results,
        "issues": [dataclasses.asdict(i) for i in outcome.issues or []],
        "status_labels": STATUS_LABELS,
        "status_order": STATUS_ORDER,
        "fail_statuses": sorted(FAIL_STATUSES),
        "report": f"/api/jobs/{job.id}/report",
    }


# ---------------------------------------------------------------- 應用程式

class App:
    def __init__(self, workdir: Path, configs_dir: Path | None):
        self.workdir = Path(workdir)
        self.configs_dir = configs_dir
        self.uploads: dict[str, Upload] = {}
        self.jobs: dict[str, Job] = {}

    def upload(self, filename: str, data: bytes) -> dict:
        name = Path(filename.replace("\\", "/")).name
        if not name.lower().endswith((".xlsx", ".xlsm")):
            raise UserError("請上傳 Excel 檔（.xlsx）")
        if not data:
            raise UserError("檔案是空的")
        uid = _new_id()
        folder = self.workdir / "uploads" / uid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (re.sub(r"[^\w.\- ()]", "_", name) or "upload.xlsx")
        path.write_bytes(data)
        try:
            sheet_names = list_sheets(path)
        except Exception as e:
            raise UserError(f"讀不到這個 Excel：{e}") from e
        sheets = []
        for sheet_name in sheet_names:
            try:
                cfg, has_preset = _make_config(self.configs_dir, path, sheet_name)
                sheet = load_sheet(cfg)
            except Exception as e:
                sheets.append({"name": sheet_name, "error": f"無法讀取：{e}"})
                continue
            if not sheet.entries:
                sheets.append({"name": sheet_name, "error": "沒有資料"})
                continue
            sheets.append({
                "name": sheet.sheet,
                "rows": len(sheet.entries),
                "source": sheet.source_lang,
                "languages": [{
                    "code": l.code, "name": l.name, "revision": l.revision_name,
                    "filled": sum(1 for e in sheet.entries if e.translations[l.code].text),
                } for l in sheet.languages],
                "preset": {
                    "from_config": has_preset,
                    "placeholders": cfg.placeholders,
                    "game_url": cfg.collect.get("game_url") or "",
                    "game_lang_codes": cfg.collect.get("game_lang_codes") or {},
                    "steps": cfg.collect.get("steps") or [],
                    "wait_seconds": round(int(cfg.collect.get("wait_ms") or 0) / 1000, 1),
                },
            })
        self.uploads[uid] = Upload(uid, path, name)
        return {"upload_id": uid, "filename": name, "sheets": sheets}

    def start_job(self, params: dict) -> dict:
        upload = self.uploads.get(params.get("upload_id", ""))
        if not upload:
            raise UserError("找不到上傳的 Excel，請重新上傳")
        sheet_name = params.get("sheet") or ""
        cfg, _ = _make_config(self.configs_dir, upload.path, sheet_name)
        sheet = load_sheet(cfg)

        placeholders = [p.strip() for p in params.get("placeholders", []) if p.strip()]
        for p in placeholders:
            try:
                re.compile(p)
            except re.error as e:
                raise UserError(f"佔位符規則有錯：{p}（{e}）") from e
        cfg.placeholders = placeholders

        langs = params.get("langs") or []
        codes = [l["code"] for l in langs if l.get("code") in sheet.codes]
        if not codes:
            raise UserError("請至少選一個語言")
        cfg.collect["game_lang_codes"] = {l["code"]: (l.get("game_code") or l["code"]).strip()
                                          for l in langs if l.get("code") in codes}

        mode = "manual" if params.get("mode") == "manual" else "auto"
        url = (params.get("url") or "").strip()
        if url and not re.match(r"https?://", url):
            raise UserError("遊戲網址要以 http:// 或 https:// 開頭")
        if mode == "auto":
            if not url:
                raise UserError("請輸入遊戲網址")
            found = detect_lang_in_url(url)
            if not found:
                raise UserError("網址裡找不到語言參數（例如 lang=en），沒辦法自動切換語言。"
                                "請把網址中語言的位置改成 {lang}，或改用「手動切換語言」。")
            cfg.collect["game_url"] = found.template
        else:
            cfg.collect["game_url"] = ""
            cfg.collect["list_url"] = url

        try:
            cfg.collect["wait_ms"] = int(float(params.get("wait_seconds") or 0) * 1000)
        except ValueError as e:
            raise UserError("等待秒數要是數字") from e
        steps = []
        if (params.get("help_selector") or "").strip():
            steps += [{"click": params["help_selector"].strip()}, {"wait": 1500}]
        raw_steps = (params.get("steps") or "").strip()
        if raw_steps:
            try:
                extra = json.loads(raw_steps)
            except ValueError as e:
                raise UserError(f"「開啟後的動作」不是正確的 JSON：{e}") from e
            if not isinstance(extra, list):
                raise UserError("「開啟後的動作」要是 JSON 陣列，例如 [{\"click\": \"#help\"}]")
            steps += extra
        cfg.collect["steps"] = steps
        device = params.get("device") or None
        if device and device not in DEVICES:
            raise UserError(f"不支援的裝置：{device}")
        cfg.collect["device"] = device
        cfg.game_name = sheet.sheet
        cfg.game_id = _slug(sheet.sheet)

        headless = params.get("headless")
        if headless is None:
            headless = False if mode == "manual" else not params.get("show_browser", False)

        jid = _new_id()
        job = Job(jid, self.workdir / "jobs" / f"{jid}_{cfg.game_id}", cfg, sheet, codes, mode, bool(headless))
        job.dir.mkdir(parents=True, exist_ok=True)
        self.jobs[jid] = job
        threading.Thread(target=job.run, daemon=True).start()
        return {"job_id": jid}

    def detect_url(self, params: dict) -> dict:
        found = detect_lang_in_url(params.get("url") or "")
        codes = params.get("codes") or []
        if not found:
            return {"found": False, "game_codes": {c: c for c in codes}}
        return {"found": True, "template": found.template, "where": found.found, "sample": found.sample,
                "game_codes": suggest_game_codes(found.sample, codes)}

    def job(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if not job:
            raise UserError("找不到這次比對")
        return job


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "fslang"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _local_host_ok(self) -> bool:
        # 防 DNS rebinding：只接受以本機名稱連進來的請求
        if self.server.server_address[0] not in ("127.0.0.1", "localhost", "::1"):
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _route(self, method: str):
        if not self._local_host_ok():
            return self._json({"error": "forbidden"}, 403)
        # 其他網站不能呼叫這裡的 API（需要自訂標頭，跨網域請求會被瀏覽器擋下）
        if method == "POST" and self.headers.get("X-Fslang") != "1":
            return self._json({"error": "forbidden"}, 403)
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if method == "GET":
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api/info":
                    return self._json({"devices": DEVICES})
                m = re.fullmatch(r"/api/jobs/([\w-]+)", path)
                if m:
                    since = int(parse_qs(parts.query).get("since", ["0"])[0] or 0)
                    return self._json(self.app.job(m.group(1)).status(since))
                m = re.fullmatch(r"/api/jobs/([\w-]+)/report", path)
                if m:
                    job = self.app.job(m.group(1))
                    if not job.report.is_file():
                        raise UserError("報表還沒產生")
                    name = f"{job.sheet.sheet}_{job.id[:15]}.xlsx"
                    disp = f"attachment; filename=\"report.xlsx\"; filename*=UTF-8''{quote(name)}"
                    return self._send(200, job.report.read_bytes(),
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      {"Content-Disposition": disp})
                m = re.fullmatch(r"/api/jobs/([\w-]+)/files/([\w.-]+\.png)", path)
                if m:
                    file = self.app.job(m.group(1)).captures / m.group(2)
                    if not file.is_file():
                        return self._json({"error": "not found"}, 404)
                    return self._send(200, file.read_bytes(), "image/png")
                return self._json({"error": "not found"}, 404)

            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                raise UserError("檔案太大（上限 50MB）")
            body = self.rfile.read(length) if length else b""
            if path == "/api/upload":
                filename = unquote(self.headers.get("X-Filename") or "upload.xlsx")
                return self._json(self.app.upload(filename, body))
            params = json.loads(body.decode("utf-8") or "{}")
            if path == "/api/detect-url":
                return self._json(self.app.detect_url(params))
            if path == "/api/jobs":
                return self._json(self.app.start_job(params))
            m = re.fullmatch(r"/api/jobs/([\w-]+)/action", path)
            if m:
                self.app.job(m.group(1)).act(params.get("action", ""))
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except UserError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"伺服器錯誤：{e}"}, 500)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def _default_configs_dir() -> Path | None:
    for candidate in (Path.cwd() / "configs", Path(__file__).resolve().parents[1] / "configs"):
        if candidate.is_dir():
            return candidate
    return None


def make_server(host: str = "127.0.0.1", port: int = 8000, workdir: str | Path = "web_runs",
                configs_dir: Path | None = None) -> ThreadingHTTPServer:
    last_error = None
    for p in range(port, port + 20) if port else [0]:
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            break
        except OSError as e:  # 連接埠被占用就換下一個
            last_error = e
    else:
        raise SystemExit(f"無法啟動伺服器：{last_error}")
    server.daemon_threads = True
    server.app = App(Path(workdir), configs_dir or _default_configs_dir())  # type: ignore[attr-defined]
    return server


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True, workdir: str | Path = "web_runs"):
    server = make_server(host, port, workdir)
    shown = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    url = f"http://{shown}:{server.server_address[1]}/"
    print(f"網頁介面已啟動：{url}")
    print("在網頁上傳 Excel、貼上遊戲網址就可以開始比對。按 Ctrl+C 結束。")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束。")
    finally:
        server.server_close()
