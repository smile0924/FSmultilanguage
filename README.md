# FS 多語系比對工具（fslang）

以**翻譯 Excel 為標準答案**，檢查遊戲裡實際顯示的各語言文字是否正確。
第一個實作的遊戲是 **Tree of Wild**（Excel：`FS - Version 137-EUR (Verified)` 的 `FS - Tree of Wild` 工作表）。

工具做三件事：

| 指令 | 做什麼 | 需要連到遊戲？ |
| --- | --- | --- |
| `lint` | 檢查 Excel 翻譯本身：佔位符 `[WILD]`、關鍵字 `WILD`/`SCATTER`、數字、多餘空白、重複字… | 不用 |
| `collect` | 用瀏覽器（Playwright）打開遊戲，擷取每個語言畫面上的文字、遊戲載入的語系檔、截圖 | 要 |
| `compare` | 把擷取到的文字逐句和 Excel 比對，輸出彩色 Excel 報表 | 不用 |

`run` = `collect` + `compare`。

---

## 1. 安裝

需要 Python 3.9 以上。

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

（Windows 可以把 `python` 換成 `py`。）

## 2. 快速開始（Tree of Wild）

### 2-1 先檢查 Excel 本身

```bash
python -m fslang lint -c configs/tree_of_wild.json
```

報表會存成 `reports/tree_of_wild_lint_<時間>.xlsx`。

### 2-2 擷取遊戲文字

還不知道遊戲網址格式時，先用**互動模式**：

```bash
python -m fslang collect -c configs/tree_of_wild.json --interactive
```

1. 程式會開一個瀏覽器視窗，打開 `https://qa-go.object1.net/touch/fsnew/list.jsp`。
2. 在瀏覽器裡打開 Tree of Wild，切換到終端機提示的語言，**打開要檢查的畫面**（例如遊戲說明/規則頁）。
3. 回到終端機按 Enter 擷取。說明有好幾頁的話，翻頁後再按 Enter；換下一個語言輸入 `n`。
   跳過這個語言輸入 `s`，結束輸入 `q`。

只想擷取部分語言：`--langs th,ja,ko`

擷取結果存放在 `captures/tree_of_wild/`（`th.json`、`th_0_0.png`…）。
擷取時終端機會印出當下的頁面網址，知道網址格式後就可以填進設定檔，改用自動模式（見第 5 節）。

### 2-3 比對並產生報表

```bash
python -m fslang compare -c configs/tree_of_wild.json
```

主控台會顯示各語言的統計和有問題的句子，完整報表存成 `reports/tree_of_wild_<時間>.xlsx`：

| 工作表 | 內容 |
| --- | --- |
| 摘要 | 每個語言各種結果的數量、正確率 |
| 對照表 | 和翻譯 Excel 一樣的版面，儲存格依結果上色，滑鼠移上去看註解（遊戲實際文字、說明） |
| 明細 | 每一句、每個語言一列；**差異的字用紅字標出來**，可篩選 |
| Excel檢查 | `lint` 的結果 |

有任何「舊翻譯 / 語言錯誤 / 內容不符 / 找不到」時，指令結束碼為 1（方便放進自動化流程）。

### 2-4 先用示範遊戲試試看

`examples/demo_game/` 是一個本機假遊戲，用 Excel 的文字做成，並故意放了幾個錯誤：

```bash
python -m http.server 8765 -d examples/demo_game      # 開一個終端機跑著
python -m fslang run -c configs/demo_tree_of_wild.json
```

預期結果：泰文第 3 列「舊翻譯」、日文第 4 列「內容不符」、韓文第 18 列「語言錯誤」、
義大利文第 9 列「找不到」、西班牙文第 2 列「警告」、孟加拉語整個顯示成英文。

## 3. 判定結果

每一句、每個語言依序判斷：

| 結果 | 顏色 | 意思 |
| --- | --- | --- |
| 正確 | 綠 | 遊戲文字和 Excel 相同（不同寬度的空白、換行、零寬字元不算差異） |
| 警告(格式差異) | 黃 | 內容相同，但大小寫、全形/半形、空白、引號樣式不同 |
| 舊翻譯 | 紅 | 遊戲還在用被 `(revised)` 欄取代的舊翻譯 |
| 語言錯誤 | 紅 | 遊戲顯示的是同一句的其他語言（例如還是英文） |
| 內容不符 | 紅 | 找到很像的句子但內容不同（相似度 ≥ 60%），差異寫在「說明」：`「Excel」→「遊戲」` |
| 找不到 | 橘 | 遊戲文字中找不到這句 |
| 未擷取 | 灰 | 這個語言沒有擷取資料 |
| Excel無翻譯 | 淺灰 | Excel 這格是空的 |

### 佔位符

Excel 裡的 `[WILD]` 在遊戲中通常是圖示，`X`（「最高 X 倍」）是實際數值，所以比對時當作萬用字元：
`[WILD] climbs up every winning` 可以對到畫面上的 `(WILD 圖示) climbs up every winning` 或 `WILD climbs up every winning`；
`Win Multiplier up to X` 可以對到 `Win Multiplier up to x2000`。佔位符規則寫在設定檔的 `placeholders`。

### 修訂欄

表頭含 `(revised)` 的欄（例如 `TH (revised)`）是前一個語言欄的修訂版：
有填值就以修訂版為準，原本的欄位變成「舊翻譯」，遊戲還在用舊的就會被標成「舊翻譯」。

## 4. Excel 檢查（lint）規則

| 規則 | 等級 | 檢查內容 |
| --- | --- | --- |
| empty | 錯誤 | 有英文原文但沒有翻譯 |
| placeholder | 錯誤 | `[WILD]`、`X` 等佔位符數量和原文不同 |
| keyword | 錯誤/警告 | 原文的大寫關鍵字（WILD、SCATTER…）不見了、黏了其他字母（`WILDI`）、大小寫不同（`Wild`） |
| spacing | 警告 | 印地語、孟加拉語、俄語等：關鍵字和前後文字之間沒有空白（`एकWILD`） |
| number | 警告 | 原文中的數字在翻譯中不見了（`2,000`、`2.000`、`2 000` 視為相同） |
| repeat | 警告 | 相鄰重複的字（`the the`、泰文 `จะจะ`） |
| whitespace | 警告 | 開頭/結尾有空白、連續多個空白 |
| untranslated | 警告 | 翻譯和英文一模一樣 |

## 5. 設定檔（`configs/*.json`）

以 `configs/tree_of_wild.json` 為例，底線開頭的鍵是註解：

| 欄位 | 說明 |
| --- | --- |
| `excel` / `sheet` | 翻譯 Excel 與工作表（相對路徑以設定檔所在資料夾為準） |
| `languages` | Excel 表頭 → 語言代碼。代碼也是擷取檔檔名（`th.json`） |
| `revisions` | 修訂欄 → 被修訂的語言欄 |
| `placeholders` | 佔位符的正規表示式 |
| `keywords` | 指定要檢查的關鍵字；不填則自動抓原文中 3 個字母以上的全大寫單字 |
| `match.similarity_threshold` | 「內容不符」的相似度門檻（預設 0.6） |
| `collect.list_url` | 互動模式一開始打開的網址 |
| `collect.game_url` | 自動模式直接開遊戲的網址，可用 `{lang}`、`{game_lang}` |
| `collect.game_lang_codes` | 遊戲網址的語言代碼和 Excel 不同時使用，例如 `{"ja": "jp", "ko": "kr"}` |
| `collect.steps` | 自動模式開啟遊戲後要做的動作（見下方） |
| `collect.wait_ms` | 開啟遊戲後等待載入的毫秒數 |
| `collect.device` | 模擬手機，例如 `"iPhone 13"`；`null` 用 `viewport` 尺寸 |

### 自動模式的 steps

```json
"game_url": "https://qa-go.object1.net/.../game.jsp?id=xxx&lang={game_lang}",
"steps": [
  {"wait": 8000},
  {"click": "#btnInfo"},
  {"capture": "說明第1頁"},
  {"click": "text=Next"},
  {"capture": "說明第2頁"}
]
```

| 動作 | 例子 | 說明 |
| --- | --- | --- |
| `click` | `{"click": "#btnHelp"}`、`{"click": "text=Tree of Wild"}` | 點擊（Playwright 選擇器） |
| `click_xy` | `{"click_xy": [1180, 60]}` | 點座標（canvas 遊戲的按鈕） |
| `wait` / `wait_for` | `{"wait": 3000}`、`{"wait_for": "#help"}` | 等待時間 / 等元素出現 |
| `press` | `{"press": "Escape"}` | 按鍵 |
| `select` / `fill` | `{"select": "#lang", "value": "{game_lang}"}` | 下拉選單 / 輸入框 |
| `eval` | `{"eval": "game.setLang('{game_lang}')"}` | 執行 JavaScript |
| `goto` | `{"goto": "https://..."}` | 換頁 |
| `capture` | `{"capture": "help1"}` | 擷取一次（沒寫的話，最後擷取一次） |

任何動作都可以加 `"frame": "iframe#game"`（在 iframe 裡操作）、`"optional": true`（失敗不中斷）。
點擊後開出新分頁時，後面的動作會自動在新分頁進行。

## 6. 擷取的內容與其他資料來源

`collect` 每次擷取會記錄：

- 每個頁面、每個 iframe 畫面上的文字，以及隱藏中的文字區塊
- **canvas 遊戲**：如果是 Cocos Creator 或 LayaAir，會讀取引擎裡 Label 的文字
- 遊戲載入的 JSON / JS / HTML / XML / 文字檔裡的字串（多數遊戲的語系檔在這裡）
- 截圖（只用來人工確認，不參與比對）

也可以不用 `collect`，直接把檔案放進 `captures/<game_id>/`，檔名以語言代碼開頭即可，同一個語言的檔案會合併：

- `th.txt`、`th_help.txt`：從遊戲複製下來的文字
- `th.json`：遊戲的語系檔
- `th.html`：遊戲說明頁

> 文字如果是直接畫在圖片上（美術字），這個工具讀不到，需要人工看截圖確認。

## 7. 新增其他遊戲

複製 `configs/tree_of_wild.json`，修改 `game_id`、`game_name`、`sheet`（例如 `FS - Cat Gold Rush`），
再依遊戲調整 `collect`。不確定 Excel 的欄位時可以先看：

```bash
python -m fslang inspect data/FS_Version_137_EUR_Verified.xlsx
```

## 8. 專案結構

```
fslang/
  excel.py      讀取翻譯 Excel（語言欄、修訂欄）
  textnorm.py   文字正規化（strict / loose）
  extract.py    從 HTML / JSON / JS 取出字串
  corpus.py     遊戲文字語料（擷取檔、手動檔案）
  matcher.py    比對引擎與判定
  lint.py       Excel 翻譯檢查
  collector.py  Playwright 擷取
  report.py     Excel 報表
  cli.py        命令列
configs/        各遊戲設定
data/           翻譯 Excel
examples/demo_game/  示範用假遊戲
tests/          測試（pip install pytest 後執行 python -m pytest）
```
