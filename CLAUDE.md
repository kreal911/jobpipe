# jobpipe — notes for Claude Code

Python 3.11+, standard library only. Do not add dependencies; `tomllib`,
`sqlite3`, `urllib` and `html` cover everything here.

## Before you change scoring
Run `python3 tests/test_pipeline.py` first, change the code, run it again.
69 checks, no network, under a second. If a check fails, that is the answer.

## Adding a new ATS source
1. `jobpipe/sources/<name>.py` exposing `parse(payload, company) -> list[Job]`
   and `fetch(entry) -> list[Job]`.
2. Register it in `jobpipe/sources/__init__.py`.
3. Add a fixture in `fixtures/<name>.json` in the real API's shape, and a
   parse check in `tests/test_pipeline.py`.
4. Document the config keys in the module docstring and in README's table.

## Rules
- Every point a job scores must trace to a term in `config/profile.toml`.
  Do not add scoring logic that is not visible in the config.
- `Job.job_key` identifies a posting on one board; `Job.dupe_key` identifies a
  role across boards. Changing either invalidates the existing database.
- All network calls go through `jobpipe/http.py`, which throttles to one
  request per host per second and sends a real User-Agent. Keep it that way.
- No LinkedIn or Indeed scraping. Public ATS JSON endpoints only.
- `store.upsert` must stay idempotent — re-running `fetch` inserts nothing new.
