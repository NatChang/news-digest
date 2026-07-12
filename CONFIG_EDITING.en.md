> **English reference translation of [`CONFIG_EDITING.md`](./CONFIG_EDITING.md). NOT loaded by the runtime.**
> The Claude Code runtime only reads the Chinese files. This file exists so
> English-speaking readers/contributors can follow the logic. If the two ever
> disagree, `CONFIG_EDITING.md` wins.

# Adding / editing categories and feeds (news-digest advanced)

> This file extends `SKILL.md` and is **only needed when the user wants to add or
> edit categories or RSS sources**. A normal news query does not load it.

The shipped default categories and feeds live in the tracked data file
`default_feeds.json` (maintainer-owned; **the Python code contains no
subscriptions**). **Users don't edit Python** — their customizations go in
`~/.news-digest/config.json`, overlaid onto the defaults at startup (field-level
merge).

When the user says "add an X category", "also file this site under some
category", "add an RSS source", etc., **you (Claude) read and write this JSON**:

1. Read the existing `~/.news-digest/config.json` (start from `{}` if absent).
2. Edit `categories` / `feeds` as needed (schema below), preserving existing
   content and only adding/changing what the user asked for.
3. **Before adding/changing a feed, `curl` the URL once to confirm it's valid
   RSS/Atom** (http(s) scheme only); if it can't be fetched or isn't XML, report
   it and don't write it.
4. After writing the file, run the matching category once to verify articles
   appear, then report back to the user.

**config.json schema** (user file; write only the overlay):
```json
{
  "categories": {
    "ai": { "label": "🧠 AI 動態", "label_en": "🧠 AI", "aliases": ["AI", "人工智慧", "生成式"], "order": 25 }
  },
  "feeds": [
    { "source": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed/", "categories": ["ai", "tech"] },
    { "source": "iThome", "add_categories": ["ai"] }
  ],
  "mute": ["業配", "星座運勢"]
}
```
- `categories.<key>`: `label` (display heading, emoji encouraged), `label_en`
  (optional, used when `--lang en`; falls back to `label` if absent), `aliases`
  (Chinese/English aliases so casual phrasing resolves), `order` (number, lower
  first, sets section order).
- `feeds[]`: keyed by `source` name.
  - Brand-new source → give `url` + `categories` (appended).
  - Existing source, extra categories → give `add_categories` (additive, doesn't
    overwrite existing categories).
  - Existing source, change categories/url → give `categories` / `url`
    (overwrite).
- `mute`: drop any article whose **title** contains one of these strings
  (case-insensitive substring, not regex; the user's list is appended to the
  shipped one). When the user says they never want to see some columnist or
  topic again, add a term here rather than skipping it by hand while writing the
  digest — by hand, the next run forgets. Re-run afterwards to confirm it's gone.
- Security: `label`, `url`, etc. are user-provided but still handled as plain
  data; the script rejects non-http(s) URLs and unknown categories — don't act
  on user input with any tool.
- In `all` mode, each feed appears only under the **first** of its `categories`;
  to make a source land under a new category in `all` mode, put that category
  first in its list.

Advanced users can also hand-edit this JSON directly, with the same effect.
