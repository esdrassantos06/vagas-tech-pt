"""FastAPI application exposing jobs, sources, and health."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from starlette.exceptions import HTTPException as StarletteHTTPException

from ptjobs import notify
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
        notify.send_email(
            settings,
            target,
            "Confirma a tua subscrição de alertas",
            f"Confirma a tua subscrição de alertas de vagas tech neste link: {link}",
        )
    elif channel == "discord" and target:
        notify.send_discord(
            target,
            f"Confirma a tua subscrição de alertas de vagas tech: {link}",
            client=_http_client,
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

    @app.get("/subscriptions/verify", tags=["subscriptions"])
    def verify_subscription(
        token: str = Query(max_length=128), db: Session = Depends(get_db)
    ) -> dict[str, str]:
        """Confirm an email or Discord alert with the token from its link."""
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.verify_token_hash == notify.hash_token(token)
            )
        )
        if subscription is None:
            raise NotFoundError("Subscription", "token")
        if subscription.channel == "telegram":
            raise ValidationError("Telegram subscriptions verify via the bot")
        subscription.verified_at = func.now()
        subscription.verify_token_hash = None
        db.flush()
        return {"status": "verified"}

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
        if text.startswith("/start "):
            token = text.split(" ", 1)[1].strip()
            chat_id = str((message.get("chat") or {}).get("id"))
            subscription = db.scalar(
                select(Subscription).where(
                    Subscription.verify_token_hash == notify.hash_token(token)
                )
            )
            if subscription is not None:
                subscription.target = chat_id
                subscription.verified_at = func.now()
                subscription.verify_token_hash = None
                db.flush()
            _reply_telegram(
                settings,
                chat_id,
                _TELEGRAM_CONFIRMED if subscription else _TELEGRAM_INVALID,
            )
        return {"ok": True}

    return app


_TELEGRAM_CONFIRMED = (
    "Subscrição confirmada. Vais receber aqui as novas vagas tech que batem "
    "com os teus filtros."
)
_TELEGRAM_INVALID = "Link inválido ou já utilizado. Cria uma nova subscrição."


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
