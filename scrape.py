#!/usr/bin/env python3
"""Scrape top-level posts from an HN "Who is hiring?" thread into structured JSON.

    python3 scrape.py                    # find this month's thread and scrape it
    python3 scrape.py 2026-10            # re-scrape a known thread
    python3 scrape.py 2026-10 49922569   # register a key -> HN item id by hand

Re-runnable: new posts are added, edited posts are updated, deleted/dead posts are
flagged (not removed), and `first_seen` is preserved. Output:
data/<key>/posts.json, with keys -> item ids in data/threads.json.
"""
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

from config import DATA, THREADS

API = "https://hacker-news.firebaseio.com/v0/item/{}.json"
SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=" + quote("Who is hiring")


def fetch(item_id):
    with urllib.request.urlopen(API.format(item_id), timeout=30) as r:
        return json.load(r)


def html_to_text(s):
    s = re.sub(r'<a href="([^"]+)"[^>]*>.*?</a>', lambda m: html.unescape(m.group(1)), s or "", flags=re.S)
    s = s.replace("<p>", "\n\n")
    s = re.sub(r"<pre><code>(.*?)</code></pre>", r"\n\1\n", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "jobs [at] foo [dot] com", "jobs at foo dot com"
OBF_RE = re.compile(r"([\w.+-]+)\s*[\[(]?\s*(?:@|at)\s*[\])]?\s*([\w-]+)\s*[\[(]?\s*(?:\.|dot)\s*[\])]?\s*([a-z]{2,6})\b", re.I)
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
SALARY_RE = re.compile(r"(?:[$€£]\s?\d[\d,.]*\s?[kK]?(?:\s?(?:-|–|to)\s?[$€£]?\s?\d[\d,.]*\s?[kK]?)?)")


def extract(post):
    text = html_to_text(post.get("text", ""))
    header = text.split("\n", 1)[0].strip()
    parts = [p.strip() for p in re.split(r"\s+[|•·—–]\s+|\s\|\s?", header) if p.strip()]
    emails = sorted(set(EMAIL_RE.findall(text)))
    if not emails:
        for u, d, tld in OBF_RE.findall(text):
            if tld.lower() in {"com", "io", "ai", "co", "org", "net", "dev", "so", "sh", "app", "xyz", "us"} and u.lower() not in {"we", "is", "or"}:
                emails.append(f"{u}@{d}.{tld}".lower())
    urls = list(dict.fromkeys(u.rstrip(".,;") for u in URL_RE.findall(text)))
    low = text.lower()
    return {
        "id": post["id"],
        "author": post.get("by"),
        "posted_at": datetime.fromtimestamp(post["time"], timezone.utc).isoformat(),
        "hn_url": f"https://news.ycombinator.com/item?id={post['id']}",
        "header": header,
        "company_guess": parts[0] if parts else header[:60],
        "header_parts": parts,
        "text": text,
        "html": post.get("text", ""),
        "emails": emails,
        "urls": urls,
        "salary_mentions": sorted(set(m.strip() for m in SALARY_RE.findall(text) if re.search(r"\d{2}", m)))[:6],
        "flags": {
            "remote": "remote" in low,
            "onsite": bool(re.search(r"\bon[- ]?site\b|in[- ]office|in[- ]person", low)),
            "hybrid": "hybrid" in low,
            "visa": bool(re.search(r"\bvisa\b", low)),
        },
        "reply_count": len(post.get("kids", [])),
    }


def latest_thread():
    """The newest "Ask HN: Who is hiring?" story by the whoishiring account: (key, item id)."""
    with urllib.request.urlopen(SEARCH, timeout=30) as r:
        hits = [h for h in json.load(r)["hits"] if h["title"].startswith("Ask HN: Who is hiring?")]
    h = hits[0]
    return h["created_at"][:7], int(h["objectID"])  # key like 2026-10


def main():
    threads = json.loads(THREADS.read_text()) if THREADS.exists() else {}
    if len(sys.argv) > 2:
        key, story_id = sys.argv[1], int(sys.argv[2])
    elif len(sys.argv) == 2:
        key = sys.argv[1]
        if key not in threads:
            sys.exit(f"unknown thread {key!r}; known: {', '.join(threads) or 'none'}")
        story_id = threads[key]
    else:
        key, story_id = latest_thread()
        # already registered under another key (e.g. by hand): keep that key
        key = next((k for k, v in threads.items() if v == story_id), key)
    if threads.get(key) != story_id:
        threads.pop(key, None)
        threads[key] = story_id  # newest last
        DATA.mkdir(parents=True, exist_ok=True)
        THREADS.write_text(json.dumps(threads, indent=2) + "\n")

    story = fetch(story_id)
    kids = story.get("kids", [])
    print(f"{story.get('title')}: {len(kids)} top-level posts")
    with ThreadPoolExecutor(16) as ex:
        items = [i for i in ex.map(fetch, kids) if i]

    out_dir = DATA / key
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "posts.json"
    existing = {p["id"]: p for p in json.loads(out.read_text())["posts"]} if out.exists() else {}
    now = datetime.now(timezone.utc).isoformat()

    seen, added, updated = set(), 0, 0
    for it in items:
        old = existing.get(it["id"])
        if it.get("deleted") or it.get("dead"):
            if old:
                old["removed"] = True
            continue
        rec = extract(it)
        rec["first_seen"] = old["first_seen"] if old else now
        rec["removed"] = False
        if not old:
            added += 1
        elif old["html"] != rec["html"]:
            updated += 1
            rec["edited_at"] = now
        existing[it["id"]] = rec
        seen.add(it["id"])
    for pid, p in existing.items():
        if pid not in seen and pid not in {i["id"] for i in items}:
            p["removed"] = True

    posts = sorted(existing.values(), key=lambda p: p["posted_at"])
    out.write_text(json.dumps({
        "thread": {"key": key, "id": story_id, "title": story.get("title"),
                   "url": f"https://news.ycombinator.com/item?id={story_id}"},
        "scraped_at": now,
        "posts": posts,
    }, indent=1, ensure_ascii=False))
    live = sum(not p["removed"] for p in posts)
    print(f"{live} live posts (+{added} new, {updated} edited) -> {out}")


if __name__ == "__main__":
    main()
