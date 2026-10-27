import {
  LANGUAGES, STATUS, languageName, parseSheet, combineSheets, compareLanguage, pageToSegments, detectLanguageFromUrl, sheetKey,
} from './compare.js';
import {
  fetchText, loadGameList, resolveGamePages, pageKindName, collectGameStrings, SOURCE_KINDS,
} from './game-source.js';

const $ = (id) => document.getElementById(id);
const EMPTY_SHEET = { headerRow: 0, columns: [], languages: [], rows: [] };

// What the game tab can collect: everything, everything but OCR, one page type, or only image lettering.
const CONTENT_ALL = '*all';
const CONTENT_NO_OCR = '*text';
const CONTENT_IMAGES = '*images';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const DEFAULT_LIST_URL = 'https://qa-go.object1.net/touch/fsnew/list.jsp';

const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(`fsml.${key}`);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try { localStorage.setItem(`fsml.${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

const state = {
  workbooks: [], // { id, name, sheets: [{ name, parsed }] }
  primaryId: 0, // the main Excel file: game lines look here first, then in the others
  primaryName: '',
  groupKey: '', // which game's sheets are in use (sheets are grouped by name across files)
  picked: new Set(), // `${workbook id}|${sheet name}` of the sheets in use
  sheetName: '',
  sheet: null,
  tab: 'game',
  gameList: null,
  gamePages: new Map(),
  currentGame: null,
  pageFiles: [],
  runs: [],
  selected: 0,
  filter: 'all',
  view: 'game', // 'game': every game string answered by the Excel; 'excel': every Excel row and where the game has it
  gameFilter: 'all',
};

// ---------------------------------------------------------------------------
// Step 1: reference workbooks

let nextWorkbookId = 1;
const pickId = (wb, sheetName) => `${wb.id}|${sheetName}`;
const gameLabel = (sheetName) => sheetName.replace(/^\s*fs\s*[-–:]\s*/i, '').trim() || sheetName;

async function addWorkbooks(files) {
  const added = [];
  for (const file of files) {
    try {
      const wb = XLSX.read(await file.arrayBuffer(), { type: 'array' });
      const sheets = wb.SheetNames.map((name) => ({
        name,
        parsed: parseSheet(XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: '', raw: false })),
      }));
      // Two files may share a name (say from two folders); both are kept and told apart by a number.
      let name = file.name;
      for (let n = 2; state.workbooks.some((w) => w.name === name); n++) name = file.name.replace(/(\.[^.]+)?$/, ` (${n})$1`);
      const entry = { id: nextWorkbookId++, name, sheets };
      state.workbooks.push(entry);
      added.push(entry);
    } catch (err) {
      alert(`無法讀取「${file.name}」：${err.message}`);
    }
  }
  if (!state.groupKey) state.groupKey = sheetGroups()[0]?.key || '';
  for (const wb of added) for (const id of autoPicks(wb, state.groupKey)) state.picked.add(id);
  if (!state.workbooks.some((w) => w.id === state.primaryId)) state.primaryId = state.workbooks[0]?.id || 0;
  renderReference();
}

/**
 * The sheets of a file to use for a game: those named after the game, or, when the file has no such
 * sheet but only one usable sheet (a per-game file), that one. Every uploaded file takes part this way.
 */
function autoPicks(wb, key) {
  const usable = wb.sheets.filter((s) => s.parsed.rows.length);
  const named = usable.filter((s) => (sheetKey(s.name) || s.name) === key);
  return (named.length ? named : usable.length === 1 ? usable : []).map((s) => pickId(wb, s.name));
}

/** What one file contributes right now, shown next to its name. */
function workbookUse(wb) {
  const usable = wb.sheets.filter((s) => s.parsed.rows.length);
  const used = wb.sheets.filter((s) => state.picked.has(pickId(wb, s.name)));
  if (!usable.length) return { tone: 'bad', text: '找不到語言標題列，未使用' };
  if (!used.length) return { tone: 'warn', text: `未使用：沒有「${state.sheetName}」分頁，請在下方勾選` };
  return { tone: 'ok', text: `使用 ${used.map((s) => `「${s.name}」`).join('')} · ${used.reduce((n, s) => n + s.parsed.rows.length, 0)} 筆` };
}

function removeWorkbook(id) {
  const wb = state.workbooks.find((w) => w.id === id);
  if (!wb) return;
  for (const s of wb.sheets) state.picked.delete(pickId(wb, s.name));
  state.workbooks = state.workbooks.filter((w) => w.id !== id);
  if (state.primaryId === id) state.primaryId = state.workbooks[0]?.id || 0;
  renderReference();
}

/** Sheets with a language header row, grouped by game across all files ("FS - Tree of Wild" = "Tree of Wild"). */
function sheetGroups() {
  const groups = new Map();
  for (const wb of state.workbooks) {
    for (const s of wb.sheets) {
      if (!s.parsed.rows.length) continue;
      const key = sheetKey(s.name) || s.name;
      if (!groups.has(key)) groups.set(key, { key, label: gameLabel(s.name), sheets: [] });
      groups.get(key).sheets.push({ wb, s });
    }
  }
  return [...groups.values()];
}

function selectGroup(key) {
  state.groupKey = key;
  state.picked = new Set(state.workbooks.flatMap((wb) => autoPicks(wb, key)));
  renderReference();
}

function renderSheetPicks(groups) {
  const current = groups.find((g) => g.key === state.groupKey);
  const item = ({ wb, s }) => `
    <label class="pick">
      <input type="checkbox" data-pick="${esc(pickId(wb, s.name))}"${state.picked.has(pickId(wb, s.name)) ? ' checked' : ''}>
      <span class="pick-name"><span class="pick-file">${esc(wb.name)}</span> › ${esc(s.name)}</span>
      <span class="pick-n">${s.parsed.rows.length} 筆</span>
    </label>`;
  // Sheets in use come first (named after the game or not); the rest can be added from the fold.
  const others = groups.filter((g) => g !== current).flatMap((g) => g.sheets);
  const inUse = [...(current?.sheets || []), ...others.filter(({ wb, s }) => state.picked.has(pickId(wb, s.name)))];
  const rest = others.filter(({ wb, s }) => !state.picked.has(pickId(wb, s.name)));
  $('sheetPicks').innerHTML = `
    ${inUse.map(item).join('')}
    ${rest.length ? `<details class="pick-more"><summary>加入其他分頁（${rest.length}）</summary>${rest.map(item).join('')}</details>` : ''}`;
}

function renderReference() {
  const has = state.workbooks.length > 0;
  $('refDrop').hidden = has;
  $('refInfo').hidden = !has;
  const groups = sheetGroups();
  if (!groups.some((g) => g.key === state.groupKey)) {
    state.groupKey = groups[0]?.key || '';
    state.picked = new Set(state.workbooks.flatMap((wb) => autoPicks(wb, state.groupKey)));
  }
  state.sheetName = groups.find((g) => g.key === state.groupKey)?.label || '';
  state.primaryName = state.workbooks.find((w) => w.id === state.primaryId)?.name || '';
  const several = state.workbooks.length > 1;
  $('refFiles').innerHTML = state.workbooks.map((w) => {
    const use = workbookUse(w);
    const primary = w.id === state.primaryId;
    return `
    <li class="file-line${several && primary ? ' primary' : ''}">
      ${several ? `<label class="primary-pick" title="遊戲字句先用這個檔案比對，找不到才用其他檔案">
        <input type="radio" name="primaryWb" value="${w.id}"${primary ? ' checked' : ''}>主要</label>` : ''}
      <span class="file-icon" aria-hidden="true">XLSX</span>
      <span class="file-text">
        <span class="file-name" title="${esc(w.name)}">${esc(w.name)}</span>
        <span class="file-use ${use.tone}">${esc(use.text)}${several && !primary ? ' · 主要檔案找不到時使用' : ''}</span>
      </span>
      <span class="meta">${w.sheets.length} 個分頁</span>
      <button type="button" class="link" data-remove-wb="${w.id}">移除</button>
    </li>`;
  }).join('');
  $('sheetSelect').innerHTML = groups.length
    ? groups.map((g) => {
      const n = g.key === state.groupKey ? state.picked.size : g.sheets.length;
      return `<option value="${esc(g.key)}"${g.key === state.groupKey ? ' selected' : ''}>${esc(g.label)}${n > 1 ? `（${n} 個分頁）` : ''}</option>`;
    }).join('')
    : '<option value="">沒有可用的分頁</option>';
  $('sheetSelect').disabled = !groups.length;
  renderSheetPicks(groups);

  const parts = [];
  for (const wb of state.workbooks) {
    for (const s of wb.sheets) if (state.picked.has(pickId(wb, s.name))) parts.push({ sheet: s.parsed, file: wb.name, sheetName: s.name });
  }
  state.sheet = parts.length ? combineSheets(parts) : null;
  const { sheet } = state;
  if (!groups.length) {
    $('sheetMeta').textContent = '這些檔案的分頁都找不到語言標題列（例如 EN、Thai、Japan）。';
    $('langChips').innerHTML = '';
  } else if (!sheet) {
    $('sheetMeta').textContent = '請至少勾選一個分頁。';
    $('langChips').innerHTML = '';
  } else {
    $('sheetMeta').textContent = parts.length > 1
      ? `${sheet.rows.length} 列，來自 ${parts.length} 個分頁（同一句同翻譯只比對一次）· ${sheet.languages.length} 種語言`
      : `${sheet.rows.length} 筆字串 · 標題在第 ${sheet.headerRow} 列 · ${sheet.languages.length} 種語言`;
    $('langChips').innerHTML = sheet.languages.map((code) => {
      const rev = sheet.columns.find((c) => c.code === code && c.revised);
      return `<span class="chip">${esc(languageName(code))}<code>${code}</code>${rev ? `<span class="rev" title="以「${esc(rev.header)}」欄優先">含修訂</span>` : ''}</span>`;
    }).join('');
  }
  autoPickGame();
  refreshLanguageSelects();
  updateRunButton();
  recompute(); // results already on screen follow the new reference
}

/**
 * Short names that tell files apart: the part after what all their names share, so
 * "FS - Version 137-EUR.xlsx" and "FS - Version 137-ASIA.xlsx" become "EUR" and "ASIA".
 */
function fileTags(files) {
  const bases = [...files].map((f) => [f, f.replace(/\.[^.]+$/, '')]);
  let prefix = bases[0]?.[1] || '';
  for (const [, b] of bases) while (prefix && !b.startsWith(prefix)) prefix = prefix.slice(0, -1);
  if (bases.length < 2) prefix = '';
  return new Map(bases.map(([f, b]) => {
    const rest = b.slice(prefix.length).replace(/^[\s_\-–.]+/, '') || b;
    return [f, rest.length > 20 ? `${rest.slice(0, 18)}…` : rest];
  }));
}

/** Where a result row comes from, shown only when more than one sheet is in use. */
function rowTag(r) {
  const sources = state.sheet?.sources || [];
  if (!r.origin || sources.length < 2) return '';
  const files = new Set(sources.map((s) => s.file));
  const file = fileTags(files).get(r.origin.file);
  // The sheet is named when its file brings more than one sheet, so "row 2" is never ambiguous.
  const sheetsOfFile = sources.filter((s) => s.file === r.origin.file).length;
  if (files.size > 1) return sheetsOfFile > 1 ? `${file} › ${gameLabel(r.origin.sheetName)}` : file;
  return gameLabel(r.origin.sheetName);
}

const rowLabel = (r) => (rowTag(r) ? `${rowTag(r)} · ${r.rowNumber}` : String(r.rowNumber));

// ---------------------------------------------------------------------------
// Step 2: what to compare against

function langOptions(codes, selected) {
  return codes.map((c) => `<option value="${c}"${c === selected ? ' selected' : ''}>${esc(languageName(c))}（${c}）</option>`).join('');
}

function excelLanguages() {
  return state.sheet?.languages.length ? state.sheet.languages : LANGUAGES.map((l) => l.code);
}

function refreshLanguageSelects() {
  const codes = excelLanguages();
  const pick = (el) => {
    const prev = el.value;
    const fallback = codes.find((c) => c !== 'en_US') || codes[0];
    el.innerHTML = langOptions(codes, codes.includes(prev) ? prev : fallback);
  };
  pick($('urlLang'));
  pick($('textLang'));
  renderFileList();
  if (state.currentGame) fillGameLanguages();
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tabs [role="tab"]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  document.querySelectorAll('.tabpanel').forEach((p) => { p.hidden = p.dataset.panel !== tab; });
  updateRunButton();
}

function gameStatus(msg, tone = '') {
  const el = $('gameStatus');
  el.className = `status ${tone}`;
  el.innerHTML = msg;
}

async function loadList() {
  const url = $('listUrl').value.trim() || DEFAULT_LIST_URL;
  store.set('listUrl', url);
  $('loadList').disabled = true;
  gameStatus('<span class="spinner"></span> 載入遊戲清單…');
  try {
    state.gameList = await loadGameList(url);
    state.gamePages.clear();
    const sel = $('gameSelect');
    sel.innerHTML = '<option value="">— 選擇遊戲 —</option>' + state.gameList.games
      .map((g) => `<option value="${esc(g.code)}">${esc(g.name)}（${esc(g.code)}）· ${esc(g.version || '—')}</option>`).join('');
    sel.disabled = false;
    gameStatus(`共 ${state.gameList.games.length} 款遊戲`, 'ok');
    if (!autoPickGame()) {
      const last = store.get('game', '');
      if (last && state.gameList.games.some((g) => g.code === last)) {
        sel.value = last;
        await selectGame(last);
      }
    }
  } catch (err) {
    gameStatus(esc(`載入失敗：${err.message}`), 'error');
  } finally {
    $('loadList').disabled = false;
  }
}

/** Picks the game whose name matches the selected sheet, e.g. "FS - Tree of Wild" → Tree of Wild. */
function autoPickGame() {
  if (!state.gameList || !state.sheetName) return false;
  const key = sheetKey(state.sheetName);
  const game = state.gameList.games.find((g) => sheetKey(g.name) === key || g.folder === key);
  if (!game || state.currentGame?.code === game.code) return Boolean(game);
  $('gameSelect').value = game.code;
  selectGame(game.code);
  return true;
}

async function selectGame(code) {
  const game = state.gameList?.games.find((g) => g.code === code);
  state.currentGame = null;
  $('pageKind').disabled = $('gameLang').disabled = true;
  $('pageKind').innerHTML = $('gameLang').innerHTML = '';
  updateRunButton();
  if (!game) return gameStatus('');
  store.set('game', code);
  gameStatus('<span class="spinner"></span> 尋找各語言頁面…');
  try {
    let resolved = state.gamePages.get(code);
    if (!resolved) {
      resolved = await resolveGamePages(state.gameList.base, game, (m) => gameStatus(`<span class="spinner"></span> ${esc(m)}`));
      state.gamePages.set(code, resolved);
    }
    if ($('gameSelect').value !== code) return; // another game was picked meanwhile
    const kinds = Object.keys(resolved.pages).sort((a, b) => (a === 'paytable' ? -1 : b === 'paytable' ? 1 : a.localeCompare(b)));
    const hasImages = Object.keys(resolved.images).length > 0;
    if (!kinds.length && !hasImages) throw new Error('遊戲程式裡找不到任何語言資源（paytable_xx_XX.html 或圖片）');
    state.currentGame = { game, ...resolved };
    const prev = store.get('content', CONTENT_ALL);
    $('pageKind').innerHTML = [
      hasImages ? `<option value="${CONTENT_ALL}">全部字句（頁面＋文字資料＋圖片 OCR）</option>` : '',
      `<option value="${CONTENT_NO_OCR}">頁面＋文字資料（不含圖片）</option>`,
      ...kinds.map((k) => `<option value="${esc(k)}">只有${esc(pageKindName(k))}</option>`),
      hasImages ? `<option value="${CONTENT_IMAGES}">只有遊戲內圖片文字（OCR）</option>` : '',
    ].join('');
    if ([...$('pageKind').options].some((o) => o.value === prev)) $('pageKind').value = prev;
    $('pageKind').disabled = false;
    fillGameLanguages();
  } catch (err) {
    gameStatus(esc(err.message), 'error');
  }
  updateRunButton();
}

/** What to collect for the selected content option. */
function contentPlan() {
  const { pages, images, texts } = state.currentGame;
  const content = $('pageKind').value;
  const kinds = Object.keys(pages);
  if (content === CONTENT_ALL) return { pageKinds: kinds, includeTexts: true, includeImages: true, has: (c) => kinds.some((k) => pages[k][c]) || texts[c] || images[c]?.length };
  if (content === CONTENT_NO_OCR) return { pageKinds: kinds, includeTexts: true, includeImages: false, has: (c) => kinds.some((k) => pages[k][c]) || texts[c] };
  if (content === CONTENT_IMAGES) return { pageKinds: [], includeTexts: false, includeImages: true, has: (c) => images[c]?.length };
  return { pageKinds: [content], includeTexts: false, includeImages: false, has: (c) => pages[content]?.[c] };
}

function fillGameLanguages() {
  const plan = contentPlan();
  store.set('content', $('pageKind').value);
  const gameLangs = LANGUAGES.map((l) => l.code).filter((c) => plan.has(c));
  const excel = state.sheet?.languages || [];
  const inExcel = gameLangs.filter((c) => excel.includes(c));
  const prev = $('gameLang').value;
  $('gameLang').innerHTML = [
    inExcel.length && inExcel.length < gameLangs.length ? `<option value="*excel">Excel 內的語言（${inExcel.length}）</option>` : '',
    `<option value="*">全部語言（${gameLangs.length}）</option>`,
    langOptions(gameLangs, prev),
  ].join('');
  if ([...$('gameLang').options].some((o) => o.value === prev)) $('gameLang').value = prev;
  $('gameLang').disabled = !gameLangs.length;
  const available = gameLangs;
  const lacking = excel.filter((c) => !plan.has(c));
  let msg = `遊戲有 ${gameLangs.length} 種語言`;
  if (lacking.length) msg += `；Excel 有但遊戲沒有：${lacking.map((c) => esc(languageName(c))).join('、')}`;
  if (plan.includeImages) msg += '<br><span class="hint-text">圖片文字用 OCR 辨識：第一次會下載辨識模型，每種語言約 20–60 秒</span>';
  gameStatus(msg, available.length ? 'ok' : 'error');
  updateRunButton();
}

function urlJobs() {
  return $('urlInput').value.split(/\s+/).map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
}

async function addPageFiles(files) {
  for (const file of files) {
    const content = await file.text();
    state.pageFiles.push({ name: file.name, content, code: detectLanguageFromUrl(file.name) || $('urlLang').value });
  }
  renderFileList();
  updateRunButton();
}

function renderFileList() {
  const codes = excelLanguages();
  $('fileList').innerHTML = state.pageFiles.map((f, i) => `
    <li>
      <span class="name" title="${esc(f.name)}">${esc(f.name)}</span>
      <select data-file="${i}" aria-label="語言">${langOptions(codes.includes(f.code) ? codes : [f.code, ...codes], f.code)}</select>
      <button type="button" class="link" data-remove="${i}">移除</button>
    </li>`).join('');
}

const pageLines = (content, source, href) => pageToSegments(content).map((text) => ({ text, kind: 'page', source, href }));

function buildJobs() {
  if (state.tab === 'game') {
    if (!state.currentGame || $('gameLang').disabled) return [];
    const resolved = state.currentGame;
    const plan = contentPlan();
    const pick = $('gameLang').value;
    const excel = state.sheet?.languages || [];
    const codes = [...$('gameLang').options].map((o) => o.value)
      .filter((c) => !c.startsWith('*') && (pick === '*' || c === pick || (pick === '*excel' && excel.includes(c))));
    const label = $('pageKind').selectedOptions[0]?.textContent || '';
    return codes.map((code) => ({
      code,
      href: resolved.pages.paytable?.[code] || Object.values(resolved.pages).map((p) => p[code]).find(Boolean),
      label,
      heavy: plan.includeImages,
      load: (onProgress) => collectGameStrings(resolved, code, { ...plan, onProgress }),
    }));
  }
  if (state.tab === 'url') {
    return urlJobs().map((url) => ({
      code: detectLanguageFromUrl(url) || $('urlLang').value, href: url, label: url,
      load: async () => pageLines(await fetchText(url), url.split('/').pop(), url),
    }));
  }
  if (state.tab === 'file') {
    return state.pageFiles.map((f) => ({ code: f.code, label: f.name, load: async () => pageLines(f.content, f.name) }));
  }
  const text = $('textInput').value;
  return text.trim() ? [{ code: $('textLang').value, label: '貼上的文字', load: async () => pageLines(text, '貼上的文字') }] : [];
}

function updateRunButton() {
  const jobs = buildJobs().length;
  $('runBtn').disabled = !jobs;
  $('runBtn').textContent = state.sheet?.rows.length ? '開始比對' : '抓取字句';
}

// ---------------------------------------------------------------------------
// Step 3: run and show results

function options() {
  // The main Excel file answers first; the others follow in the order they are listed.
  const primary = state.workbooks.find((w) => w.id === state.primaryId);
  const priority = [primary, ...state.workbooks.filter((w) => w !== primary)].filter(Boolean).map((w) => w.name);
  return {
    threshold: Number($('threshold').value) / 100,
    placeholders: $('optPlaceholders').checked,
    ignoreCase: $('optCase').checked,
    priority,
  };
}

const sheetOrEmpty = () => state.sheet || EMPTY_SHEET;

async function run() {
  const jobs = buildJobs();
  if (!jobs.length) return;
  $('runBtn').disabled = true;
  $('exportBtn').disabled = true;
  $('results').hidden = false;
  state.runs = jobs.map((j) => ({ ...j, state: 'loading', progress: '', lines: null, result: null, error: null }));
  state.selected = 0;
  state.filter = 'all';
  state.view = 'game';
  state.gameFilter = 'all';
  renderSummary();
  $('detail').innerHTML = '';
  const opts = options();
  const queue = [...state.runs];
  const worker = async () => {
    for (let r = queue.shift(); r; r = queue.shift()) {
      try {
        r.lines = await r.load((msg) => { r.progress = msg; renderSummary(); });
        r.result = compareLanguage(sheetOrEmpty(), r.code, r.lines, opts);
        r.state = 'done';
      } catch (err) {
        r.error = err.message;
        r.state = 'error';
      }
      renderSummary();
      if (state.runs.indexOf(r) === state.selected) renderDetail();
    }
  };
  // OCR is heavy (one recognition engine per language), so fewer languages run at once.
  const parallel = jobs.some((j) => j.heavy) ? 2 : 4;
  await Promise.all(Array.from({ length: Math.min(parallel, queue.length) }, worker));
  const firstProblem = state.runs.findIndex((r) => r.result && (r.result.counts.diff || r.result.counts.outdated || r.result.counts.check || r.result.counts.minor));
  selectRun(firstProblem >= 0 ? firstProblem : 0);
  $('exportBtn').disabled = !state.runs.some((r) => r.result);
  updateRunButton();
}

/** Re-runs the comparison on already collected lines, after an option or an OCR correction changes. */
function recompute(onlyRun) {
  if (!state.runs.length) return;
  const opts = options();
  for (const r of onlyRun ? [onlyRun] : state.runs) {
    if (r.lines) r.result = compareLanguage(sheetOrEmpty(), r.code, r.lines, opts);
  }
  renderSummary();
  renderDetail();
}

/** Lines worth listing: anything with a letter (payout numbers such as "5" are left out). */
const listedLines = (run) => run.lines.map((l, i) => ({ ...l, index: i })).filter((l) => /\p{L}/u.test(l.text));

// Each game line is judged as a whole sentence against the Excel row (or two joined rows) that answers it.
const LINE_STATUS = {
  match: STATUS.match,
  outdated: STATUS.outdated,
  diff: STATUS.diff,
  check: STATUS.check,
  minor: STATUS.minor,
  unlisted: { label: 'Excel 未列出', tone: 'muted' },
};

/** Every game line in game order, with its whole-sentence result and the Excel rows that answer it. */
function gameLines(run) {
  const byId = new Map(run.result.results.map((r) => [r.id, r]));
  return listedLines(run).map((line, n) => {
    const lr = run.result.lineResults?.[line.index] || null;
    const rows = lr ? lr.rowIds.map((id) => byId.get(id)).filter(Boolean) : [];
    return { n: n + 1, line, rows, lr, status: lr ? lr.status : 'unlisted' };
  });
}

function lineCounts(run) {
  const c = { lines: 0, match: 0, bad: 0, check: 0, minor: 0, unlisted: 0 };
  for (const g of gameLines(run)) {
    c.lines++;
    if (g.status === 'diff' || g.status === 'outdated') c.bad++;
    else c[g.status]++;
  }
  c.notFound = run.result.counts.missing;
  return c;
}

function verdict(run) {
  if (!run.result.results.length) return '<span class="badge muted">Excel 無此語言</span>';
  const c = lineCounts(run);
  if (c.bad) return `<span class="badge bad">有 ${c.bad} 處錯誤</span>`;
  if (c.check) return `<span class="badge warn">${c.check} 處需看圖確認</span>`;
  if (c.minor) return `<span class="badge warn">${c.minor} 處輕微差異</span>`;
  if (c.notFound) return `<span class="badge muted">Excel 有 ${c.notFound} 句遊戲裡找不到</span>`;
  return '<span class="badge ok">全部正確</span>';
}

function numCell(n, tone, title = '') {
  return `<td class="num ${n ? tone : 'zero'}"${title ? ` title="${esc(title)}"` : ''}>${n}</td>`;
}

function renderSummary() {
  const rows = state.runs.map((r, i) => {
    const lang = `<td class="lang-cell"><strong>${esc(languageName(r.code))}</strong><code>${esc(r.code)}</code></td>`;
    const src = r.href
      ? `<td class="src"><a href="${esc(r.href)}" target="_blank" rel="noopener" title="${esc(r.href)}">${esc(r.label === r.href ? r.href.split('/').pop() : r.label)}</a></td>`
      : `<td class="src" title="${esc(r.label)}">${esc(r.label)}</td>`;
    if (r.state === 'loading') return `<tr data-run="${i}">${lang}<td colspan="8"><span class="spinner"></span> ${esc(r.progress || '讀取中…')}</td>${src}</tr>`;
    if (r.state === 'error') return `<tr data-run="${i}">${lang}<td colspan="8" class="err">讀取失敗：${esc(r.error)}</td>${src}</tr>`;
    const c = lineCounts(r);
    const ocr = r.lines.filter((l) => l.ocr).length;
    return `<tr data-run="${i}" class="${i === state.selected ? 'selected' : ''}">${lang}<td>${verdict(r)}</td>
      <td class="num" title="${ocr ? `其中 ${ocr} 句來自圖片 OCR` : ''}">${c.lines}${ocr ? `<span class="ocr-n">（圖 ${ocr}）</span>` : ''}</td>
      ${numCell(c.match, 'ok')}${numCell(c.bad, 'bad')}${numCell(c.check, 'warn')}${numCell(c.minor, 'warn')}
      ${numCell(c.unlisted, '', '遊戲裡有、Excel 沒有對應的句子')}${numCell(c.notFound, 'warn', 'Excel 有、遊戲裡找不到的句子')}${src}</tr>`;
  }).join('');
  $('summary').innerHTML = `<table>
    <thead><tr><th>語言</th><th>結果</th><th class="num">遊戲字句</th><th class="num">相符</th><th class="num">錯誤</th><th class="num">看圖確認</th><th class="num">空白/標點</th><th class="num">Excel 未列出</th><th class="num">Excel 找不到</th><th>來源</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function selectRun(i) {
  state.selected = i;
  state.filter = 'all';
  state.gameFilter = 'all';
  renderSummary();
  renderDetail();
}

const visible = (s) => esc(s).replace(/ /g, '<span class="ws" title="空白">␣</span>');

function renderExpected(r, ops = r.expectedOps) {
  return ops.map(([t, s]) => {
    if (t === '=') return esc(s);
    if (t === '-') return `<mark class="del" title="遊戲裡缺少">${visible(s)}</mark>`;
    return '';
  }).join('');
}

function sourceTag(line) {
  if (!line) return '';
  return `<span class="line-src">${esc(SOURCE_KINDS[line.kind] || '')}${line.source ? ` · ${esc(line.source)}` : ''}</span>`;
}

function lineImage(line, cls = 'ocr-img') {
  return line?.image ? `<img class="${cls}" src="${line.image}" alt="${esc(line.text)}" loading="lazy">` : '';
}

/** The game text of a match with its differences marked; `trim` shortens long surrounding text. */
function renderMatchedText(p, trim) {
  const ops = p.ops.map(([t, s], k) => {
    if (t === '=') return esc(s);
    if (t === '+') return `<mark class="ins" title="遊戲裡的文字與 Excel 不同">${visible(s)}</mark>`;
    // Text missing from the game with nothing in its place: show where it should be.
    return p.ops[k + 1]?.[0] === '+' ? '' : `<span class="gap" title="此處缺少：${esc(s)}">‸</span>`;
  }).join('');
  const b = trim && p.before.length > 60 ? `…${p.before.slice(-60)}` : p.before;
  const a = trim && p.after.length > 60 ? `${p.after.slice(0, 60)}…` : p.after;
  return `${b ? `<span class="ctx">${esc(b)}</span>` : ''}${ops}${a ? `<span class="ctx">${esc(a)}</span>` : ''}`;
}

function renderPage(r, run) {
  const p = r.page;
  if (!p) return '<span class="ctx">—</span>';
  const line = run.lines[p.line];
  if (p.weak) {
    const text = p.before + p.ops.filter(([t]) => t !== '-').map(([, s]) => s).join('') + p.after;
    return `<span class="weak-label">最接近的文字（相似度低，可能不是同一句）</span>${lineImage(line, 'ocr-img weak')}<span class="weak">${esc(text)}</span>${sourceTag(line)}`;
  }
  return `${lineImage(line)}${p.ocr ? '<span class="ocr-tag">OCR</span>' : ''}${renderMatchedText(p, true)}${sourceTag(line)}`;
}

function renderNotes(notes) {
  return notes.length ? `<ul class="notes">${notes.map((n) => `<li class="${n.startsWith('Excel 內疑似') || n.startsWith('另一份') ? 'warn' : ''}">${esc(n)}</li>`).join('')}</ul>` : '';
}

function rowTitle(r) {
  if (!r.origin) return '';
  const also = r.alsoIn.length ? `\n也在：${r.alsoIn.map((o) => `${o.file} › ${o.sheetName} 第 ${o.rowNumber} 列`).join('、')}` : '';
  return `${r.origin.file} › ${r.origin.sheetName} 第 ${r.rowNumber} 列${also}`;
}

// --- Game view: every game string in order, answered by the Excel ---------------------------------

const GAME_FILTERS = [
  ['all', '全部'],
  ['problems', '需處理'],
  ['match', '相符'],
  ['unlisted', 'Excel 未列出'],
];

function gameLineMatches(g, f) {
  if (f === 'all') return true;
  if (f === 'problems') return ['diff', 'outdated', 'check', 'minor'].includes(g.status);
  return g.status === f;
}

function renderGameText(g) {
  const { line, lr } = g;
  const marked = lr && g.status !== 'match' ? `<div class="text">${renderMatchedText(lr.page, false)}</div>` : '';
  const head = `<span class="kind kind-${line.kind}">${esc(SOURCE_KINDS[line.kind] || '')}</span><span class="line-where" title="${esc([line.source, ...(line.also || [])].join('、'))}">${esc(line.source || '')}</span>`;
  if (line.ocr) {
    const low = line.confidence < 60 && !line.edited;
    return `${head}${lineImage(line)}
      <input class="ocr-edit" data-line="${line.index}" value="${esc(line.text)}" aria-label="OCR 辨識文字，可修正" title="OCR 辨識結果，認錯可直接修改">
      <span class="ocr-meta${low ? ' low' : ''}">OCR 信心 ${line.confidence}%${low ? '（偏低，請看圖確認）' : ''}${line.edited ? ' · 已手動修正' : ''}${line.alts?.length ? ` · 另一種辨識：${esc(line.alts.join(' / '))}` : ''}</span>
      ${marked}`;
  }
  return `${head}${marked || `<div class="text">${esc(line.text)}</div>`}`;
}

function renderAnswers(g) {
  if (!g.rows.length) return `<span class="ctx">${state.sheet?.rows.length ? 'Excel 沒有這句' : '—'}</span>`;
  // The answer's differences are marked for this line when one row answers it on its own.
  const ops = g.rows.length === 1 ? g.lr.expectedOps : null;
  const fallback = state.primaryName && state.workbooks.length > 1;
  return g.rows.map((r) => `
    <div class="answer">
      <div class="answer-head">
        <span class="row-chip" title="${esc(rowTitle(r))}">${esc(rowLabel(r))}</span>
        ${fallback && r.origin && r.origin.file !== state.primaryName ? '<span class="backup-tag" title="主要 Excel 沒有這句，改用其他檔案的答案">備用</span>' : ''}
        ${r.source === 'revised' ? `<span class="src-tag">${esc(r.header)}</span>` : ''}
      </div>
      <div class="text">${ops ? renderExpected(r, ops) : esc(r.expected)}</div>
      ${r.english && r.english !== r.expected ? `<div class="en">${esc(r.english)}</div>` : ''}
      ${renderNotes(r.notes.filter((n) => n.startsWith('Excel 內疑似') || n.startsWith('另一份')))}
    </div>`).join('');
}

function renderGameView(run) {
  const all = gameLines(run);
  const list = all.filter((g) => gameLineMatches(g, state.gameFilter));
  const filters = GAME_FILTERS.map(([k, label]) => {
    const n = all.filter((g) => gameLineMatches(g, k)).length;
    return `<button type="button" data-game-filter="${k}" aria-pressed="${state.gameFilter === k}">${esc(label)}<span class="n">${n}</span></button>`;
  }).join('');
  const body = list.map((g) => {
    const st = LINE_STATUS[g.status];
    const notes = g.lr?.notes || [];
    return `<tr class="tone-${st.tone}">
      <td class="c-row" data-label="序號">${g.n}</td>
      <td class="c-status" data-label="結果"><span class="badge ${st.tone}">${esc(st.label)}</span>${renderNotes(notes)}</td>
      <td data-label="遊戲字句">${renderGameText(g)}</td>
      <td data-label="Excel 正確答案">${renderAnswers(g)}</td>
    </tr>`;
  }).join('');
  const missing = run.result.counts.missing;
  return `
    <div class="filters" role="group" aria-label="篩選">${filters}</div>
    <div class="rows game">
      ${list.length ? `<table>
        <thead><tr><th class="c-row">#</th><th class="c-status">結果</th><th>遊戲字句</th><th>Excel 正確答案</th></tr></thead>
        <tbody>${body}</tbody></table>` : '<p class="empty">沒有符合篩選的字句</p>'}
    </div>
    ${missing ? `<p class="callout">Excel 還有 <strong>${missing}</strong> 句在遊戲裡找不到。<button type="button" class="link" data-view="excel" data-filter="missing">查看這些句子</button></p>` : ''}`;
}

// --- Excel view: every Excel row, and where (or whether) the game has it ---------------------------

const FILTERS = [
  ['all', '全部'],
  ['problems', '需處理'],
  ...Object.entries(STATUS).map(([k, v]) => [k, k === 'missing' ? '遊戲找不到' : v.label]),
];

function matchesFilter(r, f) {
  if (f === 'all') return true;
  if (f === 'problems') return ['diff', 'outdated', 'check', 'minor'].includes(r.status);
  return r.status === f;
}

function renderExcelView(run) {
  const { result } = run;
  const list = result.results.filter((r) => matchesFilter(r, state.filter));
  const filters = FILTERS.map(([k, label]) => {
    const n = result.results.filter((r) => matchesFilter(r, k)).length;
    return `<button type="button" data-filter="${k}" aria-pressed="${state.filter === k}">${esc(label)}<span class="n">${n}</span></button>`;
  }).join('');
  const body = list.map((r) => {
    const st = STATUS[r.status];
    return `<tr class="tone-${st.tone}">
      <td class="c-row" data-label="Excel 列"${r.origin ? ` title="${esc(rowTitle(r))}"` : ''}>${r.rowNumber}${rowTag(r) ? `<span class="row-src">${esc(rowTag(r))}</span>` : ''}</td>
      <td class="c-status" data-label="狀態"><span class="badge ${st.tone}">${esc(r.status === 'missing' ? '遊戲找不到' : st.label)}</span>${renderNotes(r.notes)}</td>
      <td class="en" data-label="英文原文">${esc(r.english)}</td>
      <td class="text" data-label="Excel 預期">${r.source === 'revised' ? `<span class="src-tag">${esc(r.header)}</span><br>` : ''}${renderExpected(r)}</td>
      <td class="text" data-label="遊戲實際">${renderPage(r, run)}</td>
      <td class="c-sim" data-label="相似度">${r.page ? `${Math.round(r.similarity * 100)}%` : '—'}</td>
    </tr>`;
  }).join('');
  return `
    <div class="filters" role="group" aria-label="篩選">${filters}</div>
    <div class="rows">
      ${list.length ? `<table>
        <thead><tr><th class="c-row">列</th><th class="c-status">狀態</th><th>英文原文</th><th>Excel 預期</th><th>遊戲實際</th><th class="c-sim">相似度</th></tr></thead>
        <tbody>${body}</tbody></table>` : `<p class="empty">${result.results.length ? '沒有符合篩選的項目' : 'Excel 這個分頁沒有此語言的內容'}</p>`}
    </div>`;
}

function renderDetail() {
  const run = state.runs[state.selected];
  if (!run || !run.result) {
    $('detail').innerHTML = run?.error ? `<div class="rows"><p class="empty">${esc(run.error)}</p></div>` : '';
    return;
  }
  const hasRows = run.result.results.length > 0;
  const view = hasRows ? state.view : 'game';
  $('detail').innerHTML = `
    <div class="detail-head">
      <h3>${esc(languageName(run.code))}（${esc(run.code)}）${state.sheetName ? `· ${esc(state.sheetName)}` : ''}</h3>
      ${run.href ? `<a href="${esc(run.href)}" target="_blank" rel="noopener">${esc(run.href)}</a>` : `<span class="meta">${esc(run.label)}</span>`}
    </div>
    <div class="view-tabs" role="tablist">
      <button type="button" role="tab" data-view="game" aria-selected="${view === 'game'}">遊戲字句（逐句比對）<span class="n">${listedLines(run).length}</span></button>
      <button type="button" role="tab" data-view="excel" aria-selected="${view === 'excel'}" ${hasRows ? '' : 'disabled'}>依 Excel 列<span class="n">${run.result.results.length}</span></button>
    </div>
    ${view === 'game' ? renderGameView(run) : renderExcelView(run)}`;
}

// ---------------------------------------------------------------------------
// Export

function pageText(r) {
  if (!r.page || r.page.weak) return '';
  return r.page.ops.filter(([t]) => t !== '-').map(([, s]) => s).join('');
}

/** One row per string, one column per language, matched by where the string lives (page line, key, image). */
function stringMatrix(runs) {
  const keys = [];
  const table = new Map();
  for (const r of runs) {
    const seen = new Map();
    for (const l of listedLines(r)) {
      const base = `${SOURCE_KINDS[l.kind] || ''}｜${l.source || ''}`;
      const n = (seen.get(base) || 0) + 1;
      seen.set(base, n);
      const key = `${base}#${n}`;
      if (!table.has(key)) { table.set(key, { kind: SOURCE_KINDS[l.kind] || '', source: l.source || '', n, byLang: {} }); keys.push(key); }
      table.get(key).byLang[r.code] = l.text;
    }
  }
  const header = ['類型', '來源', '序號', ...runs.map((r) => `${languageName(r.code)} ${r.code}`)];
  return [header, ...keys.map((k) => { const t = table.get(k); return [t.kind, t.source, t.n, ...runs.map((r) => t.byLang[r.code] || '')]; })];
}

function exportReport() {
  const wb = XLSX.utils.book_new();
  const done = state.runs.filter((r) => r.result);
  const summary = [['語言', '代碼', '遊戲字句', '其中圖片 OCR', '相符', '錯誤', '需看圖確認', '空白/標點差異', 'Excel 未列出', 'Excel 有但遊戲找不到', '來源']];
  for (const r of state.runs) {
    if (!r.result) { summary.push([languageName(r.code), r.code, '', '', '', '', '', '', '', '', `讀取失敗：${r.error}`]); continue; }
    const c = lineCounts(r);
    summary.push([languageName(r.code), r.code, c.lines, r.lines.filter((l) => l.ocr).length, c.match, c.bad, c.check, c.minor, c.unlisted, c.notFound, r.href || r.label]);
  }
  const ws = XLSX.utils.aoa_to_sheet(summary);
  ws['!cols'] = [{ wch: 10 }, { wch: 8 }, { wch: 9 }, { wch: 12 }, { wch: 7 }, { wch: 7 }, { wch: 11 }, { wch: 13 }, { wch: 12 }, { wch: 18 }, { wch: 80 }];
  XLSX.utils.book_append_sheet(wb, ws, '總覽');

  const matrix = XLSX.utils.aoa_to_sheet(stringMatrix(done));
  matrix['!cols'] = [{ wch: 9 }, { wch: 34 }, { wch: 5 }, ...done.map(() => ({ wch: 40 }))];
  XLSX.utils.book_append_sheet(wb, matrix, '全部字句對照');

  // Per language: every game string in order with its Excel answer, then the Excel lines the game lacks.
  for (const r of done) {
    const rows = [['序號', '類型', '遊戲來源', '遊戲字句', '結果', 'Excel 正確答案', 'Excel 檔案', '分頁', 'Excel 列', '英文原文', '備註']];
    for (const g of gameLines(r)) {
      const notes = [...(g.lr?.notes || []), ...g.rows.flatMap((x) => x.notes.filter((n) => n.startsWith('Excel 內疑似') || n.startsWith('另一份')))];
      if (g.rows.some((x) => state.primaryName && x.origin && x.origin.file !== state.primaryName)) notes.unshift('主要 Excel 沒有這句，答案來自其他檔案');
      if (g.line.ocr) notes.unshift(`圖片 OCR（信心 ${g.line.confidence}%${g.line.edited ? '，已手動修正' : ''}）`);
      const join = (f) => g.rows.map(f).join('\n');
      rows.push([g.n, SOURCE_KINDS[g.line.kind] || '', g.line.source || '', g.line.text, LINE_STATUS[g.status].label,
        join((x) => x.expected), join((x) => x.origin?.file || ''), join((x) => x.origin?.sheetName || ''), join((x) => x.rowNumber), join((x) => x.english), notes.join('\n')]);
    }
    const missing = r.result.results.filter((x) => x.status === 'missing');
    if (missing.length) {
      rows.push([], ['Excel 有、但遊戲裡找不到']);
      for (const x of missing) {
        rows.push(['', '', '', '', '遊戲找不到', x.expected, x.origin?.file || '', x.origin?.sheetName || '', x.rowNumber, x.english, x.notes.join('\n')]);
      }
    }
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    sheet['!cols'] = [{ wch: 6 }, { wch: 9 }, { wch: 28 }, { wch: 50 }, { wch: 12 }, { wch: 50 }, { wch: 24 }, { wch: 18 }, { wch: 8 }, { wch: 40 }, { wch: 40 }];
    XLSX.utils.book_append_sheet(wb, sheet, `${languageName(r.code)} ${r.code}`.slice(0, 31));
  }
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
  const name = (state.sheetName || state.currentGame?.game.name || '遊戲字句').replace(/[\\/:*?"<>|]/g, '_');
  XLSX.writeFile(wb, `${state.sheet?.rows.length ? '比對結果' : '遊戲字句'}_${name}_${stamp}.xlsx`);
}

// ---------------------------------------------------------------------------
// Wiring

function wireDrop(zone, onFiles) {
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add('dragging'); }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('dragging')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]);
  });
}

function init() {
  $('listUrl').value = store.get('listUrl', DEFAULT_LIST_URL);
  $('threshold').value = store.get('threshold', 60);
  $('thresholdOut').textContent = `${$('threshold').value}%`;
  $('optPlaceholders').checked = store.get('placeholders', true);
  $('optCase').checked = store.get('ignoreCase', false);
  refreshLanguageSelects();

  for (const id of ['refFile', 'refMore']) {
    $(id).addEventListener('change', (e) => { if (e.target.files.length) addWorkbooks([...e.target.files]); e.target.value = ''; });
  }
  wireDrop($('refCard'), addWorkbooks);
  $('refFiles').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-remove-wb]');
    if (b) removeWorkbook(Number(b.dataset.removeWb));
  });
  $('refFiles').addEventListener('change', (e) => {
    if (e.target.name !== 'primaryWb') return;
    state.primaryId = Number(e.target.value);
    renderReference();
  });
  $('sheetSelect').addEventListener('change', (e) => selectGroup(e.target.value));
  $('sheetPicks').addEventListener('change', (e) => {
    const id = e.target.dataset.pick;
    if (!id) return;
    if (e.target.checked) state.picked.add(id);
    else state.picked.delete(id);
    renderReference();
  });

  document.querySelectorAll('.tabs [role="tab"]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
  $('loadList').addEventListener('click', loadList);
  $('listUrl').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadList(); });
  $('gameSelect').addEventListener('change', (e) => selectGame(e.target.value));
  $('pageKind').addEventListener('change', fillGameLanguages);
  $('gameLang').addEventListener('change', updateRunButton);
  $('urlInput').addEventListener('input', updateRunButton);
  $('textInput').addEventListener('input', updateRunButton);
  $('pageFiles').addEventListener('change', (e) => { addPageFiles([...e.target.files]); e.target.value = ''; });
  wireDrop($('pageDrop'), addPageFiles);
  $('fileList').addEventListener('change', (e) => {
    const i = e.target.dataset.file;
    if (i !== undefined) state.pageFiles[i].code = e.target.value;
  });
  $('fileList').addEventListener('click', (e) => {
    const i = e.target.dataset.remove;
    if (i === undefined) return;
    state.pageFiles.splice(Number(i), 1);
    renderFileList();
    updateRunButton();
  });

  $('threshold').addEventListener('input', (e) => { $('thresholdOut').textContent = `${e.target.value}%`; });
  $('threshold').addEventListener('change', (e) => { store.set('threshold', Number(e.target.value)); recompute(); });
  $('optPlaceholders').addEventListener('change', (e) => { store.set('placeholders', e.target.checked); recompute(); });
  $('optCase').addEventListener('change', (e) => { store.set('ignoreCase', e.target.checked); recompute(); });
  $('runBtn').addEventListener('click', run);
  $('exportBtn').addEventListener('click', exportReport);

  $('summary').addEventListener('click', (e) => {
    if (e.target.closest('a')) return;
    const tr = e.target.closest('tr[data-run]');
    if (tr) selectRun(Number(tr.dataset.run));
  });
  $('detail').addEventListener('click', (e) => {
    if (e.target.matches('img.ocr-img')) { e.target.classList.toggle('zoomed'); return; }
    const b = e.target.closest('button[data-filter], button[data-game-filter], button[data-view]');
    if (!b) return;
    if (b.dataset.filter) state.filter = b.dataset.filter;
    if (b.dataset.gameFilter) state.gameFilter = b.dataset.gameFilter;
    if (b.dataset.view) state.view = b.dataset.view;
    renderDetail();
  });
  // Correcting an OCR misread re-checks that language against the Excel right away.
  $('detail').addEventListener('change', (e) => {
    const input = e.target.closest('input.ocr-edit');
    const run = state.runs[state.selected];
    if (!input || !run?.lines) return;
    const line = run.lines[Number(input.dataset.line)];
    const text = input.value.trim();
    if (!line || !text || text === line.text) return;
    line.text = text;
    line.alts = []; // the correction replaces every machine reading
    line.edited = true;
    recompute(run);
  });

  loadList();
}

init();
