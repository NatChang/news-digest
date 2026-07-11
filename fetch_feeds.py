#!/usr/bin/env python3
"""
Fetch curated RSS feeds, filter by publish date, group by category/source.

Output is Markdown intended to be post-processed by Claude (translate any
non-Chinese titles to Chinese, then present). Each item already carries its
original article link so it stays clickable.

Usage:
    python3 fetch_feeds.py [--days N] [--category CAT] [--limit N] [--format md|json]

--days N       Include items published within the last N days (default: 1).
--category     One of: invest | itproduct | tech | all  (default: all).
               Chinese aliases accepted: 投資 / 產業, IT / 產品, 技術 / 技術發表.
--limit N      Max items per source after date filtering (default: 20).
--format       md (default) or json.
"""
import argparse
import json
import os
import subprocess
import sys
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

# --- Feed catalogue -------------------------------------------------------
# Each feed: (source_name, url, [categories]). A feed may belong to several.
FEEDS = [
    # 投資產業動態
    ("Storm 風傳媒財經",   "https://www.storm.mg/api/getRss/channel_id/4",              ["invest"]),
    ("鉅亨網 cnYES 台股",  "https://news.cnyes.com/rss/v1/news/category/tw_stock",       ["invest"]),
    ("DIGITIMES Asia",     "https://www.digitimes.com/rss/daily.xml",                    ["invest", "tech"]),
    ("SemiAnalysis",       "https://www.semianalysis.com/feed",                          ["invest", "tech"]),
    ("綠角財經筆記",       "https://greenhornfinancefootnote.blogspot.com/feeds/posts/default", ["invest"]),
    ("TechNews 科技新報",  "https://technews.tw/tn-rss/",                                ["invest", "tech"]),
    # IT 最新產品動態
    ("Engadget",           "https://www.engadget.com/rss.xml",                           ["itproduct"]),
    ("The Verge",          "https://www.theverge.com/rss/index.xml",                     ["itproduct"]),
    ("iThome",             "https://www.ithome.com.tw/rss",                              ["itproduct", "tech"]),
    ("電腦玩物",           "https://feeds.feedburner.com/playpcesor",                    ["itproduct"]),
    # 最新技術發表
    ("TechBridge 技術共筆", "https://blog.techbridge.cc/atom.xml",                       ["tech"]),
    ("Linux Journal",      "https://www.linuxjournal.com/node/feed",                     ["tech"]),
]

CATEGORY_LABELS = {
    "invest":    "📈 投資產業動態",
    "itproduct": "💻 IT 最新產品動態",
    "tech":      "🔬 最新技術發表",
}

CATEGORY_ALIASES = {
    "invest": "invest", "投資": "invest", "產業": "invest", "財經": "invest",
    "itproduct": "itproduct", "it": "itproduct", "產品": "itproduct", "product": "itproduct",
    "tech": "tech", "技術": "tech", "技術發表": "tech", "科技": "tech",
    "all": "all", "全部": "all",
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch(url, timeout=20):
    try:
        out = subprocess.run(
            ["curl", "-sL", "-m", str(timeout), "-A", UA,
             "--max-filesize", "10m", "--max-redirs", "3",
             "-H", "Accept: application/rss+xml, application/atom+xml, application/xml, text/xml",
             url],
            capture_output=True, timeout=timeout + 5,
        )
        return out.stdout
    except Exception as e:
        sys.stderr.write(f"[fetch fail] {url}: {e}\n")
        return b""


def strip_ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    # RFC822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    # ISO 8601 (Atom)
    try:
        t = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def has_cjk(s):
    return bool(re.search(r"[一-鿿]", s))


# Storm 風傳媒 RSS gives links like https://www.storm.mg/12345?utm_source=rss
# which 404; the working URL is https://www.storm.mg/article/12345
_STORM_RSS = re.compile(r"^(https?://www\.storm\.mg)/(\d+)(?:\?.*)?$")


def normalize_link(link):
    if not link:
        return link
    m = _STORM_RSS.match(link)
    if m:
        return f"{m.group(1)}/article/{m.group(2)}"
    return link


# --- "Seen" state (for --unseen): remember which article links were already
# shown, so re-running the same day only surfaces new articles. Stored as
# {link: first-seen ISO timestamp} in a local JSON file outside the repo. ---
DEFAULT_STATE = os.path.expanduser("~/.news-digest/seen.json")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def prune_state(state, keep_days=14):
    """Drop entries older than keep_days so the file can't grow forever."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    out = {}
    for link, ts in state.items():
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                out[link] = ts
        except Exception:
            continue
    return out


def save_state(path, state):
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write(f"[state save fail] {path}: {e}\n")


def ran_today(path):
    """True if the state file was last written earlier today (local date).
    Used by --auto-unseen: the first run of a day shows everything, later
    runs the same day show only new articles."""
    try:
        last = datetime.fromtimestamp(os.path.getmtime(path)).date()
        return last == datetime.now().date()
    except OSError:
        return False


def parse_feed(raw):
    """Return list of dicts: title, link, published(datetime|None)."""
    items = []
    if not raw:
        return items
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # tolerate leading junk / BOM
        try:
            root = ET.fromstring(raw.decode("utf-8", "ignore").lstrip())
        except Exception:
            return items

    entries = []
    for el in root.iter():
        if strip_ns(el.tag) in ("item", "entry"):
            entries.append(el)

    for e in entries:
        title, link, date = "", "", None
        for c in e:
            tag = strip_ns(c.tag)
            if tag == "title" and not title:
                title = (c.text or "").strip()
            elif tag == "link":
                # RSS: text; Atom: href attribute
                href = c.get("href")
                rel = c.get("rel")
                if href:
                    if rel in (None, "alternate") and not link:
                        link = href
                elif c.text and not link:
                    link = c.text.strip()
            elif tag in ("pubDate", "published", "updated", "date") and date is None:
                date = parse_date(c.text)
        if title:
            items.append({"title": title, "link": link, "published": date})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--category", default="all")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--format", default="md", choices=["md", "json"])
    ap.add_argument("--unseen", action="store_true",
                    help="always show only articles not shown in previous runs; records shown ones")
    ap.add_argument("--auto-unseen", action="store_true",
                    help="first run of the day shows everything; later runs the same day show only new ones")
    ap.add_argument("--state", default=DEFAULT_STATE,
                    help=f"path to the seen-links state file (default: {DEFAULT_STATE})")
    args = ap.parse_args()

    cat = CATEGORY_ALIASES.get(args.category.strip().lower(), None)
    if cat is None:
        cat = CATEGORY_ALIASES.get(args.category.strip(), "all")
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    # record: whether to load/persist the seen-links state at all.
    # filter_seen: whether to actually hide already-seen articles this run.
    record = args.unseen or args.auto_unseen
    filter_seen = args.unseen or (args.auto_unseen and ran_today(args.state))
    state = load_state(args.state) if record else {}

    # category -> source -> [items]
    result = {}
    # (category, source) -> how many in-window items the per-source limit cut off
    truncated = {}
    for source, url, cats in FEEDS:
        if cat != "all" and cat not in cats:
            continue
        raw = fetch(url)
        items = parse_feed(raw)
        kept = []
        for it in items:
            d = it["published"]
            if d is not None and d < cutoff:
                continue
            if it["link"] and not it["link"].startswith("http"):
                it["link"] = urljoin(url, it["link"])
            it["link"] = normalize_link(it["link"])
            if filter_seen and it["link"] and it["link"] in state:
                continue
            kept.append(it)
        cut = max(0, len(kept) - args.limit)
        kept = kept[: args.limit]
        if not kept:
            continue
        # assign to the first matching displayed category
        display_cats = cats if cat == "all" else [cat]
        primary = display_cats[0]
        result.setdefault(primary, {}).setdefault(source, []).extend(kept)
        if cut:
            truncated[(primary, source)] = truncated.get((primary, source), 0) + cut

    # Record everything we're about to show, then prune + persist.
    if record:
        now = datetime.now(timezone.utc).isoformat()
        for srcs in result.values():
            for its in srcs.values():
                for it in its:
                    if it["link"]:
                        state.setdefault(it["link"], now)
        save_state(args.state, prune_state(state))

    if args.format == "json":
        serial = {
            c: {s: [{**i, "published": i["published"].isoformat() if i["published"] else None} for i in its]
                for s, its in srcs.items()}
            for c, srcs in result.items()
        }
        print(json.dumps(serial, ensure_ascii=False, indent=2))
        return

    # Markdown
    order = ["invest", "itproduct", "tech"]
    lines = []
    win = f"最近 {args.days} 天" if args.days != 1 else "最近 24 小時"
    lines.append(f"# 新聞摘要（{win}）\n")
    any_out = False
    for c in order:
        if c not in result:
            continue
        any_out = True
        lines.append(f"\n## {CATEGORY_LABELS[c]}\n")
        for source, its in result[c].items():
            lines.append(f"### {source}")
            for it in its:
                d = it["published"]
                ds = d.astimezone().strftime("%m/%d %H:%M") if d else "—"
                flag = "" if has_cjk(it["title"]) else "  [EN→需翻譯]"
                link = it["link"] or ""
                lines.append(f"- [{it['title']}]({link}) · {ds}{flag}")
            cut = truncated.get((c, source))
            if cut:
                more = "再跑一次或加 --limit 可看到" if record else "加 --limit 可看到"
                lines.append(f"_（尚有 {cut} 則未顯示，已達每來源 {args.limit} 則上限；{more}）_")
            lines.append("")
    if not any_out:
        if filter_seen:
            lines.append("_沒有新文章（你沒看過的都看完了）。_")
        else:
            lines.append("_此區間內沒有新文章。_")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
