#!/usr/bin/env python3
"""
Read-later list for the news digest.

Why this exists: the digest's --unseen de-dup shows an article once and then
never again (seen.json). Anything you did not open at that moment is gone. This
is the escape hatch — a separate store at ~/.news-digest/saved.json that is
NEVER auto-pruned (unlike seen.json's 14-day window). Items leave it only when
you say so.

Stored as {link: {title, source, published, saved_at, read_at, note}}.

Usage:
    saved.py add --link URL --title T [--source S] [--published ISO] [--note N]
    saved.py add --json              # read [{link,title,source,...}, ...] from stdin
    saved.py list [--all] [--format md|json]
    saved.py done <n|url> [...]      # mark read (kept, but out of the default view)
    saved.py drop <n|url> [...]      # remove entirely
    saved.py purge                   # drop every item already marked read
    saved.py --store PATH ...        # override the store location

<n> is the number shown by `list` — it indexes the DEFAULT (unread) view, so
run `list` first if you are unsure. A URL (or a unique substring of one) also
works and is order-independent.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# Reuse the digest's Markdown-injection neutralizer: feed titles are untrusted
# input and land in the same kind of Markdown list here as they do there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fetch_feeds import md_safe_title
except Exception:  # keep working even if the sibling module moves
    def md_safe_title(s):
        s = re.sub(r"\s+", " ", s).strip()
        return (s.replace("[", "［").replace("]", "］")
                 .replace("<", "＜").replace(">", "＞"))

DEFAULT_STORE = os.path.expanduser("~/.news-digest/saved.json")


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Never destroy a store we cannot parse: refuse to run instead.
        sys.stderr.write(f"[saved] {path}: {e}\n")
        sys.exit(1)


def save(path, store):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # atomic: a crash mid-write can't truncate the store


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def view(store, include_read=False):
    """The canonical ordering that `list` prints and <n> indexes into:
    unread first, oldest-saved first (a FIFO reading queue)."""
    items = [(k, v) for k, v in store.items() if include_read or not v.get("read_at")]
    return sorted(items, key=lambda kv: (bool(kv[1].get("read_at")),
                                         kv[1].get("saved_at") or ""))


def add_one(store, link, title, source=None, published=None, note=None):
    link = (link or "").strip()
    if not link.startswith(("http://", "https://")):
        return None  # only ever store real article URLs
    if link in store:  # idempotent: re-saving refreshes nothing, just no-ops
        return "dup"
    store[link] = {
        "title": (title or link).strip(),
        "source": source or "",
        "published": published or "",
        "saved_at": now_iso(),
        "read_at": None,
        "note": note or "",
    }
    return "new"


def resolve(store, tokens):
    """Turn `done`/`drop` arguments into links. A token is either an index into
    the default (unread) view, or a URL / unique substring of one."""
    ordered = view(store)
    links, missing = [], []
    for tok in tokens:
        tok = tok.strip()
        # A digit is an index only if it actually indexes the view. Out-of-range
        # numbers fall through to URL matching: article ids like 11148604 are
        # all-digits too, and are the natural way to name a Storm/etc. article.
        if tok.isdigit() and 1 <= int(tok) <= len(ordered):
            links.append(ordered[int(tok) - 1][0])
            continue
        hits = [k for k in store if tok == k] or [k for k in store if tok in k]
        if len(hits) == 1:
            links.append(hits[0])
        else:
            missing.append(tok)  # zero matches, or ambiguous
    return links, missing


def fmt_md(store, include_read):
    ordered = view(store, include_read)
    if not ordered:
        return "_待讀清單是空的。_"
    lines = ["# 📌 稍後再讀\n"]
    unread = sum(1 for _, v in ordered if not v.get("read_at"))
    lines.append(f"_未讀 {unread} 篇" + (f"，已讀 {len(ordered) - unread} 篇_" if include_read else "_") + "\n")
    for n, (link, v) in enumerate(ordered, 1):
        mark = "✅ " if v.get("read_at") else ""
        meta = " · ".join(x for x in (v.get("source"), (v.get("published") or "")[:16].replace("T", " ")) if x)
        safe = (link or "").replace("(", "%28").replace(")", "%29")
        lines.append(f"{n}. {mark}[{md_safe_title(v.get('title', ''))}]({safe})"
                     + (f" · {meta}" if meta else ""))
        if v.get("note"):
            lines.append(f"   ↳ _{md_safe_title(v['note'])}_")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Read-later list for the news digest")
    ap.add_argument("--store", default=DEFAULT_STORE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="save an article for later")
    a.add_argument("--link")
    a.add_argument("--title")
    a.add_argument("--source")
    a.add_argument("--published")
    a.add_argument("--note")
    a.add_argument("--json", action="store_true",
                   help="read a JSON array of items from stdin instead (for bulk saves)")

    l = sub.add_parser("list", help="show the reading list")
    l.add_argument("--all", action="store_true", help="include items already marked read")
    l.add_argument("--format", default="md", choices=["md", "json"])

    d = sub.add_parser("done", help="mark items read")
    d.add_argument("targets", nargs="+")

    r = sub.add_parser("drop", help="remove items entirely")
    r.add_argument("targets", nargs="+")

    sub.add_parser("purge", help="remove every item already marked read")

    args = ap.parse_args()
    store = load(args.store)

    if args.cmd == "add":
        if args.json:
            try:
                payload = json.load(sys.stdin)
            except Exception as e:
                sys.exit(f"[saved] bad JSON on stdin: {e}")
            if not isinstance(payload, list):
                sys.exit("[saved] --json expects a JSON array")
        else:
            if not args.link:
                sys.exit("[saved] add needs --link (or --json)")
            payload = [{"link": args.link, "title": args.title, "source": args.source,
                        "published": args.published, "note": args.note}]
        added = dups = bad = 0
        for it in payload:
            if not isinstance(it, dict):
                bad += 1
                continue
            res = add_one(store, it.get("link"), it.get("title"), it.get("source"),
                          it.get("published"), it.get("note"))
            if res == "new":
                added += 1
            elif res == "dup":
                dups += 1
            else:
                bad += 1
        save(args.store, store)
        parts = [f"已存 {added} 篇"]
        if dups:
            parts.append(f"{dups} 篇已在清單中")
        if bad:
            parts.append(f"{bad} 篇無效（連結不是 http(s)）")
        unread = len(view(store))
        print("；".join(parts) + f"。待讀共 {unread} 篇。")

    elif args.cmd == "list":
        if args.format == "json":
            print(json.dumps(dict(view(store, args.all)), ensure_ascii=False, indent=2))
        else:
            print(fmt_md(store, args.all))

    elif args.cmd in ("done", "drop"):
        links, missing = resolve(store, args.targets)
        for link in links:
            if args.cmd == "done":
                store[link]["read_at"] = now_iso()
            else:
                store.pop(link, None)
        save(args.store, store)
        verb = "標記為已讀" if args.cmd == "done" else "移除"
        print(f"已{verb} {len(links)} 篇。待讀剩 {len(view(store))} 篇。")
        if missing:
            print(f"找不到（或指涉不明確）：{', '.join(missing)}", file=sys.stderr)

    elif args.cmd == "purge":
        gone = [k for k, v in store.items() if v.get("read_at")]
        for k in gone:
            store.pop(k)
        save(args.store, store)
        print(f"已清掉 {len(gone)} 篇讀過的。待讀剩 {len(view(store))} 篇。")


if __name__ == "__main__":
    main()
