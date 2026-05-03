"""OpenAI-compatible DeepSeek chat completions via stdlib HTTP (no extra deps)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import structlog

_LOGGER = structlog.get_logger(__name__)


class DeepSeekChatCompletionError(RuntimeError):
    """Failed to finish a chat completion call."""


@dataclass(frozen=True)
class DeepSeekCompletionResult:
    """Normalized assistant output from DeepSeek-compatible APIs."""

    text: str
    raw_finish_reason: Optional[str]


class DeepSeekChatCompletionClient:
    """Minimal ``/v1/chat/completions`` client for DeepSeek-hosted models."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key required")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def complete_chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        trace_id: str,
        request_id: str,
    ) -> DeepSeekCompletionResult:
        """Call chat completions synchronously."""

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "temperature": 0.7,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self._base_url}/v1/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("X-Trace-Id", trace_id)
        req.add_header("X-Request-Id", request_id)
        _LOGGER.info(
            "deepseek_completion_request",
            trace_id=trace_id,
            request_id=request_id,
            url=url,
            model=self._model,
            message_count=len(messages),
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2048]
            _LOGGER.warning(
                "deepseek_completion_http_error",
                status=exc.code,
                trace_id=trace_id,
                request_id=request_id,
                detail_prefix=detail[:200],
            )
            raise DeepSeekChatCompletionError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            _LOGGER.warning("deepseek_completion_url_error", trace_id=trace_id, error=str(exc))
            raise DeepSeekChatCompletionError("network_error") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeepSeekChatCompletionError("invalid_json_response") from exc
        text, reason = self._extract_message(decoded)
        return DeepSeekCompletionResult(text=text, raw_finish_reason=reason)

    @staticmethod
    def _extract_message(decoded: Dict[str, Any]) -> tuple[str, Optional[str]]:
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekChatCompletionError("missing_choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise DeepSeekChatCompletionError("invalid_choice_shape")
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                finish = first.get("finish_reason")
                return content, finish if isinstance(finish, str) else None
        # Some providers expose text at top-level
        alt = decoded.get("output_text") or first.get("text")
        if isinstance(alt, str):
            finish = first.get("finish_reason")
            return alt, finish if isinstance(finish, str) else None
        raise DeepSeekChatCompletionError("missing_message_content")
