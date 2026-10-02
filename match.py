#!/usr/bin/env python3
"""Score every scraped post against your profile and resume, via headless Claude.

    python3 match.py 10_1_26 [--model sonnet] [--batch 6] [--workers 6] [--limit N]

Uses `claude -p` (your existing Claude Code login, no API key). Results are cached
per post in data/<key>/matches.json, keyed on the post's HTML plus the profile and
resume text, so re-runs only score new or edited posts, or everything after you edit
the profile.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import candidate

ROOT = Path(__file__).parent

JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "company": {"type": "string"},
        "company_blurb": {"type": "string", "description": "One line: what the company does"},
        "roles": {"type": "array", "items": {"type": "string"}},
        "best_role": {"type": "string", "description": "The single listed role that best fits the candidate"},
        "role_category": {"enum": ["ai", "fullstack", "hardware", "infra", "other"]},
        "seniority": {"type": "string"},
        "locations": {"type": "array", "items": {"type": "string"}},
        "work_mode": {"enum": ["remote_us", "remote_global", "remote_other_region", "hybrid", "onsite", "unknown"]},
        "location_fit": {"enum": ["good", "maybe", "bad"]},
        "salary": {"type": "string", "description": "As stated, or empty"},
        "equity": {"type": "boolean"},
        "stage": {"type": "string", "description": "e.g. seed, Series B, public, bootstrapped; empty if unknown"},
        "tech": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reasons": {"type": "array", "items": {"type": "string"}, "description": "2-4 short, specific reasons it fits"},
        "concerns": {"type": "array", "items": {"type": "string"}, "description": "0-3 short dealbreakers or gaps"},
        "apply_method": {"enum": ["email", "url", "both", "unknown"]},
        "apply_email": {"type": "string"},
        "apply_url": {"type": "string", "description": "The most direct application/job link, not the homepage if a better one exists"},
        "apply_instructions": {"type": "string", "description": "Any specific ask: subject line, mention HN, include X"},
        "outreach_hook": {"type": "string", "description": "One sentence connecting the candidate's concrete experience to this company's need"},
    },
    "required": ["id", "company", "roles", "best_role", "role_category", "work_mode", "location_fit", "score",
                 "reasons", "concerns", "apply_method", "apply_email", "apply_url", "outreach_hook",
                 "company_blurb", "salary", "equity", "stage", "tech", "locations", "seniority", "apply_instructions"],
}
SCHEMA = {"type": "object", "properties": {"jobs": {"type": "array", "items": JOB_SCHEMA}}, "required": ["jobs"]}

SYSTEM = """You are a sharp technical recruiter scoring Hacker News "Who is hiring?" posts for ONE candidate.
Score each post 0-100 for how worth this candidate's time it is to apply, using the profile:
- Role focus is listed in priority order: the first item weighs most. Set role_category to the closest
  of ai / fullstack / hardware / infra / other.
- Location: a posting the candidate can't work from (not in their acceptable locations, or remote
  restricted to a region they don't live in) is a near-dealbreaker (cap score at 30).
- Seniority: postings well below the candidate's level, or outside their field, score low (<20).
- Any listed dealbreaker caps the score at 20.
- Reward concrete overlap with the resume. Penalize hard requirements the candidate clearly lacks.
Calibrate: 85+ = apply today, 70-84 = strong, 50-69 = worth a look, <50 = probably skip.
Reasons and concerns must be specific to the post, not generic. Extract apply details exactly as written in the post.
Return one entry per post, using the post's id."""


def digest(*parts):
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def score_batch(posts, context, model):
    body = "\n\n".join(f"<post id=\"{p['id']}\">\n{p['text']}\n</post>" for p in posts)
    prompt = f"{context}\n\nScore these {len(posts)} posts:\n\n{body}"
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "",
         "--strict-mcp-config", "--no-session-persistence",
         "--append-system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)],
        input=prompt, capture_output=True, text=True, timeout=600,
    )
    out = json.loads(proc.stdout)
    if out.get("is_error") or "structured_output" not in out:
        raise RuntimeError(out.get("result") or proc.stderr[-500:])
    return out["structured_output"]["jobs"], out.get("total_cost_usd", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    data_dir = ROOT / "data" / args.key
    posts = [p for p in json.loads((data_dir / "posts.json").read_text())["posts"] if not p["removed"]]
    if not candidate.ready():
        sys.exit("Fill in your profile first: run server.py and open the Profile page.")
    context = candidate.as_prompt()
    ctx_hash = digest(context, SYSTEM)

    out_path = data_dir / "matches.json"
    cache = json.loads(out_path.read_text()) if out_path.exists() else {}
    todo = [p for p in posts if cache.get(str(p["id"]), {}).get("_hash") != digest(p["html"], ctx_hash)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(posts)} posts, {len(todo)} to score with {args.model}")
    if not todo:
        return

    by_id = {p["id"]: p for p in todo}
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    cost, failed = 0.0, 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(score_batch, b, context, args.model): b for b in batches}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                jobs, c = f.result()
            except Exception as e:  # keep going; failed posts get retried next run
                failed += len(futs[f])
                print(f"  batch failed: {str(e)[:200]}", file=sys.stderr)
                continue
            cost += c
            for j in jobs:
                if j["id"] in by_id:
                    j["_hash"] = digest(by_id[j["id"]]["html"], ctx_hash)
                    cache[str(j["id"])] = j
            out_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
            print(f"  {n}/{len(batches)} batches  (${cost:.2f})")
    print(f"done: {len(cache)} scored, {failed} failed, ${cost:.2f} -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
