"""Per-channel digest rendering, unsubscribe links and new tag aliases."""

import json

import httpx

from ptjobs.digest import Digest, render_discord, render_email_html, render_telegram
from ptjobs.models import Job
from ptjobs.normalize import canonical_tag, normalize_tags
from ptjobs.notify import send_email, send_telegram

UNSUB = "https://v.example/subscriptions/unsubscribe/abc"
JOBS_URL = "https://v.example/jobs"


def _job(i: int = 1, **over) -> Job:
    base = {
        "title": f"Senior Python Developer {i}",
        "company": "Feedzai",
        "city": "lisboa",
        "work_mode": "remote",
        "salary_min": 40000,
        "salary_max": 50000,
        "tags": ["python"],
        "url": f"https://x/jobs/{i}",
    }
    base.update(over)
    return Job(**base)


def _digest(jobs: list[Job], remaining: int = 0) -> Digest:
    return Digest(
        jobs=jobs, remaining=remaining, jobs_url=JOBS_URL, unsubscribe_url=UNSUB
    )


def test_telegram_escapes_and_has_footer() -> None:
    text = render_telegram(_digest([_job(title="<b>Dev</b> & Co")], remaining=3))
    assert "&lt;b&gt;Dev&lt;/b&gt; &amp; Co" in text
    assert '<a href="https://x/jobs/1">' in text
    assert "/parar" in text and "/filtros" in text
    assert "3" in text and JOBS_URL in text


def test_email_html_escapes_and_links_unsubscribe() -> None:
    html = render_email_html(_digest([_job(company="<script>x</script>")]))
    assert "<script>" not in html
    assert UNSUB in html and JOBS_URL in html
    assert "Remoto" in html and "40" in html


def test_discord_uses_embeds_and_unsubscribe_link() -> None:
    payload = render_discord(_digest([_job(i) for i in range(10)], remaining=5))
    assert len(payload["embeds"]) == 10
    assert payload["embeds"][0]["url"] == "https://x/jobs/0"
    assert UNSUB in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_send_email_sends_html_and_list_unsubscribe() -> None:
    from ptjobs.config import Settings

    captured = {}

    class FakeSMTP:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            captured["message"] = message

    send_email(
        Settings(smtp_host="h"),
        "a@b.com",
        "Assunto",
        "texto",
        html="<p>ola</p>",
        unsubscribe_url=UNSUB,
        smtp_factory=FakeSMTP,
    )
    message = captured["message"]
    assert message["List-Unsubscribe"] == f"<{UNSUB}>"
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message.get_body(preferencelist=("html",)) is not None


def test_send_telegram_uses_html_parse_mode() -> None:
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_telegram("T", "1", "<b>x</b>", client=client, html=True)
    assert sent["parse_mode"] == "HTML"
    assert sent["link_preview_options"] == {"is_disabled": True}


def test_new_tag_aliases() -> None:
    assert {"nextjs", "nestjs"} <= set(normalize_tags("Next.js and NestJS"))
    assert {"react", "react-native"} <= set(normalize_tags("React Native developer"))
    assert "tailwind" in normalize_tags("TailwindCSS")
    assert canonical_tag("Next") == "nextjs"
    assert canonical_tag("node.js") == "node"


def test_telegram_stays_under_limit_and_counts_overflow() -> None:
    long_url = "https://www.linkedin.com/jobs/view/" + "x" * 400
    jobs = [_job(i, url=long_url + str(i), title="T" * 200) for i in range(10)]
    digest = _digest(jobs, remaining=5)
    text = render_telegram(digest)
    assert len(text) <= 4096
    shown = text.count('<a href="https://www.linkedin')
    assert 0 < shown < 10
    assert f"e mais {10 - shown + 5}" in text


def test_verification_message_for_discord_is_an_embed() -> None:
    from ptjobs.digest import render_discord_verification

    payload = render_discord_verification("https://v/subscriptions/verify?token=t")
    assert payload["embeds"][0]["url"] == "https://v/subscriptions/verify?token=t"
    assert payload["allowed_mentions"] == {"parse": []}
