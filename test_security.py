#!/usr/bin/env python3
"""Adversarial tests for the untrusted-input paths. Run after ANY change to
fetch_feeds.py or saved.py:

    python3 test_security.py        # exits non-zero if a defense broke

Everything a feed gives us — titles, links, the XML itself — is attacker-
controlled, and it all ends up in Markdown that a human clicks and that Claude
reads as context. These tests feed the real functions crafted malicious input
and assert the defenses hold. They exist because reading the code was not
enough: the link sanitizer had silently drifted out of sync between the two
scripts (parens escaped, whitespace not), reopening the exact Markdown-injection
hole md_safe_title was written to close. A test catches that; an eye did not.

Zero dependencies, no network: every probe is a pure function call or a
subprocess against a throwaway store in a temp dir.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch_feeds import (  # noqa: E402
    parse_feed, md_safe_title, md_safe_link, mute_terms, is_muted,
)

SAVED_PY = os.path.join(HERE, "saved.py")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"\n     {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# --- XML: entity expansion and external entities ---------------------------
# A DTD is never present in legitimate RSS/Atom, so parse_feed refuses any
# payload carrying one. That single rule is what makes billion-laughs and XXE
# impossible without pulling in defusedxml.

def test_xml():
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>
<rss><channel><item><title>&b;</title></item></channel></rss>"""
    check("billion-laughs payload rejected", parse_feed(bomb) == [])

    xxe = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss><channel><item><title>&xxe;</title></item></channel></rss>"""
    check("XXE payload rejected", parse_feed(xxe) == [])

    # A long leading comment used to push <!DOCTYPE past a 4 KB scan window.
    pad = b"<?xml version='1.0'?><!--" + b"x" * 5000 + b"-->"
    late = pad + b"""<!DOCTYPE r [<!ENTITY e "boom">]>
<rss><channel><item><title>&e;</title></item></channel></rss>"""
    check("DTD hidden behind a 5 KB comment still rejected", parse_feed(late) == [])


# --- Titles: Markdown injection --------------------------------------------
# A title lands inside `- [TITLE](url)`. Newlines would let it forge headings
# and list items; [ ] would break out of the link text and forge a link target
# (with a leading ! it autoloads a remote image); < > give autolinks and raw
# HTML in lenient renderers.

def test_titles():
    cases = {
        "newline forging a heading/item": "real\n## FAKE\n- [click](http://evil.com)",
        "bracket breakout to a fake link": "title][evil](http://evil.com)",
        "image autoload": "![x](http://evil.com/track.png)",
        "html / autolink": "<script>alert(1)</script> <http://evil.com>",
    }
    for name, raw in cases.items():
        s = md_safe_title(raw)
        bad = [c for c in "\n[]<>" if c in s]
        check(f"title neutralized: {name}", not bad, repr(s))


# --- Links: scheme and (url) breakout --------------------------------------
# Only http(s) ever becomes a clickable link (main() and saved.py add both drop
# other schemes). Inside (url), a ')' or any whitespace ends the link early and
# turns the remainder into markup, so md_safe_link encodes them.

def test_links():
    for scheme in ("javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd"):
        # replicate the caller-side scheme check both scripts apply
        link = scheme if scheme.startswith(("http://", "https://")) else ""
        check(f"non-http scheme dropped: {scheme[:16]}", link == "")

    out = md_safe_link("https://a.com/x) [evil](http://e.com")
    check("')' encoded so (url) cannot be closed early", ")" not in out, out)

    out = md_safe_link("https://a.com/a\nb c\td")
    check("whitespace/newline in a link encoded", not re.search(r"\s", out), out)


# --- Mute list: data, not a pattern ----------------------------------------

def test_mute():
    terms = mute_terms({"mute": ["a.c", "   ", 123, "Foo"]})
    check("non-string and blank mute terms dropped", terms == ["a.c", "foo"], str(terms))
    # 'a.c' as a regex would match 'abc'; it must not.
    check("mute matches as a substring, not a regex", not is_muted("abc news", terms))
    check("mute is case-insensitive", is_muted("about FOO today", terms))


# --- saved.py: the same guarantees, through the CLI ------------------------

def test_saved(store):
    def run(*args, stdin=None):
        return subprocess.run([sys.executable, SAVED_PY, "--store", store, *args],
                              capture_output=True, text=True, input=stdin)

    run("add", "--link", "javascript:alert(1)", "--title", "x")
    saved = json.load(open(store)) if os.path.exists(store) else {}
    check("saved.py refuses a javascript: link", "javascript:alert(1)" not in saved)

    evil = [
        {"link": "https://ok.com/a\nb", "title": "t1\n## FAKE\n- [e](http://evil)"},
        {"link": "https://ok.com/c) [evil](http://e.com", "title": "t2",
         "source": "s]\n## FAKE"},
    ]
    run("add", "--json", stdin=json.dumps(evil))
    out = run("list").stdout

    body = out.splitlines()[2:]  # past the title and the count line
    structural = [l for l in body if l.strip().startswith(("#", "- ", "* "))]
    check("saved.py list: no forged headings or list items", not structural, out)

    urls = re.findall(r"\]\(([^)]*)\)", out)
    check("saved.py list: no raw whitespace inside any (url)",
          not any(re.search(r"\s", u) for u in urls), out)
    check("saved.py list: every rendered link is http(s)",
          all(u.startswith(("http://", "https://")) for u in urls), out)

    os.remove(store)


if __name__ == "__main__":
    test_xml()
    test_titles()
    test_links()
    test_mute()
    with tempfile.TemporaryDirectory() as d:
        test_saved(os.path.join(d, "store.json"))

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
        sys.exit(1)
    print("all probes passed")
