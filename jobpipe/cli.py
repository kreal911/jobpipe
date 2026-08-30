"""jobpipe — pull public ATS job feeds, score them against your lanes, rank them.

  python3 -m jobpipe verify      test every source in sources.toml
  python3 -m jobpipe fetch       pull postings into the database
  python3 -m jobpipe score       score anything unscored (--rescore for all)
  python3 -m jobpipe digest      write out/digest.{md,html,json}
  python3 -m jobpipe run         fetch + score + digest (this is the cron target)
  python3 -m jobpipe mark KEY STATUS   shortlist|applied|rejected|ignored
  python3 -m jobpipe stats
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from . import digest as digest_mod
from . import score as score_mod
from . import sources as src
from . import store

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "jobs.db"
DEFAULT_OUT = ROOT / "out"


def load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _label(entry: dict) -> str:
    key = entry.get("token") or entry.get("slug") or entry.get("board") \
        or entry.get("company_id") or entry.get("tenant", "?")
    return f"{entry.get('company', key)} [{entry['type']}:{key}]"


def cmd_verify(args) -> int:
    cfg = load_toml(Path(args.sources))
    bad = 0
    for entry in cfg.get("source", []):
        try:
            jobs = src.fetch(entry)
            print(f"  OK    {_label(entry):<46} {len(jobs):>4} postings")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  FAIL  {_label(entry):<46} {e}")
    print(f"\n{len(cfg.get('source', [])) - bad} of {len(cfg.get('source', []))} sources reachable.")
    return 1 if bad else 0


def _offline_jobs() -> list:
    """Parse the recorded fixtures instead of calling the network."""
    import json
    from .sources import ashby, greenhouse, lever, smartrecruiters, workday
    fix = ROOT / "fixtures"

    def load(n):
        return json.loads((fix / n).read_text())

    jobs = []
    jobs += greenhouse.parse(load("greenhouse.json"), "Acme (fixture)")
    jobs += lever.parse(load("lever.json"), "Globex (fixture)")
    jobs += ashby.parse(load("ashby.json"), "Initech (fixture)")
    jobs += smartrecruiters.parse(load("smartrecruiters.json"), "Umbrella (fixture)")
    jobs += workday.parse(load("workday.json"), "Cedars-Sinai (fixture)",
                          "cedars-sinai.wd1.myworkdayjobs.com", "External")
    return jobs


def cmd_fetch(args) -> int:
    if getattr(args, "offline", False):
        con = store.connect(args.db)
        run_id = store.start_run(con)
        jobs = _offline_jobs()
        seen, new = store.upsert(con, jobs)
        store.finish_run(con, run_id, seen, new, [])
        print(f"  offline fixtures: {seen} postings, {new} new")
        return 0
    cfg = load_toml(Path(args.sources))
    con = store.connect(args.db)
    run_id = store.start_run(con)
    total = new_total = 0
    errors: list[str] = []
    for entry in cfg.get("source", []):
        try:
            jobs = src.fetch(entry)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{_label(entry)}: {e}")
            print(f"  FAIL  {_label(entry)}: {e}", file=sys.stderr)
            continue
        seen, new = store.upsert(con, jobs)
        total += seen
        new_total += new
        print(f"  {_label(entry):<46} {seen:>4} pulled, {new:>3} new")
    store.finish_run(con, run_id, total, new_total, errors)
    print(f"\n{total} postings, {new_total} new.")
    return 0


def cmd_score(args) -> int:
    profile = load_toml(Path(args.profile))
    con = store.connect(args.db)
    rows = store.all_jobs(con) if args.rescore else store.unscored(con)
    results = [score_mod.score_job({k: r[k] for k in r.keys()}, profile) for r in rows]
    store.save_scores(con, results)
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print(f"scored {len(results)}: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    if args.explain:
        top = sorted(results, key=lambda r: -r["score"])[:args.top]
        by_key = {r["job_key"]: r for r in rows}
        for r in top:
            j = by_key[r["job_key"]]
            print(f"\n{r['score']:>5g}  {j['title']}  [{j['company']}]  -> {r['lane']} ({r['verdict']})")
            for reason in r["reasons"]:
                print(f"         {reason['pts']:+6g}  {reason['term']} ({reason['where']})")
    return 0


def cmd_digest(args) -> int:
    con = store.connect(args.db)
    rows = store.ranked(con, limit=args.top)
    last = con.execute("SELECT started FROM runs ORDER BY id DESC LIMIT 1 OFFSET 1").fetchone()
    cutoff = last["started"] if last else ""
    new_keys = {r["job_key"] for r in rows if r["first_seen"] >= cutoff} if cutoff else set()
    run = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    stats = {"fetched": run["fetched"] if run else 0,
             "new": run["new_jobs"] if run else 0,
             "errors": json.loads(run["errors"]) if run and run["errors"] else []}
    files = digest_mod.write_all(rows, new_keys, stats, Path(args.out))
    for f in files:
        print(f"  wrote {f}")
    return 0


def cmd_packets(args) -> int:
    """Write one application packet per APPLY role."""
    from . import apply as apply_mod
    applicant_path = Path(getattr(args, "applicant", ROOT / "config" / "applicant.toml"))
    if not applicant_path.exists():
        print(f"  no applicant profile at {applicant_path} -- skipping packets", file=sys.stderr)
        return 0
    app = load_toml(applicant_path)
    con = store.connect(args.db)
    rows = [dict(r) for r in store.ranked(con) if r["verdict"] == "keep"]
    if getattr(args, "all", False):
        rows = [dict(r) for r in store.ranked(con)]
    written = apply_mod.write_packets(rows, app, Path(args.out) / "packets")
    todo = sum(1 for k, v in app.get("answers", {}).items()
               if not str(v).strip() or str(v).strip().upper() == "TODO")
    print(f"  wrote {len(written)} packets to {Path(args.out) / 'packets'}")
    if todo:
        print(f"  {todo} unanswered question(s) in config/applicant.toml -- "
              f"they render as >>> TODO <<< in every packet")
    return 0


def cmd_run(args) -> int:
    cmd_fetch(args)
    cmd_score(args)
    rc = cmd_digest(args)
    cmd_packets(args)
    return rc


def cmd_mark(args) -> int:
    con = store.connect(args.db)
    cur = con.execute("UPDATE jobs SET status=? WHERE job_key=?", (args.status, args.job_key))
    con.commit()
    print(f"{cur.rowcount} row(s) set to {args.status}")
    return 0 if cur.rowcount else 1


def cmd_stats(args) -> int:
    con = store.connect(args.db)
    for label, sql in (
        ("jobs by source", "SELECT source AS k, COUNT(*) AS n FROM jobs GROUP BY 1 ORDER BY n DESC"),
        ("jobs by status", "SELECT status AS k, COUNT(*) AS n FROM jobs GROUP BY 1 ORDER BY n DESC"),
        ("scores", "SELECT verdict AS k, COUNT(*) AS n FROM scores GROUP BY 1 ORDER BY n DESC"),
        ("lanes", "SELECT lane AS k, COUNT(*) AS n FROM scores WHERE verdict!='drop' GROUP BY 1 ORDER BY n DESC"),
    ):
        print(f"\n{label}:")
        for r in con.execute(sql):
            print(f"  {str(r['k']):<44} {r['n']}")
    dupes = con.execute("SELECT COUNT(*) n FROM jobs WHERE is_primary=0").fetchone()["n"]
    print(f"\ncross-board duplicates suppressed: {dupes}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="jobpipe", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--sources", default=str(ROOT / "config" / "sources.toml"))
    p.add_argument("--profile", default=str(ROOT / "config" / "profile.toml"))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify").set_defaults(fn=cmd_verify)

    fp = sub.add_parser("fetch")
    fp.add_argument("--offline", action="store_true",
                    help="parse fixtures/ instead of calling the network (smoke test)")
    fp.set_defaults(fn=cmd_fetch)

    sp = sub.add_parser("score")
    sp.add_argument("--rescore", action="store_true", help="re-score every job, not just new ones")
    sp.add_argument("--explain", action="store_true", help="print the point breakdown")
    sp.add_argument("--top", type=int, default=10)
    sp.set_defaults(fn=cmd_score)

    dp = sub.add_parser("digest")
    dp.add_argument("--top", type=int, default=100)
    dp.set_defaults(fn=cmd_digest)

    pp = sub.add_parser("packets")
    pp.add_argument("--all", action="store_true",
                    help="packet every ranked role, not just the APPLY bucket")
    pp.set_defaults(fn=cmd_packets)

    rp = sub.add_parser("run")
    rp.add_argument("--offline", action="store_true",
                    help="use fixtures/ instead of the network (smoke test)")
    rp.add_argument("--rescore", action="store_true")
    rp.add_argument("--explain", action="store_true")
    rp.add_argument("--top", type=int, default=100)
    rp.set_defaults(fn=cmd_run)

    mp = sub.add_parser("mark")
    mp.add_argument("job_key")
    mp.add_argument("status", choices=["new", "shortlist", "applied", "rejected", "ignored"])
    mp.set_defaults(fn=cmd_mark)

    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    return args.fn(args)
