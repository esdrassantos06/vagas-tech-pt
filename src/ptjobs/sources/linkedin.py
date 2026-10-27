"""LinkedIn source: parse the public guest job-search cards."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from urllib.parse import urlencode

from parsel import Selector
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from ptjobs.errors import ParseError, SourceError
from ptjobs.sources.base import RawJob

SOURCE = "linkedin"
GUEST_SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
DEFAULT_LOCATION = "Portugal"
PAGE_SIZE = 10


def parse(html_text: str) -> list[RawJob]:
    """Parse LinkedIn guest job cards into RawJob entries.

    Raises ParseError if no job cards are found (blocked, authwall, or markup
    changed).
    """
    cards = Selector(html_text).css("div.base-card")
    if not cards:
        raise ParseError(f"{SOURCE}: no job cards found")

    jobs: list[RawJob] = []
    for card in cards:
        urn = card.attrib.get("data-entity-urn", "")
        external_id = urn.rsplit(":", 1)[-1] if ":" in urn else ""
        url = card.css("a.base-card__full-link::attr(href)").get() or ""
        title = card.css("h3.base-search-card__title::text").get()
        if not external_id or not url or not title:
            continue
        company = card.css("h4.base-search-card__subtitle a::text").get()
        location = card.css("span.job-search-card__location::text").get()
        posted = card.css("time::attr(datetime)").get()
        jobs.append(
            RawJob(
                source=SOURCE,
                external_id=external_id,
                url=url.split("?")[0],
                title=title.strip(),
                company=(company or "").strip(),
                location_raw=(location or "").strip() or None,
                published_at=_parse_date(posted),
            )
        )
    if not jobs:
        raise ParseError(f"{SOURCE}: no cards matched the expected shape")
    return jobs


def search_url(
    keyword: str, *, location: str = DEFAULT_LOCATION, start: int = 0
) -> str:
    """Build the LinkedIn public guest job-search URL for a keyword."""
    query = urlencode({"keywords": keyword, "location": location, "start": start})
    return f"{GUEST_SEARCH_URL}?{query}"


def fetch_html(url: str, *, timeout_ms: int = 20000) -> str:
    """Load a URL in a stealthed headless Chromium and return its HTML."""
    try:
        with Stealth().use_sync(sync_playwright()) as play:
            browser = play.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                return str(page.content())
            finally:
                browser.close()
    except Exception as exc:
        raise SourceError(f"{SOURCE}: browser fetch failed for {url}") from exc


def _parse_date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None
    except ValueError:
        return None


def fetch(
    keywords: Iterable[str],
    *,
    location: str = DEFAULT_LOCATION,
    html_fetcher: Callable[[str], str] = fetch_html,
    pause_seconds: float = 3.0,
    max_pages: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> list[RawJob]:
    """Fetch LinkedIn guest cards for each keyword, paging until a short page.

    A failure on one keyword (blocked page, network error, no cards) is
    recorded and the next keyword continues; the last error is raised only
    when no keyword produced any job.
    """
    collected: dict[str, RawJob] = {}
    last_error: ParseError | SourceError | None = None
    first_request = True
    for keyword in keywords:
        for page in range(max_pages):
            if not first_request:
                sleep(pause_seconds)
            first_request = False
            url = search_url(keyword, location=location, start=page * PAGE_SIZE)
            try:
                jobs = parse(html_fetcher(url))
            except (ParseError, SourceError) as exc:
                if page == 0:
                    last_error = exc
                break
            for job in jobs:
                collected.setdefault(job.external_id, job)
            if len(jobs) < PAGE_SIZE:
                break
    if last_error is not None and not collected:
        raise last_error
    return list(collected.values())
