"""Application packets: one folder per role, ready to submit.

Reads the scored rows and config/applicant.toml and writes, per job:

    out/packets/<Company>__<Title>/
        cover_letter.md   tailored to the terms that actually matched
        answers.md        the standard ATS questions, pre-filled
        packet.md         apply URL, which resume to attach, checklist

Nothing here invents a fact. Every claim traces to config/applicant.toml
or to the posting itself (its title, company, location, and the scoring
terms that matched). Anything only you can answer stays a visible
>>> TODO <<< rather than a plausible guess -- a wrong answer to "do you
require sponsorship" or "do you hold a clearance" is a misrepresentation
on a real application, not a rounding error.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9]+")
TODO = ">>> TODO <<<"


def _slug(text: str, limit: int = 44) -> str:
    return SAFE.sub("-", (text or "").strip())[:limit].strip("-") or "role"


def _val(answers: dict, key: str) -> str:
    v = str(answers.get(key, "") or "").strip()
    return TODO if not v or v.upper() == "TODO" else v


def _matched_terms(row: dict, limit: int = 8) -> list[str]:
    """The posting's own language, as evidenced by what scored."""
    raw = row.get("reasons")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    out = []
    for r in raw or []:
        term, where = r.get("term", ""), r.get("where")
        if where in ("meta", "-", "filter") or not term:
            continue
        if r.get("pts", 0) <= 0:
            continue                      # never sell a penalty back to them
        out.append(term)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:limit]


def _relevant_experience(app: dict, terms: list[str], limit: int = 3) -> list[dict]:
    """Rank the real roles by overlap with this posting's matched terms."""
    blob = " ".join(terms).lower()
    scored = []
    for i, job in enumerate(app.get("experience", [])):
        hits = sum(1 for th in job.get("themes", []) if th.lower().split()[0] in blob)
        scored.append((-hits, i, job))       # ties keep resume order (recency)
    scored.sort(key=lambda x: (x[0], x[1]))
    return [j for _, _, j in scored[:limit]]


def cover_letter(row: dict, app: dict) -> str:
    ident = app.get("identity", {})
    terms = _matched_terms(row)
    jobs = _relevant_experience(app, terms)
    company = row.get("company", "")
    title = row.get("title", "")

    focus = ", ".join(terms[:5]) if terms else "the scope described in the posting"
    lead = jobs[0] if jobs else {}

    lines = [
        f"{ident.get('name','')}",
        f"{ident.get('location','')} | {ident.get('email','')} | {ident.get('phone','')} | {ident.get('linkedin','')}",
        "",
        f"Re: {title} - {company}",
        "",
        f"Dear {company} Hiring Team,",
        "",
        f"I am applying for the {title} role. Your posting emphasises {focus}, "
        f"which is the substance of what I have been doing rather than an adjacent interest.",
        "",
    ]

    # The CURRENT role is always stated first and always as the current one.
    # `lead` is the most *relevant* role, which is often not the most recent --
    # calling that "most recently" would misstate where he works today.
    current = (app.get("experience") or [{}])[0]
    if current:
        lines += [
            f"I am currently {current.get('title','')} at {current.get('company','')} "
            f"({current.get('dates','')}), working on "
            f"{'; '.join(current.get('themes', [])[:3])}.",
            "",
        ]
    if lead and lead is not current:
        themes = "; ".join(lead.get("themes", [])[:4])
        lines += [
            f"Most directly relevant to this role: as {lead.get('title','')} at "
            f"{lead.get('company','')} ({lead.get('dates','')}), I worked on {themes}.",
            "",
        ]
    others = [j for j in jobs[1:3] if j is not current]
    if others:
        prior = "; ".join(f"{j.get('title','')} at {j.get('company','')}" for j in others)
        lines += [f"Earlier: {prior}.", ""]

    certs = app.get("certifications", {}).get("certs", [])
    degrees = app.get("education", {}).get("degrees", [])
    if certs:
        lines += ["Relevant credentials: " + "; ".join(certs) + ".", ""]
    if degrees:
        lines += ["Education: " + "; ".join(degrees) + ".", ""]

    closing = str(app.get("voice", {}).get("closing", "") or "").strip()
    lines += [closing if closing and closing.upper() != "TODO" and not closing.startswith("TODO")
              else f"{TODO} - add your closing paragraph in config/applicant.toml [voice].closing",
              "",
              "Thank you for your consideration.",
              "",
              ident.get("name", "")]
    return "\n".join(lines)


def answer_sheet(row: dict, app: dict) -> str:
    a = app.get("answers", {})
    ident = app.get("identity", {})
    exp = app.get("experience", [])
    unanswered = [k for k, v in a.items()
                  if not str(v).strip() or str(v).strip().upper() == "TODO"]
    out = [
        f"# Application answers - {row.get('title','')} ({row.get('company','')})",
        "",
    ]
    # Only explain the marker when one is actually present -- otherwise the
    # explanation itself trips any "does this packet still have TODOs" check.
    if unanswered:
        out += ["Copy/paste. A field showing the unanswered marker is a question only",
                "you can answer; fill it once in config/applicant.toml and every future",
                "packet inherits it.", ""]
    else:
        out += ["Copy/paste - every field below is answered.", ""]

    out += [
        "## Identity",
        f"- Full name: {ident.get('name','')}",
        f"- Email: {ident.get('email','')}",
        f"- Phone: {ident.get('phone','')}",
        f"- Location: {ident.get('location','')}",
        f"- LinkedIn: {ident.get('linkedin','')}",
        "",
        "## Eligibility  (verify every one of these before submitting)",
        f"- Work authorization: {_val(a,'work_authorization')}",
        f"- Requires sponsorship: {_val(a,'requires_sponsorship')}",
        f"- Security clearance: {_val(a,'security_clearance')}",
        "",
        "## Rate  (answer the one the employer is actually asking)",
        f"- Full-time salary: {_val(a,'salary_expectation')}",
        f"- Contract, C2C:    {_val(a,'contract_rate_c2c')}",
        f"- Contract, W2:     {_val(a,'contract_rate_w2')}",
        "",
        "## Logistics",
        f"- Notice period: {_val(a,'notice_period')}",
        f"- Earliest start: {_val(a,'earliest_start')}",
        f"- Willing to relocate: {_val(a,'willing_to_relocate')}",
        f"- Remote preference: {_val(a,'remote_preference')}",
        f"- How did you hear about us: {_val(a,'referral_source')}",
        "",
        "## Voluntary self-identification  (asked as separate fields by most ATSes)",
        f"- Race / ethnicity: {_val(a,'eeo_race_ethnicity')}",
        f"- Gender: {_val(a,'eeo_gender')}",
        f"- Veteran status: {_val(a,'veteran_status')}",
        f"- Disability status: {_val(a,'disability_status')}",
        "",
        "## Education",
    ]
    out += [f"- {d}" for d in app.get("education", {}).get("degrees", [])]
    out += ["", "## Certifications"]
    out += [f"- {c}" for c in app.get("certifications", {}).get("certs", [])]
    out += ["", "## Employment history (most recent first)"]
    for j in exp:
        out.append(f"- {j.get('title','')} | {j.get('company','')} | {j.get('dates','')} | {j.get('place','')}")
    return "\n".join(out)


def packet_index(row: dict, app: dict, resume: str) -> str:
    terms = _matched_terms(row)
    todos = [k for k in app.get("answers", {}) if _val(app["answers"], k) == TODO]
    return "\n".join([
        f"# {row.get('title','')} - {row.get('company','')}",
        "",
        f"- Score: {row.get('score','')}  ({row.get('lane','')})",
        f"- Location: {row.get('location') or 'n/a'}",
        f"- Source: {row.get('source','')}",
        f"- Apply: {row.get('url','')}",
        f"- Attach resume: {resume}",
        f"- Job key: {row.get('job_key','')}",
        "",
        "## Why this matched",
        ("- " + "\n- ".join(terms)) if terms else "- (no terms recorded)",
        "",
        "## Before you submit",
        f"- Unanswered questions in this packet: {len(todos)}"
        + (f" ({', '.join(todos)})" if todos else ""),
        "- Confirm the eligibility answers are still true for THIS employer.",
        "",
        "## Files",
        "- cover_letter.md",
        "- answers.md",
        "",
        f"After submitting:  python -m jobpipe mark {row.get('job_key','')} applied",
    ])


def write_packets(rows, app: dict, outdir: Path) -> list[Path]:
    """One folder per role. Returns the packet directories written."""
    outdir.mkdir(parents=True, exist_ok=True)
    resumes = app.get("resumes", {})
    written: list[Path] = []
    index = ["# Application packets", ""]

    for row in rows:
        d = outdir / f"{_slug(row.get('company',''), 28)}__{_slug(row.get('title',''))}"
        d.mkdir(parents=True, exist_ok=True)
        resume = resumes.get(row.get("lane", ""), resumes.get("default", ""))
        (d / "cover_letter.md").write_text(cover_letter(row, app), encoding="utf-8")
        (d / "answers.md").write_text(answer_sheet(row, app), encoding="utf-8")
        (d / "packet.md").write_text(packet_index(row, app, resume), encoding="utf-8")
        written.append(d)
        index.append(f"- [{row.get('score','')}] {row.get('title','')} - "
                     f"{row.get('company','')} -> `{d.name}/`  {row.get('url','')}")

    # Prune packets for roles no longer in the bucket -- once a job is marked
    # applied/rejected it drops out of `ranked`, and a stale folder left behind
    # is an invitation to apply to the same posting twice.
    keep = {d.name for d in written}
    for stale in outdir.iterdir():
        if stale.is_dir() and stale.name not in keep and (stale / "packet.md").exists():
            for f in stale.iterdir():
                f.unlink()
            stale.rmdir()

    (outdir / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return written
