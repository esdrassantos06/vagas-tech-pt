"""Telegram bot commands: create and manage a subscription from the chat."""

from sqlalchemy import select

from ptjobs.models import Subscription
from ptjobs.notify import hash_token, new_token
from ptjobs.telegram_bot import handle

CHAT = "4242"
BASE = "https://vagas.example"


def _say(db, text: str) -> str:
    return handle(db, CHAT, text, base_url=BASE)


def _subs(db) -> list[Subscription]:
    db.expire_all()
    return list(db.scalars(select(Subscription).where(Subscription.target == CHAT)))


def test_start_creates_verified_subscription(db_session) -> None:
    reply = _say(db_session, "/start")
    subs = _subs(db_session)
    assert len(subs) == 1
    assert subs[0].channel == "telegram"
    assert subs[0].verified_at is not None
    assert "/tags" in reply


def test_start_twice_does_not_duplicate(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/start")
    assert len(_subs(db_session)) == 1


def test_start_with_token_verifies_pending_subscription(db_session) -> None:
    token = new_token()
    db_session.add(
        Subscription(
            channel="telegram",
            target=None,
            filters={},
            manage_token_hash="m",
            verify_token_hash=hash_token(token),
        )
    )
    db_session.flush()
    reply = _say(db_session, f"/start {token}")
    assert "confirmada" in reply.lower()
    assert _subs(db_session)[0].verified_at is not None


def test_start_with_unknown_token_replies_invalid(db_session) -> None:
    assert "inválido" in _say(db_session, "/start nope").lower()


def test_tags_are_lowercased(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/tags Python React")
    assert _subs(db_session)[0].filters == {"tags": ["python", "react"]}


def test_cities_are_normalized(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/cidades Lisbon Porto")
    assert _subs(db_session)[0].filters == {"cities": ["lisboa", "porto"]}


def test_work_mode_accepts_portuguese(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/modo remoto híbrido")
    assert _subs(db_session)[0].filters == {"work_modes": ["remote", "hybrid"]}


def test_invalid_work_mode_is_rejected_and_kept(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/modo remoto")
    reply = _say(db_session, "/modo qualquer")
    assert "não" in reply.lower()
    assert _subs(db_session)[0].filters == {"work_modes": ["remote"]}


def test_salary_sets_and_clears(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/salario 40000")
    assert _subs(db_session)[0].filters == {"min_salary": 40000}
    _say(db_session, "/salario")
    assert _subs(db_session)[0].filters == {}


def test_command_without_args_clears_that_filter(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/tags python")
    _say(db_session, "/tags")
    assert _subs(db_session)[0].filters == {}


def test_too_many_tags_rejected(db_session) -> None:
    _say(db_session, "/start")
    reply = _say(db_session, "/tags " + " ".join(f"t{i}" for i in range(21)))
    assert "não" in reply.lower()
    assert _subs(db_session)[0].filters == {}


def test_limpar_clears_all_filters(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/tags python")
    _say(db_session, "/cidades porto")
    _say(db_session, "/limpar")
    assert _subs(db_session)[0].filters == {}


def test_filtros_shows_current_filters(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/tags python")
    reply = _say(db_session, "/filtros")
    assert "python" in reply


def test_parar_deletes_subscription(db_session) -> None:
    _say(db_session, "/start")
    _say(db_session, "/parar")
    assert _subs(db_session) == []


def test_token_rotates_manage_token(db_session) -> None:
    _say(db_session, "/start")
    before = _subs(db_session)[0].manage_token_hash
    reply = _say(db_session, "/token")
    after = _subs(db_session)[0].manage_token_hash
    assert before != after
    token = next(word for word in reply.split() if hash_token(word) == after)
    assert token
    assert BASE in reply


def test_commands_need_a_subscription(db_session) -> None:
    assert "/start" in _say(db_session, "/tags python")
    assert _subs(db_session) == []


def test_unknown_command_shows_help(db_session) -> None:
    assert "/tags" in _say(db_session, "/xpto")
