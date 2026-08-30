"""Canonical job record shared by every source adapter."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")


def strip_html(raw: str | None) -> str:
    """Plain text from HTML that may be entity-escaped one or two times."""
    if not raw:
        return ""
    txt = html.unescape(html.unescape(str(raw)))
    txt = _TAG.sub(" ", txt)
    txt = html.unescape(txt)
    return _WS.sub(" ", txt).strip()


def norm_title(title: str) -> str:
    """Normalize a title for cross-board duplicate detection."""
    t = title.lower()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(sr|snr)\b", "senior", t)
    t = re.sub(r"\bjr\b", "junior", t)
    t = re.sub(r"\b(remote|hybrid|onsite|on site|us|usa|united states)\b", " ", t)
    return _WS.sub(" ", t).strip()


@dataclass
class Job:
    source: str                 # greenhouse | lever | ashby | smartrecruiters | workday
    company: str                # display name from config
    external_id: str            # id as given by the board
    title: str
    url: str
    location: str = ""
    remote: bool | None = None
    employment_type: str = ""
    department: str = ""
    description: str = ""       # plain text
    comp_text: str = ""
    posted_at: str = ""         # ISO8601 or ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def job_key(self) -> str:
        """Stable identity for one posting on one board."""
        seed = f"{self.source}|{self.company.lower()}|{self.external_id}"
        return hashlib.sha1(seed.encode()).hexdigest()[:16]

    @property
    def dupe_key(self) -> str:
        """Identity across boards: same company + same normalized title."""
        seed = f"{self.company.lower()}|{norm_title(self.title)}"
        return hashlib.sha1(seed.encode()).hexdigest()[:16]

    def haystack(self) -> tuple[str, str]:
        """(title_text, body_text) both lowercased, for scoring."""
        title = f"{self.title} {self.department}".lower()
        body = f"{self.description} {self.location} {self.employment_type}".lower()
        return title, body

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["job_key"] = self.job_key
        d["dupe_key"] = self.dupe_key
        d["remote"] = 1 if self.remote else (0 if self.remote is False else None)
        return d


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
