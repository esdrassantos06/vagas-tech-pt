"""Discord slash commands over HTTP interactions."""

import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import select

from ptjobs import discord_bot
from ptjobs.api import create_app, get_db
from ptjobs.config import Settings, get_settings
from ptjobs.errors import ChannelError
from ptjobs.models import Subscription

KEY = Ed25519PrivateKey.generate()
PUBLIC = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
WEBHOOK = "https://discord.com/api/webhooks/9/tok"


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(discord_bot, "create_webhook", lambda *a, **k: WEBHOOK)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        discord_public_key=PUBLIC, discord_bot_token="BT"
    )
    return TestClient(app, raise_server_exceptions=False)


def _post(client, payload: object, *, timestamp: str | None = None, key=KEY):
    body = json.dumps(payload).encode()
    ts = timestamp or str(int(time.time()))
    signature = key.sign(ts.encode() + body).hex()
    return client.post(
        "/discord/interactions",
        content=body,
        headers={
            "content-type": "application/json",
            "x-signature-ed25519": signature,
            "x-signature-timestamp": ts,
        },
    )


def _command(client, name: str, value: str | None = None, channel: str = "c1"):
    options = [{"name": "valores", "type": 3, "value": value}] if value else []
    resp = _post(
        client,
        {"type": 2, "channel_id": channel, "data": {"name": name, "options": options}},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["flags"] == 64
    return data["content"]


def test_ping_is_answered_with_pong(client) -> None:
    assert _post(client, {"type": 1}).json() == {"type": 1}


def test_rejects_bad_or_stale_signatures(client) -> None:
    assert (
        _post(client, {"type": 1}, key=Ed25519PrivateKey.generate()).status_code == 401
    )
    stale = str(int(time.time()) - 3600)
    assert _post(client, {"type": 1}, timestamp=stale).status_code == 401
    unsigned = client.post("/discord/interactions", json={"type": 1})
    assert unsigned.status_code == 401


def test_alertas_creates_verified_channel_subscription(client, db_session) -> None:
    assert "Alertas ativos" in _command(client, "alertas")
    sub = db_session.scalars(select(Subscription)).one()
    assert (sub.channel, sub.target, sub.discord_channel_id) == (
        "discord",
        WEBHOOK,
        "c1",
    )
    assert sub.verified_at is not None and sub.unsubscribe_token
    assert "Já tens alertas" in _command(client, "alertas")
    assert len(db_session.scalars(select(Subscription)).all()) == 1


def test_filter_commands_reuse_bot_logic(client, db_session) -> None:
    assert "/alertas" in _command(client, "tags", "python")
    _command(client, "alertas")
    _command(client, "tags", "react next")
    _command(client, "tags", "+go -react")
    _command(client, "nivel", "jr")
    assert "Nível: junior" in _command(client, "filtros")
    db_session.expire_all()
    sub = db_session.scalars(select(Subscription)).one()
    assert sub.filters == {"tags": ["nextjs", "golang"], "seniorities": ["junior"]}
    assert "/alertas" in _command(client, "parar")
    assert db_session.scalars(select(Subscription)).all() == []


def test_commands_are_admin_only_and_guild_only() -> None:
    payloads = discord_bot.command_payloads()
    assert {p["name"] for p in payloads} >= {"alertas", "tags", "nivel", "parar"}
    assert all(p["default_member_permissions"] == "32" for p in payloads)
    assert all(p["contexts"] == [0] for p in payloads)


def test_create_webhook_calls_discord_and_hides_token() -> None:
    seen = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["authorization"])
        return httpx.Response(200, json={"id": "5", "token": "abc"})

    url = discord_bot.create_webhook(
        "SECRET", "c1", client=httpx.Client(transport=httpx.MockTransport(ok))
    )
    assert url == "https://discord.com/api/webhooks/5/abc"
    assert seen == {
        "url": "https://discord.com/api/v10/channels/c1/webhooks",
        "auth": "Bot SECRET",
    }
    denied = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ChannelError) as exc:
        discord_bot.create_webhook("SECRET", "c1", client=denied)
    assert "SECRET" not in str(exc.value) and exc.value.__suppress_context__


def test_signed_non_object_body_is_rejected(client) -> None:
    assert _post(client, [1, 2]).status_code == 422


def test_parar_also_deletes_the_channel_webhook(client, monkeypatch) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(
        discord_bot, "delete_webhook", lambda url, **k: deleted.append(url)
    )
    _command(client, "alertas")
    _command(client, "parar")
    assert deleted == [WEBHOOK]


def test_delete_webhook_is_best_effort_and_only_for_discord() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    discord_bot.delete_webhook(WEBHOOK, client=client)
    discord_bot.delete_webhook("https://evil.example/x", client=client)
    assert calls == [f"DELETE {WEBHOOK}"]
