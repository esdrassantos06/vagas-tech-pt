"""Discord answers that mean the destination is gone for good."""

import httpx
import pytest

from ptjobs.errors import ChannelError, DestinationGoneError
from ptjobs.notify import send_discord, send_discord_bot


def _client(status: int, body: dict | None = None) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body or {}))
    )


@pytest.mark.parametrize(
    "status,body",
    [(404, {"code": 10003}), (403, {"code": 50001})],
)
def test_bot_without_access_or_channel_is_gone(status: int, body: dict) -> None:
    with pytest.raises(DestinationGoneError):
        send_discord_bot("BT", "111", {"content": "x"}, client=_client(status, body))


@pytest.mark.parametrize(
    "status,body",
    [(403, {"code": 50013}), (500, {}), (429, {"code": 0})],
)
def test_fixable_or_temporary_errors_are_not_gone(status: int, body: dict) -> None:
    with pytest.raises(ChannelError) as exc:
        send_discord_bot("BT", "111", {"content": "x"}, client=_client(status, body))
    assert not isinstance(exc.value, DestinationGoneError)


def test_deleted_webhook_is_gone() -> None:
    with pytest.raises(DestinationGoneError):
        send_discord(
            "https://discord.com/api/webhooks/1/a",
            "x",
            client=_client(404, {"code": 10015}),
        )
