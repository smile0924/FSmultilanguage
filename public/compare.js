// Pure text-comparison logic, shared by the browser UI and the Node tests (no DOM access here).

// Header names a translation sheet may use for each language, in English or Chinese.
export const LANGUAGES = [
  { code: 'en_US', name: '英文', aliases: ['en', 'eng', 'english', 'us', 'uk', '英文', '英語', '英语', '英'] },
  { code: 'zh_CN', name: '簡體中文', aliases: ['cn', 'zh', 'chinese', 'sc', 'chs', 'zh_hans', '簡中', '简中', '簡體', '简体', '簡體中文', '简体中文', '中文', '中'] },
  { code: 'th_TH', name: '泰文', aliases: ['th', 'thai', 'thailand', '泰文', '泰語', '泰语', '泰'] },
  { code: 'id_ID', name: '印尼文', aliases: ['id', 'indonesia', 'indonesian', 'bahasa', 'bahasa_indonesia', '印尼', '印尼文', '印尼語', '印尼语'] },
  { code: 'vi_VN', name: '越南文', aliases: ['vi', 'vn', 'vietnam', 'vietnamese', '越南', '越南文', '越南語', '越南语', '越文'] },
  { code: 'ja_JP', name: '日文', aliases: ['ja', 'jp', 'japan', 'japanese', '日文', '日語', '日语', '日'] },
  { code: 'ko_KR', name: '韓文', aliases: ['ko', 'kr', 'korea', 'korean', '韓文', '韩文', '韓語', '韩语', '韓', '韩'] },
  { code: 'ru_RU', name: '俄文', aliases: ['ru', 'russia', 'russian', '俄文', '俄語', '俄语', '俄'] },
  { code: 'tr_TR', name: '土耳其文', aliases: ['tr', 'turkey', 'turkish', 'turkiye', '土耳其', '土耳其文', '土耳其語', '土耳其语'] },
  { code: 'es_ES', name: '西班牙文', aliases: ['es', 'spain', 'spanish', 'espanol', '西班牙', '西班牙文', '西班牙語', '西班牙语', '西語', '西语'] },
  { code: 'fr_FR', name: '法文', aliases: ['fr', 'france', 'french', '法文', '法語', '法语', '法'] },
  { code: 'pt_PT', name: '葡萄牙文', aliases: ['pt', 'portugal', 'portuguese', '葡萄牙', '葡萄牙文', '葡萄牙語', '葡萄牙语', '葡語', '葡语', '葡'] },
  { code: 'bn_BN', name: '孟加拉文', aliases: ['bn', 'bd', 'bangladesh', 'bengali', 'bangla', 'bn_bd', '孟加拉', '孟加拉文', '孟加拉語', '孟加拉语'] },
  { code: 'hi_HI', name: '印地文', aliases: ['hi', 'hindi', 'india', 'hi_in', '印地', '印地文', '印地語', '印地语', '印度'] },
  { code: 'it_IT', name: '義大利文', aliases: ['it', 'italy', 'italian', 'italia', '義大利', '義大利文', '意大利', '意大利文', '義語', '意语'] },
];

const LANG_BY_CODE = new Map(LANGUAGES.map((l) => [l.code, l]));

export function languageName(code) {
  return LANG_BY_CODE.get(code)?.name ?? code;
}

const REVISED_RE = /revis\w*|修訂|修正|更新/i;

/** Maps a spreadsheet header such as "Thai", "TH (revised)" or "Hindi " to { code, revised }. */
export function parseHeader(header) {
  const raw = String(header ?? '').trim();
  if (!raw) return null;
  const revised = REVISED_RE.test(raw);
  const key = raw
    .replace(/\(.*?\)|（.*?）/g, ' ')
    .replace(REVISED_RE, ' ')
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, '_');
  if (!key) return null;
  const lang = LANGUAGES.find((l) => l.code.toLowerCase() === key || l.aliases.includes(key));
  return lang ? { code: lang.code, revised } : null;
}

/**
 * Reads one sheet (array of rows, as produced by SheetJS sheet_to_json with header: 1)
 * into { headerRow, columns, languages, rows }. Each row keeps its Excel row number and,
 * per language, the original translation plus the revised one when a "(revised)" column exists.
 */
export function parseSheet(aoa) {
  let headerIndex = -1;
  let columns = [];
  for (let r = 0; r < Math.min(aoa.length, 10); r++) {
    const cols = (aoa[r] || [])
      .map((h, index) => ({ index, header: String(h ?? '').trim(), ...parseHeader(h) }))
      .filter((c) => c.code);
    if (cols.length >= 2) {
      headerIndex = r;
      columns = cols;
      break;
    }
  }
  if (headerIndex < 0) return { headerRow: 0, columns: [], languages: [], rows: [] };

  const languages = [...new Set(columns.map((c) => c.code))];
  const rows = [];
  for (let r = headerIndex + 1; r < aoa.length; r++) {
    const line = aoa[r] || [];
    const cells = {};
    let any = false;
    for (const col of columns) {
      const value = String(line[col.index] ?? '').trim();
      if (!value) continue;
      any = true;
      cells[col.code] ??= { original: '', revised: '', originalHeader: '', revisedHeader: '' };
      if (col.revised) {
        cells[col.code].revised = value;
        cells[col.code].revisedHeader = col.header;
      } else {
        cells[col.code].original = value;
        cells[col.code].originalHeader = col.header;
      }
    }
    if (any) rows.push({ rowNumber: r + 1, cells });
  }
  return { headerRow: headerIndex + 1, columns, languages, rows };
}

/**
 * Merges sheets from several workbooks (say a EUR and an ASIA file with different language columns, or
 * an extra shared-strings sheet) into one reference. Rows keep an `origin` { file, sheetName, rowNumber }
 * and a unique `id`. Repeats are folded per language by compareLanguage, since two files often share a
 * line but not their language columns.
 * parts: [{ sheet: parseSheet(...), file, sheetName }]
 */
export function combineSheets(parts) {
  const columns = [];
  const languages = [];
  const rows = [];
  parts.forEach((p, pi) => {
    for (const c of p.sheet.columns) {
      if (!columns.some((x) => x.code === c.code && x.revised === c.revised)) columns.push(c);
    }
    for (const code of p.sheet.languages) if (!languages.includes(code)) languages.push(code);
    for (const row of p.sheet.rows) {
      rows.push({ ...row, id: `${pi}:${row.rowNumber}`, origin: { file: p.file, sheetName: p.sheetName, rowNumber: row.rowNumber } });
    }
  });
  return { headerRow: parts[0]?.sheet.headerRow ?? 0, columns, languages, rows, sources: parts.map(({ file, sheetName }) => ({ file, sheetName })) };
}

export function hasRevisedColumn(sheet, code) {
  return sheet.columns.some((c) => c.code === code && c.revised);
}

/** The text a page should show for this row: the revised translation wins over the original. */
export function expectedFor(row, code) {
  const cell = row.cells[code];
  if (!cell) return null;
  const text = cell.revised || cell.original;
  if (!text) return null;
  return {
    text,
    source: cell.revised ? 'revised' : 'original',
    header: cell.revised ? cell.revisedHeader : cell.originalHeader,
    original: cell.original,
    originalHeader: cell.originalHeader,
  };
}

// ---------------------------------------------------------------------------
// Normalisation

/** Collapses whitespace and drops invisible characters that never change what a player sees. */
export function normalize(s, { ignoreCase = false } = {}) {
  let t = String(s ?? '')
    .normalize('NFC')
    .replace(/[   -   　]/g, ' ')
    .replace(/[​⁠﻿­]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (ignoreCase) t = t.toLocaleLowerCase();
  return t;
}

const LOOSE_MAP = {
  '‘': "'", '’': "'", '‛': "'", '′': "'", '｀': "'", '`': "'",
  '“': '"', '”': '"', '„': '"', '″': '"', '«': '"', '»': '"',
  '–': '-', '—': '-', '−': '-', '‐': '-', '‑': '-', '‚': ',',
  '…': '...', '。': '.', '、': ',',
};

/** Ignores spacing, quote style, dash style and full-width punctuation: differences a QA flags as "minor". */
export function loose(s, opts) {
  return normalize(s, opts)
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/[‘’‛′｀`“”„″«»–—−‐‑‚…。、]/g, (ch) => LOOSE_MAP[ch])
    .replace(/ /g, '');
}

// ---------------------------------------------------------------------------
// Page text extraction

const BREAK_TAGS = new Set([
  'address', 'article', 'aside', 'blockquote', 'body', 'br', 'button', 'caption', 'dd', 'div', 'dl', 'dt',
  'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header',
  'hr', 'html', 'label', 'li', 'main', 'nav', 'ol', 'option', 'p', 'pre', 'section', 'table', 'tbody', 'td',
  'tfoot', 'th', 'thead', 'title', 'tr', 'ul',
]);

const NAMED_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…', ndash: '–', mdash: '—', lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”', times: '×', copy: '©', reg: '®', trade: '™' };

function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') {
      const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : m;
    }
    return NAMED_ENTITIES[e.toLowerCase()] ?? m;
  });
}

export function looksLikeHtml(text) {
  return /<\/?(p|div|span|h[1-6]|br|li|td|table|html|body)\b[^>]*>/i.test(text);
}

/** Splits an HTML page into visible text lines: block elements, <br> and the paytables' empty <i></i> separate lines. */
export function extractSegments(html) {
  const s = String(html)
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(script|style|template|noscript)\b[\s\S]*?<\/\1\s*>/gi, ' ')
    .replace(/<i\b[^>]*>\s*<\/i\s*>/gi, '<br>');
  const segments = [];
  let buf = '';
  const flush = () => {
    const t = normalize(decodeEntities(buf));
    if (t) segments.push(t);
    buf = '';
  };
  for (const part of s.split(/(<\/?[a-zA-Z][^>]*>)/)) {
    const tag = /^<\/?([a-zA-Z][\w-]*)/.exec(part);
    if (tag) {
      if (BREAK_TAGS.has(tag[1].toLowerCase())) flush();
    } else {
      buf += part;
    }
  }
  flush();
  return segments;
}

export function textToSegments(text) {
  return String(text).split(/\r?\n/).map((l) => normalize(l)).filter(Boolean);
}

export function pageToSegments(content) {
  return looksLikeHtml(content) ? extractSegments(content) : textToSegments(content);
}

// ---------------------------------------------------------------------------
// Alignment

const graphemeSegmenter = typeof Intl !== 'undefined' && Intl.Segmenter
  ? new Intl.Segmenter(undefined, { granularity: 'grapheme' })
  : null;

/** Splits into user-perceived characters so Thai/Indic vowel marks never get separated from their consonant. */
export function graphemes(s) {
  return graphemeSegmenter ? Array.from(graphemeSegmenter.segment(s), (x) => x.segment) : Array.from(s);
}

/**
 * Edit distance between `pat` and `txt` (grapheme arrays). By default semi-global: the whole of `pat`
 * against the best-matching stretch of `txt`. With `global`, the whole of both. Returns the distance,
 * the matched range in `txt` and the edit script.
 */
export function alignInside(pat, txt, freeAt = null, global = false) {
  const m = pat.length;
  const n = txt.length;
  const W = n + 1;
  const dir = new Uint8Array((m + 1) * W); // 0 match, 1 substitute, 2 missing from txt, 3 extra in txt
  let prev = new Uint32Array(W);
  let cur = new Uint32Array(W);
  if (global && !(freeAt && freeAt.has(0))) for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    cur[0] = i;
    dir[i * W] = 2;
    const pc = pat[i - 1];
    // Extra text right after pattern position i is free where a placeholder such as [WILD] was.
    const insCost = freeAt && freeAt.has(i) ? 0 : 1;
    for (let j = 1; j <= n; j++) {
      const eq = pc === txt[j - 1];
      let best = prev[j - 1] + (eq ? 0 : 1);
      let d = eq ? 0 : 1;
      if (prev[j] + 1 < best) { best = prev[j] + 1; d = 2; }
      if (cur[j - 1] + insCost < best) { best = cur[j - 1] + insCost; d = 3; }
      cur[j] = best;
      dir[i * W + j] = d;
    }
    [prev, cur] = [cur, prev];
  }
  let end = 0;
  let dist = m === 0 ? (global ? n : 0) : Infinity;
  if (global) { end = n; if (m > 0) dist = prev[n]; }
  else if (m > 0) for (let j = 0; j <= n; j++) if (prev[j] < dist) { dist = prev[j]; end = j; }

  // Steps refer to positions: ['=', i, j] match, ['~', i, j] substitute, ['-', i] missing, ['+', j] extra.
  const steps = [];
  let i = m;
  let j = end;
  while (i > 0) {
    const d = dir[i * W + j];
    if (d === 0) { steps.push(['=', i - 1, j - 1]); i--; j--; }
    else if (d === 1) { steps.push(['~', i - 1, j - 1]); i--; j--; }
    else if (d === 2) { steps.push(['-', i - 1]); i--; }
    else { steps.push(['+', i, j - 1]); j--; } // i = how much of the pattern came before the extra text
  }
  if (global) while (j > 0) { steps.push(['+', 0, j - 1]); j--; }
  steps.reverse();
  const ops = [];
  for (const [t, a, b] of steps) {
    if (t === '=') ops.push(['=', pat[a]]);
    else if (t === '~') ops.push(['-', pat[a]], ['+', txt[b]]);
    else if (t === '-') ops.push(['-', pat[a]]);
    else ops.push(['+', txt[b]]);
  }
  return { dist, start: j, end, steps, ops: mergeOps(ops) };
}

// Characters OCR mixes up in game lettering ("MUItiplier", "1O", Cyrillic "Х" for "X"); both sides are
// folded the same way, so this never hides a difference between two real texts in the same script.
const OCR_CONFUSABLE = {
  l: 'i', '|': 'i', 1: 'i', 0: 'o',
  а: 'a', е: 'e', о: 'o', р: 'p', с: 'c', у: 'y', х: 'x', к: 'k', м: 'm', т: 't', н: 'h', в: 'b', і: 'i', ј: 'j', ѕ: 's',
};

/**
 * A comparison key for each user-perceived character plus where it sits in the original text.
 * `fold` (for image text) ignores case, spacing, quote/dash style, full-width punctuation and OCR
 * look-alikes; without it the characters are kept as written (lower-cased with `ignoreCase`).
 * With `holes`, placeholders like [WILD] are left out of the key and their positions noted in `free`.
 */
export function keyed(s, { holes = false, fold = true, ignoreCase = false } = {}) {
  const text = normalize(s);
  const g = graphemes(text);
  const inHole = new Set();
  if (holes) {
    const starts = [];
    let pos = 0;
    for (const ch of g) { starts.push(pos); pos += ch.length; }
    for (const m of text.matchAll(/\[[^\]\n]{1,30}\]/g)) {
      starts.forEach((p, i) => { if (p >= m.index && p < m.index + m[0].length) inHole.add(i); });
    }
  }
  const key = [];
  const map = [];
  const free = new Set();
  g.forEach((ch, i) => {
    if (inHole.has(i)) { free.add(key.length); return; }
    if (!fold) { key.push(ignoreCase ? ch.toLocaleLowerCase() : ch); map.push(i); return; }
    const k = loose(ch, { ignoreCase: true });
    if (k) { key.push(OCR_CONFUSABLE[k] ?? k); map.push(i); }
  });
  return { g, key, map, free };
}

const NO_SPACE_CHAR = /[฀-໿က-႟ក-៿぀-ヿ㐀-鿿＀-￯]/;

/** Joins text broken over lines: no space inside Thai/Japanese/Chinese ("ทั้ง\nหมด"), a space elsewhere. */
export function joinBrokenLines(s) {
  return String(s).replace(/[ \t]*\r?\n[ \t]*/g, (m, at, all) => (
    NO_SPACE_CHAR.test(all[at - 1] || '') && NO_SPACE_CHAR.test(all[at + m.length] || '') ? '' : ' '));
}

/** Aligns two keyed texts (whole to whole with `global`) and marks the differences on the original characters. */
export function keyedAlign(P, T, { global = false } = {}) {
  const a = alignInside(P.key, T.key, P.free?.size ? P.free : null, global);
  const pState = P.g.map(() => '=');
  const tState = T.g.map(() => '=');
  let freeIns = 0; // characters that stood in for placeholders
  for (const [t, i, j] of a.steps) {
    if (t === '~' || t === '-') pState[P.map[i]] = '-';
    if (t === '+' && P.free?.has(i)) freeIns++;
    else if (t === '~' || t === '+') tState[T.map[j]] = '+';
  }
  const from = a.start < T.map.length ? T.map[a.start] : T.g.length;
  const to = a.end > 0 ? T.map[a.end - 1] + 1 : from;
  const runs = (g, st, x, y) => mergeOps(g.slice(x, y).map((ch, k) => [st[x + k], ch]));
  return {
    dist: a.dist,
    freeIns,
    start: a.start,
    end: a.end,
    similarity: P.key.length ? 1 - a.dist / P.key.length : 0,
    expectedOps: runs(P.g, pState, 0, P.g.length),
    page: { before: T.g.slice(0, from).join(''), ops: runs(T.g, tState, from, to), after: T.g.slice(to).join('') },
  };
}

/** Groups an edit script into runs; every changed stretch becomes one "-" run followed by one "+" run. */
function mergeOps(ops) {
  const out = [];
  let del = '';
  let ins = '';
  const flush = () => {
    if (del) out.push(['-', del]);
    if (ins) out.push(['+', ins]);
    del = ins = '';
  };
  for (const [t, ch] of ops) {
    if (t === '=') {
      flush();
      const last = out[out.length - 1];
      if (last && last[0] === '=') last[1] += ch;
      else out.push(['=', ch]);
    } else if (t === '-') del += ch;
    else ins += ch;
  }
  flush();
  return out;
}

// ---------------------------------------------------------------------------
// Hints

const NO_SPACE_SCRIPT = /[฀-໿က-႟ក-៿]/; // Thai, Lao, Myanmar, Khmer

/** Finds words typed twice in a row, e.g. "the the" or Thai "จะจะ". */
export function findRepeats(text, code) {
  if (typeof Intl === 'undefined' || !Intl.Segmenter) return [];
  const locale = code ? code.replace('_', '-') : undefined;
  let seg;
  try {
    seg = new Intl.Segmenter(locale, { granularity: 'word' });
  } catch {
    seg = new Intl.Segmenter(undefined, { granularity: 'word' });
  }
  const found = new Set();
  let last = null;
  let gapOk = true;
  for (const { segment, isWordLike } of seg.segment(String(text))) {
    if (!isWordLike) {
      if (!/^\s+$/.test(segment)) last = null;
      gapOk = true;
      continue;
    }
    const isNumber = /^[\d.,]+$/.test(segment);
    if (!isNumber && last && gapOk && segment.toLocaleLowerCase() === last.toLocaleLowerCase()) found.add(`${last}${segment}`);
    if (!isNumber && NO_SPACE_SCRIPT.test(segment)) {
      const g = graphemes(segment);
      if (g.length >= 2 && g.length % 2 === 0) {
        const half = g.length / 2;
        if (g.slice(0, half).join('') === g.slice(half).join('')) found.add(segment);
      }
    }
    last = isNumber ? null : segment;
  }
  return [...found];
}

// ---------------------------------------------------------------------------
// Comparison

export const STATUS = {
  match: { label: '相符', tone: 'ok' },
  outdated: { label: '仍為舊翻譯', tone: 'bad' },
  minor: { label: '空白/標點差異', tone: 'warn' },
  diff: { label: '文字不符', tone: 'bad' },
  check: { label: '需看圖確認', tone: 'warn' },
  missing: { label: '找不到', tone: 'muted' },
  covered: { label: '已由其他檔案比對', tone: 'muted' },
};

/** "2.000", "2 000" and "2,000" all become "2000". */
function stripThousands(s) {
  return s.replace(/(\d)[\s.,'’]?(?=\d{3}(?!\d))/g, '$1');
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** "[WILD] climbs up" as a whole-line pattern where the bracketed token stands for any short text. */
function placeholderPattern(target) {
  const parts = target.split(/\[[^\]\n]{1,30}\]/);
  if (parts.length < 2 || !parts.join('').trim()) return null;
  return new RegExp(`^${parts.map(escapeRegExp).join('[^\\n]{0,40}?')}$`);
}

const RANK = { none: -1, missing: 0, diff: 1, check: 1.5, minor: 2, outdated: 3, match: 4 };
// Statuses a two-line or two-row join may have: a join is only believable when the text agrees.
const EXACTISH = new Set(['match', 'minor', 'outdated']);
// Scripts whose sentences follow each other without a space.
const JOIN_WITHOUT_SPACE = new Set(['ja_JP', 'zh_CN']);
// Image text must be mostly the Excel sentence; a little icon noise around it is fine.
const OCR_MIN_COVER = 0.85;
// Characters a placeholder like [WILD] may stand for: a symbol name, not a sentence.
const HOLE_ROOM = 12;

/**
 * Compares the game's lines with the Excel, whole sentence against whole sentence: a line matches an
 * Excel row when the two are the same text (or nearly, for a typo), never because one contains the other.
 * Two joins are allowed when the text agrees exactly: one game line holding two consecutive Excel rows,
 * and one Excel row split over two consecutive game lines.
 *
 * `lines` are strings or { text, ocr, alts, source } objects; OCR lines (text read from images) are
 * compared ignoring case and spacing. `priority` lists Excel file names, main file first: a line takes its
 * answer from the first file that has one, and only looks further when that file has none.
 *
 * Returns per-row results (`results`), per-line results (`lineResults`), the row ids answering each line
 * (`lineRows`) and the lines no row accounts for (`extraLines`).
 */
export function compareLanguage(sheet, code, lines, options = {}) {
  const { threshold = 0.6, ignoreCase = false, placeholders = true, priority = [] } = options;
  const norm = (s) => normalize(s, { ignoreCase });
  const joiner = JOIN_WITHOUT_SPACE.has(code) ? '' : ' ';

  // --- Game side: every line, and each pair of neighbouring lines from the same page.
  const items = lines.map((l, index) => {
    const o = typeof l === 'string' ? { text: l } : l;
    return { index, text: normalize(o.text), alts: (o.alts || []).map((a) => normalize(a)).filter(Boolean), ocr: Boolean(o.ocr), source: o.source ?? null };
  }).filter((l) => l.text);
  const units = [];
  items.forEach((l, k) => {
    units.push({ lines: [l.index], text: l.text, alts: l.alts, ocr: l.ocr, pair: false });
    const next = items[k + 1];
    if (!l.ocr && next && !next.ocr && next.index === l.index + 1 && next.source === l.source) {
      // A sentence broken over two lines continues without a space in Thai/Japanese/Chinese.
      units.push({ lines: [l.index, next.index], text: joinBrokenLines(`${l.text}\n${next.text}`), alts: [], ocr: false, pair: true });
    }
  });

  // --- Excel side: one entry per row (the same line + translation in two files counts once), plus
  // each pair of consecutive rows of a sheet.
  const tierOf = (row) => {
    if (!row.origin) return 0;
    const t = priority.indexOf(row.origin.file);
    return t < 0 ? priority.length : t;
  };
  const byEnglish = new Map();
  for (const row of sheet.rows) {
    const en = normalize(row.cells.en_US?.revised || row.cells.en_US?.original || '', { ignoreCase: true });
    const exp = expectedFor(row, code);
    if (!en || !exp || !row.origin) continue;
    if (!byEnglish.has(en)) byEnglish.set(en, []);
    byEnglish.get(en).push({ row, text: normalize(exp.text) });
  }
  const results = [];
  const entries = [];
  const firstOf = new Map();
  for (const row of sheet.rows) {
    const exp = expectedFor(row, code);
    if (!exp) continue;
    const english = row.cells.en_US?.revised || row.cells.en_US?.original || '';
    const repeatKey = `${normalize(english, { ignoreCase: true })}\u0000${normalize(exp.text)}`;
    if (row.origin && firstOf.has(repeatKey)) { firstOf.get(repeatKey).alsoIn.push(row.origin); continue; }
    const r = {
      id: row.id ?? row.rowNumber,
      origin: row.origin || null,
      alsoIn: [],
      rowNumber: row.rowNumber,
      tier: tierOf(row),
      english,
      expected: exp.text,
      source: exp.source,
      header: exp.header,
      original: exp.original,
      originalHeader: exp.originalHeader,
      status: 'missing',
      similarity: 0,
      expectedOps: [['=', exp.text]],
      page: null,
      notes: [],
    };
    for (const rep of findRepeats(exp.text, code)) r.notes.push(`Excel 內疑似重複字：「${rep}」`);
    for (const other of byEnglish.get(normalize(english, { ignoreCase: true })) || []) {
      if (other.row === row || other.row.origin.file === row.origin?.file || other.text === normalize(exp.text)) continue;
      r.notes.push(`另一份 Excel 的翻譯不同：「${other.text}」（${other.row.origin.file} 第 ${other.row.rowNumber} 列）`);
    }
    if (row.origin) firstOf.set(repeatKey, r);
    results.push(r);
    entries.push({
      rows: [r], text: exp.text, original: exp.source === 'revised' ? exp.original : '', tier: r.tier, pair: false,
      sheetOf: row.origin ? `${row.origin.file}\u0000${row.origin.sheetName}` : '', rowNumber: row.rowNumber, weak: null,
    });
  }
  const pairEntries = [];
  for (let k = 0; k + 1 < entries.length; k++) {
    const a = entries[k];
    const b = entries[k + 1];
    if (a.sheetOf !== b.sheetOf || b.rowNumber !== a.rowNumber + 1) continue;
    pairEntries.push({ rows: [a.rows[0], b.rows[0]], text: `${a.text}${joiner}${b.text}`, original: '', tier: Math.max(a.tier, b.tier), pair: true });
  }

  // --- Scoring one game text against one Excel text.
  const keys = new Map();
  const K = (text, fold, holes) => {
    const id = `${fold ? 1 : 0}${holes ? 1 : 0}\u0000${text}`;
    if (!keys.has(id)) keys.set(id, keyed(text, { fold, holes, ignoreCase }));
    return keys.get(id);
  };
  const patterns = new Map();
  const patternOf = (text) => {
    if (!patterns.has(text)) patterns.set(text, placeholders ? placeholderPattern(norm(text)) : null);
    return patterns.get(text);
  };
  const plain = (expected, text) => ({ expectedOps: [['=', expected]], page: { before: '', ops: [['=', text]], after: '' } });

  const scoreText = (t, entry) => {
    const a = norm(entry.text);
    const b = norm(t);
    if (a === b || patternOf(entry.text)?.test(b)) return { status: 'match', similarity: 1, align: plain(entry.text, t) };
    const P = K(entry.text, false, placeholders);
    const T = K(t, false, false);
    const lp = P.key.length;
    const lt = T.key.length;
    if (!lp || !lt) return null;
    // Room a placeholder may take (a symbol name such as "WILD"); beyond it, extra text counts as different.
    const room = P.free.size * HOLE_ROOM;
    const sim = (al) => {
      const absorbed = Math.min(al.freeIns, room);
      return 1 - (al.dist + al.freeIns - absorbed) / Math.max(lp, lt - absorbed);
    };
    let whole = null;
    const near = () => (whole ??= keyedAlign(P, T, { global: true }));
    if (entry.original && norm(entry.original) === b) return { status: 'outdated', similarity: sim(near()), align: near() };
    if (loose(entry.text, { ignoreCase }) === loose(t, { ignoreCase })) return { status: 'minor', similarity: sim(near()), align: near() };
    // Lines far apart in length cannot be the same sentence.
    if (Math.abs(lp - Math.max(lp, lt - room)) / Math.max(lp, lt - room) > 1 - Math.min(threshold, 0.4) && lt > lp) return null;
    if (Math.abs(lp - lt) / Math.max(lp, lt) > 1 - Math.min(threshold, 0.4) && lt <= lp) return null;
    // "Tier 1" against "Tier 2" is a different label, not a typo.
    if (lt <= 16 && a.replace(/\d+/g, '#') === b.replace(/\d+/g, '#')) return null;
    const similarity = sim(near());
    return { status: similarity >= threshold ? 'diff' : 'none', similarity, align: near() };
  };

  const scoreImage = (t, entry) => {
    const P = K(entry.text, true, placeholders);
    const T = K(t, true, false);
    if (!P.key.length || !T.key.length) return null;
    const room = P.free.size * HOLE_ROOM;
    // The Excel sentence (with at most a symbol name per placeholder) must fill most of the image reading.
    const fills = (al) => (al.end - al.start - Math.min(al.freeIns ?? 0, room)) / T.key.length >= OCR_MIN_COVER && (al.freeIns ?? 0) <= room;
    const al = keyedAlign(P, T);
    if (al.dist === 0 && fills(al)) return { status: 'match', similarity: 1, align: { expectedOps: [['=', entry.text]], page: al.page } };
    if (entry.original) {
      const O = K(entry.original, true, placeholders);
      const old = keyedAlign(O, T);
      if (old.dist === 0 && fills(old)) return { status: 'outdated', similarity: al.similarity, align: al };
    }
    if (!fills(al)) return { status: 'none', similarity: al.similarity * 0.5, align: al };
    // OCR slips ("Came" for "Game", Korean 달/탈) look just like typos, so an image near miss is never
    // called an error outright: someone has to look at the picture.
    return { status: al.similarity >= threshold ? 'check' : 'none', similarity: al.similarity, align: al };
  };

  const score = (unit, entry) => {
    let best = null;
    [unit.text, ...unit.alts].forEach((t, vi) => {
      const s = unit.ocr ? scoreImage(t, entry) : scoreText(t, entry);
      if (s && (!best || RANK[s.status] > RANK[best.status] || (RANK[s.status] === RANK[best.status] && s.similarity > best.similarity))) {
        best = { ...s, alt: vi > 0 };
      }
    });
    return best;
  };

  // Main file first; within a file the closer result wins.
  const better = (a, b) => {
    if (!b) return true;
    if (a.entry.tier !== b.entry.tier) return a.entry.tier < b.entry.tier;
    if (RANK[a.status] !== RANK[b.status]) return RANK[a.status] > RANK[b.status];
    return a.similarity > b.similarity;
  };

  for (const unit of units) {
    unit.pick = null;
    for (const entry of [...entries, ...pairEntries]) {
      if (unit.pair && entry.pair) continue;
      const s = score(unit, entry);
      if (!s) continue;
      if (s.status === 'none') {
        if (!unit.pair && !entry.pair && s.similarity >= 0.4 && (!entry.weak || s.similarity > entry.weak.similarity)) entry.weak = { unit, ...s };
        continue;
      }
      if ((unit.pair || entry.pair) && !EXACTISH.has(s.status)) continue;
      const cand = { unit, entry, ...s };
      if (better(cand, unit.pick)) unit.pick = cand;
    }
  }

  // --- Each line takes its own result, or that of the two-line join it belongs to when that is better.
  const lineResults = lines.map(() => null);
  const lineRows = lines.map(() => []);
  for (const item of items) {
    let chosen = units.find((u) => !u.pair && u.lines[0] === item.index)?.pick || null;
    for (const u of units) if (u.pair && u.pick && u.lines.includes(item.index) && better(u.pick, chosen)) chosen = u.pick;
    if (!chosen) continue;
    const { unit, entry } = chosen;
    const notes = [];
    if (unit.pair) notes.push('這句在遊戲裡分成兩行');
    if (entry.pair) notes.push(`Excel 第 ${entry.rows.map((r) => r.rowNumber).join('、')} 列在遊戲裡是同一行`);
    if (chosen.status === 'minor') notes.push('文字相同，只有空白、引號或全形/半形標點不同');
    if (chosen.status === 'outdated') notes.push(`遊戲裡是「${entry.rows[0].originalHeader}」欄的舊翻譯，應更新為「${entry.rows[0].header}」`);
    if (chosen.status === 'check') notes.push('OCR 讀到的字和 Excel 不完全一樣，可能是辨識誤差；請看縮圖，若圖片正確可直接修正辨識文字');
    if (chosen.alt) notes.push('採用這張圖的另一種 OCR 辨識結果');
    if (chosen.status === 'diff' && stripThousands(norm(entry.text)) === stripThousands(norm(item.text))) notes.push('只有數字的千分位符號不同（例如 2.000 / 2 000 / 2,000）');
    const own = !unit.pair; // a two-line join is shown line by line without marks
    lineResults[item.index] = {
      status: chosen.status,
      similarity: chosen.similarity,
      rowIds: entry.rows.map((r) => r.id),
      expectedOps: own && !entry.pair ? chosen.align.expectedOps : null,
      page: own ? chosen.align.page : { before: '', ops: [['=', item.text]], after: '' },
      ocr: item.ocr,
      notes,
    };
    lineRows[item.index] = entry.rows.map((r) => r.id);
  }

  // --- Each Excel row: the best line that took it as its answer.
  const rowById = new Map(results.map((r) => [r.id, r]));
  for (const item of items) {
    const lr = lineResults[item.index];
    if (!lr) continue;
    for (const id of lr.rowIds) {
      const r = rowById.get(id);
      if (r.status !== 'missing' && (RANK[r.status] > RANK[lr.status] || (RANK[r.status] === RANK[lr.status] && r.similarity >= lr.similarity))) continue;
      r.status = lr.status;
      r.similarity = lr.similarity;
      r.page = { ...lr.page, line: item.index, ocr: item.ocr };
      r.expectedOps = lr.expectedOps || [['=', r.expected]];
      r.lineNotes = lr.notes;
    }
  }
  const answeredEnglish = new Map();
  for (const r of results) if (r.status !== 'missing' && r.english) answeredEnglish.set(normalize(r.english, { ignoreCase: true }), r);
  for (const r of results) {
    if (r.lineNotes) { r.notes.push(...r.lineNotes.filter((n) => !r.notes.includes(n))); delete r.lineNotes; }
    if (r.status !== 'missing') continue;
    // A row whose English line was answered from another file is not missing from the game.
    const by = r.english && answeredEnglish.get(normalize(r.english, { ignoreCase: true }));
    if (by && by !== r) {
      r.status = 'covered';
      r.notes.push(`遊戲這句已用「${by.origin?.file || ''}」第 ${by.rowNumber} 列比對`);
      continue;
    }
    const weak = entries.find((e) => e.rows[0] === r)?.weak;
    if (weak) {
      const line = weak.unit.lines[0];
      r.similarity = weak.similarity;
      r.page = { ...weak.align.page, line, ocr: weak.unit.ocr, weak: true };
    }
  }

  const extraByText = new Map();
  for (const item of items) {
    if (lineResults[item.index] || (item.text.match(/\p{L}/gu) || []).length < 2) continue;
    const key = `${item.ocr ? 'i' : 't'}:${item.text}`;
    const seen = extraByText.get(key);
    if (seen) { seen.count++; seen.lines.push(item.index); }
    else extraByText.set(key, { text: item.text, count: 1, lines: [item.index], ocr: item.ocr, repeats: item.ocr ? [] : findRepeats(item.text, code) });
  }

  const counts = Object.fromEntries(Object.keys(STATUS).map((k) => [k, 0]));
  for (const r of results) counts[r.status]++;
  return {
    code,
    results,
    lineResults,
    lineRows,
    lineCoverage: lineResults.map((lr) => (lr ? 1 : 0)),
    extraLines: [...extraByText.values()],
    counts,
    segmentCount: items.length,
  };
}

export function detectLanguageFromUrl(url) {
  const m = /[_\-/.]([a-z]{2}_[A-Z]{2})(?=[._\-/]|$)/.exec(String(url));
  return m && LANG_BY_CODE.has(m[1]) ? m[1] : null;
}

/** Loosely matches a sheet name like "FS - Tree of Wild" to a game name or folder. */
export function sheetKey(name) {
  return String(name ?? '').replace(/^\s*fs\s*[-–:]\s*/i, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}
