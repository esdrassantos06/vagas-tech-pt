"""FastAPI application exposing jobs, sources, and health."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from ptjobs import digest, discord_bot, notify, telegram_bot
from ptjobs.config import Settings, get_settings
from ptjobs.db import session_scope
from ptjobs.errors import (
    AppError,
    ChannelError,
    NotFoundError,
    RateLimitError,
    UnauthorizedError,
    ValidationError,
)
from ptjobs.models import INT32_MAX, SOURCES, Job, ScrapeRun, Subscription
from ptjobs.schemas import (
    ErrorResponse,
    JobAlsoOn,
    JobDetail,
    JobPage,
    JobSummary,
    RecoverRequest,
    Seniority,
    Source,
    SourceStatus,
    SubscriptionCreate,
    SubscriptionCreated,
    SubscriptionOut,
    SubscriptionUpdate,
    UnsubscribeRequest,
    WorkMode,
)

logger = logging.getLogger(__name__)

ACTIVE_WINDOW = timedelta(days=7)
INT64_MAX = 2**63 - 1
_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


_http_client = httpx.Client(timeout=10.0)
MAX_UNVERIFIED_PER_TARGET = 3
REPOSITORY_URL = "https://github.com/esdrassantos06/vagas-tech-pt"

SUMMARY = "Open source aggregator of tech jobs in Portugal."
API_GUIDE = """Jobs are scraped from LinkedIn, itjobs.pt and net-empregos,
normalized and deduplicated across sources.

## Get job alerts

1. `POST /subscriptions` with a channel and optional filters, for example
   `{"channel": "telegram", "filters": {"tags": ["python"]}}`.
2. Confirm it:
   - **Email**: click the link sent to your inbox.
   - **Discord**: click the link posted through your channel webhook.
   - **Telegram**: open the returned `deep_link` and press Start.
3. Keep the `manage_token`. Send it as `Authorization: Bearer <token>` to
   `/subscriptions/me` to read, update or delete the alert.

New matching jobs arrive as a digest every two hours.

Source code: https://github.com/esdrassantos06/vagas-tech-pt
"""
OPENAPI_TAGS = [
    {
        "name": "jobs",
        "description": "Tech jobs from LinkedIn, itjobs.pt and net-empregos.",
    },
    {
        "name": "subscriptions",
        "description": "Job alerts by email, Discord or Telegram, with opt-in.",
    },
    {"name": "meta", "description": "Service information and scraper health."},
]
SCALAR_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>vagas-tech-pt API</title>
  </head>
  <body>
    <div id="app"></div>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.76.0"></script>
    <script>Scalar.createApiReference("#app", { url: "/openapi.json" })</script>
  </body>
</html>
"""
UNVERIFIED_TTL = timedelta(hours=24)
RECOVER_INTERVAL = timedelta(hours=1)
INVALID_VERIFY_PAGE = (
    "Link já usado ou inválido",
    "Se já confirmaste, os alertas estão ativos. Caso contrário, cria uma nova "
    "subscrição.",
)
TELEGRAM_VERIFY_PAGE = (
    "Confirma no Telegram",
    "As subscrições de Telegram confirmam-se abrindo o link do bot.",
)
VERIFIED_PAGE = (
    "Subscrição confirmada",
    "Vais começar a receber as novas vagas tech que batem com os teus filtros.",
)
UNSUBSCRIBED_PAGE = (
    "Alertas cancelados",
    "Não vais receber mais alertas deste canal.",
)


class _PageError(Exception):
    """Abort an HTML route with a styled error page."""

    def __init__(self, status: int, title: str, message: str) -> None:
        super().__init__(title)
        self.status, self.title, self.message = status, title, message


# Chunked bodies without Content-Length are not counted; the proxy should cap those.
MAX_BODY_BYTES = 64 * 1024
SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

Verifier = "Callable[[str, str | None, str], None]"


def _default_verifier(channel: str, target: str | None, token: str) -> None:
    settings = get_settings()
    link = f"{settings.public_base_url}/subscriptions/verify?token={token}"
    if channel == "email" and target:
        text_body, html_body = digest.render_verification_email(link)
        notify.send_email(
            settings,
            target,
            "Confirma a tua subscrição de alertas",
            text_body,
            html=html_body,
        )
    elif channel == "discord" and target:
        notify.send_discord(
            target, digest.render_discord_verification(link), client=_http_client
        )


def get_verifier() -> Callable[[str, str | None, str], None]:
    """Return the verification-message sender (overridable in tests)."""
    return _default_verifier


def _subscription_from_token(db: Session, token: str) -> Subscription | None:
    return db.scalar(
        select(Subscription).where(
            Subscription.manage_token_hash == notify.hash_token(token)
        )
    )


def get_db() -> Iterator[Session]:
    """Yield a database session for the request lifetime."""
    with session_scope() as session:
        yield session


def _error(
    status: int, code: str, message: str, details: object = None
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(status_code=status, content=jsonable_encoder(body))


def _strip_nul(value: str | None) -> str | None:
    return value.replace("\x00", "") or None if value else value


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="vagas-tech-pt",
        version=version("ptjobs"),
        summary=SUMMARY,
        description=API_GUIDE,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/swagger",
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )

    @app.exception_handler(_PageError)
    async def _page_error(_: Request, exc: _PageError) -> HTMLResponse:
        return HTMLResponse(
            digest.render_page(exc.title, exc.message), status_code=exc.status
        )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error(
            422, "VALIDATION_ERROR", "Request validation failed", exc.errors()
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "An unexpected error occurred")

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add baseline security headers to every response."""
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.middleware("http")
    async def _limit_body_size(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject bodies whose declared Content-Length exceeds MAX_BODY_BYTES."""
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _error(413, "PAYLOAD_TOO_LARGE", "Request body too large")
        return await call_next(request)

    @app.get("/docs", include_in_schema=False)
    def scalar_docs() -> HTMLResponse:
        """Serve the Scalar API reference for the OpenAPI schema."""
        return HTMLResponse(SCALAR_HTML)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, object]:
        """Describe the service and link to its documentation."""
        return {
            "name": app.title,
            "description": app.summary,
            "version": app.version,
            "docs": "/docs",
            "swagger": "/swagger",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
            "health": "/health",
            "repository": REPOSITORY_URL,
            "sources": list(SOURCES),
            "endpoints": sorted(
                {
                    route.path
                    for route in app.routes
                    if isinstance(route, APIRoute) and route.path != "/"
                }
            ),
        }

    @app.get("/health", tags=["meta"])
    def health(db: Session = Depends(get_db)) -> JSONResponse:
        """Report service health; 503 DB_UNAVAILABLE if the database is down."""
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return _error(503, "DB_UNAVAILABLE", "Database is unavailable")
        return JSONResponse({"status": "ok"})

    @app.get("/jobs", response_model=JobPage, tags=["jobs"])
    def list_jobs(
        db: Session = Depends(get_db),
        q: str | None = Query(default=None, max_length=100),
        city: str | None = Query(default=None, max_length=100),
        work_mode: WorkMode | None = None,
        seniority: Seniority | None = None,
        tag: str | None = Query(default=None, max_length=50),
        min_salary: int | None = Query(default=None, ge=0, le=INT32_MAX),
        source: Source | None = None,
        since: datetime | None = None,
        page: int = Query(default=1, ge=1, le=10_000),
        size: int = Query(default=20, ge=1, le=100),
    ) -> JobPage:
        """Search active tech jobs (seen in the last 7 days), newest first.

        A posting found on several sources is returned once. Filters combine with
        AND, and `q` matches the title or company. Paginate with `page` and `size`."""
        window_start = datetime.now(UTC) - ACTIVE_WINDOW
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            window_start = max(window_start, since)
        conditions = [Job.last_seen_at >= window_start]
        if source:
            conditions.append(Job.source == source)
        if city := _strip_nul(city):
            conditions.append(Job.city == city)
        if work_mode:
            conditions.append(Job.work_mode == work_mode)
        if seniority:
            conditions.append(Job.seniority == seniority)
        if tag := _strip_nul(tag):
            conditions.append(Job.tags.contains([tag]))
        if min_salary is not None:
            conditions.append(Job.salary_max >= min_salary)
        if q := _strip_nul(q):
            conditions.append(
                or_(
                    Job.title.icontains(q, autoescape=True),
                    Job.company.icontains(q, autoescape=True),
                )
            )

        deduped = (
            select(Job)
            .where(*conditions)
            .ext(distinct_on(Job.fingerprint))
            .order_by(Job.fingerprint, Job.first_seen_at.desc(), Job.id.desc())
            .subquery()
        )
        job = aliased(Job, deduped)
        total = db.scalar(select(func.count()).select_from(deduped)) or 0
        rows = db.scalars(
            select(job)
            .order_by(job.first_seen_at.desc(), job.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        ).all()
        return JobPage(
            items=[JobSummary.model_validate(r) for r in rows],
            total=total,
            page=page,
            size=size,
        )

    @app.get("/jobs/{job_id}", response_model=JobDetail, tags=["jobs"])
    def get_job(
        job_id: int = Path(ge=1, le=INT64_MAX),
        db: Session = Depends(get_db),
    ) -> JobDetail:
        """Get one job, with `also_on` listing the same posting on other sources."""
        job = db.get(Job, job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        siblings = db.scalars(
            select(Job).where(Job.fingerprint == job.fingerprint, Job.id != job.id)
        ).all()
        detail = JobDetail.model_validate(job)
        detail.also_on = [JobAlsoOn.model_validate(s) for s in siblings]
        return detail

    @app.get("/sources", response_model=list[SourceStatus], tags=["meta"])
    def list_sources(db: Session = Depends(get_db)) -> list[SourceStatus]:
        """Latest scrape run per source (ok, empty or failed) to check scrapers."""
        rows = db.scalars(
            select(ScrapeRun)
            .ext(distinct_on(ScrapeRun.source))
            .order_by(
                ScrapeRun.source,
                ScrapeRun.started_at.desc(),
                ScrapeRun.id.desc(),
            )
        ).all()
        return sorted(
            (SourceStatus.model_validate(r) for r in rows), key=lambda r: r.source
        )

    @app.post("/subscriptions", status_code=202, tags=["subscriptions"])
    def create_subscription(
        body: SubscriptionCreate,
        db: Session = Depends(get_db),
        verifier: Callable[[str, str | None, str], None] = Depends(get_verifier),
    ) -> SubscriptionCreated:
        """Create a job alert and return its `manage_token` once.

        - **email**: `target` is your address; a confirmation link is emailed.
        - **discord**: `target` is a channel webhook URL; the link is posted there.
        - **telegram**: omit `target`, open the returned `deep_link` and press Start.

        Alerts are only sent after confirmation. Empty filters match every job."""
        target = None if body.channel == "telegram" else body.target
        if target is not None and body.channel == "email":
            target = target.lower()
        if target is not None:
            cutoff = datetime.now(UTC) - UNVERIFIED_TTL
            unverified = db.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.target == target,
                    Subscription.verified_at.is_(None),
                    Subscription.created_at >= cutoff,
                )
            )
            if (unverified or 0) >= MAX_UNVERIFIED_PER_TARGET:
                raise RateLimitError("Too many pending subscriptions for this target")

        manage_token = notify.new_token()
        verify_token = notify.new_token()
        subscription = Subscription(
            channel=body.channel,
            target=target,
            filters=body.filters.model_dump(exclude_defaults=True),
            manage_token_hash=notify.hash_token(manage_token),
            verify_token_hash=notify.hash_token(verify_token),
            unsubscribe_token=notify.new_token(),
        )
        db.add(subscription)
        db.flush()

        verifier(body.channel, target, verify_token)
        deep_link = None
        if body.channel == "telegram":
            username = get_settings().telegram_bot_username
            deep_link = f"https://t.me/{username}?start={verify_token}"
        return SubscriptionCreated(
            id=subscription.id, manage_token=manage_token, deep_link=deep_link
        )

    def _pending_verification(db: Session, token: str) -> Subscription:
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.verify_token_hash == notify.hash_token(token)
            )
        )
        if subscription is None:
            raise _PageError(404, *INVALID_VERIFY_PAGE)
        if subscription.channel == "telegram":
            raise _PageError(422, *TELEGRAM_VERIFY_PAGE)
        return subscription

    @app.get(
        "/subscriptions/verify",
        response_class=HTMLResponse,
        tags=["subscriptions"],
    )
    def verify_page(
        token: str = Query(max_length=128),
        db: Session = Depends(get_db),
    ) -> HTMLResponse:
        """Show the confirmation button for an email or Discord alert.

        Opening the link changes nothing, so link previews and mail scanners
        cannot confirm a subscription; the button POSTs to the same URL.
        """
        _pending_verification(db, token)
        return HTMLResponse(
            digest.render_page(
                "Confirma a tua subscrição",
                "Carrega em Confirmar para começares a receber as novas vagas tech.",
                confirm=True,
            )
        )

    @app.post(
        "/subscriptions/verify",
        response_class=HTMLResponse,
        tags=["subscriptions"],
    )
    def verify_subscription(
        token: str = Query(max_length=128), db: Session = Depends(get_db)
    ) -> HTMLResponse:
        """Confirm an email or Discord alert with the token from its link."""
        subscription = _pending_verification(db, token)
        subscription.verified_at = func.now()
        subscription.verify_token_hash = None
        db.flush()
        return HTMLResponse(digest.render_page(*VERIFIED_PAGE))

    @app.get(
        "/subscriptions/me", response_model=SubscriptionOut, tags=["subscriptions"]
    )
    def get_me(
        db: Session = Depends(get_db),
        authorization: str | None = Header(default=None),
    ) -> SubscriptionOut:
        """Read your alert. Send `Authorization: Bearer <manage_token>`."""
        return SubscriptionOut.model_validate(_require_subscription(db, authorization))

    @app.patch(
        "/subscriptions/me", response_model=SubscriptionOut, tags=["subscriptions"]
    )
    def update_me(
        body: SubscriptionUpdate,
        db: Session = Depends(get_db),
        authorization: str | None = Header(default=None),
    ) -> SubscriptionOut:
        """Replace your alert filters. The target is fixed; create a new alert."""
        subscription = _require_subscription(db, authorization)
        if body.filters is not None:
            subscription.filters = body.filters.model_dump(exclude_defaults=True)
        db.flush()
        return SubscriptionOut.model_validate(subscription)

    @app.delete("/subscriptions/me", status_code=204, tags=["subscriptions"])
    def delete_me(
        db: Session = Depends(get_db),
        authorization: str | None = Header(default=None),
    ) -> None:
        """Delete your alert. Send `Authorization: Bearer <manage_token>`."""
        db.delete(_require_subscription(db, authorization))
        db.flush()

    @app.post("/subscriptions/unsubscribe", tags=["subscriptions"])
    def unsubscribe(
        body: UnsubscribeRequest, db: Session = Depends(get_db)
    ) -> dict[str, str]:
        """Delete an alert using the `manage_token` in the request body."""
        subscription = _subscription_from_token(db, body.token)
        if subscription is None:
            raise NotFoundError("Subscription", "token")
        db.delete(subscription)
        db.flush()
        return {"status": "unsubscribed"}

    @app.get(
        "/subscriptions/unsubscribe/{token}",
        response_class=HTMLResponse,
        tags=["subscriptions"],
    )
    def unsubscribe_page(token: str = Path(max_length=128)) -> HTMLResponse:
        """Show the cancel button for the unsubscribe link sent in every digest."""
        return HTMLResponse(
            digest.render_page(
                "Cancelar alertas",
                "Carrega em Confirmar para deixares de receber estes alertas.",
                confirm=True,
            )
        )

    @app.post(
        "/subscriptions/unsubscribe/{token}",
        response_class=HTMLResponse,
        tags=["subscriptions"],
    )
    def unsubscribe_by_link(
        token: str = Path(max_length=128), db: Session = Depends(get_db)
    ) -> HTMLResponse:
        """Delete the alert behind an unsubscribe link (also RFC 8058 one-click).

        Always answers with the same page so tokens cannot be probed.
        """
        subscription = db.scalar(
            select(Subscription).where(Subscription.unsubscribe_token == token)
        )
        if subscription is not None:
            db.delete(subscription)
            db.flush()
        return HTMLResponse(digest.render_page(*UNSUBSCRIBED_PAGE))

    @app.post("/subscriptions/recover", status_code=202, tags=["subscriptions"])
    def recover_token(
        body: RecoverRequest,
        background: BackgroundTasks,
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, str]:
        """Email a new manage token to a verified email alert.

        The answer is the same whether or not the address is subscribed, the
        email is sent after the response, and each alert can recover once per
        hour. The old token stops working.
        """
        email = body.email.strip().lower()
        cutoff = datetime.now(UTC) - RECOVER_INTERVAL
        subscriptions = db.scalars(
            select(Subscription).where(
                Subscription.channel == "email",
                Subscription.target == email,
                Subscription.verified_at.is_not(None),
                or_(
                    Subscription.recovered_at.is_(None),
                    Subscription.recovered_at < cutoff,
                ),
            )
        ).all()
        for subscription in subscriptions:
            token = notify.new_token()
            subscription.manage_token_hash = notify.hash_token(token)
            subscription.recovered_at = func.now()
            if not subscription.unsubscribe_token:
                subscription.unsubscribe_token = notify.new_token()
            text_body, html_body = digest.render_recovery_email(
                token,
                f"{settings.public_base_url}/docs",
                notify.unsubscribe_url(
                    settings.public_base_url, subscription.unsubscribe_token
                ),
            )
            background.add_task(_send_quietly, settings, email, text_body, html_body)
        db.flush()
        return {"status": "accepted"}

    @app.post("/discord/interactions", tags=["subscriptions"])
    async def discord_interactions(
        request: Request,
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        """Receive Discord slash commands; only Discord can call it (Ed25519)."""
        body = await request.body()
        if not settings.discord_public_key or not discord_bot.verify(
            settings.discord_public_key,
            request.headers.get("x-signature-ed25519", ""),
            request.headers.get("x-signature-timestamp", ""),
            body,
        ):
            raise UnauthorizedError("Invalid request signature")
        try:
            interaction = json.loads(body)
        except ValueError:
            raise ValidationError("Body must be JSON") from None
        if not isinstance(interaction, dict):
            raise ValidationError("Body must be a JSON object")
        return await run_in_threadpool(
            discord_bot.handle,
            db,
            interaction,
            settings=settings,
            client=_http_client,
        )

    @app.post("/telegram/webhook", tags=["subscriptions"])
    def telegram_webhook(
        update: dict[str, Any],
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        """Receive Telegram bot updates; only Telegram can call it (secret header)."""
        secret = settings.telegram_webhook_secret
        if not secret or not hmac.compare_digest(
            x_telegram_bot_api_secret_token or "", secret
        ):
            raise UnauthorizedError("Invalid webhook secret")
        message = update.get("message") or {}
        text = str(message.get("text") or "")
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None and text.startswith("/"):
            reply = telegram_bot.handle(
                db, str(chat_id), text, base_url=settings.public_base_url
            )
            _reply_telegram(settings, str(chat_id), reply)
        return {"ok": True}

    return app


def _send_quietly(settings: Settings, to: str, text_body: str, html_body: str) -> None:
    try:
        notify.send_email(
            settings, to, "Novo token de gestão", text_body, html=html_body
        )
    except ChannelError:
        logger.exception("recovery email failed")


def _reply_telegram(settings: Settings, chat_id: str, text: str) -> None:
    """Best-effort bot reply; a failure must not make Telegram retry the update."""
    if not settings.telegram_bot_token:
        return
    try:
        notify.send_telegram(
            settings.telegram_bot_token, chat_id, text, client=_http_client
        )
    except ChannelError:
        logger.exception("telegram reply failed")


def _require_subscription(db: Session, authorization: str | None) -> Subscription:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Bearer manage token required")
    subscription = _subscription_from_token(db, authorization.removeprefix("Bearer "))
    if subscription is None:
        raise UnauthorizedError("Invalid manage token")
    return subscription


app = create_app()
