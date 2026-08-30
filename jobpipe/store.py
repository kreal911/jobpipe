"""SQLite store: jobs, scores, run log. Dedupe on job_key and dupe_key."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .model import Job, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_key         TEXT PRIMARY KEY,
  dupe_key        TEXT NOT NULL,
  source          TEXT NOT NULL,
  company         TEXT NOT NULL,
  external_id     TEXT NOT NULL,
  title           TEXT NOT NULL,
  url             TEXT NOT NULL,
  location        TEXT,
  remote          INTEGER,
  employment_type TEXT,
  department      TEXT,
  description     TEXT,
  comp_text       TEXT,
  posted_at       TEXT,
  first_seen      TEXT NOT NULL,
  last_seen       TEXT NOT NULL,
  is_primary      INTEGER NOT NULL DEFAULT 1,
  status          TEXT NOT NULL DEFAULT 'new'   -- new|shortlist|applied|rejected|ignored
);
CREATE INDEX IF NOT EXISTS jobs_dupe   ON jobs(dupe_key);
CREATE INDEX IF NOT EXISTS jobs_seen   ON jobs(last_seen);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS scores (
  job_key   TEXT PRIMARY KEY REFERENCES jobs(job_key) ON DELETE CASCADE,
  score     REAL NOT NULL,
  lane      TEXT NOT NULL,
  verdict   TEXT NOT NULL,               -- keep|maybe|drop
  reasons   TEXT NOT NULL,               -- JSON
  scored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scores_score ON scores(score DESC);

CREATE TABLE IF NOT EXISTS runs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  started   TEXT NOT NULL,
  finished  TEXT,
  fetched   INTEGER DEFAULT 0,
  new_jobs  INTEGER DEFAULT 0,
  errors    TEXT
);
"""

FIELDS = ["job_key", "dupe_key", "source", "company", "external_id", "title", "url",
          "location", "remote", "employment_type", "department", "description",
          "comp_text", "posted_at"]


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, jobs: list[Job]) -> tuple[int, int]:
    """Insert new jobs, refresh last_seen on known ones. Returns (seen, new)."""
    now = utcnow()
    new = 0
    for job in jobs:
        row = job.to_row()
        cur = con.execute("SELECT job_key FROM jobs WHERE job_key = ?", (row["job_key"],))
        if cur.fetchone():
            con.execute(
                "UPDATE jobs SET last_seen=?, title=?, url=?, location=?, description=?,"
                " comp_text=?, posted_at=? WHERE job_key=?",
                (now, row["title"], row["url"], row["location"], row["description"],
                 row["comp_text"], row["posted_at"], row["job_key"]))
            continue
        # cross-board duplicate? keep the first one as primary
        dupe = con.execute(
            "SELECT job_key FROM jobs WHERE dupe_key=? LIMIT 1", (row["dupe_key"],)).fetchone()
        cols = ", ".join(FIELDS + ["first_seen", "last_seen", "is_primary"])
        marks = ", ".join("?" * (len(FIELDS) + 3))
        con.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})",
                    [row[f] for f in FIELDS] + [now, now, 0 if dupe else 1])
        new += 1
    con.commit()
    return len(jobs), new


def save_scores(con: sqlite3.Connection, results: list[dict]) -> None:
    now = utcnow()
    con.executemany(
        "INSERT INTO scores (job_key, score, lane, verdict, reasons, scored_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(job_key) DO UPDATE SET score=excluded.score, lane=excluded.lane,"
        " verdict=excluded.verdict, reasons=excluded.reasons, scored_at=excluded.scored_at",
        [(r["job_key"], r["score"], r["lane"], r["verdict"], json.dumps(r["reasons"]), now)
         for r in results])
    con.commit()


def unscored(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT j.* FROM jobs j LEFT JOIN scores s USING(job_key)"
        " WHERE s.job_key IS NULL AND j.is_primary = 1").fetchall()


def all_jobs(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM jobs WHERE is_primary = 1").fetchall()


def ranked(con: sqlite3.Connection, verdicts=("keep", "maybe"), limit: int = 100):
    marks = ",".join("?" * len(verdicts))
    return con.execute(
        f"SELECT j.*, s.score, s.lane, s.verdict, s.reasons FROM jobs j"
        f" JOIN scores s USING(job_key)"
        f" WHERE s.verdict IN ({marks}) AND j.is_primary = 1"
        f"   AND j.status NOT IN ('rejected','ignored','applied')"
        f" ORDER BY s.score DESC, j.first_seen DESC LIMIT ?",
        (*verdicts, limit)).fetchall()


def start_run(con: sqlite3.Connection) -> int:
    cur = con.execute("INSERT INTO runs (started) VALUES (?)", (utcnow(),))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, fetched: int, new: int, errors: list[str]) -> None:
    con.execute("UPDATE runs SET finished=?, fetched=?, new_jobs=?, errors=? WHERE id=?",
                (utcnow(), fetched, new, json.dumps(errors), run_id))
    con.commit()
