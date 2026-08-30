"""Offline end-to-end test: fixtures -> parse -> store -> score -> digest.

Run:  python3 tests/test_pipeline.py
No network. Uses fixtures/ recorded in the shape each ATS documents.
"""
from __future__ import annotations

import json
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobpipe import digest as digest_mod           # noqa: E402
from jobpipe import score as score_mod             # noqa: E402
from jobpipe import store                          # noqa: E402
from jobpipe.sources import ashby, greenhouse, lever, smartrecruiters, workday  # noqa: E402

FIX = ROOT / "fixtures"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


def load(name):
    return json.loads((FIX / name).read_text())


def main() -> int:
    print("\n== parse ==")
    jobs = []
    jobs += greenhouse.parse(load("greenhouse.json"), "Acme")
    jobs += lever.parse(load("lever.json"), "Globex")
    jobs += ashby.parse(load("ashby.json"), "Initech")
    jobs += smartrecruiters.parse(load("smartrecruiters.json"), "Umbrella")
    jobs += workday.parse(load("workday.json"), "Cedars-Sinai",
                          "cedars-sinai.wd1.myworkdayjobs.com", "External")

    check("all five adapters returned jobs", len(jobs) == 4 + 3 + 2 + 2 + 2,
          f"got {len(jobs)}")
    check("ashby skips unlisted postings",
          all(j.external_id != "ash-3003" for j in jobs))
    check("greenhouse HTML entities decoded",
          all("&lt;" not in j.description and "<p>" not in j.description for j in jobs))
    check("every job has a url", all(j.url for j in jobs))
    check("every job has an id", all(j.external_id for j in jobs))
    gh = next(j for j in jobs if j.external_id == "4001")
    check("greenhouse department parsed", gh.department == "Risk & Compliance", gh.department)
    lev = next(j for j in jobs if j.external_id == "lev-7001")
    check("lever comp parsed", "140000" in lev.comp_text, lev.comp_text)
    check("lever list content folded into description", "SAP EWM" in lev.description)
    ash = next(j for j in jobs if j.external_id == "ash-3001")
    check("ashby comp parsed", ash.comp_text == "$165K - $200K", ash.comp_text)
    check("ashby remote flag", ash.remote is True)
    wd = next(j for j in jobs if j.external_id.endswith("R-8801"))
    check("workday url built from externalPath",
          wd.url == "https://cedars-sinai.wd1.myworkdayjobs.com/en-US/External/job/Los-Angeles/Manager-AI-Risk_R-8801",
          wd.url)

    print("\n== workday detail merge ==")
    # The /jobs list endpoint returns no jobDescription and collapses a
    # multi-office posting to "2 Locations". detail=true must repair both.
    thin = workday.parse(
        {"jobPostings": [{"title": "Senior Associate, Quantitative Analyst - Model Risk Office",
                          "externalPath": "/job/Riverwoods-IL/x_R249475-1",
                          "locationsText": "3 Locations",
                          "bulletFields": ["R249475", "Full time"]}]},
        "Capital One", "capitalone.wd12.myworkdayjobs.com", "Capital_One")[0]
    check("list endpoint alone yields no usable description", len(thin.description) < 40,
          f"{len(thin.description)} chars: {thin.description!r}")
    check("list endpoint alone yields an opaque location",
          thin.location == "3 Locations", thin.location)

    merged = workday.merge_detail(thin, load("workday_detail.json"))
    check("detail merge supplies the real description",
          "model validation" in merged.description and "model risk" in merged.description,
          f"{len(merged.description)} chars")
    check("detail merge strips HTML from the description",
          "<p>" not in merged.description and "<b>" not in merged.description)
    check("detail merge resolves the opaque location into real cities",
          "Riverwoods, IL" in merged.location and "McLean, VA" in merged.location,
          merged.location)
    check("detail merge keeps employment type", merged.employment_type == "Full time",
          merged.employment_type)
    check("a merged job now passes the DC location filter",
          score_mod.hard_filter(
              {"title": merged.title, "description": merged.description,
               "location": merged.location, "remote": merged.remote, "posted_at": ""},
              tomllib.load(open(ROOT / "config" / "profile.toml", "rb"))["filters"]) is None)

    print("\n== store / dedupe ==")
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "jobs.db"
        con = store.connect(db)
        store.start_run(con)
        seen, new = store.upsert(con, jobs)
        check("first upsert inserts everything", new == len(jobs), f"{new}/{len(jobs)}")
        seen2, new2 = store.upsert(con, jobs)
        check("second upsert inserts nothing (idempotent)", new2 == 0, f"new2={new2}")

        # same role, different board -> suppressed as a cross-board duplicate
        clone = lever.parse(load("lever.json"), "Globex")[0]
        clone.source = "greenhouse"
        clone.external_id = "gh-dupe-1"
        clone.title = "SAP TM Functional Analyst (Remote)"   # same role, other board
        _, new3 = store.upsert(con, [clone])
        check("cross-board duplicate inserted but not primary", new3 == 1)
        prim = con.execute("SELECT is_primary FROM jobs WHERE external_id='gh-dupe-1'").fetchone()
        check("duplicate marked is_primary=0", prim["is_primary"] == 0)
        check("all_jobs excludes the duplicate", len(store.all_jobs(con)) == len(jobs))

        # documented boundary: a seniority change is a DIFFERENT role, not a duplicate
        near = lever.parse(load("lever.json"), "Globex")[0]
        near.source, near.external_id = "greenhouse", "gh-near-1"
        near.title = "Senior SAP TM Functional Analyst"
        store.upsert(con, [near])
        np = con.execute("SELECT is_primary FROM jobs WHERE external_id='gh-near-1'").fetchone()
        check("seniority change is NOT treated as a duplicate", np["is_primary"] == 1)

        print("\n== score ==")
        profile = tomllib.load(open(ROOT / "config" / "profile.toml", "rb"))
        rows = store.all_jobs(con)
        results = [score_mod.score_job({k: r[k] for k in r.keys()}, profile) for r in rows]
        store.save_scores(con, results)
        by_ext = {r["external_id"]: r for r in rows}
        res = {r["job_key"]: r for r in results}

        def got(ext):
            return res[by_ext[ext]["job_key"]]

        check("intern rejected by hard filter", got("4002")["verdict"] == "drop",
              str(got("4002")["reasons"]))
        check("intern rejection names the term",
              "intern" in json.dumps(got("4002")["reasons"]))
        check("account executive rejected", got("lev-7002")["verdict"] == "drop")
        check("nurse role scores drop", got("R-8801".replace("R-8801", "/job/Los-Angeles/RN-ICU_R-8802"))["verdict"] == "drop")
        check("out-of-area role rejected (New York)", got("lev-7003")["verdict"] == "drop",
              str(got("lev-7003")["reasons"]))
        check("warehouse role rejected (Austin)", got("sr-5002")["verdict"] == "drop")

        gov = got("4001")
        check("AI governance role lands in the AI lane",
              gov["lane"] == "AI Governance & Risk", gov["lane"])
        check("AI governance role is a keep", gov["verdict"] == "keep", str(gov["score"]))
        qa = got("4004")
        check("QA manager role lands in the QA lane",
              qa["lane"] == "QA / Quality Engineering Leadership", qa["lane"])
        check("QA manager role is a keep", qa["verdict"] == "keep", str(qa["score"]))
        sap = got("lev-7001")
        check("SAP TM role lands in the SAP lane",
              sap["lane"] == "SAP Functional / TM / SCM", sap["lane"])
        check("SAP TM role is a keep", sap["verdict"] == "keep", str(sap["score"]))
        tpm = got("ash-3001")
        check("TPM responsible-AI role is keep or maybe",
              tpm["verdict"] in ("keep", "maybe"), f"{tpm['lane']} {tpm['score']}")
        ic = got("ash-3002")
        check("IC platform engineer does not reach keep", ic["verdict"] != "keep",
              f"{ic['score']} {ic['lane']}")
        check("every keep carries its reasons",
              all(r["reasons"] for r in results if r["verdict"] == "keep"))
        check("scores are non-negative", all(r["score"] >= 0 for r in results))
        check("scores spread at the top (no ties at a ceiling)",
              len({r["score"] for r in results if r["verdict"] == "keep"}) > 1,
              str(sorted((r["score"] for r in results if r["verdict"] == "keep"), reverse=True)))
        check("bonus points never create a score without a lane",
              all(r["lane"] != "-" or r["score"] == 0 for r in results))

        print("\n== require_title_any gate ==")
        # require_any searches the body, so a generic TPM at an AI company
        # passes it on the word "ai" in the description. The title gate is
        # what distinguishes a Responsible AI TPM from a Reliability TPM.
        pm_lane = next(l for l in profile["lanes"] if l["name"].startswith("Program /"))
        body = "we build ai systems with governance and compliance in a regulated industry"
        on_topic, _ = score_mod.score_lane(
            "technical program manager, responsible ai", body, pm_lane)
        off_topic, reasons = score_mod.score_lane(
            "technical program manager, reliability", body, pm_lane)
        check("title gate keeps an on-topic PM role", on_topic > 0, str(on_topic))
        check("title gate rejects a generic PM role despite ai in the body",
              off_topic == 0, str(off_topic))
        check("title-gate rejection is traceable to the config key",
              reasons and reasons[0]["term"] == "require_title_any", str(reasons))
        check("a lane with no require_title_any is unaffected",
              score_mod.score_lane("qa manager", "test strategy and uat",
                                   next(l for l in profile["lanes"]
                                        if l["name"].startswith("QA /")))[0] > 0)

        print("\n== digest ==")
        ranked = store.ranked(con)
        check("ranked returns only keep/maybe",
              all(r["verdict"] in ("keep", "maybe") for r in ranked), f"{len(ranked)} rows")
        check("ranked is sorted descending",
              all(ranked[i]["score"] >= ranked[i + 1]["score"] for i in range(len(ranked) - 1)))
        out = Path(tmp) / "out"
        files = digest_mod.write_all(ranked, {ranked[0]["job_key"]},
                                     {"fetched": len(jobs), "new": len(jobs), "errors": []}, out)
        check("three digest files written", len(files) == 3)
        md = (out / "digest.md").read_text()
        htm = (out / "digest.html").read_text()
        js = json.loads((out / "digest.json").read_text())
        check("markdown lists the top role", ranked[0]["title"] in md)
        check("markdown shows the why line", "why:" in md)
        check("html escapes and closes", htm.startswith("<!doctype html") and htm.endswith("</html>"))
        check("html marks the new job", "NEW" in htm)
        check("json rows carry parsed reasons",
              isinstance(js[0]["reasons"], list) and bool(js[0]["reasons"]))
        check("json drops the bulky description", "description" not in js[0])

        print("\n== application packets ==")
        from jobpipe import apply as apply_mod            # noqa: E402
        applicant = {
            "identity": {"name": "Test Person", "email": "t@example.com",
                         "phone": "555", "location": "LA", "linkedin": "x"},
            "education": {"degrees": ["B.S. Computer Science (2008)"]},
            "certifications": {"certs": ["ISTQB Certified Tester (2022)"]},
            "experience": [{"title": "QA Manager", "company": "Acme",
                            "dates": "2021-2025", "place": "LA",
                            "themes": ["test plans", "quality assurance"]}],
            "resumes": {"QA / Quality Engineering Leadership": "qa.pdf", "default": "d.pdf"},
            "answers": {"work_authorization": "TODO", "salary_expectation": "$200k"},
            "voice": {"closing": "TODO"},
        }
        prow = dict(ranked[0])
        pdir = Path(tmp) / "packets"
        made = apply_mod.write_packets([prow], applicant, pdir)
        check("a packet directory is written per role", len(made) == 1)
        cl = (made[0] / "cover_letter.md").read_text(encoding="utf-8")
        ans = (made[0] / "answers.md").read_text(encoding="utf-8")
        pk = (made[0] / "packet.md").read_text(encoding="utf-8")
        check("cover letter names the real applicant", "Test Person" in cl)
        check("cover letter names the employer", prow["company"] in cl)
        check("cover letter cites only verified credentials",
              "ISTQB Certified Tester (2022)" in cl and "B.S. Computer Science (2008)" in cl)
        check("an unfilled answer renders as a visible TODO, never a guess",
              apply_mod.TODO in ans and "work_authorization" not in ans.replace("Work authorization", ""))
        check("a filled answer is used verbatim", "$200k" in ans)
        check("unanswered closing is flagged rather than invented",
              apply_mod.TODO in cl)
        check("packet carries the apply url and the mark command",
              prow["url"] in pk and prow["job_key"] in pk)
        check("resume is chosen by lane", ("qa.pdf" if prow["lane"].startswith("QA /") else "d.pdf") in pk)
        check("index lists every packet", (pdir / "INDEX.md").read_text(encoding="utf-8").count("- [") == 1)
        check("penalty terms are never sold back as strengths",
              all(not t.startswith("phd") for t in apply_mod._matched_terms(prow)))
        # A packet left behind after its role drops out (marked applied) would
        # invite a duplicate application.
        stale = pdir / "Ghost-Co__Removed-Role"
        stale.mkdir(parents=True, exist_ok=True)
        (stale / "packet.md").write_text("stale", encoding="utf-8")
        apply_mod.write_packets([prow], applicant, pdir)
        check("packets for roles no longer in the bucket are pruned", not stale.exists())
        check("the current role's packet survives pruning", made[0].exists())

        print("\n== status handling ==")
        key = ranked[0]["job_key"]
        con.execute("UPDATE jobs SET status='applied' WHERE job_key=?", (key,))
        con.commit()
        check("applied roles drop out of the digest",
              key not in {r["job_key"] for r in store.ranked(con)})

        # Windows will not delete an open file, so the TemporaryDirectory
        # cleanup below raises PermissionError unless SQLite lets go first.
        con.close()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
