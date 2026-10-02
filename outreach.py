#!/usr/bin/env python3
"""Write a tailored outreach email for one or more scored posts.

    python3 outreach.py 10_1_26 49922584 [49922609 ...] [--model sonnet] [--force]

Drafts land in data/<key>/outreach.json; the dashboard shows, edits and opens them.
Nothing is sent. Sending stays with you, from Gmail.
"""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import candidate
from match import ROOT

SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
}

SYSTEM = """Write a short cold application email from the candidate to a company that posted on
Hacker News "Who is hiring?". Rules:
- 90-150 words of plain text, no markdown, no bullet lists, no em dashes.
- Open with the specific role and that they saw it on HN Who's Hiring. No "I hope this finds you well".
- One or two concrete, verifiable things from the resume that map to what THIS post asks for. Name the
  real projects and employers from the resume; prefer the ones listed under strengths.
- Follow any instructions in the post (subject line format, things to include) exactly.
- Close with a low-friction ask, then end with exactly the signature you are given.
- Say the resume is attached.
- Do not invent facts, numbers, or enthusiasm about things the post doesn't say."""


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def draft(key, post_id, model="sonnet", force=False):
    d = ROOT / "data" / key
    out_path = d / "outreach.json"
    drafts = load(out_path, {})
    if str(post_id) in drafts and not force:
        return drafts[str(post_id)]
    post = next(p for p in load(d / "posts.json", {})["posts"] if p["id"] == post_id)
    match = load(d / "matches.json", {}).get(str(post_id), {})
    prompt = (f"{candidate.as_prompt()}\n\n<signature>\n{candidate.signature()}\n</signature>\n\n"
              f"<post>\n{post['text']}\n</post>\n\n"
              f"Best-fit role: {match.get('best_role', '')}\nWhy it fits: {'; '.join(match.get('reasons', []))}\n"
              f"Hook: {match.get('outreach_hook', '')}\nApply instructions: {match.get('apply_instructions', '')}")
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
         "--strict-mcp-config", "--no-session-persistence",
         "--append-system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)],
        input=prompt, capture_output=True, text=True, timeout=300,
    )
    out = json.loads(proc.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(out.get("result") or proc.stderr[-500:])
    rec = {
        **out["structured_output"],
        "to": match.get("apply_email") or (post["emails"][0] if post["emails"] else ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    drafts = load(out_path, {})  # re-read: the dashboard may have written meanwhile
    drafts[str(post_id)] = rec
    out_path.write_text(json.dumps(drafts, indent=1, ensure_ascii=False))
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for i in a.ids:
        r = draft(a.key, i, a.model, a.force)
        print(f"--- {i} -> {r['to'] or '(no email; use apply link)'}\nSubject: {r['subject']}\n\n{r['body']}\n")
