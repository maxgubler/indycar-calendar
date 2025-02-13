# indycar-calendar

- Fetch / Parse: `indycar_schedule.py`
- Write / Diff: `update.py`
- Git Commit / GitHub Pull Request: `sportstimes.py`

Run `sportstimes.py` as a recurring job / task for the complete flow and continual updates.

## Config

Set `GITHUB_API_KEY` (classic personal access token) in `.env` file to use `sportstimes.py`

Optional / used for debug:

- `SPORTSTIMES_F1_REPO_NAME`
- `DEBUG_NEW_SCHEDULE_PATH`

Run `python <file>.py --help` for argument information. The default arguments should work for the current year.
