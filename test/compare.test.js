import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {
  parseHeader, parseSheet, extractSegments, compareLanguage, alignInside, graphemes, findRepeats, detectLanguageFromUrl, sheetKey,
  joinBrokenLines, combineSheets,
} from '../public/compare.js';

const XLSX = vm.runInThisContext(`(function(){${fs.readFileSync(new URL('../public/vendor/xlsx.full.min.js', import.meta.url), 'utf8')}\n;return XLSX;})()`);

// The real translation workbook and paytable are company data kept out of the repository; the tests
// that use them are skipped when test/fixtures does not have them.
const EXCEL_FIXTURE = new URL('./fixtures/FS_Version_137.xlsx', import.meta.url);
const PAYTABLE_FIXTURE = new URL('./fixtures/paytable_th_TH.html', import.meta.url);
const noExcel = !fs.existsSync(EXCEL_FIXTURE) && '需要 test/fixtures/FS_Version_137.xlsx';
const noPaytable = (noExcel || !fs.existsSync(PAYTABLE_FIXTURE)) && '需要 test/fixtures 的 Excel 與 paytable_th_TH.html';

function fixtureSheet() {
  const wb = XLSX.read(fs.readFileSync(EXCEL_FIXTURE));
  return parseSheet(XLSX.utils.sheet_to_json(wb.Sheets['FS - Tree of Wild'], { header: 1, defval: '', raw: false }));
}

const sheetOf = (rows) => parseSheet([['EN', 'Thai', 'TH (revised)'], ...rows]);
const byRow = (out, n) => out.results.find((r) => r.rowNumber === n);

test('maps spreadsheet headers to language codes', () => {
  assert.deepEqual(parseHeader('Thai'), { code: 'th_TH', revised: false });
  assert.deepEqual(parseHeader('TH (revised)'), { code: 'th_TH', revised: true });
  assert.deepEqual(parseHeader('Hindi '), { code: 'hi_HI', revised: false });
  assert.deepEqual(parseHeader('Bangladesh'), { code: 'bn_BN', revised: false });
  assert.equal(parseHeader('Notes'), null);
  assert.deepEqual(parseHeader('印尼文'), { code: 'id_ID', revised: false });
  assert.deepEqual(parseHeader('泰文（修訂）'), { code: 'th_TH', revised: true });
  assert.deepEqual(parseHeader('zh-CN'), { code: 'zh_CN', revised: false });
});

test('reads the Tree of Wild sheet with the revised Thai column', { skip: noExcel }, () => {
  const sheet = fixtureSheet();
  assert.equal(sheet.rows.length, 23);
  assert.equal(sheet.languages.length, 12);
  const row3 = sheet.rows.find((r) => r.rowNumber === 3);
  assert.equal(row3.cells.th_TH.original, 'รับหมุนฟรีที่ด้านบนและเพิ่มตัวคูณทุกครั้งที่กระโดด!');
  assert.equal(row3.cells.th_TH.revised, 'รับฟรีสปินด้านบนและเพิ่มตัวคูณทุกครั้งที่กระโดด!');
});

test('splits paytable HTML into lines, using empty <i></i> as a line break', () => {
  const html = `<div><h2 data="7">Main Game</h2><p data="16">First line.
      <i></i>
      Second <span>line</span>.</p><ul><li><p><span>5</span><b class="odds"></b></p></li></ul></div>`;
  assert.deepEqual(extractSegments(html), ['Main Game', 'First line.', 'Second line.', '5']);
});

test('real Thai paytable matches the revised Excel column', { skip: noPaytable }, () => {
  const sheet = fixtureSheet();
  const html = fs.readFileSync(PAYTABLE_FIXTURE, 'utf8');
  const out = compareLanguage(sheet, 'th_TH', extractSegments(html));
  assert.equal(out.counts.match, 18);
  assert.equal(out.counts.diff + out.counts.outdated + out.counts.minor, 0);
  assert.deepEqual(out.results.filter((r) => r.status === 'missing').map((r) => r.rowNumber), [2, 3, 4, 5, 6]);
  assert.equal(byRow(out, 9).source, 'revised');
  assert.ok(byRow(out, 11).notes.some((n) => n.includes('จะจะ')));
});

test('flags a page that still shows the original translation instead of the revision', () => {
  const sheet = sheetOf([['Free Spins', 'หมุนฟรี', 'ฟรีสปิน']]);
  const out = compareLanguage(sheet, 'th_TH', ['หมุนฟรี']);
  assert.equal(out.results[0].status, 'outdated');
  assert.equal(out.lineResults[0].status, 'outdated');
});

test('sentences are compared whole: a phrase inside a longer line is not a match', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['Free Spins', 'หมุนฟรี'], ['WILD', 'WILD']]);
  const out = compareLanguage(sheet, 'th_TH', ['ได้รับ หมุนฟรี 10 ครั้ง', 'สัญลักษณ์ WILD จะคงอยู่บนรีล']);
  assert.deepEqual(out.results.map((r) => r.status), ['missing', 'missing']);
  assert.deepEqual(out.lineResults, [null, null]);
});

test('two Excel rows in one game line, and one Excel row over two game lines, still match', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['A', 'ทิศทางการปีนเป็นแบบสุ่ม'], ['B', 'เส้นทางผสมจะได้ค่าตัวคูณ'], ['C', 'จ่าย x[mul] ของเงินเดิมพันและเข้าสู่ฟรีเกมทันที!']]);
  const out = compareLanguage(sheet, 'th_TH', [
    { text: 'ทิศทางการปีนเป็นแบบสุ่ม เส้นทางผสมจะได้ค่าตัวคูณ', source: 'p' },
    { text: 'จ่าย x[mul] ของเงินเดิมพันและเข้าสู่', source: 'p' },
    { text: 'ฟรีเกมทันที!', source: 'p' },
  ]);
  assert.deepEqual(out.results.map((r) => r.status), ['match', 'match', 'match']);
  assert.deepEqual(out.lineRows, [[2, 3], [4], [4]]);
});

test('the main Excel file answers first; other files only fill in what it lacks', () => {
  const main = parseSheet([['EN', 'Thai'], ['Free Spins', 'ฟรีสปินน'], ['Start', 'เริ่ม']]);
  const other = parseSheet([['EN', 'Thai'], ['Free Spins', 'ฟรีสปิน'], ['Loading', 'กำลังโหลด']]);
  const sheet = combineSheets([{ sheet: other, file: 'other.xlsx', sheetName: 'S' }, { sheet: main, file: 'main.xlsx', sheetName: 'S' }]);
  const out = compareLanguage(sheet, 'th_TH', ['ฟรีสปิน', 'เริ่ม', 'กำลังโหลด'], { priority: ['main.xlsx', 'other.xlsx'] });
  const from = (i) => out.results.find((r) => r.id === out.lineRows[i][0]);
  // The main file's near miss wins over the other file's exact line: the main file is the answer key.
  assert.equal(from(0).origin.file, 'main.xlsx');
  assert.equal(out.lineResults[0].status, 'diff');
  assert.equal(from(1).origin.file, 'main.xlsx');
  // The main file has no "Loading", so the other file answers it.
  assert.equal(from(2).origin.file, 'other.xlsx');
  assert.equal(out.lineResults[2].status, 'match');
});

test('typos are reported with the differing text', () => {
  const sheet = sheetOf([['x', 'สัญลักษณ์ WILD จะเลื่อนขึ้นหนึ่งตำแหน่ง', '']]);
  const out = compareLanguage(sheet, 'th_TH', ['สัญลักษณ์ WILD จะเลือนขึ้นหนึ่งตำแหน่ง']);
  const r = out.results[0];
  assert.equal(r.status, 'diff');
  assert.ok(r.similarity > 0.9);
  assert.deepEqual(r.expectedOps.filter(([t]) => t === '-'), [['-', 'ลื่']]);
  assert.deepEqual(r.page.ops.filter(([t]) => t === '+'), [['+', 'ลื']]);
});

test('spacing and punctuation-width differences are minor', () => {
  const sheet = parseSheet([['EN', 'French'], ['x', 'déterminée aléatoirement : verticalement'], ['y', 'Débloquez！']]);
  const out = compareLanguage(sheet, 'fr_FR', ['déterminée aléatoirement: verticalement', 'Débloquez!']);
  assert.deepEqual(out.results.map((r) => r.status), ['minor', 'minor']);
});

test('a short line is not matched to a fragment of an unrelated sentence', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['Unlock Free Game', '']]);
  const out = compareLanguage(sheet, 'en_US', ['In the Free Game, there are 2,916 ways to win.']);
  assert.equal(out.results[0].status, 'missing');
});

test('a whole short line with a changed word is a mismatch, not missing', () => {
  const sheet = parseSheet([['EN', 'Japan'], ['Santa WILD Climb', 'サンタWILDクライム']]);
  const out = compareLanguage(sheet, 'ja_JP', ['サンタワイルドクライム']);
  assert.equal(out.results[0].status, 'diff');
});

test('thousands separators get an explanatory note', () => {
  const sheet = parseSheet([['EN', 'Spain'], ['x', 'En el juego principal, hay 2.000 formas de ganar']]);
  const r = compareLanguage(sheet, 'es_ES', ['En el juego principal, hay 2,000 formas de ganar']).results[0];
  assert.equal(r.status, 'diff');
  assert.ok(r.notes.some((n) => n.includes('千分位')));
});

test('bracketed placeholders match any short text', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['[WILD] climbs up', '[WILD] ปีนขึ้น']]);
  assert.equal(compareLanguage(sheet, 'th_TH', ['WILD ปีนขึ้น']).results[0].status, 'match');
  assert.equal(compareLanguage(sheet, 'th_TH', ['WILD ปีนขึ้น'], { placeholders: false }).results[0].status, 'diff');
});

test('page lines the sheet does not list are reported once', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['Tier 1', 'ระดับ 1']]);
  const out = compareLanguage(sheet, 'th_TH', ['ระดับ 1', 'สองครั้ง', '+2x', 'สองครั้ง']);
  assert.deepEqual(out.extraLines.map((l) => [l.text, l.count]), [['สองครั้ง', 2]]);
});

test('alignment keeps Thai vowel marks with their consonant', () => {
  const a = alignInside(graphemes('ที่สุด'), graphemes('xxที่สุดyy'));
  assert.equal(a.dist, 0);
  assert.equal(a.start, 2);
});

test('finds doubled words', () => {
  assert.deepEqual(findRepeats('สัญลักษณ์ WILD จะจะเลื่อนขึ้น', 'th_TH'), ['จะจะ']);
  assert.deepEqual(findRepeats('the the reel', 'en_US'), ['thethe']);
  assert.deepEqual(findRepeats('2 2 reels', 'en_US'), []);
});

test('image lettering matches ignoring case and spacing', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['Unlock Free Game', 'ปลดล็อกเกมฟรี'], ['Tier 1', 'ระดับ 1']]);
  const out = compareLanguage(sheet, 'en_US', [{ text: 'UNLOCK FREE GAME', ocr: true }, { text: 'Tier1', ocr: true }]);
  assert.deepEqual(out.results.map((r) => r.status), ['match', 'match']);
  assert.equal(out.results[0].page.ocr, true);
  assert.deepEqual(out.lineRows, [[2], [3]]);
});

test('an icon read as stray letters at a [WILD] placeholder still matches', () => {
  const sheet = parseSheet([['EN', 'ITALY'], ['[WILD] climbs up', 'Il [WILD] scala ogni vittoria']]);
  assert.equal(compareLanguage(sheet, 'it_IT', [{ text: 'Il e scala ogni vittoria', ocr: true }]).results[0].status, 'match');
  assert.equal(compareLanguage(sheet, 'it_IT', [{ text: 'PS Il scala ogni vittoria', ocr: true }]).results[0].status, 'match');
});

test('OCR look-alikes are not reported as differences', () => {
  const sheet = parseSheet([['EN', 'Russia'], ['x', 'Множитель выигрыша до X'], ['y', 'Win Multiplier up to X']]);
  const out = compareLanguage(sheet, 'ru_RU', [{ text: 'Множитель выигрыша до Х', ocr: true }]);
  assert.equal(out.results[0].status, 'match');
  const en = parseSheet([['EN', 'Thai'], ['increases Multiplier with every hop!', '']]);
  assert.equal(compareLanguage(en, 'en_US', [{ text: 'increases MUItiplier with every hop!', ocr: true }]).results[0].status, 'match');
});

test('a near miss in image text asks for a visual check; another reading of the image can still match', () => {
  const sheet = parseSheet([['EN', 'Thai'], ['Unlock Free Game', '']]);
  assert.equal(compareLanguage(sheet, 'en_US', [{ text: 'Unlock tree Came', ocr: true }]).results[0].status, 'check');
  const r = compareLanguage(sheet, 'en_US', [{ text: 'Unlock tree Came', alts: ['UNLOCK FREE GAME'], ocr: true }]).results[0];
  assert.equal(r.status, 'match');
  assert.ok(r.notes.some((n) => n.includes('另一種')));
});

test('image lettering still showing the original translation is outdated', () => {
  const sheet = sheetOf([['Free Spins', 'หมุนฟรี 10 ครั้ง', 'ฟรีสปิน 10 ครั้ง']]);
  assert.equal(compareLanguage(sheet, 'th_TH', [{ text: 'หมุนฟรี 10 ครั้ง', ocr: true }]).results[0].status, 'outdated');
});

test('strings files are joined without spaces only inside Thai/Japanese words', () => {
  assert.equal(joinBrokenLines('แทนที่สำหรับ\nสัญลักษณ์ทั้ง\nหมด'), 'แทนที่สำหรับสัญลักษณ์ทั้งหมด');
  assert.equal(joinBrokenLines('PENGGANTI UNTUK\nSEMUA SIMBOL.'), 'PENGGANTI UNTUK SEMUA SIMBOL.');
  assert.equal(joinBrokenLines('Simbol WILD \nmenggantikan'), 'Simbol WILD menggantikan');
});

test('several Excel sheets combine into one reference', () => {
  const a = parseSheet([['EN', 'Thai'], ['Free Spins', 'ฟรีสปิน'], ['Tier 1', 'ระดับ 1']]);
  const b = parseSheet([['EN', 'Thai', 'Japan'], ['Tier 1', 'ระดับ 1'], ['Free Spins', 'หมุนฟรี', 'フリースピン'], ['Buy', 'ซื้อ', '購入']]);
  const sheet = combineSheets([{ sheet: a, file: 'v137.xlsx', sheetName: 'FS - Tree of Wild' }, { sheet: b, file: 'v136.xlsx', sheetName: 'Tree of Wild' }]);
  assert.equal(sheet.rows.length, 5);
  assert.deepEqual(sheet.languages, ['en_US', 'th_TH', 'ja_JP']);

  const out = compareLanguage(sheet, 'th_TH', ['ฟรีสปิน', 'ระดับ 1', 'ซื้อ']);
  // "Tier 1" has the same Thai in both files, so it is compared once and the second place is listed.
  // v136's other "Free Spins" translation is not missing: the game line was answered by v137's row.
  assert.deepEqual(out.results.map((r) => [r.origin.file, r.rowNumber, r.status]), [
    ['v137.xlsx', 2, 'match'], ['v137.xlsx', 3, 'match'], ['v136.xlsx', 3, 'covered'], ['v136.xlsx', 4, 'match'],
  ]);
  assert.deepEqual(out.results[1].alsoIn, [{ file: 'v136.xlsx', sheetName: 'Tree of Wild', rowNumber: 2 }]);
  // "Free Spins" is translated differently in the two files: both rows say so.
  assert.ok(out.results[0].notes.some((n) => n.includes('หมุนฟรี') && n.includes('v136.xlsx')));
  assert.ok(out.results[2].notes.some((n) => n.includes('ฟรีสปิน') && n.includes('v137.xlsx')));
  assert.deepEqual(out.lineRows, [['0:2'], ['0:3'], ['1:4']]);
  // Japanese only exists in the second file.
  assert.deepEqual(compareLanguage(sheet, 'ja_JP', ['購入']).results.map((r) => r.origin.file), ['v136.xlsx', 'v136.xlsx']);
});

test('detects the language from page URLs and file names', () => {
  assert.equal(detectLanguageFromUrl('https://x/assets/paytable_th_TH.ba9e9fcd.html'), 'th_TH');
  assert.equal(detectLanguageFromUrl('paytable_pt_PT.html'), 'pt_PT');
  assert.equal(detectLanguageFromUrl('https://x/list.jsp'), null);
});

test('matches sheet names to games', () => {
  assert.equal(sheetKey('FS - Tree of Wild'), sheetKey('Tree of Wild'));
  assert.equal(sheetKey('FS - Cat Gold Rush'), 'catgoldrush');
});
