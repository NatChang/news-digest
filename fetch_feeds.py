#!/usr/bin/env python3
"""
Fetch curated RSS feeds, filter by publish date, group by category/source.

Emits Markdown (or JSON) for the caller to post-process — e.g. translate any
non-Chinese titles before display. Each item carries its original article link
so it stays clickable. Categories and feeds come from default_feeds.json plus
an optional per-user ~/.news-digest/config.json (see load_config).

Usage:
    python3 fetch_feeds.py [--days N] [--category CAT] [--limit N]
                           [--format md|json] [--unseen | --auto-unseen]
                           [--state PATH] [--config PATH]

--days N        Include items published within the last N days (default: 1).
--category      A category key or alias, or 'all' (default: all). Built-ins:
                invest / itproduct / tech; users may define more in config.
--limit N       Max items per source after date filtering (default: 20).
--format        md (default) or json.
--unseen        Show only links not shown in previous runs; records shown ones.
--auto-unseen   First run of a day shows everything; later same-day runs only new.
--state PATH    Seen-links state file (default: ~/.news-digest/seen.json).
--config PATH   User categories/feeds overlay (default: ~/.news-digest/config.json).
--version       Print the version (from _version.py) and exit.

The config's optional "mute" list drops any article whose title contains one of
its terms, so a columnist or topic you never want to read never shows up.
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

from _version import __version__

# --- Feed catalogue -------------------------------------------------------
# The shipped default categories/feeds live in a tracked data file next to this
# script (default_feeds.json), NOT hardcoded here — the feed list is data, so it
# can be curated without touching code. Users add/override on top of it in
# ~/.news-digest/config.json (see load_config). Both files share one schema:
#   {"categories": {key: {label, order, aliases}}, "feeds": [{source, url, categories}]}
_DEFAULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_feeds.json")

# Where users define their own categories/feeds (JSON, zero extra deps).
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.news-digest/config.json")


def _read_catalogue_file(path, label):
    """Read a {categories, feeds} JSON file. Never raises: on any problem it
    returns an empty catalogue with a warning so the skill keeps working."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {"categories": data.get("categories") or {},
                    "feeds": data.get("feeds") or [],
                    "mute": data.get("mute") or []}
        sys.stderr.write(f"[{label}] {path}: top level must be an object; ignored\n")
    except FileNotFoundError:
        if label == "defaults":
            sys.stderr.write(f"[{label}] {path} not found; starting from empty defaults\n")
    except Exception as e:
        sys.stderr.write(f"[{label}] {path}: {e}; ignored\n")
    return {"categories": {}, "feeds": [], "mute": []}


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
    mute: the two lists are concatenated (a user can only add terms, and the
        same term twice is the same term).
    Untrusted values (labels/urls) are validated later where they are used.
    """
    merged = {
        "categories": dict(base.get("categories", {})),
        "feeds": [dict(f) for f in base.get("feeds", [])],
        "mute": list(base.get("mute", [])) + list(user.get("mute") or []),
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
    """Return the merged config: shipped defaults (default_feeds.json, tracked)
    overlaid with the user's own categories/feeds (~/.news-digest/config.json,
    private, optional). Never raises: a missing or malformed file on either side
    just contributes nothing, so the skill keeps working."""
    defaults = _read_catalogue_file(_DEFAULTS_PATH, "defaults")
    user = _read_catalogue_file(path, "config") if os.path.exists(path) else {}
    return _merge_config(defaults, user)


def build_catalogue(cfg, lang="zh"):
    """Derive the runtime lookups from a merged config:
        feeds   -> [(source, url, [categories]), ...] with http(s)-only URLs
        labels  -> {cat: display label}  (uses label_en when lang == "en")
        aliases -> {alias(lowercased & original): canonical cat} (+ all)
        order   -> [cat, ...] sorted by each category's `order`
    """
    cats = cfg.get("categories", {})

    def _label(spec, key):
        if lang == "en":
            return spec.get("label_en") or spec.get("label") or key
        return spec.get("label") or key
    labels = {k: _label(v, k) for k, v in cats.items()}
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
    seen_urls = {}  # normalized url -> source name that claimed it first
    for f in cfg.get("feeds", []):
        src, url, fcats = f.get("source"), f.get("url"), f.get("categories")
        if not (src and url and fcats):
            continue
        # untrusted URL: only fetch http(s)
        if not str(url).startswith(("http://", "https://")):
            sys.stderr.write(f"[config] feed {src!r}: non-http(s) url skipped\n")
            continue
        # de-dup by URL so the same feed under two different source names can't
        # surface the same articles twice (merge only de-dups by source name).
        norm = str(url).strip().rstrip("/")
        if norm in seen_urls:
            sys.stderr.write(
                f"[config] feed {src!r}: same url as {seen_urls[norm]!r}; skipped as duplicate\n")
            continue
        # keep only categories that actually exist, preserving order
        valid = [c for c in fcats if c in cats]
        if not valid:
            sys.stderr.write(f"[config] feed {src!r}: no known category, skipped\n")
            continue
        seen_urls[norm] = src
        feeds.append((src, url, valid))
    return feeds, labels, aliases, order


def mute_terms(cfg):
    """Lowercased keywords from the config's `mute` list. An article whose title
    contains any of them is dropped before it ever reaches the digest — the way
    to stop a recurring columnist or topic you never want to read. Plain
    substring match (no regex), so a term is exactly the text it looks like."""
    terms = []
    for t in cfg.get("mute", []):
        if isinstance(t, str) and t.strip():
            terms.append(t.strip().lower())
    return terms


def is_muted(title, terms):
    t = (title or "").lower()
    return any(term in t for term in terms)


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch(url, timeout=20):
    try:
        out = subprocess.run(
            # --proto / --proto-redir: a feed URL is config data and a redirect
            # target is remote data; pin both to http(s) so neither can steer
            # curl into file:// / scp:// etc. Current curl defaults to this for
            # redirects, but the guarantee belongs in the call, not in the
            # version we happen to run.
            ["curl", "-sL", "-m", str(timeout), "-A", UA,
             "--proto", "=http,https", "--proto-redir", "=http,https",
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


# feed 的連結同樣不可信：( ) 與空白（含換行/tab）都會提前終止 Markdown 的 (url)，
# 讓後面的殘字變成可點的注入連結，故一律百分比編碼。合法網址本就不含這些字元。
# 任何要把連結寫進 Markdown 的地方都必須走這裡（saved.py 也匯入同一支）。
def md_safe_link(link):
    link = (link or "").replace("(", "%28").replace(")", "%29")
    return re.sub(r"\s", "%20", link)


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
            os.makedirs(d, mode=0o700, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        # What you read is nobody else's business: keep the reading history (and
        # the sibling config, which holds the mute list) owner-only.
        os.chmod(path, 0o600)
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
    # 等實體爆炸類 DoS（零依賴，取代 defusedxml；殘餘僅 DoS，非資料外洩）。
    # 兩者都掃全文：DOCTYPE 只掃開頭的話，一段夠長的前置註解就能把它推出視窗外。
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
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


# Boilerplate strings per output language. The article titles themselves are
# not translated here (that is the caller's job, guided by the [translate→LANG]
# marker below); only the digest's own framing text is localized.
_STRINGS = {
    "zh": {
        "title_24h":  "新聞摘要（最近 24 小時）",
        "title_days": "新聞摘要（最近 {n} 天）",
        "title_date": "新聞摘要（{d}）",
        "title_range": "新聞摘要（{a} ～ {b}）",
        "none_unseen": "沒有新文章（你沒看過的都看完了）。",
        "none":        "此區間內沒有新文章。",
        "more_record": "再跑一次或加 --limit 可看到",
        "more":        "加 --limit 可看到",
        "truncated":   "（尚有 {cut} 則未顯示，已達每來源 {limit} 則上限；{more}）",
    },
    "en": {
        "title_24h":  "News Digest (last 24 hours)",
        "title_days": "News Digest (last {n} days)",
        "title_date": "News Digest ({d})",
        "title_range": "News Digest ({a} – {b})",
        "none_unseen": "No new items — you're all caught up.",
        "none":        "No items in this window.",
        "more_record": "run again or add --limit to see them",
        "more":        "add --limit to see them",
        "truncated":   "({cut} more hidden; per-source cap of {limit} reached; {more})",
    },
}


def needs_translation(title, lang):
    """Whether a title is NOT in the target language and should be translated.
    zh target -> flag non-Chinese titles; en target -> flag Chinese titles."""
    return (not has_cjk(title)) if lang == "zh" else has_cjk(title)


def _iso_date(s):
    """argparse type: accept YYYY-MM-DD, return the (y, m, d) tuple."""
    try:
        dt = datetime.strptime(s.strip(), "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r}; expected YYYY-MM-DD")
    return (dt.year, dt.month, dt.day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="version",
                    version=f"news-digest {__version__}")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--date", type=_iso_date,
                    help="only a single local calendar day (YYYY-MM-DD); overrides --days")
    ap.add_argument("--since", type=_iso_date,
                    help="only items on/after this local day (YYYY-MM-DD); overrides --days")
    ap.add_argument("--until", type=_iso_date,
                    help="only items on/before this local day (YYYY-MM-DD); overrides --days")
    ap.add_argument("--category", default="all")
    ap.add_argument("--lang", default="zh", choices=["zh", "en"],
                    help="output language for the digest's framing text and the "
                         "translate marker (default: zh)")
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

    cfg = load_config(args.config)
    feeds_cat, CATEGORY_LABELS, aliases, order = build_catalogue(cfg, args.lang)
    muted = mute_terms(cfg)
    S = _STRINGS[args.lang]

    raw_cat = args.category.strip()
    cat = aliases.get(raw_cat.lower()) or aliases.get(raw_cat) or "all"
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    # Absolute date window (--date / --since / --until). When any is given it
    # overrides the relative --days cutoff. Dates are read as LOCAL calendar
    # days and converted to UTC bounds (published times are stored in UTC).
    if args.date and (args.since or args.until):
        ap.error("--date cannot be combined with --since/--until")
    local_tz = datetime.now(timezone.utc).astimezone().tzinfo

    def _day_start(ymd):
        return datetime(ymd[0], ymd[1], ymd[2], tzinfo=local_tz).astimezone(timezone.utc)

    start_dt = end_dt = None  # end_dt is exclusive
    if args.date:
        start_dt = _day_start(args.date)
        end_dt = start_dt + timedelta(days=1)
    else:
        if args.since:
            start_dt = _day_start(args.since)
        if args.until:
            end_dt = _day_start(args.until) + timedelta(days=1)
    date_filter = start_dt is not None or end_dt is not None

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
            if is_muted(it["title"], muted):
                continue
            d = it["published"]
            if date_filter:
                # a specific-day query only makes sense for dated items
                if d is None:
                    continue
                if start_dt is not None and d < start_dt:
                    continue
                if end_dt is not None and d >= end_dt:
                    continue
            elif d is not None and d < cutoff:
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
    if date_filter:
        def _fmt(ymd):
            return f"{ymd[0]:04d}-{ymd[1]:02d}-{ymd[2]:02d}"
        if args.date:
            title = S["title_date"].format(d=_fmt(args.date))
        else:
            a = _fmt(args.since) if args.since else "…"
            b = _fmt(args.until) if args.until else "…"
            title = S["title_range"].format(a=a, b=b)
    elif args.days == 1:
        title = S["title_24h"]
    else:
        title = S["title_days"].format(n=args.days)
    lines.append(f"# {title}\n")
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
                flag = f"  [translate→{args.lang}]" if needs_translation(it["title"], args.lang) else ""
                lines.append(f"- [{md_safe_title(it['title'])}]({md_safe_link(it['link'])}) · {ds}{flag}")
            cut = truncated.get((c, source))
            if cut:
                more = S["more_record"] if record else S["more"]
                lines.append("_" + S["truncated"].format(cut=cut, limit=args.limit, more=more) + "_")
            lines.append("")
    if not any_out:
        lines.append("_" + (S["none_unseen"] if filter_seen else S["none"]) + "_")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
