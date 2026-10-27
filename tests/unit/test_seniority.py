"""Seniority detection from job titles and the seniority alert filter."""

import pytest

from ptjobs.digest import Digest, render_telegram
from ptjobs.models import Job
from ptjobs.normalize import normalize_seniority
from ptjobs.notify import job_matches


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Python Developer", "senior"),
        ("Sénior Backend Engineer (m/f)", "senior"),
        ("Sr. Java Developer", "senior"),
        ("Junior Frontend Developer", "junior"),
        ("Programador Júnior", "junior"),
        ("Jr Data Analyst", "junior"),
        ("Entry-level QA Engineer", "junior"),
        ("Mid-Level DevOps Engineer", "mid"),
        ("Programador Pleno .NET", "mid"),
        ("Intermediate Software Engineer", "mid"),
        ("Estágio Profissional - Desenvolvimento Web", "intern"),
        ("Software Engineering Intern", "intern"),
        ("Trainee Developer", "intern"),
        ("Tech Lead Java", "lead"),
        ("Principal Engineer", "lead"),
        ("Staff Software Engineer", "lead"),
        ("Head of Engineering", "lead"),
        ("Senior Tech Lead", "senior"),
        ("Junior/Mid PHP Developer", "junior"),
        ("Mid-Senior Data Engineer", "mid"),
        ("Python Developer", "unknown"),
        ("Internal Tools Engineer", "unknown"),
        ("Leading fintech hiring Backend Engineer", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_detects_seniority_from_title(title: str | None, expected: str) -> None:
    assert normalize_seniority(title) == expected


@pytest.mark.parametrize(
    "word,expected",
    [("jr", "junior"), ("pleno", "mid"), ("sr", "senior"), ("estagio", "intern")],
)
def test_user_shorthands(word: str, expected: str) -> None:
    assert normalize_seniority(word) == expected


def test_filter_matches_only_listed_levels_and_skips_unknown() -> None:
    filters = {"seniorities": ["junior", "mid"]}
    assert job_matches(Job(title="x", seniority="junior", tags=[]), filters)
    assert not job_matches(Job(title="x", seniority="senior", tags=[]), filters)
    assert not job_matches(Job(title="x", seniority="unknown", tags=[]), filters)
    assert job_matches(Job(title="x", seniority="unknown", tags=[]), {})


def test_digest_shows_the_level() -> None:
    job = Job(
        title="Dev",
        company="Acme",
        seniority="junior",
        work_mode="remote",
        tags=[],
        url="https://x/1",
    )
    text = render_telegram(
        Digest(jobs=[job], remaining=0, jobs_url="https://v/jobs", unsubscribe_url="u")
    )
    assert "Júnior" in text
