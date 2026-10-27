from pathlib import Path

import httpx
import pytest

from ptjobs.errors import ParseError
from ptjobs.sources import itjobs, linkedin, net_empregos
from ptjobs.sources.http import HttpClient

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _http(handler) -> HttpClient:
    inner = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpClient(inner, sleep=lambda _: None, monotonic=lambda: 0.0)


def test_net_empregos_fetch_yields_jobs() -> None:
    body = (FIXTURES / "net_empregos" / "listing.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body, headers={"content-type": "text/xml; charset=iso-8859-1"}
        )

    jobs = net_empregos.fetch(_http(handler))
    assert [j.external_id for j in jobs] == ["16136990"] or len(jobs) == 2


def test_itjobs_fetch_paginates_until_empty() -> None:
    page1 = (FIXTURES / "itjobs" / "listing.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=" in str(request.url):
            return httpx.Response(200, text='<ul class="listing"></ul>')
        return httpx.Response(200, text=page1)

    jobs = itjobs.fetch(_http(handler), max_pages=3)
    assert len(jobs) == 2


def test_itjobs_fetch_raises_when_first_page_breaks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>redesigned</body></html>")

    with pytest.raises(ParseError):
        itjobs.fetch(_http(handler))


def test_linkedin_search_url() -> None:
    url = linkedin.search_url("python developer", start=25)
    assert url.startswith(linkedin.GUEST_SEARCH_URL)
    assert "keywords=python+developer" in url
    assert "location=Portugal" in url
    assert "start=25" in url


def test_linkedin_fetch_uses_injected_fetcher_and_paces() -> None:
    html = (FIXTURES / "linkedin" / "listing.html").read_text(encoding="utf-8")
    calls: list[str] = []
    slept: list[float] = []

    jobs = linkedin.fetch(
        ["python", "golang"],
        html_fetcher=lambda url: calls.append(url) or html,
        sleep=slept.append,
    )

    assert len(jobs) == 2
    assert len(calls) == 2
    assert slept == [3.0]


def test_linkedin_fetch_keeps_partials_when_one_keyword_empty() -> None:
    html = (FIXTURES / "linkedin" / "listing.html").read_text(encoding="utf-8")

    def fetcher(url: str) -> str:
        return html if "python" in url else "<html><body></body></html>"

    jobs = linkedin.fetch(
        ["python", "obscure"], html_fetcher=fetcher, sleep=lambda _: None
    )
    assert len(jobs) == 2


def test_linkedin_fetch_raises_when_all_keywords_empty() -> None:
    with pytest.raises(ParseError):
        linkedin.fetch(
            ["a", "b"],
            html_fetcher=lambda url: "<html></html>",
            sleep=lambda _: None,
        )


def test_itjobs_fetch_keeps_page1_when_later_page_errors() -> None:
    from ptjobs.errors import SourceError

    page1 = (FIXTURES / "itjobs" / "listing.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=page1)

    jobs = itjobs.fetch(_http(handler), max_pages=2)
    assert len(jobs) == 2
    assert SourceError  # imported for clarity


def test_itjobs_fetch_stops_when_page_repeats() -> None:
    page1 = (FIXTURES / "itjobs" / "listing.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page1)

    jobs = itjobs.fetch(_http(handler), max_pages=5)
    assert len(jobs) == 2


def test_net_empregos_fetch_preserves_iso8859_accents() -> None:
    body = (FIXTURES / "net_empregos" / "listing.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/xml"})

    jobs = net_empregos.fetch(_http(handler))
    assert "Água" in jobs[0].title
