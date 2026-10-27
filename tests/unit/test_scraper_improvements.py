"""Scraper quality: tag precision, dates, work modes, salaries, pagination."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from ptjobs.errors import SourceError
from ptjobs.normalize import normalize_tags, parse_salary
from ptjobs.sources import itjobs, linkedin, net_empregos
from ptjobs.sources.http import HttpClient

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.parametrize(
    "text",
    [
        "Comercial Go Fitness Espinho",
        "Estágio Remunerado Mecatrónico - Leiria - Go Technicians",
        "Operador de loja (BP Pingo Doce & Go)",
    ],
)
def test_go_brand_names_are_not_golang(text: str) -> None:
    assert "golang" not in normalize_tags(text)


@pytest.mark.parametrize(
    "text",
    ["Senior Go developer", "Backend em Go e Python", "Golang engineer", "Go engineer"],
)
def test_go_in_programming_context_is_golang(text: str) -> None:
    assert "golang" in normalize_tags(text)


def test_salary_with_non_breaking_spaces() -> None:
    assert parse_salary("€39\xa0000 - €44\xa0000") == (39000, 44000)


def test_net_empregos_unescapes_title_and_reads_date() -> None:
    xml = (
        '<?xml version="1.0" encoding="iso-8859-1"?><rss><channel><item>'
        "<title>Java &amp;amp; Angular</title>"
        "<link>https://www.net-empregos.com/7/x/</link>"
        "<description>&lt;b&gt;Data: &lt;/b&gt;27-10-2026&lt;br&gt;</description>"
        "</item></channel></rss>"
    ).encode("latin-1")
    job = net_empregos.parse(xml)[0]
    assert job.title == "Java & Angular"
    assert job.published_at is not None
    assert job.published_at.date() == date(2026, 10, 27)


def test_net_empregos_fetches_only_it_category_feeds() -> None:
    body = (FIXTURES / "net_empregos" / "listing.xml").read_bytes()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=body)

    client = HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
    )
    jobs = net_empregos.fetch(client)
    assert all("rss.asp?cat=" in url for url in seen)
    assert len(seen) == len(net_empregos.IT_CATEGORIES)
    assert len(jobs) == 2


def test_itjobs_keeps_work_mode_and_salary_details() -> None:
    html = (FIXTURES / "itjobs" / "listing.html").read_text(encoding="utf-8")
    jobs = itjobs.parse(html)
    assert jobs[0].location_raw == "Lisboa"
    assert "Híbrido" in (jobs[0].description or "")
    assert "Presencial" in (jobs[1].description or "")

    salary = "Híbrido <i class='fa fa-money'></i> €39\xa0000 - €44\xa0000"
    with_salary = html.replace("Híbrido", salary, 1)
    assert "€39" in (itjobs.parse(with_salary)[0].salary_raw or "")


def test_linkedin_reads_listing_date() -> None:
    html = (FIXTURES / "linkedin" / "listing.html").read_text(encoding="utf-8")
    job = linkedin.parse(html)[0]
    assert job.published_at is not None
    assert job.published_at.date() == date(2026, 9, 29)


def _cards(start: int, count: int) -> str:
    cards = "".join(
        f'<div class="base-card" data-entity-urn="urn:li:jobPosting:{start + i}">'
        f'<a class="base-card__full-link" href="https://x/jobs/view/{start + i}"></a>'
        f'<h3 class="base-search-card__title">Job {start + i}</h3></div>'
        for i in range(count)
    )
    return f"<ul>{cards}</ul>"


def test_linkedin_paginates_until_a_short_page() -> None:
    pages = {0: _cards(0, 10), 10: _cards(10, 10), 20: _cards(20, 4)}
    requested: list[int] = []

    def fetcher(url: str) -> str:
        start = int(url.rsplit("start=", 1)[1])
        requested.append(start)
        return pages.get(start, "<html></html>")

    jobs = linkedin.fetch(["python"], html_fetcher=fetcher, sleep=lambda _: None)
    assert requested == [0, 10, 20]
    assert len(jobs) == 24


def test_linkedin_keyword_failure_does_not_lose_other_keywords() -> None:
    def fetcher(url: str) -> str:
        if "keywords=broken" in url:
            raise SourceError("blocked")
        return _cards(0, 3)

    jobs = linkedin.fetch(
        ["broken", "python"], html_fetcher=fetcher, sleep=lambda _: None
    )
    assert len(jobs) == 3
