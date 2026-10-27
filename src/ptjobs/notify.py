"""Subscription matching, secure tokens, and notification channel senders."""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from smtplib import SMTP, SMTPException
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from ptjobs.config import Settings
from ptjobs.errors import ChannelError
from ptjobs.models import Job, Subscription

_DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com"})
_TELEGRAM_API = "https://api.telegram.org"


def new_token() -> str:
    """Return a cryptographically random URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token, for storage and comparison."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def job_matches(job: Job, filters: dict[str, Any]) -> bool:
    """Return True if a job satisfies every non-empty filter dimension.

    Dimensions are ANDed; values within a dimension are ORed. Recognized keys:
    keywords, cities, work_modes, tags (lists), and min_salary (int; 0 means no
    filter). An empty or missing filter set matches every job.
    """
    keywords = filters.get("keywords")
    if keywords:
        haystack = " ".join(
            filter(None, (job.title, job.company, job.description))
        ).lower()
        if not any(str(k).lower() in haystack for k in keywords):
            return False

    cities = filters.get("cities")
    if cities and job.city not in cities:
        return False

    work_modes = filters.get("work_modes")
    if work_modes and job.work_mode not in work_modes:
        return False

    tags = filters.get("tags")
    if tags and not (set(job.tags) & set(tags)):
        return False

    min_salary = filters.get("min_salary")
    return not (
        min_salary and (job.salary_max is None or job.salary_max < int(min_salary))
    )


def validate_discord_webhook(url: str) -> bool:
    """Return True only for a plain https discord.com/discordapp.com webhook URL.

    Query strings and fragments are rejected so one webhook cannot be registered
    under many distinct targets.
    """
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in _DISCORD_HOSTS
        and parsed.path.startswith("/api/webhooks/")
        and not parsed.query
        and not parsed.fragment
    )


def send_discord(webhook_url: str, content: str, *, client: httpx.Client) -> None:
    """Post a message to a validated Discord webhook; ChannelError on failure."""
    if not validate_discord_webhook(webhook_url):
        raise ChannelError(f"Invalid Discord webhook URL: {webhook_url}")
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    try:
        response = client.post(webhook_url, json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ChannelError(
            f"Discord delivery failed: HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ChannelError("Discord delivery failed") from exc


def send_telegram(
    bot_token: str, chat_id: str, text: str, *, client: httpx.Client
) -> None:
    """Send a Telegram message via the Bot API, raising ChannelError on failure."""
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"
    try:
        response = client.post(url, json={"chat_id": chat_id, "text": text})
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ChannelError(
            f"Telegram delivery failed: HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ChannelError("Telegram delivery failed") from exc


logger = logging.getLogger(__name__)
ACTIVE_WINDOW = timedelta(days=7)
Deliver = Callable[[Subscription, str], None]
SmtpFactory = Callable[[str, int], SMTP]


def send_email(
    settings: Settings,
    to: str,
    subject: str,
    body: str,
    *,
    smtp_factory: SmtpFactory = SMTP,
) -> None:
    """Send a plain-text email over SMTP, raising ChannelError on failure."""
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtp_factory(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, SMTPException, ValueError) as exc:
        raise ChannelError("Email delivery failed") from exc


CHANNEL_MAX_CHARS = {"discord": 2000, "telegram": 4096, "email": 20000}


def _more_line(remaining: int, more_url: str) -> str:
    if remaining <= 0:
        return ""
    target = f": {more_url}" if more_url else ""
    return f"\n\n... e mais {remaining} vaga(s){target}"


def format_digest(jobs: list[Job], *, max_chars: int = 4096, more_url: str = "") -> str:
    """Render a plain-text digest that fits in max_chars.

    Jobs that do not fit are summarized in a final "e mais N vaga(s)" line that
    links to more_url when given.
    """
    body = "Novas vagas tech:\n"
    shown = 0
    for job in jobs:
        entry = f"\n- {job.title} @ {job.company}\n  {job.url}"
        footer = _more_line(len(jobs) - shown - 1, more_url)
        if len(body) + len(entry) + len(footer) > max_chars:
            break
        body += entry
        shown += 1
    return body + _more_line(len(jobs) - shown, more_url)


def _new_matching_jobs(
    session: Session, subscription: Subscription, since: datetime
) -> list[Job]:
    deduped = (
        select(Job)
        .where(Job.first_seen_at > since)
        .ext(distinct_on(Job.fingerprint))
        .order_by(Job.fingerprint, Job.first_seen_at.desc(), Job.id.desc())
        .subquery()
    )
    job = aliased(Job, deduped)
    candidates = session.scalars(
        select(job).order_by(job.first_seen_at.desc(), job.id.desc())
    ).all()
    return [j for j in candidates if job_matches(j, subscription.filters)]


def run_digest(
    session: Session,
    *,
    deliver: Deliver,
    now: datetime | None = None,
    jobs_url: str = "",
) -> int:
    """Deliver one digest per verified subscription with new matching jobs.

    The watermark (last_notified_at) advances only after a successful delivery,
    so a channel failure leaves the subscription to retry on the next run.
    Returns the number of digests delivered.
    """
    now = now or datetime.now(UTC)
    subscriptions = session.scalars(
        select(Subscription).where(Subscription.verified_at.is_not(None))
    ).all()
    sent = 0
    for subscription in subscriptions:
        since = subscription.last_notified_at or (now - ACTIVE_WINDOW)
        jobs = _new_matching_jobs(session, subscription, since)
        if not jobs:
            continue
        try:
            body = format_digest(
                jobs,
                max_chars=CHANNEL_MAX_CHARS.get(subscription.channel, 2000),
                more_url=jobs_url,
            )
            deliver(subscription, body)
        except ChannelError:
            logger.exception("digest delivery failed for %s", subscription.id)
            continue
        subscription.last_notified_at = now
        session.flush()
        sent += 1
    return sent


_DIGEST_SUBJECT = "Novas vagas tech em Portugal"


def build_deliver(
    settings: Settings, http_client: httpx.Client, *, smtp_factory: SmtpFactory = SMTP
) -> Deliver:
    """Return a delivery callback routing a digest to a subscription's channel."""

    def deliver(subscription: Subscription, body: str) -> None:
        if not subscription.target:
            raise ChannelError(f"Subscription {subscription.id} has no target")
        if subscription.channel == "email":
            send_email(
                settings,
                subscription.target,
                _DIGEST_SUBJECT,
                body,
                smtp_factory=smtp_factory,
            )
        elif subscription.channel == "discord":
            send_discord(subscription.target, body, client=http_client)
        elif subscription.channel == "telegram":
            send_telegram(
                settings.telegram_bot_token,
                subscription.target,
                body,
                client=http_client,
            )
        else:
            raise ChannelError(f"Unknown channel: {subscription.channel}")

    return deliver


def alert_admin(settings: Settings, http_client: httpx.Client, message: str) -> None:
    """Send an operational alert to the admin Discord webhook, if configured."""
    if not settings.admin_discord_webhook:
        logger.warning("admin alert (no webhook configured): %s", message)
        return
    try:
        send_discord(settings.admin_discord_webhook, message, client=http_client)
    except ChannelError:
        logger.exception("failed to deliver admin alert")
