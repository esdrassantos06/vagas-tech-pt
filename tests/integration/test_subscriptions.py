import pytest
from fastapi.testclient import TestClient

from ptjobs.api import create_app, get_db, get_verifier
from ptjobs.models import Subscription


@pytest.fixture
def recorder():
    sent: list[tuple] = []
    yield sent


@pytest.fixture
def client(db_session, recorder):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_verifier] = lambda: (
        lambda channel, target, token: recorder.append((channel, target, token))
    )
    return TestClient(app, raise_server_exceptions=False)


def _create(client, **body):
    base = {
        "channel": "discord",
        "target": "https://discord.com/api/webhooks/1/abc",
        "filters": {"tags": ["python"]},
    }
    base.update(body)
    return client.post("/subscriptions", json=base)


def test_create_discord_returns_token_once(client, db_session, recorder):
    resp = _create(client)
    assert resp.status_code == 202
    body = resp.json()
    assert "manage_token" in body and len(body["manage_token"]) >= 32
    assert db_session.query(Subscription).count() == 1
    sub = db_session.query(Subscription).one()
    assert sub.verified_at is None
    assert sub.manage_token_hash != body["manage_token"]
    assert len(recorder) == 1 and recorder[0][0] == "discord"


def test_create_rejects_invalid_discord_webhook(client):
    resp = _create(client, target="https://evil.com/hook")
    assert resp.status_code == 422


def test_create_rejects_invalid_email(client):
    resp = _create(client, channel="email", target="not-an-email")
    assert resp.status_code == 422


def test_create_telegram_forces_null_target(client, db_session):
    resp = _create(client, channel="telegram", target="999888")
    assert resp.status_code == 202
    assert "deep_link" in resp.json()
    assert db_session.query(Subscription).one().target is None


def test_verify_rejects_telegram(client, db_session):
    resp = _create(client, channel="telegram", target=None)
    token = resp.json()["deep_link"].split("start=")[1]
    assert (
        client.get("/subscriptions/verify", params={"token": token}).status_code == 422
    )


def test_verify_marks_verified(client, db_session, recorder):
    _create(client)
    token = recorder[0][2]
    resp = client.get("/subscriptions/verify", params={"token": token})
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(Subscription).one().verified_at is not None


def test_verify_bad_token_404(client):
    assert (
        client.get("/subscriptions/verify", params={"token": "nope"}).status_code == 404
    )


def test_me_requires_valid_token(client):
    _create(client)
    assert client.get("/subscriptions/me").status_code == 401
    assert (
        client.get(
            "/subscriptions/me", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )


def test_me_returns_subscription_without_hashes(client):
    token = _create(client).json()["manage_token"]
    resp = client.get("/subscriptions/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "discord"
    assert "manage_token_hash" not in body


def test_patch_updates_filters(client, db_session):
    token = _create(client).json()["manage_token"]
    resp = client.patch(
        "/subscriptions/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"filters": {"tags": ["go"]}},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.query(Subscription).one().filters == {"tags": ["go"]}


def test_patch_cannot_change_target(client, db_session):
    token = _create(client).json()["manage_token"]
    client.patch(
        "/subscriptions/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "https://discord.com/api/webhooks/9/evil"},
    )
    db_session.expire_all()
    assert db_session.query(Subscription).one().target == (
        "https://discord.com/api/webhooks/1/abc"
    )


def test_delete_removes_subscription(client, db_session):
    token = _create(client).json()["manage_token"]
    resp = client.delete(
        "/subscriptions/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 204
    assert db_session.query(Subscription).count() == 0


def test_unsubscribe_deletes(client, db_session, recorder):
    _create(client)
    mtoken = _create(client).json()["manage_token"]
    resp = client.post("/subscriptions/unsubscribe", json={"token": mtoken})
    assert resp.status_code == 200
    assert db_session.query(Subscription).count() == 1


def test_cap_unverified_per_target(client):
    for _ in range(3):
        assert _create(client).status_code == 202
    assert _create(client).status_code == 429


def test_cap_ignores_expired_unverified(client, db_session):
    from datetime import UTC, datetime, timedelta

    old = datetime.now(UTC) - timedelta(days=2)
    for _ in range(3):
        _create(client)
    for sub in db_session.query(Subscription).all():
        sub.created_at = old
    db_session.flush()
    assert _create(client).status_code == 202


def test_verification_messages_are_proper_portuguese(monkeypatch) -> None:
    from ptjobs import api, notify

    sent: list[tuple] = []
    monkeypatch.setattr(
        notify,
        "send_email",
        lambda settings, to, subject, body, **k: sent.append((subject, body)),
    )
    monkeypatch.setattr(
        notify,
        "send_discord",
        lambda url, content, **k: sent.append(("discord", content)),
    )
    api._default_verifier("email", "a@b.com", "tok")
    api._default_verifier("discord", "https://discord.com/api/webhooks/1/a", "tok")
    text = " ".join(part for pair in sent for part in pair)
    assert "subscrição" in text
    assert "subscricao" not in text
    assert "Confirme" not in text
