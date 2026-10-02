# hn_whos_hiring

Rank every post in Hacker News' monthly "Who is hiring?" thread against your resume, then draft emails and prefill applications from a local dashboard.

Needs Python 3.10+ and [Claude Code](https://claude.com/claude-code) (scoring and emails use `claude -p` with your login, no API key). No pip installs.

## Run

```bash
python3 scrape.py 10_1_26 49922569   # once per month: a key you choose + the thread's HN item id
python3 server.py                    # then open http://localhost:8787
```

1. **Profile**: upload your resume PDF and fill in role focus, locations and standard application answers.
2. **Refresh**: fetches new posts and scores them (about $0.01 per post).
3. Review the matches, sorted by fit. Keys: `j`/`k` move, `i` interested, `a` applied, `s` skip, `d` draft email, `o` open apply link.

Everything you enter stays local, in `profile/` and `data/`, both gitignored.

## Applying with Claude

The dashboard can queue a job for a Gmail draft (with your resume attached) or for form prefill. Open Claude Code in this folder and say **"process the outreach queue"**. It follows `CLAUDE.md`: it fills forms in Chrome from your profile and **never submits or sends**.
