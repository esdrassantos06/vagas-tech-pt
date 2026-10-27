"""itjobs.pt source: parse its public job listing HTML."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from parsel import Selector

from ptjobs.errors import ParseError, SourceError
from ptjobs.sources.base import RawJob
from ptjobs.sources.http import HttpClient

SOURCE = "itjobs"
BASE_URL = "https://www.itjobs.pt"
LISTING_URL = f"{BASE_URL}/emprego"
_ID_RE = re.compile(r"/oferta/(\d+)/")


def parse(html_text: str) -> list[RawJob]:
    """Parse an itjobs.pt listing page into RawJob entries.

    Raises ParseError if no listing items are found (markup changed).
    """
    items = Selector(html_text).css("ul.listing > li")
    if not items:
        raise ParseError(f"{SOURCE}: no listing items found")

    jobs: list[RawJob] = []
    for item in items:
        href = item.css("div.list-title a.title::attr(href)").get()
        title = item.css("div.list-title a.title::text").get()
        match = _ID_RE.search(href or "")
        if not href or not title or not match:
            continue
        details = [t.strip() for t in item.css("div.list-details ::text").getall()]
        details = [t for t in details if t]
        salary = next((t for t in details if "€" in t), None)
        extra = " ".join(t for t in details[1:] if t != salary)
        jobs.append(
            RawJob(
                source=SOURCE,
                external_id=match.group(1),
                url=urljoin(BASE_URL, href),
                title=title.strip(),
                company=(item.css("div.list-name a::text").get() or "").strip(),
                location_raw=details[0] if details else None,
                description=extra or None,
                salary_raw=salary,
            )
        )
    if not jobs:
        raise ParseError(f"{SOURCE}: no items matched the expected shape")
    return jobs


def fetch(client: HttpClient, *, max_pages: int = 3) -> list[RawJob]:
    """Fetch itjobs listing pages until one is empty or markup breaks.

    A ParseError on the first page propagates (markup changed); on later pages
    it is treated as the end of pagination.
    """
    collected: list[RawJob] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
        try:
            jobs = parse(client.get(url).text)
        except ParseError, SourceError:
            if page == 1:
                raise
            break
        new = [job for job in jobs if job.external_id not in seen]
        if not new:
            break
        seen.update(job.external_id for job in new)
        collected.extend(new)
    return collected
