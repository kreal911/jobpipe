"""Ashby public job board API.

GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
Config: {type="ashby", company="Acme", board="acme"}
"""
from __future__ import annotations

from ..http import get_json
from ..model import Job, strip_html

URL = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"


def parse(payload: dict, company: str) -> list[Job]:
    out = []
    for j in payload.get("jobs", []) or []:
        if j.get("isListed") is False:
            continue
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))
        comp = ""
        c = j.get("compensation") or {}
        if isinstance(c, dict):
            comp = c.get("scrapeableCompensationSalarySummary") or c.get("compensationTierSummary") or ""
        locs = [j.get("location", "")] + list(j.get("secondaryLocations") or [])
        locs = [l if isinstance(l, str) else (l or {}).get("location", "") for l in locs]
        out.append(Job(
            source="ashby",
            company=company,
            external_id=str(j.get("id") or j.get("jobId") or j.get("jobUrl") or j.get("title", "")),
            title=j.get("title", "") or "",
            url=j.get("jobUrl") or j.get("applyUrl", "") or "",
            location=", ".join(x for x in locs if x),
            remote=bool(j.get("isRemote")) if j.get("isRemote") is not None
                   else ((j.get("workplaceType") or "").lower() == "remote" or None),
            employment_type=j.get("employmentType", "") or "",
            department=" / ".join(x for x in [j.get("department", ""), j.get("team", "")] if x),
            description=desc,
            comp_text=comp or "",
            posted_at=j.get("publishedAt", "") or "",
            raw=j,
        ))
    return out


def fetch(entry: dict) -> list[Job]:
    payload = get_json(URL.format(board=entry["board"]))
    return parse(payload, entry.get("company") or entry["board"])
