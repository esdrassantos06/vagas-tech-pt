import pytest
from fastapi.testclient import TestClient

from ptjobs.api import create_app, get_db, get_verifier
from ptjobs.config import Settings, get_settings
from ptjobs.models import Subscription
from ptjobs.notify import hash_token, new_token

SECRET = "webhook-secret"


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_verifier] = lambda: lambda c, t, tok: None
    app.dependency_overrides[get_settings] = lambda: Settings(
        telegram_webhook_secret=SECRET,
        telegram_bot_username="ptjobsbot",
        telegram_bot_token="BOT",
    )
    return TestClient(app, raise_server_exceptions=False)


def _pending_telegram(db_session) -> str:
    token = new_token()
    sub = Subscription(
        channel="telegram",
        target=None,
        filters={},
        manage_token_hash="m",
        verify_token_hash=hash_token(token),
    )
    db_session.add(sub)
    db_session.flush()
    return token


def _update(token: str, chat_id: int = 42) -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "text": f"/start {token}",
        }
    }


def test_start_sets_chat_and_verifies(client, db_session):
    token = _pending_telegram(db_session)
    resp = client.post(
        "/telegram/webhook",
        json=_update(token),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    sub = db_session.query(Subscription).one()
    assert sub.target == "42"
    assert sub.verified_at is not None


def test_rejects_wrong_secret(client, db_session):
    token = _pending_telegram(db_session)
    resp = client.post(
        "/telegram/webhook",
        json=_update(token),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_unknown_token_is_ignored(client):
    resp = client.post(
        "/telegram/webhook",
        json=_update("nope"),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200


def test_non_start_message_is_ignored(client):
    resp = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1}, "text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200


@pytest.fixture
def sent(monkeypatch):
    messages: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "ptjobs.notify.send_telegram",
        lambda token, chat_id, text, *, client: messages.append((token, chat_id, text)),
    )
    return messages


def test_start_replies_with_confirmation(client, db_session, sent):
    token = _pending_telegram(db_session)
    client.post(
        "/telegram/webhook",
        json=_update(token, chat_id=77),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert len(sent) == 1
    assert sent[0][1] == "77"
    assert "confirmada" in sent[0][2].lower()


def test_unknown_token_replies_invalid_link(client, sent):
    client.post(
        "/telegram/webhook",
        json=_update("nope", chat_id=5),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert len(sent) == 1
    assert "inválido" in sent[0][2].lower()


def test_reply_failure_still_verifies_and_returns_200(client, db_session, monkeypatch):
    from ptjobs.errors import ChannelError

    def boom(*args, **kwargs):
        raise ChannelError("telegram down")

    monkeypatch.setattr("ptjobs.notify.send_telegram", boom)
    token = _pending_telegram(db_session)
    resp = client.post(
        "/telegram/webhook",
        json=_update(token),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(Subscription).one().verified_at is not None
