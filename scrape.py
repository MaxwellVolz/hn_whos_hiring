#!/usr/bin/env python3
"""Scrape top-level posts from an HN "Who is hiring?" thread into structured JSON.

    python3 scrape.py 10_1_26            # thread key from threads.json
    python3 scrape.py 10_1_26 49922569   # register a new key -> item id

Re-runnable: new posts are added, edited posts are updated, deleted/dead posts are
flagged (not removed), and `first_seen` is preserved. Output:
data/<key>/posts.json
"""
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
API = "https://hacker-news.firebaseio.com/v0/item/{}.json"


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


def main():
    threads_file = ROOT / "threads.json"
    threads = json.loads(threads_file.read_text()) if threads_file.exists() else {}
    if len(sys.argv) < 2:
        sys.exit(f"usage: scrape.py <key> [item_id]   known: {', '.join(threads)}")
    key = sys.argv[1]
    if len(sys.argv) > 2:
        threads[key] = int(sys.argv[2])
        threads_file.write_text(json.dumps(threads, indent=2) + "\n")
    story_id = threads[key]

    story = fetch(story_id)
    kids = story.get("kids", [])
    print(f"{story.get('title')}: {len(kids)} top-level posts")
    with ThreadPoolExecutor(16) as ex:
        items = [i for i in ex.map(fetch, kids) if i]

    out_dir = ROOT / "data" / key
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
    print(f"{live} live posts (+{added} new, {updated} edited) -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
