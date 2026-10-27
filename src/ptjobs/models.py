"""SQLAlchemy ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

INT32_MAX = 2**31 - 1
SOURCES = ("linkedin", "itjobs", "net_empregos")
WORK_MODES = ("onsite", "hybrid", "remote", "unknown")
RUN_STATUSES = ("ok", "empty", "failed")
CHANNELS = ("email", "discord", "telegram")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Job(Base):
    """A normalized job posting, unique per (source, external_id)."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
        CheckConstraint(
            "source IN ('linkedin', 'itjobs', 'net_empregos')",
            name="ck_jobs_source",
        ),
        CheckConstraint(
            "work_mode IN ('onsite', 'hybrid', 'remote', 'unknown')",
            name="ck_jobs_work_mode",
        ),
        Index("ix_jobs_fingerprint", "fingerprint"),
        Index("ix_jobs_first_seen_at", "first_seen_at"),
        Index("ix_jobs_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str] = mapped_column(Text, nullable=False)
    location_raw: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    work_mode: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)


class ScrapeRun(Base):
    """One execution of a source scraper, for monitoring and drift detection."""

    __tablename__ = "scrape_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'empty', 'failed')", name="ck_scrape_runs_status"
        ),
        Index("ix_scrape_runs_source_started", "source", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    jobs_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Subscription(Base):
    """A saved alert: filters plus a delivery channel, verified via opt-in."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email', 'discord', 'telegram')",
            name="ck_subscriptions_channel",
        ),
        Index("ix_subscriptions_verified_at", "verified_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa_text("uuidv7()")
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    filters: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    manage_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    verify_token_hash: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
