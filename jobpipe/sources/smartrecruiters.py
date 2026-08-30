"""SmartRecruiters public posting API.

GET https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100&offset=N
Detail (optional, for description): .../postings/{postingId}
Config: {type="smartrecruiters", company="Acme", company_id="Acme", detail=true}
"""
from __future__ import annotations

from ..http import FetchError, get_json
from ..model import Job, strip_html

LIST = "https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100&offset={offset}"
DETAIL = "https://api.smartrecruiters.com/v1/companies/{cid}/postings/{pid}"


def _loc(l: dict) -> tuple[str, bool | None]:
    if not l:
        return "", None
    parts = [l.get("city", ""), l.get("region", ""), l.get("country", "")]
    return ", ".join(p for p in parts if p), l.get("remote")


def parse(payload: dict, company: str) -> list[Job]:
    out = []
    for j in payload.get("content", []) or []:
        loc, remote = _loc(j.get("location") or {})
        out.append(Job(
            source="smartrecruiters",
            company=company,
            external_id=str(j.get("id") or j.get("uuid", "")),
            title=j.get("name", "") or "",
            url=(j.get("applyUrl")
                 or f"https://jobs.smartrecruiters.com/{(j.get('company') or {}).get('identifier','')}/{j.get('id','')}"),
            location=loc,
            remote=remote,
            employment_type=(j.get("typeOfEmployment") or {}).get("label", "") or "",
            department=(j.get("department") or {}).get("label", "") or "",
            description=_detail_text(j),
            posted_at=j.get("releasedDate", "") or "",
            raw=j,
        ))
    return out


def _detail_text(j: dict) -> str:
    ad = j.get("jobAd") or {}
    secs = ad.get("sections") or {}
    chunks = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        chunks.append(strip_html((secs.get(key) or {}).get("text", "")))
    return " ".join(c for c in chunks if c).strip()


def fetch(entry: dict) -> list[Job]:
    cid = entry["company_id"]
    company = entry.get("company") or cid
    jobs: list[Job] = []
    offset = 0
    while True:
        payload = get_json(LIST.format(cid=cid, offset=offset))
        batch = parse(payload, company)
        jobs.extend(batch)
        total = payload.get("totalFound", len(jobs))
        offset += payload.get("limit", 100)
        if offset >= total or not batch:
            break
    if entry.get("detail"):
        for job in jobs:
            try:
                d = get_json(DETAIL.format(cid=cid, pid=job.external_id))
                job.description = _detail_text(d) or job.description
                job.url = d.get("applyUrl") or job.url
            except FetchError:
                continue
    return jobs
