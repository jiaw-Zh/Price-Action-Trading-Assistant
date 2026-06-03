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


import json

class LarkChannel:
    """Send messages via Lark / Feishu Custom App Bot."""

    name = "lark"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        receive_id: str,
        receive_id_type: str = "chat_id",
        proxy_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._receive_id = receive_id
        self._receive_id_type = receive_id_type
        self._proxy_url = proxy_url
        self._timeout_s = timeout_s

    async def _get_tenant_access_token(self) -> str:
        """Fetch tenant_access_token from Lark API."""
        log = get_logger("lark")
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._app_id,
            "app_secret": self._app_secret,
        }
        async with httpx.AsyncClient(
            proxy=self._proxy_url, timeout=self._timeout_s
        ) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            code = data.get("code", 0)
            if code != 0:
                log.error("lark_token_api_error", response=data)
                raise RuntimeError(f"Lark Token API error: {data}")
            return data["tenant_access_token"]

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

        # Construct Lark Interactive Card Content
        card_content = {
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
        }

        # Construct Lark Send Message Payload
        # Content must be a JSON-escaped string
        payload = {
            "receive_id": self._receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card_content),
        }

        token = await self._get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={self._receive_id_type}"

        async with httpx.AsyncClient(
            proxy=self._proxy_url, timeout=self._timeout_s
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            err = data.get("code", 0)
            if err != 0:
                log.error("lark_send_api_error", response=data)
                raise RuntimeError(f"Lark API error: {data}")
