"""Tests for notification channels.

Uses ``httpx.MockTransport`` to intercept outbound HTTP without any
network. Each test asserts on both the request payload and the
exception behavior.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from pa_assistant.notifications import (
    NotificationMessage,
    send_to_all,
)
from pa_assistant.notifications.lark import LarkChannel, _compute_lark_sign
from pa_assistant.notifications.telegram import TelegramChannel, _escape_mdv2
from pa_assistant.notifications.wechat import WeChatWorkChannel


def _patch_async_client(handler: Any) -> Any:
    """Patch ``httpx.AsyncClient`` to use a MockTransport for the test.

    The handler is a callable: ``(httpx.Request) -> httpx.Response``.
    """
    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        # Pop unsupported kwargs that MockTransport doesn't care about
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    return patch.object(httpx.AsyncClient, "__init__", patched_init)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def test_escape_mdv2_handles_special_chars() -> None:
    """All MarkdownV2 reserved chars get escaped."""
    text = "Hello (world) -> $100.50!"
    escaped = _escape_mdv2(text)
    assert "\\(" in escaped
    assert "\\)" in escaped
    assert "\\." in escaped
    assert "\\!" in escaped
    assert "\\-" in escaped


@pytest.mark.asyncio
async def test_telegram_sends_with_markdown_v2_format() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    with _patch_async_client(handler):
        ch = TelegramChannel(bot_token="xxx", chat_id="123")
        await ch.send(
            NotificationMessage(title="Test", body="Hello world", format="markdown")
        )

    assert "/bot xxx/sendMessage".replace(" ", "") in captured["url"]
    assert captured["body"]["chat_id"] == "123"
    assert captured["body"]["parse_mode"] == "MarkdownV2"
    # Title is bolded as MarkdownV2 (asterisks; chars in title are escaped)
    assert "*Test*" in captured["body"]["text"]


@pytest.mark.asyncio
async def test_telegram_plain_format_omits_parse_mode() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    with _patch_async_client(handler):
        ch = TelegramChannel(bot_token="xxx", chat_id="123")
        await ch.send(NotificationMessage(title="T", body="B", format="plain"))

    assert "parse_mode" not in captured["body"]


@pytest.mark.asyncio
async def test_telegram_raises_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "Bad"})

    with _patch_async_client(handler):
        ch = TelegramChannel(bot_token="xxx", chat_id="123")
        with pytest.raises(RuntimeError, match="Telegram API error"):
            await ch.send(NotificationMessage(title="T", body="B"))


@pytest.mark.asyncio
async def test_telegram_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    with _patch_async_client(handler):
        ch = TelegramChannel(bot_token="xxx", chat_id="123")
        with pytest.raises(httpx.HTTPStatusError):
            await ch.send(NotificationMessage(title="T", body="B"))


# ---------------------------------------------------------------------------
# WeChat Work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wechat_sends_markdown_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    with _patch_async_client(handler):
        ch = WeChatWorkChannel(webhook_url="https://qyapi/x")
        await ch.send(
            NotificationMessage(title="Title", body="Body", format="markdown")
        )

    assert captured["body"]["msgtype"] == "markdown"
    assert "## Title" in captured["body"]["markdown"]["content"]
    assert "Body" in captured["body"]["markdown"]["content"]


@pytest.mark.asyncio
async def test_wechat_plain_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0})

    with _patch_async_client(handler):
        ch = WeChatWorkChannel(webhook_url="https://qyapi/x")
        await ch.send(NotificationMessage(title="T", body="B", format="plain"))

    assert captured["body"]["msgtype"] == "text"


@pytest.mark.asyncio
async def test_wechat_raises_on_api_errcode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid"})

    with _patch_async_client(handler):
        ch = WeChatWorkChannel(webhook_url="https://qyapi/x")
        with pytest.raises(RuntimeError, match="WeChat Work API error"):
            await ch.send(NotificationMessage(title="T", body="B"))


# ---------------------------------------------------------------------------
# Lark
# ---------------------------------------------------------------------------


def test_lark_sign_deterministic_for_same_inputs() -> None:
    a = _compute_lark_sign(1700000000, "secret")
    b = _compute_lark_sign(1700000000, "secret")
    assert a == b
    # Different timestamp produces different sign
    c = _compute_lark_sign(1700000001, "secret")
    assert a != c


@pytest.mark.asyncio
async def test_lark_sends_text_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    with _patch_async_client(handler):
        ch = LarkChannel(webhook_url="https://lark/x")
        await ch.send(NotificationMessage(title="T", body="B"))

    assert captured["body"]["msg_type"] == "text"
    assert "T" in captured["body"]["content"]["text"]
    # No signing fields without secret
    assert "sign" not in captured["body"]


@pytest.mark.asyncio
async def test_lark_includes_sign_when_secret_provided() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    with _patch_async_client(handler):
        ch = LarkChannel(webhook_url="https://lark/x", signing_secret="topsecret")
        await ch.send(NotificationMessage(title="T", body="B"))

    assert "sign" in captured["body"]
    assert "timestamp" in captured["body"]


@pytest.mark.asyncio
async def test_lark_raises_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 9499, "msg": "bad"})

    with _patch_async_client(handler):
        ch = LarkChannel(webhook_url="https://lark/x")
        with pytest.raises(RuntimeError, match="Lark API error"):
            await ch.send(NotificationMessage(title="T", body="B"))


# ---------------------------------------------------------------------------
# Fan-out (send_to_all)
# ---------------------------------------------------------------------------


class _StubChannel:
    """A controllable channel for fan-out tests."""

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self._fail = fail
        self.received: list[NotificationMessage] = []

    async def send(self, message: NotificationMessage) -> None:
        self.received.append(message)
        if self._fail:
            raise RuntimeError(f"{self.name} simulated failure")


@pytest.mark.asyncio
async def test_send_to_all_dispatches_to_every_channel() -> None:
    a = _StubChannel("a")
    b = _StubChannel("b")
    msg = NotificationMessage(title="T", body="B")
    outcome = await send_to_all([a, b], msg)
    assert outcome == {"a": None, "b": None}
    assert a.received == [msg]
    assert b.received == [msg]


@pytest.mark.asyncio
async def test_send_to_all_isolates_failures() -> None:
    """One channel failing must not block the others."""
    ok = _StubChannel("ok")
    bad = _StubChannel("bad", fail=True)
    msg = NotificationMessage(title="T", body="B")
    outcome = await send_to_all([ok, bad], msg)
    assert outcome["ok"] is None
    assert isinstance(outcome["bad"], RuntimeError)
    # ok still received the message
    assert ok.received == [msg]


@pytest.mark.asyncio
async def test_send_to_all_with_no_channels_returns_empty() -> None:
    outcome = await send_to_all([], NotificationMessage(title="T", body="B"))
    assert outcome == {}
