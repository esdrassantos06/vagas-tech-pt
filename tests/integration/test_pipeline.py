from sqlalchemy import func, select

from ptjobs.models import Job, ScrapeRun
from ptjobs.pipeline import run_source
from ptjobs.sources.base import RawJob


def _raw(
    ext_id: str, title: str = "Python Developer", company: str = "Feedzai"
) -> RawJob:
    return RawJob(
        source="itjobs",
        external_id=ext_id,
        url=f"https://www.itjobs.pt/oferta/{ext_id}/x",
        title=title,
        company=company,
        location_raw="Lisboa",
        description="We use Python and React. Regime remoto. 40k-50k EUR.",
    )


def test_run_source_inserts_and_normalizes(db_session) -> None:
    run = run_source("itjobs", lambda: [_raw("1")], session=db_session)

    jobs = db_session.scalars(select(Job)).all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "itjobs"
    assert job.external_id == "1"
    assert job.city == "lisboa"
    assert job.work_mode == "remote"
    assert "python" in job.tags and "react" in job.tags
    assert job.salary_min == 40000 and job.salary_max == 50000
    assert len(job.fingerprint) == 40
    assert run.status == "ok"
    assert run.jobs_found == 1 and run.jobs_new == 1


def test_run_source_is_idempotent(db_session) -> None:
    run_source("itjobs", lambda: [_raw("1")], session=db_session)
    first = db_session.scalars(select(Job)).one()
    first_seen = first.first_seen_at

    run2 = run_source(
        "itjobs", lambda: [_raw("1", title="Python Developer")], session=db_session
    )
    db_session.expire_all()
    jobs = db_session.scalars(select(Job)).all()
    assert len(jobs) == 1
    assert jobs[0].first_seen_at == first_seen
    assert jobs[0].last_seen_at > first_seen
    assert run2.jobs_new == 0


def test_run_source_empty_marks_empty(db_session) -> None:
    run = run_source("itjobs", lambda: [], session=db_session)
    assert run.status == "empty"
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0


def test_run_source_failure_marks_failed_and_alerts(db_session) -> None:
    alerts: list[str] = []

    def boom() -> list[RawJob]:
        raise RuntimeError("markup changed")

    run = run_source("itjobs", boom, session=db_session, alert=alerts.append)

    assert run.status == "failed"
    assert run.error is not None and "markup changed" in run.error
    assert len(alerts) == 1
    assert db_session.scalar(select(func.count()).select_from(ScrapeRun)) == 1


def test_net_empregos_it_feeds_keep_untagged_jobs(db_session) -> None:
    def fetch() -> list[RawJob]:
        return [
            RawJob(
                source="net_empregos",
                external_id="9",
                url="https://www.net-empregos.com/9/x/",
                title="Programador ABAP",
                company="Consultora",
                location_raw="Porto",
                description="Projetos SAP para clientes.",
            )
        ]

    run = run_source("net_empregos", fetch, session=db_session)
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    assert run.jobs_found == 1
    assert run.jobs_new == 1


def test_run_source_dedups_within_batch(db_session) -> None:
    run = run_source("itjobs", lambda: [_raw("1"), _raw("1")], session=db_session)
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    assert run.jobs_new == 1
    assert run.status == "ok"


def test_run_source_db_error_records_failed_run_and_alerts(db_session) -> None:
    alerts: list[str] = []

    def bad() -> list[RawJob]:
        return [
            RawJob(
                source="itjobs",
                external_id="1",
                url=None,  # type: ignore[arg-type]
                title="X",
                company="Y",
            )
        ]

    run = run_source("itjobs", bad, session=db_session, alert=alerts.append)

    assert run.status == "failed"
    assert len(alerts) == 1
    assert db_session.scalar(select(func.count()).select_from(ScrapeRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0


def test_run_source_detects_seniority(db_session) -> None:
    run_source(
        "itjobs",
        lambda: [_raw("1", title="Junior Python Developer")],
        session=db_session,
    )
    assert db_session.scalars(select(Job)).one().seniority == "junior"
