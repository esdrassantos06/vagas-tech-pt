from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ptjobs.api import create_app, get_db
from ptjobs.models import Job, ScrapeRun


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app, raise_server_exceptions=False)


def _job(session, **over) -> Job:
    now = datetime.now(UTC)
    defaults = {
        "source": "itjobs",
        "external_id": "1",
        "url": "https://www.itjobs.pt/oferta/1/x",
        "title": "Senior Python Developer",
        "company": "Feedzai",
        "city": "lisboa",
        "work_mode": "remote",
        "salary_min": 40000,
        "salary_max": 50000,
        "tags": ["python", "react"],
        "first_seen_at": now,
        "last_seen_at": now,
        "fingerprint": "f" * 40,
    }
    defaults.update(over)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def test_health_ok(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_jobs_lists_active(client, db_session) -> None:
    _job(db_session, external_id="1")
    resp = client.get("/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Senior Python Developer"
    assert body["items"][0]["tags"] == ["python", "react"]


def test_jobs_hides_stale(client, db_session) -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    _job(db_session, external_id="old", first_seen_at=old, last_seen_at=old)
    resp = client.get("/jobs")
    assert resp.json()["total"] == 0


def test_jobs_dedup_by_fingerprint(client, db_session) -> None:
    _job(db_session, source="itjobs", external_id="1", fingerprint="dup" + "0" * 37)
    _job(db_session, source="linkedin", external_id="2", fingerprint="dup" + "0" * 37)
    resp = client.get("/jobs")
    assert resp.json()["total"] == 1


def test_jobs_filters(client, db_session) -> None:
    _job(
        db_session,
        external_id="1",
        city="lisboa",
        work_mode="remote",
        tags=["python"],
        salary_max=60000,
        company="Feedzai",
        fingerprint="a" * 40,
    )
    _job(
        db_session,
        external_id="2",
        city="porto",
        work_mode="onsite",
        tags=["java"],
        salary_max=30000,
        company="Talkdesk",
        fingerprint="b" * 40,
    )

    assert client.get("/jobs?city=porto").json()["total"] == 1
    assert client.get("/jobs?work_mode=remote").json()["total"] == 1
    assert client.get("/jobs?tag=python").json()["total"] == 1
    assert client.get("/jobs?min_salary=50000").json()["total"] == 1
    assert client.get("/jobs?q=talkdesk").json()["total"] == 1
    assert client.get("/jobs?source=itjobs").json()["total"] == 2


def test_jobs_pagination(client, db_session) -> None:
    for i in range(5):
        _job(db_session, external_id=str(i), fingerprint=str(i) * 40)
    page1 = client.get("/jobs?page=1&size=2").json()
    assert page1["total"] == 5 and len(page1["items"]) == 2
    assert page1["page"] == 1 and page1["size"] == 2


def test_job_detail_with_also_on(client, db_session) -> None:
    a = _job(db_session, source="itjobs", external_id="1", fingerprint="z" * 40)
    _job(
        db_session,
        source="linkedin",
        external_id="2",
        url="https://linkedin.com/jobs/view/2",
        fingerprint="z" * 40,
    )
    resp = client.get(f"/jobs/{a.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == a.id
    assert len(body["also_on"]) == 1
    assert body["also_on"][0]["source"] == "linkedin"


def test_job_detail_404_envelope(client) -> None:
    resp = client.get("/jobs/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_jobs_validation_error_envelope(client) -> None:
    resp = client.get("/jobs?size=9999")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_sources_last_run(client, db_session) -> None:
    db_session.add(ScrapeRun(source="itjobs", status="ok", jobs_found=10, jobs_new=3))
    db_session.flush()
    resp = client.get("/sources")
    assert resp.status_code == 200
    rows = resp.json()
    itjobs = next(r for r in rows if r["source"] == "itjobs")
    assert itjobs["status"] == "ok"
    assert itjobs["jobs_found"] == 10


def test_unknown_route_returns_envelope(client) -> None:
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_overflow_params_return_422_not_500(client) -> None:
    assert client.get("/jobs?page=99999999999999999999").status_code == 422
    assert client.get("/jobs?min_salary=99999999999999999999").status_code == 422
    assert client.get("/jobs/99999999999999999999").status_code == 422


def test_nul_byte_in_query_is_handled(client, db_session) -> None:
    _job(db_session, external_id="1", fingerprint="n" * 40)
    resp = client.get("/jobs?q=%00")
    assert resp.status_code == 200


def test_q_wildcards_are_escaped(client, db_session) -> None:
    _job(db_session, external_id="1", title="Python Dev", fingerprint="w" * 40)
    assert client.get("/jobs?q=%25").json()["total"] == 0
    assert client.get("/jobs?q=Python").json()["total"] == 1


def test_health_returns_503_when_db_down(db_session) -> None:
    from sqlalchemy.exc import OperationalError

    class BrokenSession:
        def execute(self, *a, **k):
            raise OperationalError("SELECT 1", {}, Exception("down"))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    broken = TestClient(app, raise_server_exceptions=False)
    resp = broken.get("/health")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "DB_UNAVAILABLE"


def test_sources_dedups_runs_with_same_started_at(client, db_session) -> None:
    ts = datetime.now(UTC)
    db_session.add(ScrapeRun(source="itjobs", status="ok", started_at=ts, jobs_found=1))
    db_session.add(ScrapeRun(source="itjobs", status="ok", started_at=ts, jobs_found=2))
    db_session.flush()
    rows = client.get("/sources").json()
    assert len([r for r in rows if r["source"] == "itjobs"]) == 1


def test_pagination_is_stable_with_tied_timestamps(client, db_session) -> None:
    ts = datetime.now(UTC)
    for i in range(3):
        _job(
            db_session,
            external_id=str(i),
            fingerprint=str(i) * 40,
            first_seen_at=ts,
            last_seen_at=ts,
        )
    ids_p1 = [j["id"] for j in client.get("/jobs?page=1&size=2").json()["items"]]
    ids_p2 = [j["id"] for j in client.get("/jobs?page=2&size=2").json()["items"]]
    assert len(set(ids_p1 + ids_p2)) == 3


def test_since_cannot_resurrect_stale_jobs(client, db_session) -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    _job(
        db_session,
        external_id="old",
        first_seen_at=old,
        last_seen_at=old,
        fingerprint="s" * 40,
    )
    since = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    assert client.get("/jobs", params={"since": since}).json()["total"] == 0


def test_subscription_example_has_no_min_salary() -> None:
    schema = create_app().openapi()["components"]["schemas"]["SubscriptionCreate"]
    example = schema["examples"][0]
    assert "min_salary" not in example["filters"]
