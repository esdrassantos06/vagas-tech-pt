from pathlib import Path

from ptjobs.sources import linkedin

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_fetch_html_reads_local_page_with_chromium() -> None:
    fixture = FIXTURES / "linkedin" / "listing.html"
    html = linkedin.fetch_html(fixture.resolve().as_uri())
    assert "base-card" in html
    jobs = linkedin.parse(html)
    assert len(jobs) == 2
