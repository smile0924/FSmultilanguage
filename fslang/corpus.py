"""遊戲「實際顯示文字」的語料庫。

來源可以是：
- `collect` 指令擷取的 JSON（格式 fslang-capture/1：DOM 文字 + 網路載入的語系檔字串）
- 手動放入的檔案：.txt（從遊戲複製的文字）、.json（遊戲語系檔）、.html、.js

擷取資料夾中，檔名以語言代碼開頭的檔案都會被併入該語言，例如
`th.json`、`th_help.txt`、`th.manual.txt` 都屬於 th。
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import textnorm
from .extract import html_to_text, strings_from_js, strings_from_json

CAPTURE_FORMAT = "fslang-capture/1"
SUPPORTED_SUFFIXES = {".json", ".txt", ".html", ".htm", ".js", ".csv", ".xml", ".properties"}
# 串接各筆紀錄時使用的分隔字元；佔位符萬用字元不會跨過它
SEP = "\x1e"

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？।॥])\s+")


@dataclass
class Record:
    source: str
    text: str


@dataclass
class Candidate:
    text: str       # strict 正規化後的一行/一句
    record: int     # 所屬紀錄編號


class _Index:
    def __init__(self, records: list[Record], norm):
        starts, parts, pos = [], [], 0
        for rec in records:
            t = norm(rec.text).replace(SEP, " ")
            starts.append(pos)
            parts.append(t)
            pos += len(t) + 1
        self.starts = starts
        self.text = SEP.join(parts)

    def record_at(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos) - 1


class Corpus:
    def __init__(self, records: list[Record] | None = None, files: list[Path] | None = None):
        self.records: list[Record] = [r for r in (records or []) if r.text and r.text.strip()]
        self.files = files or []
        self._strict = None
        self._loose = None
        self._candidates = None

    def __len__(self):
        return len(self.records)

    @property
    def strict_index(self) -> _Index:
        if self._strict is None:
            self._strict = _Index(self.records, textnorm.strict)
        return self._strict

    @property
    def loose_index(self) -> _Index:
        if self._loose is None:
            self._loose = _Index(self.records, textnorm.loose)
        return self._loose

    @property
    def candidates(self) -> list[Candidate]:
        """模糊比對用的候選片段：每一行，以及長行切出的每一句。"""
        if self._candidates is None:
            seen: set[str] = set()
            out: list[Candidate] = []

            def add(text: str, idx: int):
                text = textnorm.strict(text)
                if text and text not in seen:
                    seen.add(text)
                    out.append(Candidate(text, idx))

            for idx, rec in enumerate(self.records):
                for line in rec.text.splitlines():
                    add(line, idx)
                    if len(line) > 60:
                        for sentence in _SENTENCE_RE.split(line):
                            add(sentence, idx)
                if "\n" in rec.text and len(rec.text) <= 500:
                    add(rec.text, idx)
            self._candidates = out
        return self._candidates

    @classmethod
    def from_files(cls, paths: list[Path]) -> "Corpus":
        records: list[Record] = []
        for p in paths:
            records.extend(load_file(p))
        return cls(records, files=list(paths))


def load_file(path: Path) -> list[Record]:
    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    name = path.name
    if suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return [Record(f"file {name}", text)]
        if isinstance(data, dict) and data.get("format") == CAPTURE_FORMAT:
            return records_from_capture(data)
        return [Record(f"file {name}", s) for s in strings_from_json(data)]
    if suffix in (".html", ".htm", ".xml"):
        return [Record(f"file {name}", html_to_text(text))]
    if suffix == ".js":
        return [Record(f"file {name}", s) for s in strings_from_js(text)]
    return [Record(f"file {name}", text)]


def records_from_capture(data: dict) -> list[Record]:
    records: list[Record] = []
    for snap in data.get("snapshots", []):
        label = snap.get("label") or ""
        for frame in snap.get("dom", []):
            src = f"DOM {label} {frame.get('url', '')}".replace("  ", " ")
            if frame.get("visible"):
                records.append(Record(src, frame["visible"]))
            for text in frame.get("canvas", []):
                records.append(Record(src + " (canvas)", text))
            for block in frame.get("hidden", []):
                records.append(Record(src + " (隱藏中)", block))
            for text in frame.get("canvas_hidden", []):
                records.append(Record(src + " (canvas 隱藏中)", text))
    for res in data.get("network", []) + [n for s in data.get("snapshots", []) for n in s.get("network", [])]:
        src = f"network {res.get('url', '')}"
        for s in res.get("strings", []):
            records.append(Record(src, s))
    return records


def _belongs_to(path: Path, lang: str) -> bool:
    stem = path.name.lower()
    lang = lang.lower()
    return stem.startswith(lang + ".") or stem.startswith(lang + "_")


def find_language_files(captures_dir: Path, lang: str) -> list[Path]:
    captures_dir = Path(captures_dir)
    if not captures_dir.is_dir():
        return []
    return sorted(p for p in captures_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and _belongs_to(p, lang))


def load_captures(captures_dir: Path, langs: list[str]) -> dict[str, Corpus]:
    result = {}
    for lang in langs:
        files = find_language_files(captures_dir, lang)
        if files:
            result[lang] = Corpus.from_files(files)
    return result
