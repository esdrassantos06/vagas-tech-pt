import pytest

from ptjobs.telegram_bot import parse_command


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/tags python react", ("tags", ["python", "react"])),
        ("/tags@vagastechpt_bot Python", ("tags", ["Python"])),
        ("/filtros", ("filtros", [])),
        ("  /parar  ", ("parar", [])),
        ("/cidades lisboa,  porto", ("cidades", ["lisboa", "porto"])),
        ("olá", None),
        ("", None),
    ],
)
def test_parse_command(text, expected) -> None:
    assert parse_command(text) == expected
