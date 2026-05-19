"""Telegram Bot API notification channel.

Reference: https://core.telegram.org/bots/api#sendmessage

Caveats
-------

* Telegram's MarkdownV2 requires escaping ``_*[]()~`>#+-=|{}.!``. Our
  ``render_markdown`` output uses safe characters but a few ASCII
  punctuation marks (``.``, ``-``, ``+``, ``(``, ``)``) appear in
  numeric context. We escape conservatively to keep parsing reliable.
* The Bot must have started a conversation with the target chat; for
  groups, the bot must be added.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx

from pa_assistant.logging import get_logger

if TYPE_CHECKING:
    from pa_assistant.notifications import NotificationMessage


# MarkdownV2 reserves these characters; outside code blocks they must be
# escaped. We do not generate code blocks in our reports, so we escape
# unconditionally over the whole body.
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_mdv2(text: str) -> str:
    return _MDV2_SPECIAL.sub(r"\\\1", text)


class TelegramChannel:
    """Send messages via the Telegram Bot API."""

    name = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        proxy_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._proxy_url = proxy_url
        self._timeout_s = timeout_s

    async def send(self, message: NotificationMessage) -> None:
        log = get_logger(__name__)
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

        if message.format == "markdown":
            # Title becomes a bold first line; body is escaped wholesale.
            title = _escape_mdv2(message.title)
            body = _escape_mdv2(message.body)
            text = f"*{title}*\n\n{body}"
            parse_mode: str | None = "MarkdownV2"
        else:
            text = f"{message.title}\n\n{message.body}"
            parse_mode = None

        payload: dict[str, object] = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(
            proxy=self._proxy_url, timeout=self._timeout_s
        ) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                log.error(
                    "telegram_send_http_error",
                    status=response.status_code,
                    body=response.text[:500],
                )
                response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                log.error("telegram_send_api_error", response=data)
                raise RuntimeError(f"Telegram API error: {data}")
