from datetime import UTC, datetime, timedelta

from ptjobs.models import Job, Subscription
from ptjobs.notify import run_digest


def _job(session, **over) -> Job:
    now = datetime.now(UTC)
    base = {
        "source": "itjobs",
        "external_id": "1",
        "url": "https://x/1",
        "title": "Python Developer",
        "company": "Feedzai",
        "city": "lisboa",
        "work_mode": "remote",
        "tags": ["python"],
        "salary_max": 50000,
        "first_seen_at": now,
        "last_seen_at": now,
        "fingerprint": "f" * 40,
    }
    base.update(over)
    job = Job(**base)
    session.add(job)
    session.flush()
    return job


def _sub(session, **over) -> Subscription:
    base = {
        "channel": "discord",
        "target": "https://discord.com/api/webhooks/1/a",
        "filters": {},
        "manage_token_hash": "h",
        "verified_at": datetime.now(UTC),
    }
    base.update(over)
    sub = Subscription(**base)
    session.add(sub)
    session.flush()
    return sub


def test_digest_delivers_new_matching_jobs(db_session) -> None:
    _job(db_session, tags=["python"])
    sub = _sub(db_session, filters={"tags": ["python"]})
    delivered: list[tuple] = []

    sent = run_digest(
        db_session, deliver=lambda s, body: delivered.append((s.id, body))
    )

    assert sent == 1
    assert len(delivered) == 1
    assert "Python Developer" in delivered[0][1]
    db_session.refresh(sub)
    assert sub.last_notified_at is not None


def test_digest_skips_unverified(db_session) -> None:
    _job(db_session)
    _sub(db_session, verified_at=None)
    sent = run_digest(db_session, deliver=lambda s, body: None)
    assert sent == 0


def test_digest_respects_watermark(db_session) -> None:
    now = datetime.now(UTC)
    _job(db_session, first_seen_at=now - timedelta(days=2), last_seen_at=now)
    _sub(db_session, last_notified_at=now - timedelta(days=1))
    sent = run_digest(db_session, deliver=lambda s, body: None)
    assert sent == 0


def test_digest_filters_non_matching(db_session) -> None:
    _job(db_session, tags=["java"])
    _sub(db_session, filters={"tags": ["python"]})
    sent = run_digest(db_session, deliver=lambda s, body: None)
    assert sent == 0


def test_channel_failure_keeps_watermark(db_session) -> None:
    from ptjobs.errors import ChannelError

    _job(db_session)
    sub = _sub(db_session, last_notified_at=None)

    def boom(s, body):
        raise ChannelError("discord down")

    sent = run_digest(db_session, deliver=boom)
    assert sent == 0
    db_session.refresh(sub)
    assert sub.last_notified_at is None
