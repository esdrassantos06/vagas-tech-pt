"""Telegram bot commands: create and manage a subscription from the chat.

The chat id identifies the user (Telegram authenticates it), so commands act on
the subscriptions whose target is that chat without needing a manage token.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ptjobs import normalize, notify
from ptjobs.models import Subscription
from ptjobs.schemas import SubscriptionFilters

HELP = (
    "Comandos:\n"
    "/start - ativar alertas neste chat\n"
    "/filtros - ver os teus filtros\n"
    "/tags python react - tecnologias\n"
    "/cidades lisboa porto - cidades\n"
    "/modo remoto hibrido presencial - regime de trabalho\n"
    "/palavras django - palavras no título ou descrição\n"
    "/salario 40000 - salário anual mínimo\n"
    "/limpar - apagar todos os filtros\n"
    "/token - novo token de gestão para a API\n"
    "/parar - cancelar os alertas\n\n"
    "Um comando sem valores apaga esse filtro, por exemplo /tags."
)
WELCOME = "Alertas ativos. Sem filtros recebes todas as vagas tech.\n\n" + HELP
ALREADY_ACTIVE = "Já tens alertas ativos neste chat.\n\n" + HELP
CONFIRMED = (
    "Subscrição confirmada. Vais receber aqui as novas vagas tech que batem "
    "com os teus filtros."
)
INVALID_LINK = "Link inválido ou já utilizado. Cria uma nova subscrição."
NEEDS_START = "Ainda não tens alertas neste chat. Envia /start para começar."
STOPPED = "Alertas cancelados. Envia /start para voltar a ativar."
CLEARED = "Filtros apagados. Recebes todas as vagas tech."
SAVED = "Filtros guardados.\n\n"
REJECTED = "Não consegui guardar: {reason}"

_LIST_FILTERS = {
    "tags": "tags",
    "cidades": "cities",
    "modo": "work_modes",
    "palavras": "keywords",
}
_LABELS = {
    "tags": "Tags",
    "cities": "Cidades",
    "work_modes": "Modo",
    "keywords": "Palavras",
    "min_salary": "Salário mínimo",
}
_COMMAND = re.compile(r"^/(\w+)(?:@\w+)?(?:\s+(.*))?$", re.DOTALL)


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Split a bot command into its name and arguments, or None if not a command."""
    match = _COMMAND.match(text.strip())
    if not match:
        return None
    args = re.split(r"[\s,]+", (match.group(2) or "").strip())
    return match.group(1).lower(), [arg for arg in args if arg]


def handle(db: Session, chat_id: str, text: str, *, base_url: str) -> str:
    """Run a bot command for a chat and return the reply to send back."""
    parsed = parse_command(text)
    if parsed is None:
        return HELP
    command, args = parsed

    if command == "start":
        return _start(db, chat_id, args)

    subscriptions = _chat_subscriptions(db, chat_id)
    if command in {"ajuda", "help"}:
        return HELP
    if not subscriptions:
        return NEEDS_START if command in _MANAGE_COMMANDS else HELP

    if command == "filtros":
        return _describe(subscriptions[0].filters)
    if command == "parar":
        for subscription in subscriptions:
            db.delete(subscription)
        db.flush()
        return STOPPED
    if command == "token":
        return _rotate_token(db, subscriptions[0], base_url)
    if command == "limpar":
        return _save(db, subscriptions, {}, CLEARED)
    if command in _LIST_FILTERS:
        return _set_list(db, subscriptions, _LIST_FILTERS[command], args)
    if command == "salario":
        return _set_salary(db, subscriptions, args)
    return HELP


_MANAGE_COMMANDS = {"filtros", "parar", "token", "limpar", "salario", *_LIST_FILTERS}


def _chat_subscriptions(db: Session, chat_id: str) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription)
            .where(
                Subscription.channel == "telegram",
                Subscription.target == chat_id,
                Subscription.verified_at.is_not(None),
            )
            .order_by(Subscription.created_at.desc())
        )
    )


def _start(db: Session, chat_id: str, args: list[str]) -> str:
    if args:
        return _verify(db, chat_id, args[0])
    if _chat_subscriptions(db, chat_id):
        return ALREADY_ACTIVE
    db.add(
        Subscription(
            channel="telegram",
            target=chat_id,
            filters={},
            manage_token_hash=notify.hash_token(notify.new_token()),
            verified_at=func.now(),
        )
    )
    db.flush()
    return WELCOME


def _verify(db: Session, chat_id: str, token: str) -> str:
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.channel == "telegram",
            Subscription.verify_token_hash == notify.hash_token(token),
        )
    )
    if subscription is None:
        return INVALID_LINK
    subscription.target = chat_id
    subscription.verified_at = func.now()
    subscription.verify_token_hash = None
    db.flush()
    return CONFIRMED


def _rotate_token(db: Session, subscription: Subscription, base_url: str) -> str:
    token = notify.new_token()
    subscription.manage_token_hash = notify.hash_token(token)
    db.flush()
    return (
        "O teu novo token de gestão (o anterior deixou de funcionar):\n\n"
        f"{token}\n\n"
        f"Usa-o em {base_url}/docs como Authorization: Bearer <token>. "
        "Não o partilhes."
    )


def _normalize_values(key: str, values: list[str]) -> list[str]:
    if key == "cities":
        return [normalize.normalize_city(v) or v.lower() for v in values]
    if key == "work_modes":
        return [normalize.normalize_work_mode(v) for v in values]
    return [v.lower() for v in values]


def _set_list(
    db: Session, subscriptions: list[Subscription], key: str, args: list[str]
) -> str:
    filters = dict(subscriptions[0].filters)
    values = _normalize_values(key, args)
    if "unknown" in values:
        return REJECTED.format(reason="usa remoto, hibrido ou presencial.")
    if values:
        filters[key] = values
    else:
        filters.pop(key, None)
    return _save(db, subscriptions, filters, None)


def _set_salary(db: Session, subscriptions: list[Subscription], args: list[str]) -> str:
    filters = dict(subscriptions[0].filters)
    if not args or args[0] == "0":
        filters.pop("min_salary", None)
    elif args[0].isdigit():
        filters["min_salary"] = int(args[0])
    else:
        return REJECTED.format(reason="indica um número, por exemplo /salario 40000.")
    return _save(db, subscriptions, filters, None)


def _save(
    db: Session,
    subscriptions: list[Subscription],
    filters: dict[str, Any],
    reply: str | None,
) -> str:
    try:
        clean = SubscriptionFilters(**filters).model_dump(exclude_defaults=True)
    except ValidationError as exc:
        return REJECTED.format(reason=exc.errors()[0]["msg"])
    for subscription in subscriptions:
        subscription.filters = clean
    db.flush()
    return reply or SAVED + _describe(clean)


def _describe(filters: dict[str, Any]) -> str:
    if not filters:
        return "Sem filtros: recebes todas as vagas tech."
    lines = ["Os teus filtros:"]
    for key, label in _LABELS.items():
        value = filters.get(key)
        if value:
            shown = ", ".join(value) if isinstance(value, list) else str(value)
            lines.append(f"- {label}: {shown}")
    return "\n".join(lines)
