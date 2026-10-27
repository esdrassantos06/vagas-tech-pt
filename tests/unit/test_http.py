import httpx
import pytest

from ptjobs.errors import SourceError
from ptjobs.sources.http import HttpClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(handler, clock: FakeClock, **kwargs) -> HttpClient:
    inner = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://x")
    return HttpClient(
        inner, sleep=clock.sleep, monotonic=clock.monotonic, base_delay=0.5, **kwargs
    )


def test_returns_ok_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, text="ok")

    clock = FakeClock()
    resp = _client(handler, clock).get("/jobs")

    assert resp.status_code == 200
    assert len(calls) == 1
    assert clock.slept == []


def test_sets_identifiable_user_agent() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200)

    _client(handler, FakeClock()).get("/jobs")
    assert "vagas-tech-pt" in seen["ua"]


def test_retries_on_503_then_succeeds() -> None:
    responses = [httpx.Response(503), httpx.Response(200, text="ok")]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    clock = FakeClock()
    resp = _client(handler, clock).get("/jobs")

    assert resp.status_code == 200
    assert clock.slept == [0.5]


def test_honors_retry_after_header() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    clock = FakeClock()
    _client(handler, clock).get("/jobs")

    assert clock.slept == [7.0]


def test_does_not_retry_on_404() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    clock = FakeClock()
    with pytest.raises(SourceError):
        _client(handler, clock).get("/jobs")

    assert len(calls) == 1
    assert clock.slept == []


def test_exhausts_retries_on_persistent_500() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    clock = FakeClock()
    with pytest.raises(SourceError):
        _client(handler, clock, max_retries=3).get("/jobs")

    assert len(calls) == 4  # first attempt + 3 retries
    assert clock.slept == [0.5, 1.0, 2.0]


def test_retries_on_network_error() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    clock = FakeClock()
    resp = _client(handler, clock).get("/jobs")

    assert resp.status_code == 200
    assert len(attempts) == 2


def test_throttles_between_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    clock = FakeClock()
    client = _client(handler, clock, min_interval=1.0)
    client.get("/a")
    client.get("/b")

    assert clock.slept == [1.0]


def test_gives_up_on_absurd_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "86400"})

    clock = FakeClock()
    with pytest.raises(SourceError):
        _client(handler, clock).get("/jobs")

    assert clock.slept == []


def test_invalid_retry_after_falls_back_to_backoff() -> None:
    responses = [
        httpx.Response(503, headers={"Retry-After": "soon"}),
        httpx.Response(200),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    clock = FakeClock()
    _client(handler, clock).get("/jobs")
    assert clock.slept == [0.5]


def test_throttle_measured_from_request_end() -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.now += 5.0
        return httpx.Response(200)

    client = _client(handler, clock, min_interval=1.0)
    client.get("/a")
    client.get("/b")

    assert clock.slept == [1.0]


def test_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/final"})
        return httpx.Response(200, text="final")

    resp = _client(handler, FakeClock()).get("/start")
    assert resp.status_code == 200
    assert resp.text == "final"


def test_wraps_non_transport_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TooManyRedirects("loop", request=request)

    clock = FakeClock()
    with pytest.raises(SourceError):
        _client(handler, clock, max_retries=1).get("/jobs")
