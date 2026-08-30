"""Source adapters. Each exposes fetch(entry: dict) -> list[Job]."""
from __future__ import annotations

from . import ashby, greenhouse, lever, smartrecruiters, workday

REGISTRY = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "workday": workday.fetch,
}


def fetch(entry: dict):
    kind = entry["type"]
    if kind not in REGISTRY:
        raise ValueError(f"unknown source type: {kind}")
    return REGISTRY[kind](entry)
