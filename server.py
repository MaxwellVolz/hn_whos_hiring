#!/usr/bin/env python3
"""Local review dashboard.

    python3 server.py [thread_key] [--port 8787]   then open http://localhost:8787

The Profile page (/setup) edits profile/. Per-job state (status, notes, edited drafts)
is saved to data/<key>/state.json and data/<key>/outreach.json. Jobs queued for Gmail
drafts or form prefill go to data/<key>/queue.json, for Claude Code to process.
"""
import argparse
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import candidate
import outreach
from config import DATA, THREADS

ROOT = Path(__file__).parent
LOCK = threading.Lock()
REFRESH = {"running": False, "log": ""}


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def jobs(key):
    d = DATA / str(key)
    posts = load(d / "posts.json", {"posts": []})
    matches, state = load(d / "matches.json", {}), load(d / "state.json", {})
    drafts, queue = load(d / "outreach.json", {}), load(d / "queue.json", [])
    queued = {}
    for q in queue:
        if q["status"] == "pending":
            queued.setdefault(str(q["id"]), []).append(q["action"])
    rows = []
    for p in posts["posts"]:
        if p["removed"]:
            continue
        sid = str(p["id"])
        m = {k: v for k, v in matches.get(sid, {}).items() if not k.startswith("_")}
        rows.append({"post": {k: p[k] for k in ("id", "author", "posted_at", "hn_url", "header", "company_guess",
                                                 "text", "emails", "urls", "salary_mentions", "flags")},
                     "match": m or None, "state": state.get(sid, {"status": "new"}),
                     "draft": drafts.get(sid), "queued": queued.get(sid, [])})
    rows.sort(key=lambda r: -(r["match"] or {}).get("score", -1))
    return {"key": key, "thread": posts.get("thread"), "scraped_at": posts.get("scraped_at"),
            "profile_ready": candidate.ready(), "refresh": REFRESH, "jobs": rows}


def run(*args):
    proc = subprocess.Popen([sys.executable, "-u", *args], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        REFRESH["log"] += line
    return proc.wait() == 0


def refresh():
    """Scrape (finding this month's thread unless one was pinned), then score. Runs in a background thread."""
    REFRESH.update(running=True, log="")
    try:
        if run(str(ROOT / "scrape.py"), *([H.key] if H.pinned else [])):
            if not H.pinned:
                H.key = next(reversed(load(THREADS, {})))
            run(str(ROOT / "match.py"), H.key)
    finally:
        REFRESH["running"] = False


class H(BaseHTTPRequestHandler):
    key = None
    pinned = False  # True when a thread key was given on the command line

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not candidate.ready():
                self.send_response(302)
                self.send_header("Location", "/setup")
                return self.end_headers()
            return self.send(200, (ROOT / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/setup":
            return self.send(200, (ROOT / "setup.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/jobs":
            return self.send(200, jobs(self.key))
        if self.path == "/api/profile":
            return self.send(200, {"fields": candidate.FIELDS, "data": candidate.load(),
                                   "resume_text": candidate.resume_text(), "has_pdf": candidate.PDF.exists()})
        if self.path == "/resume.pdf" and candidate.PDF.exists():
            return self.send(200, candidate.PDF.read_bytes(), "application/pdf")
        if self.path == "/api/refresh":
            return self.send(200, REFRESH)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/resume":  # raw PDF bytes
            try:
                return self.send(200, {"resume_text": candidate.save_resume_pdf(raw)})
            except Exception as e:
                return self.send(500, {"error": str(e)[:500]})
        body = json.loads(raw or b"{}")
        d = DATA / str(self.key)
        sid = str(body.get("id"))
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.path == "/api/profile":
                candidate.save(body["data"])
                if "resume_text" in body:
                    candidate.DIR.mkdir(parents=True, exist_ok=True)
                    candidate.TEXT.write_text(body["resume_text"].strip() + "\n")
                return self.send(200, {"ok": True, "ready": candidate.ready()})
            if self.path == "/api/refresh":
                if not REFRESH["running"]:
                    threading.Thread(target=refresh, daemon=True).start()
                return self.send(200, {"ok": True})
            if self.path == "/api/state":
                with LOCK:
                    st = load(d / "state.json", {})
                    cur = st.get(sid, {"status": "new"})
                    cur.update({k: body[k] for k in ("status", "notes") if k in body}, updated_at=now)
                    st[sid] = cur
                    save(d / "state.json", st)
                return self.send(200, cur)
            if self.path == "/api/draft":  # generate (slow: one headless Claude call)
                return self.send(200, outreach.draft(self.key, int(sid), force=bool(body.get("force"))))
            if self.path == "/api/draft/save":
                with LOCK:
                    dr = load(d / "outreach.json", {})
                    dr[sid] = {**dr.get(sid, {}), **{k: body[k] for k in ("to", "subject", "body")}, "edited_at": now}
                    save(d / "outreach.json", dr)
                return self.send(200, dr[sid])
            if self.path == "/api/queue":
                with LOCK:
                    q = load(d / "queue.json", [])
                    if not any(x["id"] == int(sid) and x["action"] == body["action"] and x["status"] == "pending" for x in q):
                        q.append({"id": int(sid), "action": body["action"], "status": "pending", "queued_at": now})
                    save(d / "queue.json", q)
                return self.send(200, {"ok": True})
        except Exception as e:
            return self.send(500, {"error": str(e)[:500]})
        self.send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threads = load(THREADS, {})
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", default=next(reversed(threads), None),
                    help="thread key from data/threads.json (default: the newest)")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    H.key, H.pinned = a.key, a.key is not None and a.key != next(reversed(threads), None)
    print(f"dashboard: http://localhost:{a.port}  ({a.key or 'click Refresh to fetch this month'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
