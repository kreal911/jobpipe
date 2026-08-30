"""Workday CXS job search endpoint (the JSON API behind every Workday career site).

POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}

Config:
  {type="workday", company="Sanofi", host="sanofi.wd3.myworkdayjobs.com",
   tenant="sanofi", site="SanofiCareers", search=["quality","AI"], detail=true}

Find host/tenant/site by opening the company's careers page: the URL is
https://<tenant>.<dc>.myworkdayjobs.com/en-US/<site>

detail (default false, but you almost always want it on):
  The /jobs LIST endpoint does NOT return jobDescription -- it only returns
  bulletFields ("R-8801 Full time"), so a job scores on its title alone and
  every body term in profile.toml silently fails to match. It also collapses
  a multi-office posting to locationsText = "2 Locations", which no
  allow_locations entry can match.

  GET .../wday/cxs/{tenant}/{site}{externalPath} returns the real
  jobPostingInfo: the full jobDescription, a concrete location, and
  additionalLocations. detail=true fetches that per posting and merges it in.

  Cost: one extra request per posting, and http.py throttles to one request
  per host per second -- so budget roughly one second per posting. Keep `max`
  modest on detail-enabled sources.
"""
from __future__ import annotations

from ..http import FetchError, post_json, get_json
from ..model import Job, strip_html

URL = "https://{host}/wday/cxs/{tenant}/{site}/jobs"
DETAIL = "https://{host}/wday/cxs/{tenant}/{site}{path}"
PAGE = 20


def parse(payload: dict, company: str, host: str, site: str) -> list[Job]:
    out = []
    for j in payload.get("jobPostings", []) or []:
        path = j.get("externalPath", "") or ""
        url = f"https://{host}/en-US/{site}{path}" if path else ""
        bullets = " ".join(str(x) for x in (j.get("bulletFields") or []))
        out.append(Job(
            source="workday",
            company=company,
            external_id=path or str(j.get("title", "")),
            title=j.get("title", "") or "",
            url=url,
            location=j.get("locationsText", "") or "",
            remote="remote" in (j.get("locationsText", "") or "").lower() or None,
            description=strip_html(j.get("jobDescription")) or bullets,
            posted_at=j.get("postedOn", "") or "",
            raw=j,
        ))
    return out


def merge_detail(job: Job, payload: dict) -> Job:
    """Fold a jobPostingInfo payload into a job parsed from the list endpoint.

    Split out from fetch() so it can be tested against a recorded fixture
    without touching the network.
    """
    info = (payload or {}).get("jobPostingInfo") or {}

    text = strip_html(info.get("jobDescription"))
    if text:
        job.description = text

    # "2 Locations" in the list response becomes the actual cities here.
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    locs = [l.strip() for l in locs if isinstance(l, str) and l.strip()]
    if locs:
        job.location = ", ".join(dict.fromkeys(locs))
        job.remote = "remote" in job.location.lower() or None

    if info.get("timeType"):
        job.employment_type = str(info["timeType"])
    if info.get("postedOn"):
        job.posted_at = str(info["postedOn"])
    if info.get("externalUrl"):
        job.url = str(info["externalUrl"])
    return job


def fetch(entry: dict) -> list[Job]:
    host, tenant, site = entry["host"], entry["tenant"], entry["site"]
    company = entry.get("company") or tenant
    url = URL.format(host=host, tenant=tenant, site=site)
    seen: dict[str, Job] = {}
    for term in entry.get("search") or [""]:
        offset = 0
        while True:
            payload = post_json(url, {"appliedFacets": {}, "limit": PAGE,
                                      "offset": offset, "searchText": term})
            batch = parse(payload, company, host, site)
            for job in batch:
                seen.setdefault(job.external_id, job)
            total = payload.get("total", 0)
            offset += PAGE
            if offset >= min(total, entry.get("max", 200)) or not batch:
                break

    jobs = list(seen.values())
    if entry.get("detail"):
        for job in jobs:
            if not job.external_id.startswith("/"):
                continue                      # not an externalPath, nothing to fetch
            try:
                merge_detail(job, get_json(
                    DETAIL.format(host=host, tenant=tenant, site=site, path=job.external_id)))
            except FetchError:
                continue                      # keep the list-endpoint version
    return jobs
