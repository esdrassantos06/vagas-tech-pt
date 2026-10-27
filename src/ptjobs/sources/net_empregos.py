"""net-empregos.com source: parse its public RSS feed."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from ptjobs.errors import ParseError
from ptjobs.sources.base import RawJob
from ptjobs.sources.http import HttpClient

SOURCE = "net_empregos"
FEED_URL = "https://www.net-empregos.com/rss.asp"
_DC_KEYS = (
    "{http://purl.org/dc/elements/1.1/}creator",
    "{https://purl.org/dc/elements/1.1/}creator",
)
_ID_RE = re.compile(r"/(\d+)/")
_LOCATION_RE = re.compile(r"Zona:\s*</b>\s*([^<]+)", re.IGNORECASE)


def parse(xml_text: str | bytes) -> list[RawJob]:
    """Parse a net-empregos RSS feed into RawJob entries.

    Raises ParseError if the document is not a feed or contains no items.
    """
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
        title = (item.findtext("title") or "").strip()
        match = _ID_RE.search(link)
        if not match or not title:
            continue
        description = html.unescape(item.findtext("description") or "")
        location = _LOCATION_RE.search(description)
        jobs.append(
            RawJob(
                source=SOURCE,
                external_id=match.group(1),
                url=link,
                title=title,
                company=_company(item),
                location_raw=location.group(1).strip() if location else None,
                description=description,
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


def fetch(client: HttpClient) -> list[RawJob]:
    """Fetch and parse the net-empregos RSS feed.

    Passes raw bytes to the parser so the feed's declared charset
    (iso-8859-1) is honored regardless of the HTTP Content-Type header.
    """
    return parse(client.get(FEED_URL).content)
