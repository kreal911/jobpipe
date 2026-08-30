"""Greenhouse job board API.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Config: {type="greenhouse", company="Anthropic", token="anthropic"}
"""
from __future__ import annotations

from ..http import get_json
from ..model import Job, strip_html

URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def parse(payload: dict, company: str) -> list[Job]:
    out = []
    for j in payload.get("jobs", []) or []:
        loc = strip_html((j.get("location") or {}).get("name", ""))
        offices = ", ".join(strip_html(o.get("name")) for o in (j.get("offices") or []) if o.get("name"))
        depts = ", ".join(strip_html(d.get("name")) for d in (j.get("departments") or []) if d.get("name"))
        out.append(Job(
            source="greenhouse",
            company=company,
            external_id=str(j.get("id", "")),
            title=strip_html(j.get("title")),
            url=j.get("absolute_url", "") or "",
            location=loc or offices,
            remote="remote" in (loc + offices).lower() or None,
            department=depts,
            description=strip_html(j.get("content")),
            posted_at=j.get("updated_at", "") or "",
            raw=j,
        ))
    return out


def fetch(entry: dict) -> list[Job]:
    payload = get_json(URL.format(token=entry["token"]))
    return parse(payload, entry.get("company") or entry["token"])
