"""Tests for HTTP proxy plumbing through ``AsyncRestClient``.

We don't need a real proxy server — we just verify the proxy URL gets
threaded into ``httpx.AsyncClient`` correctly when configured, and
omitted when not.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from pa_assistant.config import Settings
from pa_assistant.ingestion._http import AsyncRestClient
from pa_assistant.ingestion.binance import BinanceRestClient
from pa_assistant.ingestion.bybit import BybitRestClient
from pa_assistant.ingestion.funding import (
    SelfAggregatedFundingProvider,
    make_funding_provider,
)
from pa_assistant.ingestion.okx import OkxRestClient


def _client_kwargs_when_constructed(client: AsyncRestClient) -> dict[str, object]:
    """Trigger lazy AsyncClient creation and snapshot the kwargs used."""
    captured: dict[str, object] = {}
    real = httpx.AsyncClient

    def _spy(**kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real(**kwargs)  # type: ignore[arg-type]

    with patch("pa_assistant.ingestion._http.httpx.AsyncClient", side_effect=_spy):
        client._get_client()
    return captured


def test_no_proxy_by_default() -> None:
    client = AsyncRestClient(base_url="https://example.com")
    captured = _client_kwargs_when_constructed(client)
    assert "proxy" not in captured


def test_proxy_url_passed_to_httpx() -> None:
    client = AsyncRestClient(
        base_url="https://example.com",
        proxy="http://127.0.0.1:7890",
    )
    captured = _client_kwargs_when_constructed(client)
    assert captured["proxy"] == "http://127.0.0.1:7890"


def test_empty_string_proxy_treated_as_unset() -> None:
    client = AsyncRestClient(base_url="https://example.com", proxy="")
    captured = _client_kwargs_when_constructed(client)
    assert "proxy" not in captured


def test_socks5_proxy_url_passed_through() -> None:
    client = AsyncRestClient(
        base_url="https://example.com",
        proxy="socks5://127.0.0.1:7890",
    )
    captured = _client_kwargs_when_constructed(client)
    assert captured["proxy"] == "socks5://127.0.0.1:7890"


def test_borrowed_client_ignores_proxy() -> None:
    """If caller supplies their own httpx client, proxy is irrelevant — caller
    is responsible for proxying."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    borrowed = httpx.AsyncClient(base_url="https://example.com", transport=transport)
    client = AsyncRestClient(
        base_url="https://example.com",
        client=borrowed,
        proxy="http://127.0.0.1:7890",
    )
    # _get_client should return the borrowed client, NOT create a new one
    with patch("pa_assistant.ingestion._http.httpx.AsyncClient") as spy:
        out = client._get_client()
    spy.assert_not_called()
    assert out is borrowed


def test_settings_have_proxy_field_default_none() -> None:
    settings = Settings()
    assert settings.http_proxy_url is None


def test_settings_proxy_field_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY_URL", "http://127.0.0.1:7890")
    settings = Settings()
    assert settings.http_proxy_url == "http://127.0.0.1:7890"


def test_binance_from_settings_threads_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY_URL", "http://127.0.0.1:7890")
    settings = Settings()
    client = BinanceRestClient.from_settings(settings)
    captured = _client_kwargs_when_constructed(client)
    assert captured["proxy"] == "http://127.0.0.1:7890"


def test_self_aggregated_provider_threads_proxy_to_okx_and_bybit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY_URL", "http://127.0.0.1:7890")
    settings = Settings()
    provider = SelfAggregatedFundingProvider.from_settings(settings)

    captured_okx = _client_kwargs_when_constructed(provider.okx)
    captured_bybit = _client_kwargs_when_constructed(provider.bybit)
    captured_binance = _client_kwargs_when_constructed(provider.binance)

    assert captured_okx["proxy"] == "http://127.0.0.1:7890"
    assert captured_bybit["proxy"] == "http://127.0.0.1:7890"
    assert captured_binance["proxy"] == "http://127.0.0.1:7890"


def test_make_funding_provider_threads_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: factory → provider → all underlying clients → httpx."""
    monkeypatch.setenv("HTTP_PROXY_URL", "http://127.0.0.1:7890")
    settings = Settings()
    provider = make_funding_provider(settings)
    assert isinstance(provider, SelfAggregatedFundingProvider)
    captured_okx = _client_kwargs_when_constructed(provider.okx)
    assert captured_okx["proxy"] == "http://127.0.0.1:7890"


def test_okx_and_bybit_default_no_proxy() -> None:
    """When constructed directly without proxy=, no proxy is set."""
    okx = OkxRestClient()
    bybit = BybitRestClient()
    assert "proxy" not in _client_kwargs_when_constructed(okx)
    assert "proxy" not in _client_kwargs_when_constructed(bybit)
