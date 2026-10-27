"""net-empregos.com source: parse its public RSS feed."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from ptjobs.errors import ParseError
from ptjobs.sources.base import RawJob
from ptjobs.sources.http import HttpClient

SOURCE = "net_empregos"
FEED_URL = "https://www.net-empregos.com/rss.asp?cat={category}"
IT_CATEGORIES = (5, 38, 37, 35)
_DC_KEYS = (
    "{http://purl.org/dc/elements/1.1/}creator",
    "{https://purl.org/dc/elements/1.1/}creator",
)
_ID_RE = re.compile(r"/(\d+)/")
_LOCATION_RE = re.compile(r"Zona:\s*</b>\s*([^<]+)", re.IGNORECASE)
_DATE_RE = re.compile(r"Data:\s*</b>\s*(\d{2})-(\d{2})-(\d{4})", re.IGNORECASE)


def parse(xml_text: str | bytes) -> list[RawJob]:
    """Parse a net-empregos RSS feed into RawJob entries.

    Bytes are decoded as windows-1252. Raises ParseError if the document is not
    a feed or contains no items.
    """
    if isinstance(xml_text, bytes):
        # The feed declares iso-8859-1 but is really windows-1252 (e.g. the euro sign).
        xml_text = xml_text.decode("cp1252", errors="replace")
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError) as exc:
        raise ParseError(f"{SOURCE}: invalid RSS") from exc

    items = root.findall(".//item")
    if not items:
        raise ParseError(f"{SOURCE}: feed has no items")

    jobs: list[RawJob] = []
    for item in items:
        link = (item.findtext("link") or "").strip()
        title = html.unescape(item.findtext("title") or "").strip()
        match = _ID_RE.search(link)
        if not match or not title:
            continue
        description = html.unescape(item.findtext("description") or "")
        location = _LOCATION_RE.search(description)
        posted = _DATE_RE.search(description)
        jobs.append(
            RawJob(
                source=SOURCE,
                external_id=match.group(1),
                url=link,
                title=title,
                company=_company(item),
                location_raw=location.group(1).strip() if location else None,
                description=description,
                published_at=_date(posted),
            )
        )
    if not jobs:
        raise ParseError(f"{SOURCE}: no items matched the expected shape")
    return jobs


def _company(item: ET.Element) -> str:
    for key in _DC_KEYS:
        value = item.findtext(key)
        if value:
            return value.strip()
    return ""


def _date(match: re.Match[str] | None) -> datetime | None:
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    return datetime(year, month, day, tzinfo=UTC)


def fetch(client: HttpClient) -> list[RawJob]:
    """Fetch the IT category feeds and return their jobs, deduplicated by id.

    Bytes are passed to the parser so the feed's real encoding is applied
    regardless of the HTTP Content-Type header.
    """
    jobs: dict[str, RawJob] = {}
    for category in IT_CATEGORIES:
        for job in parse(client.get(FEED_URL.format(category=category)).content):
            jobs.setdefault(job.external_id, job)
    return list(jobs.values())
