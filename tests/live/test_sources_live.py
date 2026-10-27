"""Canary tests that hit the real sources. Skipped unless `-m live` is passed.

When one of these fails, a source likely changed its markup: refresh the matching
fixture in tests/fixtures/<source>/ and update the parser.
"""

import httpx
import pytest

from ptjobs.sources import itjobs, linkedin, net_empregos
from ptjobs.sources.http import HttpClient

pytestmark = pytest.mark.live


def _client() -> HttpClient:
    inner = httpx.Client(timeout=20.0, follow_redirects=True)
    return HttpClient(inner, min_interval=1.0)


def test_net_empregos_live() -> None:
    jobs = net_empregos.fetch(_client())
    assert len(jobs) > 0
    assert all(job.external_id and job.url for job in jobs)


def test_itjobs_live() -> None:
    jobs = itjobs.fetch(_client(), max_pages=1)
    assert len(jobs) > 0
    assert all(job.title and job.url for job in jobs)


def test_linkedin_live() -> None:
    jobs = linkedin.fetch(["python developer"])
    assert len(jobs) > 0
    assert all(job.external_id for job in jobs)
