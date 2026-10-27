"""Input limits and malformed-byte handling at the API boundary."""

import pytest
from fastapi.testclient import TestClient

from ptjobs.api import create_app, get_db, get_verifier

HOOK = "https://discord.com/api/webhooks/1/abc"


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_verifier] = lambda: lambda c, t, tok: None
    return TestClient(app, raise_server_exceptions=False)


def _sub(client, **body):
    payload = {"channel": "discord", "target": HOOK, "filters": {}}
    payload.update(body)
    return client.post("/subscriptions", json=payload)


@pytest.mark.parametrize(
    "filters",
    [
        {"tags": ["py\u0000thon"]},
        {"keywords": ["x"] * 21},
        {"tags": ["x" * 51]},
        {"cities": [""]},
        {"work_modes": ["anywhere"]},
        {"min_salary": 10**30},
        {"min_salary": -1},
    ],
    ids=[
        "nul",
        "too-many-items",
        "item-too-long",
        "empty-item",
        "bad-work-mode",
        "salary-too-big",
        "salary-negative",
    ],
)
def test_bad_filters_rejected_with_422(client, filters) -> None:
    resp = _sub(client, filters=filters)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_reasonable_filters_accepted(client) -> None:
    filters = {
        "tags": ["python"] * 20,
        "work_modes": ["remote", "hybrid"],
        "min_salary": 40000,
    }
    assert _sub(client, filters=filters).status_code == 202


def test_target_too_long_rejected(client) -> None:
    assert _sub(client, target=HOOK + "a" * 500).status_code == 422


def test_discord_webhook_with_query_string_rejected(client) -> None:
    assert _sub(client, target=HOOK + "?a=1").status_code == 422


def test_nul_in_target_rejected(client) -> None:
    assert _sub(client, channel="email", target="a\u0000@x.com").status_code == 422


def test_oversized_body_rejected_with_413(client) -> None:
    resp = client.post(
        "/subscriptions",
        content=b'{"channel":"discord","target":"' + b"a" * 70_000 + b'"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_long_tokens_rejected(client) -> None:
    assert (
        client.get("/subscriptions/verify", params={"token": "x" * 129}).status_code
        == 422
    )
    assert (
        client.post("/subscriptions/unsubscribe", json={"token": "x" * 129}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "params",
    [
        {"source": "it\x00jobs"},
        {"source": "bogus"},
        {"work_mode": "re\x00mote"},
        {"work_mode": "x" * 10_000},
    ],
    ids=["nul-source", "unknown-source", "nul-work-mode", "long-work-mode"],
)
def test_jobs_enum_params_rejected_with_422(client, params) -> None:
    assert client.get("/jobs", params=params).status_code == 422


def test_nul_in_city_is_ignored_not_500(client) -> None:
    assert client.get("/jobs", params={"city": "lis\x00boa"}).status_code == 200
