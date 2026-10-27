"""Discord bot: slash commands received as signed HTTP interactions.

/alertas creates a webhook in the channel and a verified subscription for it;
the other commands reuse the Telegram bot's filter logic on that channel.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ptjobs import notify, telegram_bot
from ptjobs.config import Settings
from ptjobs.errors import ChannelError
from ptjobs.models import Subscription

logger = logging.getLogger(__name__)
API = "https://discord.com/api/v10"
MAX_CLOCK_SKEW = 300
_PING, _PONG, _REPLY, _EPHEMERAL = 1, 1, 4, 64
_MANAGE_GUILD = str(1 << 5)
_START = "/alertas"
COMMANDS = {
    "alertas": "Ativar alertas de vagas tech neste canal",
    "filtros": "Ver os filtros deste canal",
    "tags": "Tecnologias, ex: python react ou +go -react",
    "cidades": "Cidades, ex: lisboa porto",
    "modo": "Regime: remoto hibrido presencial",
    "nivel": "Nível: estagio junior mid senior lead",
    "palavras": "Palavras no título ou descrição",
    "salario": "Salário anual mínimo, ex: 40000 (0 apaga)",
    "limpar": "Apagar todos os filtros",
    "token": "Novo token de gestão para a API (só tu vês)",
    "parar": "Cancelar os alertas deste canal",
}
_WITH_VALUES = {"tags", "cidades", "modo", "nivel", "palavras", "salario"}
WELCOME = (
    "Alertas ativos neste canal. Sem filtros recebes todas as vagas tech. "
    "Usa /tags, /cidades, /modo, /nivel, /palavras e /salario para filtrar."
)
ALREADY_ACTIVE = "Já tens alertas ativos neste canal. Vê-os com /filtros."
NO_WEBHOOK = (
    "Não consegui criar o webhook. Dá ao bot a permissão Gerir Webhooks "
    "neste canal e tenta outra vez."
)


def verify(
    public_key: str,
    signature: str,
    timestamp: str,
    body: bytes,
    *,
    now: float | None = None,
) -> bool:
    """Return True for a fresh request signed with the application's Ed25519 key."""
    try:
        if abs((now or time.time()) - int(timestamp)) > MAX_CLOCK_SKEW:
            return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature), timestamp.encode() + body
        )
    except ValueError, InvalidSignature:
        return False
    return True


def command_payloads() -> list[dict[str, Any]]:
    """Slash command definitions: admin-only (Manage Server), servers only."""
    return [
        {
            "name": name,
            "description": description,
            "type": 1,
            "default_member_permissions": _MANAGE_GUILD,
            "contexts": [0],
            "integration_types": [0],
            "options": [
                {
                    "type": 3,
                    "name": "valores",
                    "description": "Valores",
                    "required": False,
                }
            ]
            if name in _WITH_VALUES
            else [],
        }
        for name, description in COMMANDS.items()
    ]


def _discord(
    method: str, url: str, bot_token: str, client: httpx.Client, **kw: Any
) -> Any:
    try:
        response = client.request(
            method, url, headers={"Authorization": f"Bot {bot_token}"}, **kw
        )
        response.raise_for_status()
    except httpx.HTTPError:
        raise ChannelError("Discord API request failed") from None
    return response.json()


def register_commands(app_id: str, bot_token: str, *, client: httpx.Client) -> None:
    """Replace the application's global slash commands with COMMANDS."""
    _discord(
        "PUT",
        f"{API}/applications/{app_id}/commands",
        bot_token,
        client,
        json=command_payloads(),
    )


def create_webhook(bot_token: str, channel_id: str, *, client: httpx.Client) -> str:
    """Create a webhook in a channel and return its URL."""
    hook = _discord(
        "POST",
        f"{API}/channels/{channel_id}/webhooks",
        bot_token,
        client,
        json={"name": "Vagas Tech PT"},
    )
    return f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"


def delete_webhook(url: str, *, client: httpx.Client) -> None:
    """Best-effort removal of a Discord webhook the bot created."""
    if not notify.validate_discord_webhook(url):
        return
    try:
        client.delete(url).raise_for_status()
    except httpx.HTTPError:
        logger.warning("could not delete a Discord webhook")


def handle(
    db: Session,
    interaction: dict[str, Any],
    *,
    settings: Settings,
    client: httpx.Client,
) -> dict[str, Any]:
    """Answer one interaction; every command reply is visible only to its author."""
    if interaction.get("type") == _PING:
        return {"type": _PONG}
    data = interaction.get("data") or {}
    values = " ".join(str(o.get("value", "")) for o in data.get("options") or [])
    parsed = telegram_bot.parse_command(f"/{data.get('name', '')} {values}")
    channel_id = str(interaction.get("channel_id") or "")
    if parsed is None or not channel_id:
        reply = "Comando desconhecido."
    elif parsed[0] == "alertas":
        reply = _start(db, channel_id, settings, client)
    else:
        subscriptions = _channel_subscriptions(db, channel_id)
        reply = (
            telegram_bot.manage(
                db,
                subscriptions,
                *parsed,
                base_url=settings.public_base_url,
                start=_START,
            )
            or "Comando desconhecido."
        )
        if parsed[0] == "parar":
            for subscription in subscriptions:
                delete_webhook(subscription.target or "", client=client)
    return {
        "type": _REPLY,
        "data": {
            "content": reply[:2000],
            "flags": _EPHEMERAL,
            "allowed_mentions": {"parse": []},
        },
    }


def _channel_subscriptions(db: Session, channel_id: str) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription).where(
                Subscription.channel == "discord",
                Subscription.discord_channel_id == channel_id,
            )
        )
    )


def _start(
    db: Session, channel_id: str, settings: Settings, client: httpx.Client
) -> str:
    if _channel_subscriptions(db, channel_id):
        return ALREADY_ACTIVE
    try:
        url = create_webhook(settings.discord_bot_token, channel_id, client=client)
    except ChannelError:
        return NO_WEBHOOK
    db.add(
        Subscription(
            channel="discord",
            target=url,
            discord_channel_id=channel_id,
            filters={},
            manage_token_hash=notify.hash_token(notify.new_token()),
            unsubscribe_token=notify.new_token(),
            verified_at=func.now(),
        )
    )
    db.flush()
    return WELCOME
