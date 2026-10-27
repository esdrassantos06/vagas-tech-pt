"""HTTP client wrapper adding polite throttling and bounded retries."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from ptjobs.errors import SourceError

USER_AGENT = "vagas-tech-pt/0.1 (+https://github.com/esdrassantos06/vagas-tech-pt)"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 120.0


class HttpClient:
    """Wrap an httpx.Client with a crawl-delay throttle and retry policy.

    Retries 429 and 5xx responses and request errors with exponential backoff,
    honoring a Retry-After header when present and reasonable. Other 4xx
    responses raise SourceError immediately, as does a Retry-After larger than
    MAX_RETRY_AFTER. A minimum interval, measured from the end of the previous
    request, is enforced to respect per-site crawl delays. Redirects are
    followed by default.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        max_retries: int = 3,
        base_delay: float = 0.5,
        min_interval: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a GET request, throttled and retried per the client policy."""
        headers = {"User-Agent": USER_AGENT, **(kwargs.pop("headers", None) or {})}
        kwargs.setdefault("follow_redirects", True)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            response = self._send(url, headers, kwargs)
            if isinstance(response, httpx.Response):
                if response.status_code < 300:
                    return response
                if response.status_code not in _RETRY_STATUSES:
                    raise SourceError(
                        f"GET {url} failed with {response.status_code}",
                        details={"status": response.status_code},
                    )
                last_error = SourceError(
                    f"GET {url} -> {response.status_code}",
                    details={"status": response.status_code},
                )
                delay = self._retry_delay(attempt, response)
            else:
                last_error = response
                delay = self._retry_delay(attempt, None)

            if attempt >= self._max_retries:
                break
            self._sleep(delay)

        raise SourceError(
            f"GET {url} failed after {self._max_retries} retries",
            details=getattr(last_error, "details", None),
        ) from last_error

    def _send(
        self, url: str, headers: dict[str, str], kwargs: dict[str, Any]
    ) -> httpx.Response | httpx.RequestError:
        self._throttle()
        try:
            return self._client.get(url, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            return exc
        finally:
            self._last_request_at = self._monotonic()

    def _throttle(self) -> None:
        if self._min_interval <= 0 or self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isascii() and retry_after.isdigit():
                seconds = float(retry_after)
                if seconds > MAX_RETRY_AFTER:
                    raise SourceError(
                        f"Retry-After {seconds}s exceeds cap",
                        details={"retry_after": seconds},
                    )
                return seconds
        return self._base_delay * (2.0**attempt)
