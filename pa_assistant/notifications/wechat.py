"""WeChat Work (企业微信) group bot webhook notification channel.

Reference: https://developer.work.weixin.qq.com/document/path/91770

Body schema (markdown variant)::

    {"msgtype": "markdown", "markdown": {"content": "..."}}

Caveats
-------

* Markdown support is **subset** of standard markdown — supports
  headings, bold, code blocks, links, but not tables. Limited but
  sufficient for our reports.
* Hard size limit: 4096 bytes per message. Our reports are typically
  ~2 KB; we don't preemptively truncate but log if exceeded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from pa_assistant.logging import get_logger

if TYPE_CHECKING:
    from pa_assistant.notifications import NotificationMessage

_MAX_BYTES = 4096


class WeChatWorkChannel:
    """Send messages via WeChat Work group bot webhook."""

    name = "wechat_work"

    def __init__(
        self,
        *,
        webhook_url: str,
        proxy_url: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._webhook_url = webhook_url
        self._proxy_url = proxy_url
        self._timeout_s = timeout_s

    async def send(self, message: NotificationMessage) -> None:
        log = get_logger(__name__)

        if message.format == "markdown":
            content = f"## {message.title}\n\n{message.body}"
            payload: dict[str, object] = {
                "msgtype": "markdown",
                "markdown": {"content": content},
            }
        else:
            content = f"{message.title}\n\n{message.body}"
            payload = {"msgtype": "text", "text": {"content": content}}

        if len(content.encode("utf-8")) > _MAX_BYTES:
            log.warning("wechat_message_oversized", bytes=len(content.encode("utf-8")))

        async with httpx.AsyncClient(
            proxy=self._proxy_url, timeout=self._timeout_s
        ) as client:
            response = await client.post(self._webhook_url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("errcode", 0) != 0:
                log.error("wechat_send_api_error", response=data)
                raise RuntimeError(f"WeChat Work API error: {data}")
