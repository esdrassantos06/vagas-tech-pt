"""Scrape pipeline: fetch raw jobs, normalize, upsert, record the run."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ptjobs import normalize
from ptjobs.errors import SourceError
from ptjobs.models import Job, ScrapeRun
from ptjobs.sources.base import RawJob

logger = logging.getLogger(__name__)

TECH_ONLY_SOURCES = frozenset({"itjobs", "linkedin", "net_empregos"})
_MUTABLE_COLUMNS = (
    "url",
    "title",
    "company",
    "location_raw",
    "city",
    "work_mode",
    "seniority",
    "salary_min",
    "salary_max",
    "tags",
    "description",
    "published_at",
    "fingerprint",
)
Fetcher = Callable[[], Iterable[RawJob]]
Describe = Callable[[RawJob], str | None]
Alert = Callable[[str], None]


def _to_row(source: str, raw: RawJob) -> dict[str, Any]:
    text = " ".join(filter(None, (raw.title, raw.description)))
    salary_min, salary_max = normalize.parse_salary(raw.salary_raw or raw.description)
    city = normalize.normalize_city(raw.location_raw)
    return {
        "source": source,
        "external_id": raw.external_id,
        "url": raw.url,
        "title": raw.title,
        "company": raw.company,
        "location_raw": raw.location_raw,
        "city": city,
        "work_mode": normalize.normalize_work_mode(text),
        "seniority": normalize.normalize_seniority(raw.title),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "tags": normalize.normalize_tags(text),
        "description": raw.description,
        "published_at": raw.published_at,
        "fingerprint": normalize.fingerprint(raw.title, raw.company, city),
    }


def _is_tech(source: str, tags: list[str]) -> bool:
    return source in TECH_ONLY_SOURCES or bool(tags)


def run_source(
    source: str,
    fetcher: Fetcher,
    *,
    session: Session,
    alert: Alert | None = None,
    describe: Describe | None = None,
) -> ScrapeRun:
    """Fetch a source, upsert its tech jobs, and persist a ScrapeRun record.

    The fetch and upsert run inside a savepoint so a database error rolls back
    only the job writes, leaving the ScrapeRun to be recorded. Rows are
    deduplicated on external_id (last wins) before the upsert, which refreshes
    last_seen_at and mutable fields on conflict. Any failure marks the run
    failed, records the message, and fires the admin alert (itself guarded).

    With describe, each job not yet stored gets its full description appended
    to the listing one; stored jobs keep the description they already have.
    """
    run = ScrapeRun(source=source)
    try:
        with session.begin_nested():
            raws = list(fetcher())
            if describe is not None:
                raws = _describe_new(session, source, raws, describe)
            rows = _dedup(
                row
                for row in (_to_row(source, raw) for raw in raws)
                if _is_tech(source, row["tags"])
            )
            new_count = _upsert(session, source, rows)
        run.jobs_found = len(raws)
        run.jobs_new = new_count
        run.status = "empty" if not raws else "ok"
    except Exception as exc:
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        logger.exception("scrape run failed for %s", source)
        if alert is not None:
            try:
                alert(f"Scrape failed for {source}: {run.error}")
            except Exception:
                logger.exception("admin alert failed for %s", source)
    run.finished_at = func.clock_timestamp()
    session.add(run)
    session.flush()
    return run


def _describe_new(
    session: Session, source: str, raws: list[RawJob], describe: Describe
) -> list[RawJob]:
    stored = dict(
        session.execute(
            select(Job.external_id, Job.description).where(
                Job.source == source,
                Job.external_id.in_([raw.external_id for raw in raws]),
            )
        ).all()
    )
    described: list[RawJob] = []
    blocked = False
    for raw in raws:
        if raw.external_id in stored:
            description = stored[raw.external_id] or raw.description
        elif blocked:
            description = raw.description
        else:
            try:
                detail = describe(raw)
            except SourceError:
                logger.warning("%s: job pages unavailable, skipping the rest", source)
                blocked = True
                detail = None
            description = " ".join(filter(None, (raw.description, detail))) or None
        described.append(replace(raw, description=description))
    return described


def _dedup(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({row["external_id"]: row for row in rows}.values())


def _upsert(session: Session, source: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    external_ids = [row["external_id"] for row in rows]
    existing = set(
        session.scalars(
            select(Job.external_id).where(
                Job.source == source, Job.external_id.in_(external_ids)
            )
        ).all()
    )
    stmt = insert(Job).values(rows)
    update_cols: dict[str, Any] = {c: stmt.excluded[c] for c in _MUTABLE_COLUMNS}
    update_cols["last_seen_at"] = func.clock_timestamp()
    # Single multi-row INSERT; chunk if a fetch ever nears 4681 rows
    # (psycopg 65535 bind-param limit). Current sources stay well under that.
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_jobs_source_external_id", set_=update_cols
        )
    )
    session.flush()
    return len(rows) - len(existing)
