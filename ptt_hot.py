#!/usr/bin/env python3
"""PTT 爆文抓取（beta；僅在 beta 分支上發佈，尚未進入穩定版）。

PTT 沒有熱門文章的 RSS。官方 atom（/atom/<板>.xml）只給最新 20 篇、不含推文數。
唯一能按熱度篩選的官方介面是網頁搜尋的 recommend: 語法：

    https://www.ptt.cc/bbs/<板>/search?q=recommend:100

回傳 HTML 文章列表，已依推文數過濾、按時間倒序。這支腳本解析它並輸出 Markdown。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from html import unescape  # 不 import html：parse() 的參數同名會遮蔽模組
from datetime import datetime, timedelta, timezone

# 標題/連結來自 PTT，是不可信的外部輸入，一律走 fetch_feeds 既有的
# Markdown 注入防護（勿在此重寫一份）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_feeds import md_safe_link, md_safe_title  # noqa: E402
from _version import __version__  # noqa: E402

BASE = "https://www.ptt.cc"
STATE = os.path.expanduser("~/.news-digest/ptt_seen.json")

# 每天固定會爆、但沒有資訊量的例行文（--no-mute 可關閉）
NOISE = [
    r"盤[後中]閒聊", r"三大法人買賣金額統計表", r"集中市場買賣金額統計表",
    r"融資融券", r"^\s*\[公告\]", r"標的$",
    # 每場比賽的直播串必爆但無內容量；全形/半形括號都出現過
    r"[\[［]LIVE[\]］]",
]

# 列表頁的推文數欄位：100 以上一律顯示「爆」，看不到實際數字；
# 破百的因此無法互相排序，這是 PTT 的顯示限制，不是解析漏抓。
# 噓文同理，只給十位數：X0=噓1–9、X5=噓50–59、XX=噓100 以上。
BLOWN = "爆"
BOOED = "XX"

# 小板流量遠低於八卦/股板，用預設 100 幾乎撈不到東西，故各自降門檻。
# 只在使用者沒指定 --min-rec 時套用。
DEFAULT_MIN_REC = 100
BOARD_MIN_REC = {"Tech_Job": 30, "Lifeismoney": 50}


def fetch(url):
    """用 curl 抓頁面。over18 cookie 讓八卦板等 18 禁板免跳確認頁。"""
    r = subprocess.run(
        ["curl", "-sS", "-m", "25", "-A", "Mozilla/5.0", "-b", "over18=1", url],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.stderr.write(f"[抓取失敗] {url}: {r.stderr.strip()}\n")
        return ""
    return r.stdout


# 文章網址內嵌 unix timestamp：/M.1783820637.A.708.html → 發文時間。
# 列表頁的 date 欄只有 MM/DD（無年份、無時分），跨年與排序都不可靠，故一律用它。
_EPOCH = re.compile(r"/M\.(\d{9,11})\.A\.")


def post_time(url):
    m = _EPOCH.search(url)
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)), timezone.utc).astimezone()


def parse(html, board):
    out = []
    # 以 r-ent 切塊；用非貪婪配對到 </div></div> 會在 title 後就截斷，抓不到 meta。
    for blk in html.split('<div class="r-ent">')[1:]:
        a = re.search(r'<a href="([^"]+)">(.*?)</a>', blk, re.S)
        if not a:
            continue  # 已刪除的文章沒有連結
        rec = re.search(r'<div class="nrec">(?:<span[^>]*>)?([^<]*)', blk)
        url = BASE + a.group(1)
        out.append({
            "board": board,
            "rec": (rec.group(1).strip() if rec else ""),
            # 列表頁的標題是 HTML 轉義過的（"玩球" → &#34;玩球&#34;）；
            # 還原後才送 md_safe_title 做 Markdown 防護。
            "title": unescape(re.sub(r"\s+", " ", a.group(2)).strip()),
            "url": url,
            "time": post_time(url),
        })
    return out


def rec_score(rec):
    """把列表頁的推文欄轉成可排序的數值；噓為負，「爆」「XX」給破表值。"""
    if rec == BLOWN:
        return 10_000
    if rec == BOOED:
        return -10_000
    if rec.startswith("X") and rec[1:].isdigit():
        return -10 * int(rec[1:])
    try:
        return int(rec)
    except ValueError:
        return 0


def rec_label(rec):
    """人看的推/噓數。PTT 只給區間，就照實印區間，別假裝有精確值。"""
    if rec == BLOWN:
        return "爆(推≥100)"
    if rec == BOOED:
        return "噓≥100"
    if rec.startswith("X") and rec[1:].isdigit():
        d = int(rec[1:])
        return "噓1–9" if d == 0 else f"噓{d * 10}–{d * 10 + 9}"
    return f"{rec}推"


def load_seen():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_seen(state):
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    kept = {}
    for link, ts in state.items():
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                kept[link] = ts
        except Exception:
            continue
    try:
        os.makedirs(os.path.dirname(STATE), mode=0o700, exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(kept, f, ensure_ascii=False)
        os.chmod(STATE, 0o600)  # 讀過什麼是隱私，比照 seen.json
    except Exception as e:
        sys.stderr.write(f"[狀態寫入失敗] {STATE}: {e}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="version",
                    version=f"news-digest {__version__}")
    ap.add_argument("--boards", default="Gossiping,Stock,Tech_Job,Lifeismoney,Baseball",
                    help="逗號分隔看板名")
    ap.add_argument("--min-rec", type=int, default=None,
                    help="推文數門檻；不給則八卦/股板 100（爆文）、小板見 BOARD_MIN_REC。"
                         "設低於 100 才看得到實際推文數")
    ap.add_argument("--boo", type=int, nargs="?", const=10, default=None,
                    help="改抓噓文：噓數 ≥ N（只給旗標不給數字則 10）。與 --min-rec 互斥")
    ap.add_argument("--days", type=int, default=2, help="只留最近 N 天的文章")
    ap.add_argument("--pages", type=int, default=1, help="每板抓幾頁搜尋結果（每頁 20 篇）")
    ap.add_argument("--limit", type=int, default=15, help="每板最多列幾篇")
    ap.add_argument("--unseen", action="store_true", help="只列沒看過的（記錄於 ptt_seen.json）")
    ap.add_argument("--no-mute", action="store_true", help="不過濾例行文")
    args = ap.parse_args()

    if args.boo is not None:
        if args.min_rec is not None:
            ap.error("--boo 與 --min-rec 不可併用")
        if args.boo < 1:
            ap.error("--boo 要給正數（噓數門檻）")

    boo = args.boo is not None
    seen = load_seen() if args.unseen else {}
    now = datetime.now(timezone.utc).astimezone()
    cutoff = now - timedelta(days=args.days)
    noise = None if args.no_mute else re.compile("|".join(NOISE))
    total = 0

    for board in [b.strip() for b in args.boards.split(",") if b.strip()]:
        if boo:
            # PTT 搜尋的 recommend: 吃負數，-N 即「噓數 ≥ N」。
            min_rec = -args.boo
        elif args.min_rec is not None:
            min_rec = args.min_rec
        else:
            min_rec = BOARD_MIN_REC.get(board, DEFAULT_MIN_REC)
        items = []
        for page in range(1, args.pages + 1):
            url = f"{BASE}/bbs/{board}/search?q=recommend%3A{min_rec}&page={page}"
            items += parse(fetch(url), board)
            if page < args.pages:
                time.sleep(1)  # PTT 會擋密集請求

        keep = []
        for it in items:
            if it["time"] and it["time"] < cutoff:
                continue
            if noise and noise.search(it["title"]):
                continue
            if args.unseen and it["url"] in seen:
                continue
            keep.append(it)

        # 噓文模式要「噓越多排越前」，也就是分數越負越前面。
        sign = -1 if boo else 1
        keep.sort(key=lambda i: (sign * rec_score(i["rec"]), i["time"] or now), reverse=True)
        keep = keep[:args.limit]

        icon, kind = ("👎", "噓文") if boo else ("🔥", "爆文")
        gate = f"噓 ≥{args.boo}" if boo else f"推文 ≥{min_rec}"
        print(f"\n## {icon} PTT {board} 板 · {gate} · 近 {args.days} 天\n")
        if not keep:
            print(f"_此區間沒有新的{kind}_")
            continue
        for it in keep:
            when = it["time"].strftime("%m/%d %H:%M") if it["time"] else "—"
            print(f"- [{md_safe_title(it['title'])}]({md_safe_link(it['url'])})"
                  f" · {rec_label(it['rec'])} · {when}")
            seen[it["url"]] = now.astimezone(timezone.utc).isoformat()
            total += 1

    if args.unseen:
        save_seen(seen)
    if total == 0:
        kind = "噓文" if boo else "爆文"
        print(f"\n_（沒有新{kind}。若想重看全部，拿掉 --unseen）_")


if __name__ == "__main__":
    main()
