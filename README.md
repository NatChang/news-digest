# news-digest

從一組**公開 RSS feed** 抓近期新聞，依分類（投資 / IT 產品 / 技術）整理成帶原文連結的重點列表；英文標題會由 Claude 翻成繁體中文。純用系統 `curl`，**不需 API key、不需登入**，`fetch_feeds.py` 只依賴 Python 3 標準庫，不上傳任何本機資料。

> 在 Claude 對話輸入 `/news-digest` 即可使用，例如 `/news-digest 最近三天 投資`。
>
> **給 Claude 的操作指令**（自然語言參數怎麼解析、去重規則、輸出格式與翻譯步驟）都寫在 [`SKILL.md`](./SKILL.md)（唯一生效檔；英文對照見 [`SKILL.en.md`](./SKILL.en.md)，僅供閱讀、runtime 不載入）。本檔只寫**人**需要知道的：安裝、直接跑 CLI、以及怎麼改來源。

## 需求與安裝

- `python3`（3.7+）、系統要有 `curl`（macOS / 多數 Linux 內建）。
- 把整個 `news-digest/` 目錄放到 skills 目錄底下，含 `SKILL.md`、`fetch_feeds.py`、`README.md`：

  ```
  ~/.claude/skills/news-digest/
  ```

## 直接跑腳本（不透過 Claude）

```bash
python3 fetch_feeds.py --days 1 --category invest
python3 fetch_feeds.py --days 7 --category all --format json
```

| 參數 | 說明 | 預設 |
|------|------|------|
| `--days N` | 只收最近 N 天內發布的文章 | `1` |
| `--category` | `invest` / `itproduct` / `tech` / `all` ＋任何自訂分類（也吃中英文別名：投資、產品、技術…） | `all` |
| `--date YYYY-MM-DD` | 只看某一本地日曆日（覆寫 `--days`） | 關閉 |
| `--since YYYY-MM-DD` | 只看該日（含）之後（覆寫 `--days`） | 關閉 |
| `--until YYYY-MM-DD` | 只看該日（含）之前（覆寫 `--days`） | 關閉 |
| `--limit N` | 每個來源最多幾則 | `20` |
| `--lang` | 輸出語言 `zh` / `en`（框架文字與翻譯標記方向；標題翻譯由 Claude 完成）| `zh` |
| `--format` | `md` 或 `json` | `md` |
| `--auto-unseen` | 當天第一次跑列全部，同一天再跑只列新的；隔天回到全部 | 關閉 |
| `--unseen` | 一律只列先前沒顯示過的文章（連當天第一次也過濾） | 關閉 |
| `--state PATH` | 已看清單存放位置 | `~/.news-digest/seen.json` |
| `--config PATH` | 使用者自訂分類/來源設定檔 | `~/.news-digest/config.json` |

**指定日期**：`--date`／`--since`／`--until` 以本地日曆日過濾，會覆寫 `--days`。注意 RSS 只提供最新的少量文章，**只有日期還落在來源當前視窗內（通常最近幾天）才抓得到**，較舊的日期沒有資料源可回溯。

**去重機制**：加了 `--auto-unseen` 或 `--unseen` 時，每次會把列出的文章連結記到 `~/.news-digest/seen.json`，之後跳過已記住的。這個檔在**家目錄、不在 repo 裡**（不會被 commit），只存連結與時間、保留近 14 天後自動修剪，不會無限膨脹。想重置已看記錄就刪掉它。Claude 透過此 skill 呼叫時**預設帶 `--unseen`**（永遠只列沒看過的）；使用者說「全部／重看」時才改列完整清單。兩種模式的差別見 `SKILL.md`。

直接跑腳本時，英文標題會保留 `[EN→需翻譯]` 標記（透過 Claude 使用才會翻成中文）。

## 加入 / 修改 分類與 feed 來源 ⭐

分兩層：**內建預設**（開發者維護）＋**使用者自訂**（你自己的）。啟動時後者疊加到前者，欄位級合併。

### 使用者自訂（推薦，不用碰 Python）

> 第一次跑**不需要**這個檔 —— 內建 11 個來源開箱即用。想自訂時才建。
> repo 附了範例 `config.example.json`，直接複製來改最快：
> ```bash
> cp config.example.json ~/.news-digest/config.json
> ```

在 `~/.news-digest/config.json` 寫你要新增/覆寫的部分即可（檔案不存在就自己建；跟 `seen.json` 同目錄）：

```json
{
  "categories": {
    "ai": { "label": "🧠 AI 動態", "aliases": ["AI", "人工智慧", "生成式"], "order": 25 }
  },
  "feeds": [
    { "source": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "categories": ["ai", "tech"] },
    { "source": "iThome", "add_categories": ["ai"] }
  ]
}
```

- `categories.<鍵>`：`label` 顯示標題（建議帶 emoji）、`label_en` 選填（`--lang en` 時用；沒有則 fallback 回 `label`）、`aliases` 中英文別名（讓口語能對應）、`order` 排序數字（越小越前）。
- `feeds[]` 以 `source` 名稱為合併鍵：
  - **全新來源** → 給 `url` + `categories`（會 append）。
  - **既有來源多掛類別** → 給 `add_categories`（疊加，不覆寫原有類別）。
  - **既有來源改類別/網址** → 給 `categories` / `url`（覆寫）。
- 腳本會自動擋掉非 http(s) 的 URL 與不存在的分類，改完存檔即生效、不需重裝。
- 更省事：直接跟 Claude 說「幫我加一個 AI 類別，來源用這兩個網址」，它會替你讀寫這個 JSON 並先驗證來源抓得到。

### 出廠預設（開發者）

想改「出廠預設」的分類與來源（clone 出去會帶的那組），改 repo 內的 `default_feeds.json`（結構與上面的 user config 相同）。**`fetch_feeds.py` 裡不含任何訂閱**，純邏輯。一般使用者不需要動它，直接用 `~/.news-digest/config.json` 疊加自己的即可。

**怎麼找一個網站的 RSS 網址**：頁尾/側欄常有「RSS / 訂閱」連結；或直接試 `/rss`、`/feed`、`/rss.xml`、`/atom.xml`、`/index.xml`。用瀏覽器打開，看到一堆 `<item>` / `<entry>` 的 XML 就對了。RSS 與 Atom 都支援。

## 改動後請跑安全性測試 ⚠️

**動過 `fetch_feeds.py` 或 `saved.py` 就跑一次**（零依賴、不連網、幾秒鐘）：

```bash
python3 test_security.py     # 有防線被破壞會以非零狀態結束
```

feed 給的**標題、連結、XML 本身都是攻擊者可控的**，而它們最後會變成你會點的 Markdown、以及 Claude 讀進去的上下文。`test_security.py` 拿惡意輸入去打真正的函式，驗證這些防線還在：DTD／實體爆炸與 XXE 一律拒收、標題不能偽造標題列或連結、`javascript:`／`data:`／`file:` 不會變成可點連結、連結裡的 `)` 與空白會被編碼（否則會提前關掉 `(url)`，讓後面的殘字變成注入連結）、`mute` 只做子字串比對而不是 regex。

會有這個檔，是因為**光用讀的不夠**：連結消毒的規則曾經在兩支腳本間悄悄分岔（一邊編碼了括號與空白，另一邊只編碼括號），把 `md_safe_title` 本來要堵的注入洞重新打開；那個 bug 讀 code 沒看出來，寫測試去打它才現形。

## 進階：來源特定的網址修正

有些 RSS 給的文章連結會導到 404，需要改寫。範例見 `fetch_feeds.py` 的 `normalize_link()`——目前處理風傳媒（`storm.mg/{id}?...` → `storm.mg/article/{id}`）。你的來源若有類似狀況，可比照在該函式加規則；沒有就不用理它。

## 運作方式

1. 逐一 `curl` 抓每個 feed 的 RSS/Atom。
2. 解析標題、連結、發布時間，濾掉超過 `--days` 天的舊文（某來源臨時抓不到會略過，不中斷其他）。
3. 依分類、來源分組，輸出 Markdown 或 JSON。
4. 透過 Claude 使用時，再由 Claude 翻譯英文標題、合併重複事件、點出當期趨勢（詳見 `SKILL.md`）。

## 使用範圍

僅抓公開 RSS 的標題與連結、供**個人閱讀**用，輸出都附回原文連結。若要公開轉載翻譯後的摘要，請保留原文連結、避免整段複製內文，並留意各來源的授權條款。
