import httpx
import pytest

from ptjobs.errors import ChannelError
from ptjobs.models import Job
from ptjobs.notify import (
    hash_token,
    job_matches,
    new_token,
    send_discord,
    send_telegram,
    validate_discord_webhook,
)


def _job(**over) -> Job:
    base = {
        "title": "Senior Python Developer",
        "company": "Feedzai",
        "city": "lisboa",
        "work_mode": "remote",
        "salary_max": 50000,
        "tags": ["python", "react"],
        "description": "We use Django and PostgreSQL.",
        "url": "https://x/1",
    }
    base.update(over)
    return Job(**base)


class TestMatching:
    def test_empty_filters_match_everything(self) -> None:
        assert job_matches(_job(), {}) is True

    def test_keyword_matches_title_or_description(self) -> None:
        assert job_matches(_job(), {"keywords": ["django"]}) is True
        assert job_matches(_job(), {"keywords": ["rust"]}) is False

    def test_city_filter(self) -> None:
        assert job_matches(_job(city="lisboa"), {"cities": ["lisboa", "porto"]}) is True
        assert job_matches(_job(city="braga"), {"cities": ["lisboa"]}) is False

    def test_work_mode_filter(self) -> None:
        assert job_matches(_job(work_mode="remote"), {"work_modes": ["remote"]}) is True
        assert (
            job_matches(_job(work_mode="onsite"), {"work_modes": ["remote"]}) is False
        )

    def test_tag_overlap(self) -> None:
        assert job_matches(_job(tags=["python"]), {"tags": ["python", "go"]}) is True
        assert job_matches(_job(tags=["java"]), {"tags": ["python"]}) is False

    def test_min_salary(self) -> None:
        assert job_matches(_job(salary_max=60000), {"min_salary": 50000}) is True
        assert job_matches(_job(salary_max=30000), {"min_salary": 50000}) is False
        assert job_matches(_job(salary_max=None), {"min_salary": 50000}) is False

    def test_all_dimensions_must_pass(self) -> None:
        f = {"cities": ["lisboa"], "tags": ["python"]}
        assert job_matches(_job(city="lisboa", tags=["python"]), f) is True
        assert job_matches(_job(city="porto", tags=["python"]), f) is False


class TestTokens:
    def test_new_token_is_urlsafe_and_unique(self) -> None:
        a, b = new_token(), new_token()
        assert a != b
        assert len(a) >= 32

    def test_hash_is_stable_and_hex(self) -> None:
        token = new_token()
        assert hash_token(token) == hash_token(token)
        assert len(hash_token(token)) == 64


class TestDiscordValidation:
    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://discord.com/api/webhooks/123/abc", True),
            ("https://discordapp.com/api/webhooks/123/abc", True),
            ("http://discord.com/api/webhooks/123/abc", False),
            ("https://evil.com/api/webhooks/123/abc", False),
            ("https://discord.com.evil.com/api/webhooks/1/a", False),
            ("https://discord.com/not-a-webhook", False),
            ("ftp://discord.com/api/webhooks/1/a", False),
            ("https://discord.com/api/webhooks/1/a?x=1", False),
            ("https://discord.com/api/webhooks/1/a#frag", False),
        ],
    )
    def test_validation(self, url: str, ok: bool) -> None:
        assert validate_discord_webhook(url) is ok


class TestSenders:
    def test_send_discord_posts_content(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.content
            return httpx.Response(204)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        send_discord("https://discord.com/api/webhooks/1/a", "hi", client=client)
        assert captured["url"] == "https://discord.com/api/webhooks/1/a"
        assert b"hi" in captured["body"]
        assert b"allowed_mentions" in captured["body"]

    def test_send_discord_rejects_bad_url(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(204))
        )
        with pytest.raises(ChannelError):
            send_discord("https://evil.com/x", "hi", client=client)

    def test_send_discord_raises_on_error_status(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        )
        with pytest.raises(ChannelError):
            send_discord("https://discord.com/api/webhooks/1/a", "hi", client=client)

    def test_send_telegram_posts_message(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        send_telegram("BOTTOKEN", "12345", "hello", client=client)
        assert "botBOTTOKEN/sendMessage" in captured["url"]

    def test_send_telegram_raises_on_error(self) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(400))
        )
        with pytest.raises(ChannelError):
            send_telegram("T", "1", "hi", client=client)


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.sent: list[tuple[str, str]] = []
        self.started_tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *a) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.user = user

    def send_message(self, message) -> None:
        self.sent.append((message["To"], message["Subject"]))


def test_send_email_uses_smtp(monkeypatch) -> None:
    from ptjobs.config import Settings
    from ptjobs.notify import send_email

    FakeSMTP.instances.clear()
    settings = Settings(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_user="u",
        smtp_password="p",
        smtp_from="alerts@test",
    )
    send_email(settings, "dev@x.com", "New jobs", "body", smtp_factory=FakeSMTP)
    assert len(FakeSMTP.instances) == 1
    assert FakeSMTP.instances[0].sent == [("dev@x.com", "New jobs")]
    assert FakeSMTP.instances[0].started_tls is True


class TestBuildDeliver:
    def _sub(self, channel: str, target: str):
        from ptjobs.models import Subscription

        return Subscription(
            channel=channel, target=target, filters={}, manage_token_hash="h"
        )

    def test_dispatches_discord(self) -> None:
        from ptjobs.config import Settings
        from ptjobs.notify import build_deliver

        seen = {}
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: seen.update(url=str(r.url)) or httpx.Response(204)
            )
        )
        deliver = build_deliver(Settings(), client)
        deliver(self._sub("discord", "https://discord.com/api/webhooks/1/a"), "body")
        assert "discord.com" in seen["url"]

    def test_dispatches_telegram(self) -> None:
        from ptjobs.config import Settings
        from ptjobs.notify import build_deliver

        seen = {}
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: (
                    seen.update(url=str(r.url))
                    or httpx.Response(200, json={"ok": True})
                )
            )
        )
        deliver = build_deliver(Settings(telegram_bot_token="BT"), client)
        deliver(self._sub("telegram", "12345"), "body")
        assert "botBT/sendMessage" in seen["url"]

    def test_missing_target_raises(self) -> None:
        from ptjobs.config import Settings
        from ptjobs.errors import ChannelError
        from ptjobs.notify import build_deliver

        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(204))
        )
        deliver = build_deliver(Settings(), client)
        with pytest.raises(ChannelError):
            deliver(self._sub("discord", None), "body")


def test_min_salary_zero_means_no_filter() -> None:
    assert job_matches(_job(salary_max=None), {"min_salary": 0}) is True


class TestDigestLimits:
    def _jobs(self, n: int) -> list[Job]:
        return [
            _job(title=f"Senior Python Developer number {i}", url=f"https://x/{i}")
            for i in range(n)
        ]

    def test_fits_within_max_chars_and_reports_rest(self) -> None:
        from ptjobs.notify import format_digest

        jobs = self._jobs(100)
        body = format_digest(jobs, max_chars=500, more_url="https://site/jobs")
        shown = body.count("\n- ")
        assert len(body) <= 500
        assert 0 < shown < 100
        assert f"e mais {100 - shown} vaga(s): https://site/jobs" in body

    def test_no_footer_when_everything_fits(self) -> None:
        from ptjobs.notify import format_digest

        body = format_digest(
            self._jobs(2), max_chars=4000, more_url="https://site/jobs"
        )
        assert body.count("\n- ") == 2
        assert "e mais" not in body


def test_send_telegram_error_hides_bot_token() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(ChannelError) as exc:
        send_telegram("123:SECRETTOKEN", "1", "hi", client=client)
    assert "SECRETTOKEN" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


def test_failed_delivery_traceback_never_contains_bot_token() -> None:
    import traceback

    from ptjobs.config import Settings
    from ptjobs.models import Subscription
    from ptjobs.notify import build_deliver

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    deliver = build_deliver(Settings(telegram_bot_token="123:SECRETTOKEN"), client)
    sub = Subscription(
        channel="telegram", target="1", filters={}, manage_token_hash="h"
    )
    with pytest.raises(ChannelError) as exc:
        deliver(sub, "body")
    assert "SECRETTOKEN" not in "".join(traceback.format_exception(exc.value))


def test_discord_error_hides_webhook_url() -> None:
    import traceback

    secret = "SECRET" + "WEBHOOK"
    url = f"https://discord.com/api/webhooks/1/{secret}"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ChannelError) as exc:
        send_discord(url, "x", client=client)
    assert secret not in "".join(traceback.format_exception(exc.value))


def test_httpx_request_urls_are_not_logged_at_info() -> None:
    import logging

    import ptjobs.notify  # noqa: F401

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
