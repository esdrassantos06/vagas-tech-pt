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
from ptjobs.digest import (
    Digest,
    render_discord,
    render_email_html,
    render_telegram,
    render_text,
)
from ptjobs.errors import ChannelError
from ptjobs.models import Job, Subscription

_DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com"})
_TELEGRAM_API = "https://api.telegram.org"
_DISCORD_API = "https://discord.com/api/v10"


def new_token() -> str:
    """Return a cryptographically random URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a token, for storage and comparison."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def job_matches(job: Job, filters: dict[str, Any]) -> bool:
    """Return True if a job satisfies every non-empty filter dimension.

    Dimensions are ANDed; values within a dimension are ORed. Recognized keys:
    keywords, cities, work_modes, tags, seniorities (lists), and min_salary
    (int; 0 means no filter). An empty or missing filter set matches every job.
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

    seniorities = filters.get("seniorities")
    if seniorities and job.seniority not in seniorities:
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


def send_discord(
    webhook_url: str, message: str | dict[str, Any], *, client: httpx.Client
) -> None:
    """Post text or a full webhook payload to a validated Discord webhook.

    Raises ChannelError on failure.
    """
    if not validate_discord_webhook(webhook_url):
        raise ChannelError(f"Invalid Discord webhook URL: {webhook_url}")
    payload = (
        message
        if isinstance(message, dict)
        else {"content": message, "allowed_mentions": {"parse": []}}
    )
    try:
        response = client.post(webhook_url, json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ChannelError(
            f"Discord delivery failed: HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError:
        raise ChannelError("Discord delivery failed") from None


def send_discord_bot(
    bot_token: str,
    channel_id: str,
    payload: dict[str, Any],
    *,
    client: httpx.Client,
) -> None:
    """Post a message to a Discord channel as the bot; ChannelError on failure."""
    if not channel_id.isdigit():
        raise ChannelError("Invalid Discord channel id")
    try:
        response = client.post(
            f"{_DISCORD_API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {bot_token}"},
            json=payload,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ChannelError(
            f"Discord delivery failed: HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError:
        raise ChannelError("Discord delivery failed") from None


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    client: httpx.Client,
    html: bool = False,
) -> None:
    """Send a Telegram message via the Bot API, raising ChannelError on failure.

    With html=True the text is parsed as Telegram HTML and link previews are off.
    """
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if html:
        payload |= {"parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    try:
        response = client.post(url, json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ChannelError(
            f"Telegram delivery failed: HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError:
        raise ChannelError("Telegram delivery failed") from None


logger = logging.getLogger(__name__)
# Bot tokens and webhook secrets live in request URLs; never let httpx log them.
logging.getLogger("httpx").setLevel(logging.WARNING)
ACTIVE_WINDOW = timedelta(days=7)
Deliver = Callable[[Subscription, Digest], None]
SmtpFactory = Callable[[str, int], SMTP]


def send_email(
    settings: Settings,
    to: str,
    subject: str,
    body: str,
    *,
    html: str | None = None,
    unsubscribe_url: str | None = None,
    smtp_factory: SmtpFactory = SMTP,
) -> None:
    """Send an email over SMTP, raising ChannelError on failure.

    body is the plain-text part; html adds an HTML alternative. unsubscribe_url
    adds RFC 8058 one-click List-Unsubscribe headers.
    """
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    if unsubscribe_url:
        message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    try:
        with smtp_factory(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except OSError, SMTPException, ValueError:
        raise ChannelError("Email delivery failed") from None


CHANNEL_MAX_JOBS = {"discord": 10, "telegram": 10, "email": 30}


def unsubscribe_url(base_url: str, token: str) -> str:
    """Public one-click unsubscribe link for a subscription token."""
    return f"{base_url}/subscriptions/unsubscribe/{token}"


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
    base_url: str = "",
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
        if not subscription.unsubscribe_token:
            subscription.unsubscribe_token = new_token()
        limit = CHANNEL_MAX_JOBS.get(subscription.channel, 10)
        digest = Digest(
            jobs=jobs[:limit],
            remaining=max(len(jobs) - limit, 0),
            jobs_url=jobs_url,
            unsubscribe_url=unsubscribe_url(base_url, subscription.unsubscribe_token),
        )
        try:
            deliver(subscription, digest)
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
    """Return a delivery callback rendering a digest for a subscription's channel."""

    def deliver(subscription: Subscription, digest: Digest) -> None:
        if subscription.channel == "discord" and subscription.discord_channel_id:
            send_discord_bot(
                settings.discord_bot_token,
                subscription.discord_channel_id,
                render_discord(digest),
                client=http_client,
            )
            return
        if not subscription.target:
            raise ChannelError(f"Subscription {subscription.id} has no target")
        if subscription.channel == "email":
            send_email(
                settings,
                subscription.target,
                _DIGEST_SUBJECT,
                render_text(digest),
                html=render_email_html(digest),
                unsubscribe_url=digest.unsubscribe_url,
                smtp_factory=smtp_factory,
            )
        elif subscription.channel == "discord":
            send_discord(
                subscription.target, render_discord(digest), client=http_client
            )
        elif subscription.channel == "telegram":
            send_telegram(
                settings.telegram_bot_token,
                subscription.target,
                render_telegram(digest),
                client=http_client,
                html=True,
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
