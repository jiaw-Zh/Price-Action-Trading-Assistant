"""Notification channels for pushing context reports.

Architecture
------------

* :class:`NotificationMessage` — the payload produced by analysis layer.
* :class:`NotificationChannel` — Protocol that every concrete channel
  implements. Stateless; constructed with credentials, exposes ``send()``.
* :func:`send_to_all` — fan-out helper that dispatches a single message
  to every configured channel concurrently, tolerating per-channel
  failures (mirrors the funding aggregator's degradation pattern).

Concrete channels live in submodules:

* :mod:`pa_assistant.notifications.telegram` — Telegram Bot API
* :mod:`pa_assistant.notifications.wechat`   — WeChat Work group bot webhook
* :mod:`pa_assistant.notifications.lark`     — Lark / Feishu group bot webhook

Design decisions
----------------

* Every channel is **async** and uses :class:`httpx.AsyncClient` (proxy
  support inherited via the project's ``http_proxy_url`` setting).
* Failures are **logged but not raised**, so one broken webhook does
  not block the others.
* No retries inside channels — if a transient failure is acceptable,
  the caller can retry. Most webhook outages are configuration issues
  that retries don't fix.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pa_assistant.config import Settings
from pa_assistant.logging import get_logger

MessageFormat = Literal["markdown", "plain"]


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """A single push message.

    Attributes
    ----------
    title:
        Short header. Channels that support a separate title (e.g. WeChat
        Work card messages) will use it; otherwise channels prepend it
        to ``body`` as a bold first line.
    body:
        Full message body. May be multiline.
    format:
        ``"markdown"`` — body is markdown-formatted. Channels do their
        best to honor it (Telegram MarkdownV2, WeChat markdown card,
        Lark text). ``"plain"`` — body is plaintext, no escaping.
    """

    title: str
    body: str
    format: MessageFormat = "markdown"


@runtime_checkable
class NotificationChannel(Protocol):
    """Protocol every concrete channel implements."""

    name: str

    async def send(self, message: NotificationMessage) -> None: ...


def configured_channels(settings: Settings) -> list[NotificationChannel]:
    """Build the list of channels for which credentials are configured.

    Channels with missing credentials are silently omitted. Returns an
    empty list when no channel is configured (caller can warn the user).
    """
    # Imports inside the function to avoid cyclic import at module load
    # and to defer construction until needed.
    from pa_assistant.notifications.lark import LarkChannel
    from pa_assistant.notifications.telegram import TelegramChannel
    from pa_assistant.notifications.wechat import WeChatWorkChannel

    channels: list[NotificationChannel] = []
    if settings.telegram_bot_token is not None and settings.telegram_chat_id:
        channels.append(
            TelegramChannel(
                bot_token=settings.telegram_bot_token.get_secret_value(),
                chat_id=settings.telegram_chat_id,
                proxy_url=settings.http_proxy_url,
            )
        )
    if settings.wechat_work_webhook_url is not None:
        channels.append(
            WeChatWorkChannel(
                webhook_url=settings.wechat_work_webhook_url.get_secret_value(),
                proxy_url=settings.http_proxy_url,
            )
        )
    if settings.lark_webhook_url is not None:
        signing_secret = (
            settings.lark_signing_secret.get_secret_value()
            if settings.lark_signing_secret
            else None
        )
        channels.append(
            LarkChannel(
                webhook_url=settings.lark_webhook_url.get_secret_value(),
                signing_secret=signing_secret,
                proxy_url=settings.http_proxy_url,
            )
        )
    return channels


async def send_to_all(
    channels: list[NotificationChannel],
    message: NotificationMessage,
) -> dict[str, BaseException | None]:
    """Send ``message`` to every channel concurrently.

    Returns a ``{channel_name: exception_or_None}`` map for inspection.
    Failures are logged but do not raise — mirrors the funding aggregator's
    "best-effort" pattern.
    """
    log = get_logger("notifications")
    if not channels:
        log.warning("send_to_all_no_channels")
        return {}

    results = await asyncio.gather(
        *(c.send(message) for c in channels),
        return_exceptions=True,
    )
    outcome: dict[str, BaseException | None] = {}
    for ch, res in zip(channels, results, strict=True):
        if isinstance(res, BaseException):
            log.error("notification_failed", channel=ch.name, error=str(res))
            outcome[ch.name] = res
        else:
            log.info("notification_sent", channel=ch.name)
            outcome[ch.name] = None
    return outcome


__all__ = [
    "MessageFormat",
    "NotificationChannel",
    "NotificationMessage",
    "configured_channels",
    "send_to_all",
]
