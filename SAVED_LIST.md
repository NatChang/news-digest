# 稍後再讀：待讀清單操作（news-digest 進階）

> 這份檔案是 `SKILL.md` 的延伸，**只有使用者要存文章／看待讀清單／標記已讀／刪除時才需要讀**。
> 一般查新聞不必載入本檔。

摘要的 `--unseen` 去重會讓一篇文章**只出現一次**，之後永遠不再出現。所以使用者想「留著晚點看」的文章必須另存 —— 用同目錄的 **`saved.py`**，狀態放 `~/.news-digest/saved.json`（與 `seen.json` 無關，**不會 14 天自動清掉**，只有使用者說丟才丟）。

| 使用者說 | 你做 |
| --- | --- |
| 「這篇存起來 / 稍後再讀 / 待讀 / 加到清單」 | `saved.py add`（見下） |
| 「我的待讀清單 / 有什麼還沒看」 | `python3 saved.py list` |
| 「第 2 篇看完了 / 這篇讀完了」 | `python3 saved.py done 2` |
| 「把第 3 篇刪掉 / 不看了」 | `python3 saved.py drop 3` |
| 「清掉讀過的」 | `python3 saved.py purge` |

**存文章**：使用者通常用自然語言指某則（「把 Samsung Health 那則存起來」「投資那三則都存」）。**你**負責從剛剛輸出的摘要裡比對標題、找出對應的連結與 metadata，再呼叫：

```bash
python3 "<skill 目錄>/saved.py" add --link URL --title "標題" --source "來源" --published "2026-07-12T02:15"
```

多篇一次存用 stdin JSON（欄位同上，`note` 選填）：

```bash
echo '[{"link":"...","title":"...","source":"...","published":"..."}]' | python3 saved.py add --json
```

- `list` 印出的編號就是 `done` / `drop` 吃的編號（預設視圖 = 未讀、先存的在前）；也可以直接給 URL。若使用者說的編號可能對不上（例如隔了很久），先跑一次 `list` 再操作。
- 若使用者在**摘要已經不在對話脈絡裡**時說「存剛剛那篇」，別猜連結 —— 先 `list` 或請他講清楚是哪一篇。
- 存入是冪等的（同一連結重複存不會變兩筆），非 http(s) 的連結會被拒絕。
