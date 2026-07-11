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

# --- Feed catalogue (built-in defaults) -----------------------------------
# Source of truth for the shipped defaults. Users DO NOT edit this — they add
# or override categories/feeds in ~/.news-digest/config.json (see load_config).
# Schema mirrors that user config so the two merge cleanly.
_BUILTIN_CONFIG = {
    "categories": {
        # key -> {label, order, aliases}. `order` sets display order (low first).
        "invest":    {"label": "📈 投資產業動態", "order": 10, "aliases": ["投資", "產業", "財經"]},
        "itproduct": {"label": "💻 IT 最新產品動態", "order": 20, "aliases": ["it", "產品", "product"]},
        "tech":      {"label": "🔬 最新技術發表", "order": 30, "aliases": ["技術", "技術發表", "科技"]},
    },
    "feeds": [
        # 投資產業動態
        {"source": "Storm 風傳媒財經",  "url": "https://www.storm.mg/api/getRss/channel_id/4",        "categories": ["invest"]},
        {"source": "鉅亨網 cnYES 台股", "url": "https://news.cnyes.com/rss/v1/news/category/tw_stock", "categories": ["invest"]},
        {"source": "DIGITIMES Asia",    "url": "https://www.digitimes.com/rss/daily.xml",             "categories": ["invest", "tech"]},
        {"source": "SemiAnalysis",      "url": "https://www.semianalysis.com/feed",                   "categories": ["invest", "tech"]},
        {"source": "TechNews 科技新報", "url": "https://technews.tw/tn-rss/",                         "categories": ["invest", "tech"]},
        # IT 最新產品動態
        {"source": "Engadget",          "url": "https://www.engadget.com/rss.xml",                    "categories": ["itproduct"]},
        {"source": "The Verge",         "url": "https://www.theverge.com/rss/index.xml",              "categories": ["itproduct"]},
        {"source": "iThome",            "url": "https://www.ithome.com.tw/rss",                       "categories": ["itproduct", "tech"]},
        {"source": "電腦玩物",          "url": "https://feeds.feedburner.com/playpcesor",             "categories": ["itproduct"]},
        # 最新技術發表
        {"source": "TechBridge 技術共筆", "url": "https://blog.techbridge.cc/atom.xml",               "categories": ["tech"]},
        {"source": "Linux Journal",     "url": "https://www.linuxjournal.com/node/feed",              "categories": ["tech"]},
    ],
}

# Where users define their own categories/feeds (JSON, zero extra deps).
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.news-digest/config.json")


def _merge_config(base, user):
    """Overlay a user config dict onto the built-in defaults.

    categories: shallow dict merge; a user key with the same name overrides
        (or extends) the built-in one field by field.
    feeds: matched by `source` name.
        - new source (has url + categories)         -> appended
        - existing source with "categories"          -> replaces its categories
        - existing source with "add_categories"      -> appends those categories
        - a user feed may itself use add_categories on a brand-new source, which
          is treated as its category list.
    Untrusted values (labels/urls) are validated later where they are used.
    """
    merged = {
        "categories": dict(base.get("categories", {})),
        "feeds": [dict(f) for f in base.get("feeds", [])],
    }
    # categories: field-by-field overlay
    for key, spec in (user.get("categories") or {}).items():
        if not isinstance(spec, dict):
            continue
        merged["categories"][key] = {**merged["categories"].get(key, {}), **spec}

    by_source = {f["source"]: f for f in merged["feeds"]}
    for uf in (user.get("feeds") or []):
        src = uf.get("source")
        if not src:
            continue
        existing = by_source.get(src)
        add = uf.get("add_categories")
        if existing is not None:
            if add:
                cats = list(existing.get("categories", []))
                for c in add:
                    if c not in cats:
                        cats.append(c)
                existing["categories"] = cats
            if uf.get("categories"):
                existing["categories"] = list(uf["categories"])
            if uf.get("url"):
                existing["url"] = uf["url"]
        else:
            cats = list(uf.get("categories") or add or [])
            if uf.get("url") and cats:
                nf = {"source": src, "url": uf["url"], "categories": cats}
                merged["feeds"].append(nf)
                by_source[src] = nf
    return merged


def load_config(path=DEFAULT_CONFIG_PATH):
    """Return the merged (built-in + user) config. Never raises: a missing or
    malformed user config just falls back to the built-in defaults with a
    warning, so the skill keeps working."""
    user = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            user = data
        else:
            sys.stderr.write(f"[config] {path}: top level must be an object; ignored\n")
    except FileNotFoundError:
        pass
    except Exception as e:
        sys.stderr.write(f"[config] {path}: {e}; using built-in defaults\n")
    return _merge_config(_BUILTIN_CONFIG, user)


def build_catalogue(cfg):
    """Derive the runtime lookups from a merged config:
        feeds   -> [(source, url, [categories]), ...] with http(s)-only URLs
        labels  -> {cat: display label}
        aliases -> {alias(lowercased & original): canonical cat} (+ all)
        order   -> [cat, ...] sorted by each category's `order`
    """
    cats = cfg.get("categories", {})
    labels = {k: (v.get("label") or k) for k, v in cats.items()}
    order = sorted(cats.keys(), key=lambda k: cats[k].get("order", 1000))

    aliases = {"all": "all", "全部": "all"}
    for key, spec in cats.items():
        aliases[key] = key
        aliases[key.lower()] = key
        for a in (spec.get("aliases") or []):
            if not isinstance(a, str):
                continue
            aliases[a] = key
            aliases[a.lower()] = key

    feeds = []
    for f in cfg.get("feeds", []):
        src, url, fcats = f.get("source"), f.get("url"), f.get("categories")
        if not (src and url and fcats):
            continue
        # untrusted URL: only fetch http(s)
        if not str(url).startswith(("http://", "https://")):
            sys.stderr.write(f"[config] feed {src!r}: non-http(s) url skipped\n")
            continue
        # keep only categories that actually exist, preserving order
        valid = [c for c in fcats if c in cats]
        if not valid:
            sys.stderr.write(f"[config] feed {src!r}: no known category, skipped\n")
            continue
        feeds.append((src, url, valid))
    return feeds, labels, aliases, order

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


# feed 標題是不可信輸入，需中和三種 Markdown 注入：
#  1. 內部換行/tab：可長出假標題(##)、假項目(-)、分隔線(---)或裸 URL 自動連結，
#     繞過下方的括號跳脫與連結白名單，故先把所有空白收斂成單一空格。
#  2. [ ]：可截斷 Markdown 連結語法、偽造連結目標（前面再加 ! 即自動載入圖片）。
#  3. < >：autolink 與寬鬆算繪器的原始 HTML。換成全形字保留可讀性但無語法效果。
def md_safe_title(s):
    s = re.sub(r"\s+", " ", s).strip()
    return (s.replace("[", "［").replace("]", "］")
             .replace("<", "＜").replace(">", "＞"))


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
    # 不可信 XML：合法 RSS/Atom 不含 DTD/實體宣告，直接拒收以擋 billion-laughs
    # 等實體爆炸類 DoS（零依賴，取代 defusedxml；殘餘僅 DoS，非資料外洩）
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw:
        sys.stderr.write("[parse skip] DTD/ENTITY not allowed\n")
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
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                    help=f"path to the user categories/feeds config (default: {DEFAULT_CONFIG_PATH})")
    args = ap.parse_args()

    feeds_cat, CATEGORY_LABELS, aliases, order = build_catalogue(load_config(args.config))

    raw_cat = args.category.strip()
    cat = aliases.get(raw_cat.lower()) or aliases.get(raw_cat) or "all"
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
    for source, url, cats in feeds_cat:
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
            # 只接受 http(s)；javascript: 等其他 scheme 一律不輸出成連結
            if it["link"] and not it["link"].startswith(("http://", "https://")):
                it["link"] = ""
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

    # Markdown (category order comes from the merged config)
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
                # 連結中的 ) 與空白（含換行/tab）會提前終止 Markdown 的 (url)，需編碼
                link = (it["link"] or "").replace("(", "%28").replace(")", "%29")
                link = re.sub(r"\s", "%20", link)
                lines.append(f"- [{md_safe_title(it['title'])}]({link}) · {ds}{flag}")
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
