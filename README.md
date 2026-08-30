# jobpipe

[![tests](https://github.com/kreal911/jobpipe/actions/workflows/tests.yml/badge.svg)](https://github.com/kreal911/jobpipe/actions/workflows/tests.yml)

Pulls job postings straight from company ATS feeds, scores them against your
lanes, and writes a ranked list. Python 3.11+, standard library only. No
install, no API keys, no accounts.

It reads the same public JSON endpoints the company's own careers page reads.
It does not touch LinkedIn or Indeed HTML — that is what gets accounts locked.

---

## Five minutes to first run

```bash
cd jobpipe

# 1. Smoke test with the recorded sample data. Proves the whole chain works.
python3 -m jobpipe run --offline
open out/digest.html

# 2. Check which of the seeded companies actually resolve.
python3 -m jobpipe verify

# 3. Edit config/sources.toml — delete what failed, add your own companies.
# 4. Real run.
python3 -m jobpipe run
open out/digest.html
```

`run` = `fetch` + `score` + `digest`. That is the command to schedule.

---

## The two files you edit

**`config/sources.toml`** — which company boards to poll.
The seeded entries are **unverified guesses**. Run `verify` before trusting any
of them. To add a company, open its Careers link and read the URL you land on:

| You land on | Add |
|---|---|
| `boards.greenhouse.io/acme` | `type="greenhouse"`, `token="acme"` |
| `jobs.lever.co/acme` | `type="lever"`, `slug="acme"` |
| `jobs.ashbyhq.com/acme` | `type="ashby"`, `board="acme"` |
| `jobs.smartrecruiters.com/Acme` | `type="smartrecruiters"`, `company_id="Acme"` |
| `acme.wd1.myworkdayjobs.com/en-US/External` | `type="workday"`, `host="acme.wd1.myworkdayjobs.com"`, `tenant="acme"`, `site="External"`, `detail=true` |

Workday is where most large healthcare, pharma and federal employers live, so
it is worth the extra three fields. Workday needs `search` terms because its
endpoint is a search box, not a full dump.

**Set `detail = true` on every Workday source.** Its list endpoint does not
return the job description at all — only `bulletFields`, i.e. `"R-8801 Full
time"` — and it collapses a multi-office posting to `locationsText =
"2 Locations"`. Without `detail`, a Workday job is scored on its title alone,
every body term in `profile.toml` silently fails to match, and the location
filter throws the posting out because `"2 Locations"` matches no city.
`detail = true` fetches the real posting per job, which supplies the full
description plus the actual city list. It costs one extra request per posting
and `http.py` throttles to one request per host per second, so keep `max`
modest — 40 is a reasonable nightly setting.

**`config/profile.toml`** — what counts as a good job.
Four lanes are set up: AI Governance & Risk, QA / Quality Engineering
Leadership, SAP Functional / TM / SCM, and Program / Product Management.
A job is scored against every lane and keeps its best result.

---

## How scoring works

Nothing is hidden. Every point traces to a term you can see and change.

1. **Hard filters run first.** Wrong location, excluded title word (intern,
   junior, account executive), older than 45 days → dropped, with the reason
   recorded.
2. **Each lane has a gate.** If none of a lane's `require_any` terms appear,
   that lane scores zero. This is what stops a nursing job from matching the
   AI lane on the word "risk".

   A lane may also set **`require_title_any`**, a second gate matched against
   the title only (title + department, the same haystack the x3 title
   multiplier uses). `require_any` searches the body too, which makes it
   useless as a subject filter — every AI lab mentions "ai" and every employer
   mentions "compliance" somewhere in a job description. The title gate asks
   what the job *is*, not what its description happens to name. The Program /
   Product Management lane uses it so that it matches PM roles about AI or
   regulated work, rather than every program manager anywhere; without it that
   lane took 36 of 47 APPLY slots. A lane that omits the key is unaffected.
3. **Terms carry weights.** A term in the title counts triple; in the body it
   counts once no matter how often it repeats. Penalty terms subtract.
4. **Raw points are divided by that lane's `target`,** so lanes are
   comparable. `target = 85` means 85 raw points reads as a score of 100.
   Scores are not capped — real ranking needs spread at the top.
5. **Small bonuses:** remote +4, salary posted +3, posted in the last week +5.
   These only apply to a job that already matched a lane.
6. **Verdict:** 50+ = APPLY, 30–49 = LOOK, below = dropped. These thresholds
   are calibrated to real postings (see the note in `profile.toml`), not to
   the fixtures — fixture jobs score 106–110, live ones top out near 65.

To see the arithmetic on any job:

```bash
python3 -m jobpipe score --rescore --explain --top 20
```

That prints each matched term and its points. If a job is ranked wrong, the
line that did it is right there — change the weight in `profile.toml` and
re-run `score --rescore`. Tuning this for a week is what makes the tool
worth having.

---

## Commands

| Command | Does |
|---|---|
| `verify` | Ping every source, print OK + count or the error |
| `fetch` | Pull postings into `data/jobs.db` (`--offline` uses fixtures) |
| `score` | Score new jobs (`--rescore` for all, `--explain` for arithmetic) |
| `digest` | Write `out/digest.{md,html,json}` |
| `packets` | Write an application packet per APPLY role into `out/packets/` |
| `run` | fetch + score + digest + packets |
| `mark KEY STATUS` | `shortlist` / `applied` / `rejected` / `ignored` |
| `stats` | Counts by source, status, verdict, lane |

Marking a job `applied`, `rejected` or `ignored` removes it from future
digests. The job key is in `digest.json`.

---

## Duplicates

The same posting seen twice on the same board updates in place. The same role
found on two different boards is stored but flagged `is_primary = 0`, so it
appears once. A seniority change ("Senior X" vs "X") is treated as a different
role on purpose.

---

## Scheduling it nightly on the Mac Mini

```bash
cp com.jobpipe.nightly.plist ~/Library/LaunchAgents/
# edit the two paths inside it first
launchctl load ~/Library/LaunchAgents/com.jobpipe.nightly.plist
```

Runs at 6:00 AM. Logs to `logs/`. To stop it:
`launchctl unload ~/Library/LaunchAgents/com.jobpipe.nightly.plist`

---

## What it deliberately does not do

- No LinkedIn or Indeed scraping.
- No auto-apply. Applications stay a human decision.
- No résumé generation yet — `out/digest.json` is the handoff file for that.

---

## The database

`data/jobs.db`, SQLite, three tables: `jobs`, `scores`, `runs`. Query it
directly for anything the CLI does not cover:

```sql
SELECT j.title, j.company, s.score, s.lane
FROM jobs j JOIN scores s USING(job_key)
WHERE s.verdict = 'keep' AND j.status = 'new'
ORDER BY s.score DESC;
```

---

## Layout

```
config/sources.toml    which boards to poll
config/profile.toml    lanes, weights, filters, thresholds
jobpipe/sources/       one adapter per ATS
jobpipe/score.py       the scoring logic
jobpipe/store.py       SQLite schema, upsert, dedupe
jobpipe/digest.py      markdown / html / json output
fixtures/              recorded API payloads for offline testing
tests/test_pipeline.py 69 checks, no network
out/                   generated digests
data/jobs.db           generated database
```

Run the tests any time you change scoring:

```bash
python3 tests/test_pipeline.py
```
