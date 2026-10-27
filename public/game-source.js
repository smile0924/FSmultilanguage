// Finds games and their per-language page URLs on the FS game servers, through the local /api/proxy.
import { pageToSegments, joinBrokenLines } from './compare.js';
import { readImageTexts } from './ocr.js';

export async function fetchText(url) {
  const res = await fetch(`/api/proxy?url=${encodeURIComponent(url)}`);
  const text = await res.text();
  if (!res.ok) {
    let msg = text;
    try { msg = JSON.parse(text).error || text; } catch { /* plain text error */ }
    throw new Error(`${res.status}：${msg.slice(0, 200)}（${url}）`);
  }
  // The static host answers missing files with its default IIS page and status 200.
  if (/<title>\s*IIS Windows\s*<\/title>/i.test(text)) throw new Error(`檔案不存在（${url}）`);
  return text;
}

/** Reads map.js next to list.jsp: { "S-TW02": ["treeofwild", "20260101P", "Tree of Wild"], ... } */
export async function loadGameList(listUrl) {
  const base = new URL('.', listUrl).href;
  const js = await fetchText(new URL('map.js', base).href);
  const games = [];
  for (const m of js.matchAll(/"([A-Z]-[A-Z0-9]+)"\s*:\s*\[([^\]]*)\]/g)) {
    const vals = [...m[2].matchAll(/"([^"]*)"/g)].map((x) => x[1]);
    games.push({ code: m[1], folder: vals[0] || '', version: vals[1] || '', name: vals[2] || vals[0] || m[1] });
  }
  if (!games.length) throw new Error('map.js 裡找不到遊戲清單');
  games.sort((a, b) => b.version.localeCompare(a.version) || a.name.localeCompare(b.name));
  return { base, games };
}

// Game bundles ship as `eval(<decoder>(<packed data>))`. Running the bundle with `eval` shadowed by a
// parameter captures the decoded source instead of executing it. It runs in a sandboxed iframe with an
// opaque origin (no access to this page); a Worker is not used because its smaller stack overflows the decoder.
const UNPACK_DOC = `<script>
addEventListener('message', (e) => {
  let code = null;
  try {
    new Function('eval', String(e.data))((s) => { code = String(s); });
    parent.postMessage({ ok: code !== null, code, error: code === null ? 'no eval() payload' : null }, '*');
  } catch (err) {
    parent.postMessage({ ok: false, error: String(err && err.message || err) }, '*');
  }
});
parent.postMessage({ ready: true }, '*');
<\/script>`;

function unpack(src) {
  return new Promise((resolve, reject) => {
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.hidden = true;
    frame.srcdoc = UNPACK_DOC;
    const done = (fn, value) => {
      clearTimeout(timer);
      removeEventListener('message', onMessage);
      frame.remove();
      fn(value);
    };
    const onMessage = (e) => {
      if (e.source !== frame.contentWindow) return;
      if (e.data?.ready) return frame.contentWindow.postMessage(src, '*');
      if (e.data?.ok) done(resolve, e.data.code);
      else done(reject, new Error(`解開遊戲程式碼失敗：${e.data?.error}`));
    };
    const timer = setTimeout(() => done(reject, new Error('解開遊戲程式碼逾時')), 20000);
    addEventListener('message', onMessage);
    document.body.append(frame);
  });
}

const PAGE_KIND_NAMES = { paytable: '賠付表 Paytable', help: '說明 Help', rule: '規則 Rule', rules: '規則 Rules', intro: '介紹 Intro' };
export const pageKindName = (kind) => PAGE_KIND_NAMES[kind] || kind;

/**
 * Follows the same path the game launcher does (map.js → project.json → js/game-*.js) and collects
 * every per-language asset:
 *   pages    { paytable: { th_TH: "https://…/paytable_th_TH.ba9e9fcd.html" } }
 *   images   { th_TH: [{ name: "maingame_lan", url, ext: "json" }, …] }  (art with lettering)
 *   texts    { th_TH: "https://…/assets/th_TH.28625f8e.json" }            (shared strings file)
 */
export async function resolveGamePages(base, game, onProgress = () => {}) {
  const ver = game.version;
  if (!ver || !/[PA]$/.test(ver)) throw new Error(`此遊戲（${game.code}）的目錄結構不支援自動尋找，請改用「貼上連結」`);
  const gameDir = new URL(`${ver}/games/${game.folder}/`, base).href;

  onProgress('讀取遊戲入口…');
  const index = await fetchText(new URL(`index.jsp?game=${encodeURIComponent(game.code)}&pm=6`, gameDir).href);
  const staticBase = /<base\s[^>]*href="([^"]+)"/i.exec(index)?.[1] || gameDir;

  onProgress('讀取 project.json…');
  const project = JSON.parse(await fetchText(new URL('project.json', gameDir).href));
  const scripts = (project.jsList || []).filter((p) => !p.startsWith('../'));
  if (!scripts.length) throw new Error('project.json 沒有遊戲程式檔');

  const pages = {};
  const images = {};
  const sharedJson = new Set();
  for (const p of scripts) {
    onProgress(`讀取 ${p}…`);
    let src = await fetchText(new URL(p, staticBase).href);
    if (/^\s*eval\s*\(/.test(src)) {
      onProgress('解開遊戲程式碼…');
      src = await unpack(src);
    }
    for (const m of src.matchAll(/assets\/([a-z]+)_([a-z]{2}_[A-Z]{2})\.[0-9a-f]{6,}\.html/g)) {
      (pages[m[1]] ??= {})[m[2]] = new URL(m[0], staticBase).href;
    }
    for (const m of src.matchAll(/assets\/(\w+?)_([a-z]{2}_[A-Z]{2})\.[0-9a-f]{6,}\.(json|png|jpg|jpeg|webp)/g)) {
      const list = (images[m[2]] ??= []);
      const url = new URL(m[0], staticBase).href;
      if (!list.some((a) => a.url === url)) list.push({ name: m[1], url, ext: m[3] });
    }
    for (const m of src.matchAll(/assets\/[a-z0-9_]+\.[0-9a-f]{6,}\.json/g)) {
      if (!/_[a-z]{2}_[A-Z]{2}\./.test(m[0])) sharedJson.add(new URL(m[0], staticBase).href);
    }
  }

  // Language-neutral spine animations (intro page, big win, buy feature …) pull their lettering from
  // per-language atlases named in a "[locale block]" of their .atlas file.
  onProgress('讀取動畫圖集…');
  await Promise.all([...sharedJson].map(async (jsonUrl) => {
    let atlas;
    try {
      atlas = await fetchText(jsonUrl.replace(/\.json$/, '.atlas'));
    } catch {
      return; // a sprite sheet, not a spine animation
    }
    const block = /\[locale block\]([\s\S]*?)\[\/locale block\]/i.exec(atlas);
    if (!block) return;
    const name = jsonUrl.split('/').pop().replace(/\.[0-9a-f]+\.json$/, '');
    for (const m of block[1].matchAll(/^\s*([a-z]{2}_[A-Z]{2})\s+(\S+\.atlas)\s*$/gm)) {
      const url = new URL(m[2], jsonUrl).href;
      const list = (images[m[1]] ??= []);
      if (!list.some((a) => a.url === url)) list.push({ name, url, ext: 'atlas' });
    }
  }));

  // The shared strings file holds symbol tips such as TXT_SLOT_TW02_WILD; app.js lists one per language.
  const texts = {};
  const appJs = /data-main="([^"]+)"/.exec(index)?.[1];
  if (appJs) {
    try {
      onProgress('讀取共用語系檔…');
      const src = await fetchText(new URL(appJs, staticBase).href);
      for (const m of src.matchAll(/["'](\.\.\/\.\.\/assets\/([a-z]{2}_[A-Z]{2})\.[0-9a-f]+\.json)["']/g)) {
        texts[m[2]] = new URL(m[1], staticBase).href;
      }
    } catch {
      /* the game still works without the shared strings */
    }
  }
  return { gameDir, staticBase, pages, images, texts, shortCode: game.code.replace(/^[A-Z]-/, '') };
}

export const SOURCE_KINDS = { page: '頁面', text: '文字資料', image: '圖片 OCR' };

/**
 * Every string the game shows in one language: page text, the game's entries in the shared strings file
 * and (optionally) lettering read from images. Each line is { text, kind, source, href, ocr?, image? }.
 */
export async function collectGameStrings(resolved, code, { pageKinds, includeTexts = true, includeImages = false, onProgress = () => {} }) {
  const lines = [];
  for (const kind of pageKinds) {
    const href = resolved.pages[kind]?.[code];
    if (!href) continue;
    onProgress(`讀取${pageKindName(kind)}…`);
    for (const text of pageToSegments(await fetchText(href))) lines.push({ text, kind: 'page', source: pageKindName(kind), href });
  }
  if (includeTexts && resolved.texts[code]) {
    const href = resolved.texts[code];
    const keyRe = new RegExp(`_${resolved.shortCode}(_|$)`);
    const json = JSON.parse((await fetchText(href)).replace(/^﻿/, ''));
    for (const [key, value] of Object.entries(json)) {
      if (keyRe.test(key) && typeof value === 'string') lines.push({ text: joinBrokenLines(value), kind: 'text', source: key, href });
    }
  }
  if (includeImages && resolved.images[code]?.length) {
    lines.push(...await readImageTexts(resolved.images[code], code, { fetchText, onProgress }));
  }
  return lines;
}
