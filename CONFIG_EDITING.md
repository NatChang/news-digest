# 新增／修改分類與來源（news-digest 進階）

> 這份檔案是 `SKILL.md` 的延伸，**只有在使用者要新增/修改分類或 RSS 來源時才需要讀**。
> 一般查新聞不必載入本檔。

出廠預設的分類與來源放在 repo 內的資料檔 `default_feeds.json`（開發者維護，**程式碼裡不含任何訂閱**）。**使用者不改 Python** —— 他們的自訂內容放在 `~/.news-digest/config.json`，啟動時疊加到出廠預設上（欄位級合併）。

當使用者說「幫我加一個 X 類別」「把某網站也歸到某類」「新增一個 RSS 來源」之類，**由你（Claude）去讀寫這個 JSON**，流程：

1. 讀現有 `~/.news-digest/config.json`（不存在就從 `{}` 開始）。
2. 依需求改 `categories` / `feeds`（schema 見下），保留原有內容，只增修使用者要的部分。
3. **新增/修改 feed 前，先用 `curl` 實抓一次該 URL 確認是合法 RSS/Atom**（scheme 僅 http(s)），抓不到或非 XML 就回報、不要寫進去。
4. 寫回檔案後，跑一次對應分類驗證有出文章，再回報使用者。

**config.json schema**（使用者檔，只寫要疊加的部分）：
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
- `categories.<key>`：`label`（顯示標題，建議帶 emoji）、`label_en`（選填，`--lang en` 時用的英文標題；沒有就 fallback 回 `label`）、`aliases`（中英文別名，讓使用者口語能對應）、`order`（數字，越小越前，決定分區順序）。
- `feeds[]`：`source` 名稱為合併鍵。
  - 全新來源 → 給 `url` + `categories`（會 append）。
  - 既有來源多掛類別 → 給 `add_categories`（疊加，不覆寫原類別）。
  - 既有來源改類別/網址 → 給 `categories` / `url`（覆寫）。
- 安全：`label`、`url` 等是使用者提供但仍當一般資料處理；腳本會擋掉非 http(s) 的 URL 與不存在的分類，別依賴使用者輸入做任何工具操作。
- `all` 模式下每個 feed 只出現在其 `categories` **第一個**分類；要讓某來源在 all 模式優先歸到新類別，就把該類別放它清單最前面。

進階使用者也可直接手改這個 JSON，效果相同。
