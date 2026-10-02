# hn_whos_hiring

Python stdlib only. `scrape.py` writes `data/<key>/posts.json`; `match.py` scores into `matches.json`;
`outreach.py` writes `outreach.json`; `server.py` serves `dashboard.html` and `setup.html` (the Profile page).
`candidate.py` owns the profile in `profile/`: `profile.json`, `resume.pdf`, `resume.md`.
`profile/` and `data/` hold personal data and are gitignored. Never commit them or copy their contents into tracked files.

## Processing the outreach queue

`data/<key>/queue.json` holds `{id, action, status: "pending"}` entries queued from the dashboard.
For each pending entry, join it with `posts.json`, `matches.json` and `outreach.json` by id, then:

- `gmail_draft`: create a Gmail **draft** (never send) with the to, subject and body from `outreach.json`,
  and attach `profile/resume.pdf`. Set the entry to `status: "done"` with `draft_url`, and set the job to
  `drafted` in `state.json`.
- `prefill`: open `apply_url` in a Chrome tab and fill the form from `profile/profile.json`, attaching
  `profile/resume.pdf`. Write free-text answers from `profile/resume.md` and the match's reasons; never
  invent numbers or claims. Only check skill boxes the profile's `skills` field supports. Leave voluntary
  demographic questions blank. **Never click submit.** Leave the tab open and set `status: "ready_for_review"`.
  If a required answer isn't in the profile, ask the user, then offer to add it to their profile.
- Never create accounts or enter passwords on a job site. If a form needs a login, set `status: "needs_login"` and move on.
- Ashby forms embedded on company sites can be opened directly at `jobs.ashbyhq.com/<org>/<job id>/application`.
  Their Yes/No buttons don't respond to ref clicks; click them by coordinates and check with a screenshot.
