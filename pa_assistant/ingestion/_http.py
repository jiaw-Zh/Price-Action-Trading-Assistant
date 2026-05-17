"""Common async HTTP client base shared by exchange connectors.

Lifts the retry / lifecycle plumbing that was duplicated between Binance,
OKX and Bybit clients. Subclasses define endpoint methods that call
:meth:`AsyncRestClient._get` and shape the response.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Final, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# Exception classes considered transient on REST calls.
_TRANSIENT_HTTPX_ERRORS: Final[tuple[type[BaseException], ...]] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def is_transient(exc: BaseException) -> bool:
    """Predicate for tenacity: should *exc* trigger a retry?"""
    if isinstance(exc, _TRANSIENT_HTTPX_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


class AsyncRestClient:
    """Async REST client with tenacity exponential-backoff retries.

    Subclasses define endpoint methods. The base owns:

    * :class:`httpx.AsyncClient` lifecycle (own / borrowed)
    * retry policy (transient errors only)
    * JSON decoding

    Retry parameters are constructor-injectable so unit tests can run with
    near-zero waits.
    """

    DEFAULT_USER_AGENT: Final[str] = "pa-assistant/0.1"

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        retry_attempts: int = 5,
        retry_min_wait: float = 1.0,
        retry_max_wait: float = 30.0,
        client: httpx.AsyncClient | None = None,
        proxy: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = {"User-Agent": self.DEFAULT_USER_AGENT, **(headers or {})}
        self._retry_attempts = retry_attempts
        self._retry_max_wait = retry_max_wait
        self._retry_min_wait = retry_min_wait
        self._proxy = proxy or None  # treat empty string as no proxy

        # External clients (e.g. test transports) are borrowed, not owned.
        self._client = client
        self._owned_client = client is None

    # ----- Lifecycle -----

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "headers": self._headers,
                "timeout": self._timeout,
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owned_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        self._get_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ----- Internal HTTP plumbing -----

    def _make_retrying(self) -> AsyncRetrying:
        return AsyncRetrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(
                multiplier=1,
                min=self._retry_min_wait,
                max=self._retry_max_wait,
            ),
            retry=retry_if_exception(is_transient),
            reraise=True,
        )

    async def _get(self, path: str, **params: Any) -> Any:
        """Issue a GET request with retry and JSON decoding."""
        async for attempt in self._make_retrying():
            with attempt:
                client = self._get_client()
                response = await client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        # AsyncRetrying with reraise=True either returns above or raises.
        raise AssertionError("unreachable: retry loop exited without result")
