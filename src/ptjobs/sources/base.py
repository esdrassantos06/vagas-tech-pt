"""Shared types for source scrapers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawJob:
    """A job posting as scraped, before normalization.

    Holds raw strings exactly as a source presented them. Canonicalization
    (city, work mode, salary, tags, fingerprint) happens later in the pipeline.
    """

    source: str
    external_id: str
    url: str
    title: str
    company: str
    location_raw: str | None = None
    description: str | None = None
    salary_raw: str | None = None
    published_at: datetime | None = None


_WS = re.compile(r"\s+")


def join_text(parts: Iterable[str]) -> str | None:
    """Join scraped text nodes into one whitespace-collapsed string, or None."""
    text = _WS.sub(" ", " ".join(part.strip() for part in parts)).strip()
    return text or None
