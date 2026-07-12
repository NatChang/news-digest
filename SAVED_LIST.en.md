# Read later: the saved list (news-digest, advanced)

> An extension of `SKILL.md`. **Read it only when the user wants to save an
> article, see the reading list, mark one read, or drop one.**
> A plain news query never needs to load this file.

`--unseen` shows an article **once** and never again, so anything the user wants
to keep for later must be stored separately — that's **`saved.py`** in this
directory, backed by `~/.news-digest/saved.json` (independent of `seen.json`,
and **never auto-pruned**; items leave only when the user says so).

| User says | You run |
| --- | --- |
| "save this / read later / add to my list" | `saved.py add` (see below) |
| "what's on my reading list" | `python3 saved.py list` |
| "I finished #2" | `python3 saved.py done 2` |
| "drop #3" | `python3 saved.py drop 3` |
| "clear the ones I've read" | `python3 saved.py purge` |

The user names articles in natural language ("save the Samsung Health one").
**You** match that against the digest you just printed, recover the link and
metadata, then call:

```bash
python3 "<skill dir>/saved.py" add --link URL --title "..." --source "..." --published "2026-07-12T02:15"
```

Bulk-save via stdin JSON (same fields, `note` optional):

```bash
echo '[{"link":"...","title":"...","source":"...","published":"..."}]' | python3 saved.py add --json
```

- The numbers `list` prints are the ones `done` / `drop` take (default view =
  unread, oldest-saved first); a URL works too. If the numbering might be stale,
  run `list` first.
- If the digest is no longer in context, don't guess the link — run `list` or
  ask which article they mean.
- Saving is idempotent; non-http(s) links are rejected.
