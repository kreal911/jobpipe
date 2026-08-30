"""Lever public postings API.

GET https://api.lever.co/v0/postings/{slug}?mode=json
Config: {type="lever", company="Acme", slug="acme"}
"""
from __future__ import annotations

from ..http import get_json
from ..model import Job, strip_html

URL = "https://api.lever.co/v0/postings/{slug}?mode=json&limit=200"


def parse(payload: list, company: str) -> list[Job]:
    out = []
    for j in payload or []:
        cats = j.get("categories") or {}
        desc = j.get("descriptionPlain") or strip_html(j.get("description"))
        extras = []
        for lst in j.get("lists") or []:
            extras.append(str(lst.get("text", "")))
            extras.append(strip_html(lst.get("content")))
        salary = j.get("salaryRange") or {}
        comp = ""
        if salary.get("min") or salary.get("max"):
            comp = f"{salary.get('currency','')} {salary.get('min','')}-{salary.get('max','')} {salary.get('interval','')}".strip()
        wt = (j.get("workplaceType") or "").lower()
        out.append(Job(
            source="lever",
            company=company,
            external_id=str(j.get("id", "")),
            title=j.get("text", "") or "",
            url=j.get("hostedUrl") or j.get("applyUrl", "") or "",
            location=cats.get("location", "") or "",
            remote=True if wt == "remote" else (False if wt else None),
            employment_type=cats.get("commitment", "") or "",
            department=" / ".join(x for x in [cats.get("department", ""), cats.get("team", "")] if x),
            description=" ".join([desc] + extras).strip(),
            comp_text=comp,
            posted_at=_iso(j.get("createdAt")),
            raw=j,
        ))
    return out


def _iso(ms) -> str:
    if not ms:
        return ""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def fetch(entry: dict) -> list[Job]:
    payload = get_json(URL.format(slug=entry["slug"]))
    return parse(payload, entry.get("company") or entry["slug"])
