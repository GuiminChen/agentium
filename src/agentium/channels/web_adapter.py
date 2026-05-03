"""Web channel adapter using simple HTTP webhooks.

Many enterprise deployments forward agent notifications to a chat system
via incoming webhook (Slack, Teams, Lark/Feishu, custom WebUI).  This
adapter posts the rendered :class:`OutboundMessage` as a JSON payload to
a configured URL.

Security:
- The adapter does *not* parse provider-specific schemas; it just posts the
  raw envelope and lets the receiver shape the surface.
- Network failures are surfaced as :class:`ChannelError`, allowing the
  orchestrator to escalate to the audit layer instead of silently dropping.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

from agentium.channels.base import (
    ChannelAdapter,
    ChannelDeliveryResult,
    ChannelError,
    ChannelKind,
    OutboundMessage,
)


HttpPostFn = Callable[[str, bytes, Dict[str, str]], int]


def _default_http_post(url: str, body: bytes, headers: Dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.URLError as exc:  # pragma: no cover - exercised via injection
        raise ChannelError(f"web channel transport error: {exc}") from exc


class WebChannelAdapter(ChannelAdapter):
    """Posts the message as JSON to ``webhook_url``."""

    name = "web"
    kind = ChannelKind.WEB

    def __init__(
        self,
        webhook_url: str,
        *,
        http_post: Optional[HttpPostFn] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._url = webhook_url
        self._http_post = http_post or _default_http_post
        self._extra_headers = dict(extra_headers or {})

    def send(self, message: OutboundMessage) -> ChannelDeliveryResult:
        payload = {
            "tenant_id": message.tenant_id,
            "run_id": message.run_id,
            "recipient": message.recipient,
            "subject": message.subject,
            "body": message.body,
            "metadata": dict(message.metadata),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._extra_headers}
        status = self._http_post(self._url, body, headers)
        if status >= 400:
            raise ChannelError(f"web channel rejected delivery (status={status})")
        return ChannelDeliveryResult(
            channel=self.name,
            delivered=True,
            detail={"status": status, "url": self._url},
        )


__all__ = ["WebChannelAdapter"]
