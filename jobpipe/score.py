"""Transparent, tunable scoring. Every point is traceable to a matched term."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TITLE_MULT = 3.0     # a term in the title counts triple
BODY_CAP = 1         # a body term counts once no matter how often it appears
_cache: dict[str, re.Pattern] = {}


def _pat(term: str) -> re.Pattern:
    if term not in _cache:
        esc = re.escape(term.lower()).replace(r"\ ", r"[\s\-/]+")
        _cache[term] = re.compile(rf"(?<![a-z0-9]){esc}(?![a-z0-9])")
    return _cache[term]


def _terms(raw) -> list[tuple[str, float]]:
    """Accept ['a','b'] or {'a'=2.0,'b'=1.0} or [['a',2.0],...]."""
    if not raw:
        return []
    if isinstance(raw, dict):
        return [(k.lower(), float(v)) for k, v in raw.items()]
    out = []
    for item in raw:
        if isinstance(item, (list, tuple)):
            out.append((str(item[0]).lower(), float(item[1])))
        else:
            out.append((str(item).lower(), 1.0))
    return out


def _hit(pattern: re.Pattern, title: str, body: str) -> str | None:
    if pattern.search(title):
        return "title"
    if pattern.search(body):
        return "body"
    return None


def _age_days(posted_at: str) -> float | None:
    if not posted_at:
        return None
    txt = posted_at.replace("Z", "+00:00")
    for parse in (datetime.fromisoformat,):
        try:
            dt = parse(txt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except Exception:
            pass
    m = re.search(r"(\d+)\+?\s*day", posted_at.lower())      # Workday "Posted 5 Days Ago"
    if m:
        return float(m.group(1))
    if "today" in posted_at.lower() or "yesterday" in posted_at.lower():
        return 1.0
    return None


def _location_unknown(loc: str, filt: dict) -> bool:
    """True when the board gave us no usable location at all.

    Workday returns "2 Locations" for a posting that spans offices, and some
    tenants return "". That is unknown, not disallowed, so it must not be
    judged by allow_locations. Both the switch and the patterns live in
    config/profile.toml [filters].
    """
    if not filt.get("treat_unknown_location_as_allowed"):
        return False
    for pattern in filt.get("unknown_location_patterns") or []:
        try:
            if re.search(pattern, loc.strip(), re.I):
                return True
        except re.error:
            continue
    return False


def hard_filter(job: dict, filt: dict) -> str | None:
    """Return a rejection reason, or None to continue."""
    title = (job.get("title") or "").lower()
    body = (job.get("description") or "").lower()
    loc = (job.get("location") or "").lower()
    remote = job.get("remote")

    for term, _ in _terms(filt.get("exclude_title")):
        if _pat(term).search(title):
            return f"title excluded: {term}"
    for term, _ in _terms(filt.get("exclude_body")):
        if _pat(term).search(body):
            return f"body excluded: {term}"

    # Runs before the allow gate and is NOT skipped for remote postings --
    # "Remote - India" is flagged remote by the board and would otherwise
    # never have its location checked at all.
    for term, _ in _terms(filt.get("exclude_locations")):
        if _pat(term).search(loc):
            return f"location excluded: {term}"

    allow = [t for t, _ in _terms(filt.get("allow_locations"))]
    if allow and not remote:
        blob = f"{loc} {title}"
        if not any(_pat(t).search(blob) for t in allow) and not _location_unknown(loc, filt):
            return f"location not allowed: {job.get('location') or '(blank)'}"

    max_age = filt.get("max_age_days")
    if max_age:
        age = _age_days(job.get("posted_at") or "")
        if age is not None and age > float(max_age):
            return f"stale: posted {age:.0f} days ago"
    return None


def score_lane(title: str, body: str, lane: dict) -> tuple[float, list[dict]]:
    reasons: list[dict] = []
    raw = 0.0

    gate = [t for t, _ in _terms(lane.get("require_any"))]
    if gate and not any(_hit(_pat(t), title, body) for t in gate):
        return 0.0, [{"term": "require_any", "where": "-", "pts": 0,
                      "note": "no gate term matched"}]

    # Second gate, matched against the TITLE only. require_any searches the
    # body too, which is useless as a subject filter because almost every
    # posting mentions "ai" or "compliance" somewhere. This asks what the job
    # actually IS, not what its description happens to name.
    tgate = [t for t, _ in _terms(lane.get("require_title_any"))]
    if tgate and not any(_pat(t).search(title) for t in tgate):
        return 0.0, [{"term": "require_title_any", "where": "-", "pts": 0,
                      "note": "no gate term in the title"}]

    for term, weight in _terms(lane.get("terms")):
        where = _hit(_pat(term), title, body)
        if not where:
            continue
        pts = weight * (TITLE_MULT if where == "title" else BODY_CAP)
        raw += pts
        reasons.append({"term": term, "where": where, "pts": round(pts, 2)})

    for term, weight in _terms(lane.get("penalties")):
        where = _hit(_pat(term), title, body)
        if not where:
            continue
        pts = -weight * (TITLE_MULT if where == "title" else BODY_CAP)
        raw += pts
        reasons.append({"term": term, "where": where, "pts": round(pts, 2)})

    return raw, reasons


def score_job(job: dict, profile: dict) -> dict:
    filt = profile.get("filters", {})
    th = profile.get("thresholds", {})
    keep_at = float(th.get("keep", 60))
    maybe_at = float(th.get("maybe", 35))

    reject = hard_filter(job, filt)
    if reject:
        return {"job_key": job["job_key"], "score": 0.0, "lane": "-",
                "verdict": "drop", "reasons": [{"term": reject, "where": "filter", "pts": 0}]}

    title = f"{job.get('title','')} {job.get('department','')}".lower()
    body = f"{job.get('description','')} {job.get('location','')} {job.get('employment_type','')}".lower()

    best_score, best_lane, best_reasons = 0.0, "-", []
    for lane in profile.get("lanes", []):
        raw, reasons = score_lane(title, body, lane)
        target = float(lane.get("target", 30))
        norm = max(0.0, 100.0 * raw / target)   # uncapped: keeps ordering at the top
        if norm > best_score:
            best_score, best_lane, best_reasons = norm, lane["name"], reasons

    # small, explicit bonuses -- only on jobs that actually matched a lane
    bonus = []
    b = profile.get("bonus", {}) if best_score > 0 else {}
    if job.get("remote") and b.get("remote"):
        best_score += float(b["remote"]); bonus.append({"term": "remote", "where": "meta", "pts": b["remote"]})
    if job.get("comp_text") and b.get("comp_posted"):
        best_score += float(b["comp_posted"]); bonus.append({"term": "comp posted", "where": "meta", "pts": b["comp_posted"]})
    age = _age_days(job.get("posted_at") or "")
    if age is not None and age <= float(b.get("fresh_days", 7)) and b.get("fresh"):
        best_score += float(b["fresh"]); bonus.append({"term": f"posted {age:.0f}d ago", "where": "meta", "pts": b["fresh"]})

    best_score = round(min(999.0, best_score), 1)
    verdict = "keep" if best_score >= keep_at else ("maybe" if best_score >= maybe_at else "drop")
    best_reasons = sorted(best_reasons + bonus, key=lambda r: -abs(r["pts"]))[:12]
    return {"job_key": job["job_key"], "score": best_score, "lane": best_lane,
            "verdict": verdict, "reasons": best_reasons}
