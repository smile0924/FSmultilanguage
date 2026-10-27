// Reads the text baked into game images (sprite atlases, spine atlases, plain PNGs) with Tesseract.js,
// which is loaded from a CDN the first time it is needed.
import { normalize, joinBrokenLines } from './compare.js';

const TESSERACT_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js';
const OCR_LANGS = {
  en_US: 'eng', zh_CN: 'chi_sim', th_TH: 'tha', id_ID: 'ind', vi_VN: 'vie', ja_JP: 'jpn', ko_KR: 'kor', ru_RU: 'rus',
  tr_TR: 'tur', es_ES: 'spa', fr_FR: 'fra', pt_PT: 'por', bn_BN: 'ben', hi_HI: 'hin', it_IT: 'ita',
};
const proxied = (url) => `/api/proxy?url=${encodeURIComponent(url)}`;

let tesseract = null;
function loadTesseract() {
  if (window.Tesseract) return Promise.resolve(window.Tesseract);
  tesseract ??= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = TESSERACT_URL;
    s.onload = () => resolve(window.Tesseract);
    s.onerror = () => { tesseract = null; reject(new Error('無法載入 OCR 元件（需要能連到 cdn.jsdelivr.net）')); };
    document.head.append(s);
  });
  return tesseract;
}

// ---------------------------------------------------------------------------
// Images and regions

async function loadBitmap(url) {
  const res = await fetch(proxied(url));
  if (!res.ok) throw new Error(`圖片讀取失敗 ${res.status}（${url}）`);
  return createImageBitmap(await res.blob());
}

/**
 * Libgdx/spine atlas text → pages [{ image, regions: [{ name, x, y, w, h, rotated }] }]. The FS atlases may
 * also name other .atlas files (including a "[locale block]" of per-language ones); those lines are skipped
 * here because the game resolver already lists the per-language atlases as assets of their own.
 */
export function parseAtlas(text) {
  const pages = [];
  let page = null;
  let region = null;
  let inLocale = false;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { page = region = null; continue; }
    if (/^\[locale block\]$/i.test(line)) { inLocale = true; continue; }
    if (/^\[\/locale block\]$/i.test(line)) { inLocale = false; continue; }
    if (inLocale || /\.atlas$/i.test(line)) continue;
    const kv = /^(\w+)\s*:\s*(.*)$/.exec(line);
    if (!kv) {
      if (!page) { page = { image: line, regions: [] }; pages.push(page); region = null; }
      else { region = { name: line, x: 0, y: 0, w: 0, h: 0, rotated: false }; page.regions.push(region); }
      continue;
    }
    if (!region) continue;
    const [, key, value] = kv;
    const nums = value.split(',').map((n) => parseInt(n, 10));
    // Spine/libgdx packs rotated regions turned the other way from TexturePacker sprite sheets.
    if (key === 'rotate') { region.rotated = value === 'true' || value === '90'; region.turn = 1; }
    else if (key === 'xy') [region.x, region.y] = nums;
    else if (key === 'size') [region.w, region.h] = nums;
  }
  return pages;
}

/** Animation frames (title_000, title_002 …) repeat one piece of lettering; reading the first is enough. */
function firstOfSequences(regions) {
  const seen = new Set();
  return regions.filter((r) => {
    const m = /^(.*?)_?\d{2,}$/.exec(r.name);
    if (!m) return true;
    if (seen.has(m[1])) return false;
    seen.add(m[1]);
    return true;
  });
}

/** Lists the text-bearing regions of one language asset: sprite atlas frames, spine regions, or the whole image. */
async function assetRegions(asset, fetchText) {
  const base = asset.url.replace(/[^/]*$/, '');
  const fromAtlas = (text) => parseAtlas(text).flatMap((p) => firstOfSequences(p.regions).map((region) => ({ imageUrl: base + p.image, region })));
  if (asset.ext === 'atlas') return fromAtlas(await fetchText(asset.url));
  if (asset.ext !== 'json') return [{ imageUrl: asset.url, region: { name: '', whole: true } }];
  const json = JSON.parse((await fetchText(asset.url)).replace(/^﻿/, ''));
  if (json.frames && json.meta?.image) {
    const frames = Array.isArray(json.frames) ? json.frames.map((f) => [f.filename, f]) : Object.entries(json.frames);
    return frames.map(([name, f]) => ({
      imageUrl: base + json.meta.image,
      region: { name: name.replace(/\.png$/i, ''), ...f.frame, rotated: Boolean(f.rotated) },
    }));
  }
  if (json.skeleton) return fromAtlas(await fetchText(asset.url.replace(/\.json$/, '.atlas')));
  return [];
}

/**
 * Cuts a region out of the sheet and turns a rotated one back upright: -1 for sprite sheets (stored turned
 * clockwise), +1 for spine atlases.
 */
function crop(img, r, turn = r.turn ?? -1) {
  if (r.whole) {
    const c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    c.getContext('2d').drawImage(img, 0, 0);
    return c;
  }
  const c = document.createElement('canvas');
  c.width = r.w;
  c.height = r.h;
  const g = c.getContext('2d');
  if (r.rotated) {
    if (turn < 0) { g.translate(0, r.h); g.rotate(-Math.PI / 2); }
    else { g.translate(r.w, 0); g.rotate(Math.PI / 2); }
    g.drawImage(img, r.x, r.y, r.h, r.w, 0, 0, r.h, r.w);
  } else {
    g.drawImage(img, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
  }
  return c;
}

/**
 * Game lettering is light text with a dark glow on transparency. "dark" flattens it on black and inverts
 * (bright fill → dark text); "light" flattens on white, which suits pale outlines around coloured fills.
 */
const DENSE_SCRIPT = new Set(['zh_CN', 'ja_JP', 'ko_KR']);

function prepare(canvas, mode, code) {
  const pad = 16;
  // Chinese/Japanese/Korean glyphs need to be big to read; other scripts read best near their drawn size and
  // are only enlarged when tiny (enlarging Thai banners made its vowel marks worse).
  const small = DENSE_SCRIPT.has(code) ? canvas.height < 160 && canvas.width < 1400 : canvas.height < 60;
  const scale = small ? 2 : 1;
  const o = document.createElement('canvas');
  o.width = canvas.width * scale + pad * 2;
  o.height = canvas.height * scale + pad * 2;
  const g = o.getContext('2d');
  g.fillStyle = mode === 'dark' ? '#000' : '#fff';
  g.fillRect(0, 0, o.width, o.height);
  g.drawImage(canvas, pad, pad, canvas.width * scale, canvas.height * scale);
  const d = g.getImageData(0, 0, o.width, o.height);
  for (let i = 0; i < d.data.length; i += 4) {
    const lum = 0.299 * d.data[i] + 0.587 * d.data[i + 1] + 0.114 * d.data[i + 2];
    d.data[i] = d.data[i + 1] = d.data[i + 2] = mode === 'dark' ? 255 - lum : lum;
  }
  g.putImageData(d, 0, 0);
  return o;
}

function thumbnail(canvas, maxW = 420, maxH = 120) {
  const s = Math.min(1, maxW / canvas.width, maxH / canvas.height);
  if (s === 1) return canvas.toDataURL('image/png');
  const t = document.createElement('canvas');
  t.width = Math.max(1, Math.round(canvas.width * s));
  t.height = Math.max(1, Math.round(canvas.height * s));
  t.getContext('2d').drawImage(canvas, 0, 0, t.width, t.height);
  return t.toDataURL('image/png');
}

// ---------------------------------------------------------------------------
// Recognition

const CJK = '\\p{sc=Han}\\p{sc=Hiragana}\\p{sc=Katakana}ー々・、。！？：（）「」';
const CJK_GAP = new RegExp(`(?<=[${CJK}])\\s+|\\s+(?=[${CJK}])`, 'gu');

/**
 * Tidies one OCR line. Stray letters from a symbol icon are left in: the comparison already ignores extra
 * text around a match and at [WILD]-style placeholders, while trimming "short unsure words" also cut real
 * ones (Korean "는", Bengali "করে").
 */
function cleanLine(line, code) {
  // Glow edges come out as " ------" or a stray " ," after the last word.
  let text = normalize(line.text).replace(/\s+[-_=~|,.]+$/, '').replace(/^[-_=~|,.]+\s+/, '');
  if (code === 'ja_JP' || code === 'zh_CN') text = text.replace(CJK_GAP, '');
  return { text, confidence: line.confidence };
}

/**
 * Reads one image both ways (see prepare) and returns the readings, most confident first. A confident
 * reading can still be wrong ("Unlock tree Came"), so cut-out lettering keeps both for the comparison.
 */
async function recognize(worker, canvas, code, whole) {
  // Sparse-text mode for whole artwork (text scattered among symbols), one text block for cut-out lettering
  // (automatic layout analysis loses long single-line banners).
  await worker.setParameters({ tessedit_pageseg_mode: whole ? '11' : '6' });
  // Whole artwork also has symbols ("A", "J 10", a "Wild" logo); keep only lines with a real word in them.
  const keep = (l) => (whole
    ? /[\p{L}\p{M}]{4,}/u.test(l.text) && l.confidence >= 70
    : /[\p{L}\p{N}]/u.test(l.text) && l.confidence >= 40);
  const readings = [];
  for (const mode of ['dark', 'light']) {
    const { data } = await worker.recognize(prepare(canvas, mode, code), {}, { text: true, blocks: true });
    const lines = data.lines?.length ? data.lines : data.text.split('\n').map((text) => ({ text, confidence: data.confidence }));
    readings.push({ confidence: data.confidence, lines: lines.map((l) => cleanLine(l, code)).filter(keep) });
    if (whole && data.confidence >= 80) break;
  }
  return readings.sort((a, b) => b.confidence - a.confidence);
}

/** One piece of lettering becomes one string even when it wraps over two lines. */
const regionText = (reading) => normalize(joinBrokenLines(reading.lines.map((l) => l.text).join('\n')));

const cache = new Map();

/**
 * OCRs every text region of a language's image assets. Returns lines like
 * { text, ocr: true, confidence, source: "maingame_lan › tipswin_word1", image: dataURL, href }.
 */
export async function readImageTexts(assets, code, { fetchText, onProgress = () => {} }) {
  const work = [];
  for (const asset of assets) {
    try {
      for (const { imageUrl, region } of await assetRegions(asset, fetchText)) {
        if (region.whole || (region.w >= 24 && region.h >= 12)) work.push({ asset, imageUrl, region });
      }
    } catch (err) {
      console.warn('skip asset', asset.url, err);
    }
  }
  const pending = work.filter((w) => !cache.has(`${w.imageUrl}#${w.region.name}`));
  let worker = null;
  const bitmaps = new Map();
  try {
    if (pending.length) {
      onProgress('載入 OCR…');
      const T = await loadTesseract();
      const lang = OCR_LANGS[code] || 'eng';
      worker = await T.createWorker(lang === 'eng' ? ['eng'] : [lang, 'eng']);
    }
    let done = 0;
    for (const w of pending) {
      onProgress(`OCR ${++done}/${pending.length}`);
      if (!bitmaps.has(w.imageUrl)) bitmaps.set(w.imageUrl, await loadBitmap(w.imageUrl));
      const img = bitmaps.get(w.imageUrl);
      let canvas = crop(img, w.region);
      let readings = await recognize(worker, canvas, code, w.region.whole);
      // A wrongly turned region still reads as confident gibberish now and then, so check the other way.
      if (w.region.rotated && readings[0].confidence < 80) {
        const other = crop(img, w.region, -(w.region.turn ?? -1));
        const second = await recognize(worker, other, code, false);
        if (second[0].confidence > readings[0].confidence) { readings = second; canvas = other; }
      }
      let lines;
      if (w.region.whole) {
        lines = readings[0].lines.map((l) => ({ text: l.text, confidence: l.confidence, alts: [] }));
      } else {
        const texts = [...new Set(readings.map(regionText).filter(Boolean))];
        lines = texts.length ? [{ text: texts[0], confidence: readings[0].confidence, alts: texts.slice(1) }] : [];
      }
      cache.set(`${w.imageUrl}#${w.region.name}`, { lines, image: thumbnail(canvas) });
    }
  } finally {
    if (worker) await worker.terminate();
  }

  const out = [];
  const byText = new Map();
  for (const w of work) {
    const hit = cache.get(`${w.imageUrl}#${w.region.name}`);
    if (!hit) continue;
    const where = w.region.name ? `${w.asset.name} › ${w.region.name}` : w.asset.name;
    for (const l of hit.lines) {
      const same = byText.get(l.text);
      if (same) { same.also.push(where); continue; } // e.g. the glowing and plain versions of one word
      const line = {
        text: l.text, alts: l.alts, ocr: true, confidence: Math.round(l.confidence),
        source: where, also: [], image: hit.image, href: w.imageUrl, kind: 'image',
      };
      byText.set(l.text, line);
      out.push(line);
    }
  }
  return out;
}
