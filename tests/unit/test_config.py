import pytest

from ptjobs.config import Settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("postgres://u:p@db:5432/app", "postgresql+psycopg://u:p@db:5432/app"),
        ("postgresql://u:p@db:5432/app", "postgresql+psycopg://u:p@db:5432/app"),
        (
            "postgresql+psycopg://u:p@db:5432/app",
            "postgresql+psycopg://u:p@db:5432/app",
        ),
    ],
)
def test_database_url_uses_psycopg_driver(raw: str, expected: str) -> None:
    assert Settings(database_url=raw).database_url == expected
