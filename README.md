# news-digest

從一組**公開 RSS feed** 抓近期新聞，依分類（投資 / IT 產品 / 技術）整理成帶原文連結的重點列表；英文標題會由 Claude 翻成繁體中文。純用系統 `curl`，**不需 API key、不需登入**，`fetch_feeds.py` 只依賴 Python 3 標準庫，不上傳任何本機資料。

> 在 Claude 對話輸入 `/news-digest` 即可使用，例如 `/news-digest 最近三天 投資`。
>
> **給 Claude 的操作指令**（自然語言參數怎麼解析、去重規則、輸出格式與翻譯步驟）都寫在 [`SKILL.md`](./SKILL.md)。本檔只寫**人**需要知道的：安裝、直接跑 CLI、以及怎麼改來源。

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
| `--category` | `invest` / `itproduct` / `tech` / `all`（也吃中文別名：投資、產品、技術…） | `all` |
| `--limit N` | 每個來源最多幾則 | `20` |
| `--format` | `md` 或 `json` | `md` |
| `--auto-unseen` | 當天第一次跑列全部，同一天再跑只列新的；隔天回到全部 | 關閉 |
| `--unseen` | 一律只列先前沒顯示過的文章（連當天第一次也過濾） | 關閉 |
| `--state PATH` | 已看清單存放位置 | `~/.news-digest/seen.json` |

**去重機制**：加了 `--auto-unseen` 或 `--unseen` 時，每次會把列出的文章連結記到 `~/.news-digest/seen.json`，之後跳過已記住的。這個檔在**家目錄、不在 repo 裡**（不會被 commit），只存連結與時間、保留近 14 天後自動修剪，不會無限膨脹。想重置已看記錄就刪掉它。兩種模式的差別與 Claude 何時自動帶 `--auto-unseen`，見 `SKILL.md`。

直接跑腳本時，英文標題會保留 `[EN→需翻譯]` 標記（透過 Claude 使用才會翻成中文）。

## 加入 / 修改 feed 來源 ⭐

所有來源集中在 `fetch_feeds.py` 最上方的 `FEEDS` 清單，每一筆是：

```python
("來源顯示名稱", "RSS_URL", [分類...]),
```

- **來源顯示名稱**：顯示在輸出的 `### 標題` 上，隨你取。
- **RSS_URL**：該來源的 RSS / Atom feed 網址（不是網站首頁）。
- **分類清單**：一或多個，值只能是 `"invest"`、`"itproduct"`、`"tech"`；同一個 feed 可同時屬多個分類。

範例——新增科技來源、並把某來源同時歸到投資與技術：

```python
FEEDS = [
    # ...既有項目...
    ("Hacker News",   "https://news.ycombinator.com/rss",   ["tech"]),
    ("Ars Technica",  "https://feeds.arstechnica.com/arstechnica/index", ["itproduct", "tech"]),
    ("某財經科技站",  "https://example.com/feed",            ["invest", "tech"]),
]
```

改完存檔即可，不需重新安裝。分類鍵與分類標題文字可在 `CATEGORY_LABELS` 調整；要整組換成非中文/其他主題，清空 `FEEDS` 重填即可。

**怎麼找一個網站的 RSS 網址**：頁尾/側欄常有「RSS / 訂閱」連結；或直接試 `/rss`、`/feed`、`/rss.xml`、`/atom.xml`、`/index.xml`。用瀏覽器打開，看到一堆 `<item>` / `<entry>` 的 XML 就對了。RSS 與 Atom 都支援。

## 進階：來源特定的網址修正

有些 RSS 給的文章連結會導到 404，需要改寫。範例見 `fetch_feeds.py` 的 `normalize_link()`——目前處理風傳媒（`storm.mg/{id}?...` → `storm.mg/article/{id}`）。你的來源若有類似狀況，可比照在該函式加規則；沒有就不用理它。

## 運作方式

1. 逐一 `curl` 抓每個 feed 的 RSS/Atom。
2. 解析標題、連結、發布時間，濾掉超過 `--days` 天的舊文（某來源臨時抓不到會略過，不中斷其他）。
3. 依分類、來源分組，輸出 Markdown 或 JSON。
4. 透過 Claude 使用時，再由 Claude 翻譯英文標題、合併重複事件、點出當期趨勢（詳見 `SKILL.md`）。

## 使用範圍

僅抓公開 RSS 的標題與連結、供**個人閱讀**用，輸出都附回原文連結。若要公開轉載翻譯後的摘要，請保留原文連結、避免整段複製內文，並留意各來源的授權條款。
