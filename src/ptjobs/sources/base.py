"""Shared types for source scrapers."""

from __future__ import annotations

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
