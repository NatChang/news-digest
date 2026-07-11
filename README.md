# news-digest

一個 Claude Code / Claude skill：從一組**公開 RSS feed** 抓取近期文章，依分類（投資 / IT 產品 / 技術）整理成帶原文連結的重點列表，英文標題會由 Claude 翻成繁體中文。

- 純用系統 `curl` 抓公開 RSS，**不需任何 API key、不需登入**。
- `fetch_feeds.py` 只依賴 **Python 3 標準庫**。
- 不上傳任何本機資料，輸出只有抓回來的公開新聞標題與連結。

## 需求

- `python3`（3.7+）
- 系統有 `curl`（macOS / 多數 Linux 內建）

## 安裝

把整個 `news-digest/` 目錄放到你的 skills 目錄底下，例如：

```
~/.claude/skills/news-digest/
├── SKILL.md
├── fetch_feeds.py
└── README.md
```

之後在 Claude 對話輸入 `/news-digest` 即可觸發（例如 `/news-digest 最近三天 投資`）。

## 直接跑腳本（不透過 Claude）

```bash
python3 fetch_feeds.py --days 1 --category invest
python3 fetch_feeds.py --days 7 --category all --format json
```

| 參數 | 說明 | 預設 |
|------|------|------|
| `--days N` | 只收最近 N 天內發布的文章 | `1` |
| `--category` | `invest` / `itproduct` / `tech` / `all`（也吃中文別名：投資、產品、技術…） | `all` |
| `--limit N` | 每個來源最多幾則 | `20` |
| `--format` | `md` 或 `json` | `md` |

輸出的 Markdown 中，標了 `[EN→需翻譯]` 的是英文標題，交給 Claude 這一步會翻成中文；直接跑腳本則會保留標記。

## 加入 / 修改 feed 來源 ⭐

所有來源都集中在 `fetch_feeds.py` 最上方的 `FEEDS` 清單。每一筆是：

```python
("來源顯示名稱", "RSS_URL", [分類...]),
```

- **來源顯示名稱**：會顯示在輸出的 `### 標題` 上，隨你取。
- **RSS_URL**：該來源的 RSS / Atom feed 網址（不是網站首頁）。
- **分類清單**：一或多個，值只能是 `"invest"`、`"itproduct"`、`"tech"`。同一個 feed 可同時屬於多個分類。

範例——新增一個科技來源、並把某來源同時歸到投資與技術：

```python
FEEDS = [
    # ...既有項目...
    ("Hacker News",   "https://news.ycombinator.com/rss",   ["tech"]),
    ("Ars Technica",  "https://feeds.arstechnica.com/arstechnica/index", ["itproduct", "tech"]),
    ("某財經科技站",  "https://example.com/feed",            ["invest", "tech"]),
]
```

改完存檔即可，不需重新安裝。

### 怎麼找一個網站的 RSS 網址

- 很多網站在頁尾或側欄有「RSS / 訂閱」連結。
- 常見路徑可直接試：`/rss`、`/feed`、`/rss.xml`、`/atom.xml`、`/index.xml`。
- 用瀏覽器打開該網址，若看到一堆 `<item>` / `<entry>` 的 XML，就是對的。
- 腳本同時支援 **RSS** 與 **Atom** 格式，貼哪種都行。

### 換掉整組來源（換成非中文 / 其他主題）

預設 `FEEDS` 是**台灣中文使用者**的清單（風傳媒、鉅亨網、iThome…）。你完全可以清空重填成自己的來源，分類鍵（`invest` / `itproduct` / `tech`）與分類標題文字也可依需求在 `CATEGORY_LABELS` 調整。

## 來源特定的網址修正（進階）

有些 RSS 給的文章連結會導到 404，需要改寫成可連的格式。範例見 `fetch_feeds.py` 裡的 `normalize_link()`——目前處理風傳媒（`storm.mg/{id}?...` → `storm.mg/article/{id}`）。若你的來源也有類似狀況，可在該函式比照增加規則；沒有的話不用理它。

## 運作方式

1. 逐一 `curl` 抓每個 feed 的 RSS/Atom。
2. 解析標題、連結、發布時間，濾掉超過 `--days` 天的舊文。
3. 依分類、來源分組，輸出 Markdown（或 JSON）。
4. （透過 Claude 使用時）Claude 再把英文標題翻成中文、合併重複事件、點出當期趨勢。

某個來源臨時抓不到（網路問題或被擋）時，腳本會略過它並繼續處理其他來源，不會整個中斷。
