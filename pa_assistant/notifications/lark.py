"""Lark / Feishu (飞书) group bot webhook notification channel.

Reference: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

The "rich text" (post) message type would let us style headings and
links, but its body is a nested array of paragraphs and runs which is
clunky to build. We use the simpler ``text`` type, with the title
prepended as a bold-marked first line. Markdown rendering on Feishu is
limited anyway.

Optional signing
----------------

Lark bots can require HMAC-SHA256 signing where ``timestamp`` and a
shared secret produce the ``sign`` field. Pass ``signing_secret`` to
enable; leave ``None`` for unsigned bots.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import TYPE_CHECKING

import httpx

from pa_assistant.logging import get_logger

if TYPE_CHECKING:
    from pa_assistant.notifications import NotificationMessage


def _compute_lark_sign(timestamp: int, secret: str) -> str:
    """Lark signing recipe: base64(HMAC-SHA256(key=f"{ts}\\n{secret}", msg=b""))."""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class LarkChannel:
    """Send messages via Lark / Feishu group bot webhook."""

    name = "lark"

    def __init__(
        self,
        *,
        webhook_url: str,
        signing_secret: str | None = None,
        proxy_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._signing_secret = signing_secret
        self._proxy_url = proxy_url
        self._timeout_s = timeout_s

    async def send(self, message: NotificationMessage) -> None:
        log = get_logger(__name__)

        # Determine card template color based on side and title/body content
        template = "grey"
        side_lower = (message.side or "").lower()
        title_lower = message.title.lower()
        body_lower = message.body.lower()

        if any(x in side_lower for x in ["bullish", "long", "up"]) or any(x in title_lower for x in ["看多", "做多"]):
            template = "green"
        elif any(x in side_lower for x in ["bearish", "short", "down"]) or any(x in title_lower for x in ["看空", "做空"]):
            template = "red"
        else:
            first_part = body_lower[:200]
            if any(x in first_part for x in ["看多", "做多", "bullish", "long"]):
                template = "green"
            elif any(x in first_part for x in ["看空", "做空", "bearish", "short"]):
                template = "red"

        # Format title cleanly
        card_title = message.title
        if message.timeframe and not message.title.startswith("["):
            card_title = f"[{message.timeframe.upper()}] {card_title}"

        # Construct Lark Interactive Card Payload
        payload: dict[str, object] = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True,
                    "enable_forward": True,
                },
                "header": {
                    "template": template,
                    "title": {
                        "content": card_title,
                        "tag": "plain_text",
                    },
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "content": message.body,
                            "tag": "lark_md",
                        },
                    }
                ],
            },
        }

        if self._signing_secret is not None:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = _compute_lark_sign(ts, self._signing_secret)


        async with httpx.AsyncClient(
            proxy=self._proxy_url, timeout=self._timeout_s
        ) as client:
            response = await client.post(self._webhook_url, json=payload)
            response.raise_for_status()
            data = response.json()
            # Lark uses ``code`` (int, 0 = ok) AND ``StatusCode`` (legacy);
            # accept either.
            err = data.get("code", data.get("StatusCode", 0))
            if err != 0:
                log.error("lark_send_api_error", response=data)
                raise RuntimeError(f"Lark API error: {data}")
