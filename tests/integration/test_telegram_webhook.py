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
        telegram_webhook_secret=SECRET, telegram_bot_username="ptjobsbot"
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
