"""Seniority detection from job titles and the seniority alert filter."""

import pytest

from ptjobs.digest import Digest, render_telegram
from ptjobs.models import Job
from ptjobs.normalize import levels_from_description, normalize_seniority
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


@pytest.mark.parametrize(
    "description,expected",
    [
        ("3+ years of experience with Python", ("mid", "senior")),
        ("Minimum 5 years of professional experience", ("mid", "senior")),
        ("At least 4 years building APIs", ("mid", "senior")),
        ("Experiência mínima de 3 anos em Java", ("mid", "senior")),
        ("Pelo menos 6 anos de experiência", ("mid", "senior")),
        ("3-5 anos de experiência em React", ("mid", "senior")),
        ("<li>5 or more years of experience</li>", ("mid", "senior")),
        ("1-2 years of experience", ("junior", "mid")),
        ("Experiência de 2 anos em SQL", ("junior", "mid")),
        ("Empresa com 20 anos de história. Procuramos dev Python.", None),
        ("Com mais de 25 anos de experiência no mercado", None),
        (
            "Somos 30 anos de experiência. Pedimos 2 anos de experiência",
            ("junior", "mid"),
        ),
        ("2 anos de experi&#xea;ncia", ("junior", "mid")),
        ("Experiência entre 4 a 7 anos como business analyst", ("mid", "senior")),
        ("Experiência entre 1 a 3 anos em análise de dados", ("junior", "mid")),
        ("Procuramos 3 a 5 anos em engenhari... Ver oferta", ("mid", "senior")),
        ("Senioridade: Senior (7 a 10+ anos de experiência)", ("mid", "senior")),
        ("Between 3 and 5 years of experience as a BA", ("mid", "senior")),
        ("Requisitos: 8+ anos de experiência em backend", ("mid", "senior")),
        ("Experiência profissional superior a 2 anos", ("junior", "mid")),
        ("Mais de 100 anos de experiência no mercado mundial", None),
        ("Com mais de 10 anos de existência a nível nacional", None),
        ("12º ano ou curso técnico profissional", None),
        ("Está pelo 5 ano consecutivo no top das empresas", None),
        ("Join our team in Lisbon", None),
        ("", None),
        (None, None),
    ],
)
def test_levels_from_required_years(
    description: str | None, expected: tuple[str, ...] | None
) -> None:
    assert levels_from_description(description) == expected


def test_filter_skips_other_levels_from_the_title() -> None:
    filters = {"seniorities": ["junior", "mid"]}
    assert job_matches(Job(title="x", seniority="junior", tags=[]), filters)
    assert not job_matches(Job(title="x", seniority="senior", tags=[]), filters)
    assert job_matches(Job(title="x", seniority="unknown", tags=[]), {})


def test_unknown_level_uses_required_years_from_the_description() -> None:
    def job(description: str | None) -> Job:
        return Job(title="x", seniority="unknown", tags=[], description=description)

    five_years = job("5+ years of experience")
    assert job_matches(five_years, {"seniorities": ["senior"]})
    assert job_matches(five_years, {"seniorities": ["mid"]})
    assert not job_matches(five_years, {"seniorities": ["junior"]})

    no_years = job("Great team, remote friendly")
    for level in ("junior", "mid", "senior"):
        assert job_matches(no_years, {"seniorities": [level]})
    assert not job_matches(no_years, {"seniorities": ["intern"]})
    assert not job_matches(no_years, {"seniorities": ["lead"]})


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
