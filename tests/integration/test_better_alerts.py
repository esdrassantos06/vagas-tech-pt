"""Unsubscribe links, token recovery by email and bot +/- edits."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ptjobs import notify
from ptjobs.api import create_app, get_db, get_verifier
from ptjobs.models import Job, Subscription
from ptjobs.telegram_bot import handle


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_verifier] = lambda: lambda c, t, tok: None
    return TestClient(app, raise_server_exceptions=False)


def _email_sub(db, email="dev@x.com") -> Subscription:
    sub = Subscription(
        channel="email",
        target=email,
        filters={},
        manage_token_hash="old",
        verified_at=datetime.now(UTC),
        unsubscribe_token=notify.new_token(),
    )
    db.add(sub)
    db.flush()
    return sub


def test_created_subscription_has_unsubscribe_token(client, db_session) -> None:
    client.post(
        "/subscriptions",
        json={"channel": "discord", "target": "https://discord.com/api/webhooks/1/a"},
    )
    assert db_session.scalars(select(Subscription)).one().unsubscribe_token


def test_unsubscribe_link_confirms_then_deletes(client, db_session) -> None:
    sub = _email_sub(db_session)
    page = client.get(f"/subscriptions/unsubscribe/{sub.unsubscribe_token}")
    assert page.status_code == 200 and "<form" in page.text
    assert db_session.scalars(select(Subscription)).one()
    done = client.post(f"/subscriptions/unsubscribe/{sub.unsubscribe_token}")
    assert done.status_code == 200
    db_session.expire_all()
    assert db_session.scalars(select(Subscription)).all() == []


def test_unsubscribe_unknown_token_does_not_leak(client) -> None:
    assert client.post("/subscriptions/unsubscribe/nope").status_code == 200


def test_recover_emails_new_token_once_per_hour(
    client, db_session, monkeypatch
) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notify,
        "send_email",
        lambda settings, to, subject, body, **k: sent.append((to, body)),
    )
    sub = _email_sub(db_session)
    assert (
        client.post("/subscriptions/recover", json={"email": "DEV@x.com"}).status_code
        == 202
    )
    db_session.expire_all()
    new_hash = db_session.get(Subscription, sub.id).manage_token_hash
    assert new_hash != "old"
    token = next(w for w in sent[0][1].split() if notify.hash_token(w) == new_hash)
    assert token
    client.post("/subscriptions/recover", json={"email": "dev@x.com"})
    assert len(sent) == 1


def test_recover_unknown_email_is_silent(client, monkeypatch) -> None:
    sent: list = []
    monkeypatch.setattr(notify, "send_email", lambda *a, **k: sent.append(a))
    assert (
        client.post("/subscriptions/recover", json={"email": "who@x.com"}).status_code
        == 202
    )
    assert sent == []


def test_digest_backfills_unsubscribe_link(db_session) -> None:
    now = datetime.now(UTC)
    db_session.add(
        Job(
            source="itjobs",
            external_id="1",
            url="https://x/1",
            title="Python Dev",
            company="F",
            work_mode="remote",
            tags=["python"],
            first_seen_at=now,
            last_seen_at=now,
            fingerprint="f" * 40,
        )
    )
    sub = Subscription(
        channel="discord",
        target="https://discord.com/api/webhooks/1/a",
        filters={},
        manage_token_hash="h",
        verified_at=now,
    )
    db_session.add(sub)
    db_session.flush()
    got: list = []
    notify.run_digest(
        db_session,
        deliver=lambda s, d: got.append(d),
        jobs_url="https://v/jobs",
        base_url="https://v",
    )
    assert sub.unsubscribe_token
    assert (
        got[0].unsubscribe_url
        == f"https://v/subscriptions/unsubscribe/{sub.unsubscribe_token}"
    )


def test_bot_plus_minus_edits_and_next_alias(db_session) -> None:
    say = lambda text: handle(db_session, "77", text, base_url="https://v")  # noqa: E731
    say("/start")
    say("/tags react next")
    say("/tags +go -react")
    db_session.expire_all()
    sub = db_session.scalars(
        select(Subscription).where(Subscription.target == "77")
    ).one()
    assert sub.filters == {"tags": ["nextjs", "golang"]}
