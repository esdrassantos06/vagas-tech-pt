"""Discord slash commands over HTTP interactions, posting as the bot."""

import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import select

from ptjobs import discord_bot, notify
from ptjobs.api import create_app, get_db, get_verifier
from ptjobs.config import Settings, get_settings
from ptjobs.errors import ChannelError
from ptjobs.models import Subscription

KEY = Ed25519PrivateKey.generate()
PUBLIC = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
CHANNEL = "111"


@pytest.fixture
def posts(monkeypatch) -> list:
    sent: list = []
    monkeypatch.setattr(
        notify,
        "send_discord_bot",
        lambda token, channel, payload, **k: sent.append((token, channel, payload)),
    )
    return sent


@pytest.fixture
def client(db_session, posts):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        discord_public_key=PUBLIC, discord_bot_token="BT", discord_application_id="42"
    )
    app.dependency_overrides[get_verifier] = lambda: lambda c, t, tok: None
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


def _command(client, name: str, value: str | None = None, channel: str = CHANNEL):
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


def test_alertas_creates_verified_channel_subscription(
    client, db_session, posts
) -> None:
    assert "Alertas ativos" in _command(client, "alertas")
    sub = db_session.scalars(select(Subscription)).one()
    assert (sub.channel, sub.target, sub.discord_channel_id) == (
        "discord",
        None,
        CHANNEL,
    )
    assert sub.verified_at is not None and sub.unsubscribe_token
    assert [(token, channel) for token, channel, _ in posts] == [("BT", CHANNEL)]
    assert "Já tens alertas" in _command(client, "alertas")
    assert len(db_session.scalars(select(Subscription)).all()) == 1


def test_alertas_needs_permission_to_post(client, db_session, monkeypatch) -> None:
    def denied(*a, **k):
        raise ChannelError("Discord delivery failed")

    monkeypatch.setattr(notify, "send_discord_bot", denied)
    assert "Send Messages" in _command(client, "alertas")
    assert db_session.scalars(select(Subscription)).all() == []


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


def test_send_discord_bot_posts_to_channel_and_hides_token() -> None:
    seen = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["authorization"])
        return httpx.Response(200, json={"id": "1"})

    notify.send_discord_bot(
        "SECRET",
        CHANNEL,
        {"content": "x"},
        client=httpx.Client(transport=httpx.MockTransport(ok)),
    )
    assert seen == {
        "url": f"https://discord.com/api/v10/channels/{CHANNEL}/messages",
        "auth": "Bot SECRET",
    }
    denied = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(ChannelError) as exc:
        notify.send_discord_bot("SECRET", CHANNEL, {"content": "x"}, client=denied)
    assert "SECRET" not in str(exc.value) and exc.value.__suppress_context__
    with pytest.raises(ChannelError):
        notify.send_discord_bot("SECRET", "../users/@me", {}, client=denied)


def test_digest_for_bot_subscription_posts_as_bot() -> None:
    from ptjobs.digest import Digest

    seen = {}
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: seen.update(url=str(r.url)) or httpx.Response(200, json={})
        )
    )
    deliver = notify.build_deliver(Settings(discord_bot_token="BT"), client)
    sub = Subscription(
        channel="discord",
        target=None,
        discord_channel_id=CHANNEL,
        filters={},
        manage_token_hash="h",
    )
    deliver(sub, Digest(jobs=[], remaining=0, jobs_url="j", unsubscribe_url="u"))
    assert seen["url"].endswith(f"/channels/{CHANNEL}/messages")


def test_signed_non_object_body_is_rejected(client) -> None:
    assert _post(client, [1, 2]).status_code == 422


def test_register_commands_overwrites_global_commands() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, url=str(request.url))
        return httpx.Response(200, json=[])

    discord_bot.register_commands(
        "APP", "BT", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert seen == {
        "method": "PUT",
        "url": "https://discord.com/api/v10/applications/APP/commands",
    }


def _api_subscription(client) -> dict:
    resp = client.post(
        "/subscriptions",
        json={"channel": "discord", "filters": {"tags": ["python"]}},
    )
    assert resp.status_code == 202
    return resp.json()


def test_api_signup_returns_install_link_and_code(client, db_session) -> None:
    body = _api_subscription(client)
    assert body["deep_link"] == (
        "https://discord.com/oauth2/authorize?client_id=42"
        "&scope=bot%20applications.commands&permissions=19456"
    )
    assert body["verify_code"] and body["manage_token"]
    sub = db_session.scalars(select(Subscription)).one()
    assert (sub.target, sub.discord_channel_id, sub.verified_at) == (None, None, None)


def test_alertas_with_code_binds_the_api_subscription(
    client, db_session, posts
) -> None:
    code = _api_subscription(client)["verify_code"]
    assert "confirmada" in _command(client, "alertas", code)
    db_session.expire_all()
    sub = db_session.scalars(select(Subscription)).one()
    assert sub.discord_channel_id == CHANNEL and sub.verified_at is not None
    assert sub.filters == {"tags": ["python"]} and sub.verify_token_hash is None
    assert len(posts) == 1
    assert "inválido" in _command(client, "alertas", code, channel="222")


def test_alertas_code_is_not_reused_in_an_active_channel(client, db_session) -> None:
    _command(client, "alertas")
    code = _api_subscription(client)["verify_code"]
    assert "Já tens alertas" in _command(client, "alertas", code)
    assert len(db_session.scalars(select(Subscription)).all()) == 2


def test_bot_codes_cannot_be_confirmed_by_link(client) -> None:
    code = _api_subscription(client)["verify_code"]
    page = client.post("/subscriptions/verify", params={"token": code})
    assert page.status_code == 422 and "/alertas" in page.text


def test_discord_webhook_target_is_still_validated(client) -> None:
    bad = client.post(
        "/subscriptions",
        json={"channel": "discord", "target": "https://evil.example/x"},
    )
    assert bad.status_code == 422
