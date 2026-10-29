from pathlib import Path

import pytest

from ptjobs.errors import ParseError
from ptjobs.sources import itjobs, linkedin, net_empregos

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read(source: str, name: str) -> str:
    return (FIXTURES / source / name).read_text(
        encoding="iso-8859-1" if source == "net_empregos" else "utf-8"
    )


class TestNetEmpregos:
    def test_parses_items(self) -> None:
        jobs = net_empregos.parse(_read("net_empregos", "listing.xml"))
        assert len(jobs) == 2
        job = jobs[0]
        assert job.source == "net_empregos"
        assert job.external_id == "16136990"
        assert job.url.startswith("https://www.net-empregos.com/16136990/")
        assert "Electromec" in job.title
        assert job.company == "Lehmus, LDA"
        assert job.location_raw == "Setubal"

    def test_empty_feed_raises(self) -> None:
        with pytest.raises(ParseError):
            net_empregos.parse("<rss><channel></channel></rss>")

    def test_broken_markup_raises(self) -> None:
        with pytest.raises(ParseError):
            net_empregos.parse("<html><body>not a feed</body></html>")


class TestItjobs:
    def test_parses_items(self) -> None:
        jobs = itjobs.parse(_read("itjobs", "listing.html"))
        assert len(jobs) == 2
        job = jobs[0]
        assert job.source == "itjobs"
        assert job.external_id == "517851"
        assert (
            job.url
            == "https://www.itjobs.pt/oferta/517851/senior-data-engineer-dataiku-platform-and-ci-cd-pipelines"
        )
        assert "Data Engineer" in job.title
        assert job.company == "ITDS Portugal"
        assert job.location_raw is not None and "Lisboa" in job.location_raw

    def test_missing_listing_raises(self) -> None:
        with pytest.raises(ParseError):
            itjobs.parse("<html><body><p>no jobs here</p></body></html>")

    def test_parses_detail_description(self) -> None:
        text = itjobs.parse_detail(_read("itjobs", "detail.html"))
        assert text is not None
        assert text.startswith("Procuramos um Java Developer para integrar")
        assert "3+ anos de experiência em Java Spring Boot" in text
        assert "<endereço ocultado>" in text
        assert "Guardar" not in text and "Enviar-me" not in text
        assert "web:" not in text and "192.0.2.10" not in text

    def test_detail_without_description_is_none(self) -> None:
        assert itjobs.parse_detail("<html><body></body></html>") is None


class TestLinkedin:
    def test_parses_cards(self) -> None:
        jobs = linkedin.parse(_read("linkedin", "listing.html"))
        assert len(jobs) == 2
        job = jobs[0]
        assert job.source == "linkedin"
        assert job.external_id == "4387969629"
        assert "linkedin.com/jobs/view/" in job.url
        assert "Python Developer" in job.title
        assert job.company == "Devoteam"
        assert job.location_raw is not None and "Portugal" in job.location_raw

    def test_empty_html_raises(self) -> None:
        with pytest.raises(ParseError):
            linkedin.parse("<html><body></body></html>")

    def test_parses_detail_description(self) -> None:
        text = linkedin.parse_detail(_read("linkedin", "detail.html"))
        assert text == (
            "About the role We need a Backend Engineer with 4+ years of "
            "experience in Python & FastAPI. Hybrid in Porto"
        )

    def test_detail_without_description_is_none(self) -> None:
        assert linkedin.parse_detail("<html><body></body></html>") is None

    def test_detail_url_uses_the_guest_job_posting_api(self) -> None:
        assert linkedin.detail_url("4387969629") == (
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4387969629"
        )


def test_net_empregos_parse_bytes_honors_declaration() -> None:
    raw = (FIXTURES / "net_empregos" / "listing.xml").read_bytes()
    jobs = net_empregos.parse(raw)
    assert "Água" in jobs[0].title


def test_net_empregos_decodes_windows_1252_euro() -> None:
    xml = (
        '<?xml version="1.0" encoding="iso-8859-1"?>'
        "<rss><channel><item><title>Estágio (180\x80/mês)</title>"
        "<link>https://www.net-empregos.com/1/x/</link></item></channel></rss>"
    ).encode("latin-1")
    jobs = net_empregos.parse(xml)
    assert "180€/mês" in jobs[0].title
