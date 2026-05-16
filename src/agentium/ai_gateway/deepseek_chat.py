"""OpenAI-compatible DeepSeek chat completions via stdlib HTTP (no extra deps).

Official DeepSeek-V4 completion model identifiers include ``deepseek-v4-flash`` (typical default)
and ``deepseek-v4-pro``; configure via ``AGENTIUM_CHAT_MODEL`` as resolved in ``load_settings()``.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import structlog

from agentium.ai_gateway.deepseek_v4_agent.dsml import (
    dsml_tool_calls_to_openai_tool_calls,
    extract_dsml_tool_block,
)
from agentium.runtime.prompt_cache_policy import PromptCachePolicy

_LOGGER = structlog.get_logger(__name__)


class DeepSeekChatCompletionError(RuntimeError):
    """Failed to finish a chat completion call."""


@dataclass(frozen=True)
class DeepSeekThinkingCompletionOptions:
    """DeepSeek-V4 thinking mode controls (official HTTP schema).

    When ``enabled`` is True, ``temperature`` must not be sent (DeepSeek ignores it).
    """

    enabled: bool
    reasoning_effort: str


@dataclass(frozen=True)
class LlmUsageSnapshot:
    """Normalized token counts from provider ``usage`` (OpenAI-compatible shape)."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


def parse_llm_usage_from_completion_json(decoded: Dict[str, Any]) -> Optional[LlmUsageSnapshot]:
    """Extract ``usage`` from a chat completions JSON object when present."""

    raw = decoded.get("usage")
    if not isinstance(raw, dict):
        return None

    def _nonneg_int(val: Any) -> Optional[int]:
        if isinstance(val, int) and val >= 0:
            return val
        return None

    pt = _nonneg_int(raw.get("prompt_tokens"))
    ct = _nonneg_int(raw.get("completion_tokens"))
    tt = _nonneg_int(raw.get("total_tokens"))
    if pt is None and ct is None and tt is None:
        return None
    return LlmUsageSnapshot(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt)


@dataclass(frozen=True)
class DeepSeekCompletionResult:
    """Normalized assistant output from DeepSeek-compatible APIs."""

    text: str
    raw_finish_reason: Optional[str]
    reasoning_content: Optional[str] = None
    usage: Optional[LlmUsageSnapshot] = None


@dataclass(frozen=True)
class DeepSeekStreamDelta:
    """One normalized chunk from an OpenAI-style ``stream: true`` completion."""

    content: str = ""
    reasoning: str = ""
    finish_reason: Optional[str] = None


@dataclass(frozen=True)
class DeepSeekChatRoundResult:
    """One completion round; may include parallel ``tool_calls``."""

    text: Optional[str]
    tool_calls: List[Dict[str, Any]]
    assistant_message: Dict[str, Any]
    raw_finish_reason: Optional[str]
    reasoning_content: Optional[str] = None
    usage: Optional[LlmUsageSnapshot] = None


class DeepSeekChatCompletionClient:
    """Minimal ``/v1/chat/completions`` client for DeepSeek-hosted models."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        prompt_cache_policy: Optional[PromptCachePolicy] = None,
        prompt_cache_http_header: bool = False,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key required")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._prompt_cache_policy = prompt_cache_policy
        self._prompt_cache_http_header = bool(prompt_cache_http_header)

    @staticmethod
    def _derive_prompt_cache_key(payload: Dict[str, Any]) -> str:
        """Stable hash over system prefix + serialized tools (provider cache key MVP)."""

        parts: List[str] = []
        messages = payload.get("messages")
        if isinstance(messages, list):
            for msg in messages[:4]:
                if not isinstance(msg, dict):
                    continue
                if str(msg.get("role") or "") != "system":
                    continue
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    parts.append(c)
        tools = payload.get("tools")
        if isinstance(tools, list) and tools:
            parts.append(json.dumps(tools, sort_keys=True, ensure_ascii=False))
        blob = "\n".join(parts).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def complete_chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        trace_id: str,
        request_id: str,
        thinking: Optional[DeepSeekThinkingCompletionOptions] = None,
        model_override: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> DeepSeekCompletionResult:
        """Call chat completions synchronously (no ``tools``)."""

        payload = self._base_payload(
            messages,
            thinking=thinking,
            model_override=model_override,
            max_tokens=max_tokens,
        )
        decoded = self._post_completion(
            payload,
            trace_id=trace_id,
            request_id=request_id,
            message_count=len(messages),
            tools_present=False,
            thinking_enabled=bool(thinking and thinking.enabled),
        )
        text, reason, reasoning = self._extract_message(decoded)
        return DeepSeekCompletionResult(
            text=text,
            raw_finish_reason=reason,
            reasoning_content=reasoning,
            usage=parse_llm_usage_from_completion_json(decoded),
        )

    def iter_complete_chat(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        trace_id: str,
        request_id: str,
        thinking: Optional[DeepSeekThinkingCompletionOptions] = None,
        model_override: Optional[str] = None,
    ) -> Iterator[DeepSeekStreamDelta]:
        """Stream chat completion chunks (no ``tools``); OpenAI-style SSE ``data:`` lines."""

        payload = self._base_payload(
            messages, thinking=thinking, model_override=model_override, max_tokens=None
        )
        yield from self._post_completion_stream(
            payload,
            trace_id=trace_id,
            request_id=request_id,
            message_count=len(messages),
            tools_present=False,
            thinking_enabled=bool(thinking and thinking.enabled),
        )

    def complete_chat_round(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        trace_id: str,
        request_id: str,
        thinking: Optional[DeepSeekThinkingCompletionOptions] = None,
        model_override: Optional[str] = None,
        dsml_fallback: bool = True,
    ) -> DeepSeekChatRoundResult:
        """Single completion round with ``tools`` (OpenAI function calling shape).

        When ``dsml_fallback`` is True and no native ``tool_calls`` arrive but assistant
        ``content`` embeds a DSML ``<|DSML|tool_calls>`` block, synthesize OpenAI-style
        tool_calls so Agentium can execute tools uniformly.
        """

        payload = self._base_payload(
            messages, thinking=thinking, model_override=model_override, max_tokens=None
        )
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        decoded = self._post_completion(
            payload,
            trace_id=trace_id,
            request_id=request_id,
            message_count=len(messages),
            tools_present=True,
            thinking_enabled=bool(thinking and thinking.enabled),
        )
        usage_snap = parse_llm_usage_from_completion_json(decoded)
        result = self._extract_round(decoded)
        if dsml_fallback and not result.tool_calls:
            blob = (result.text or "").strip()
            if blob:
                inner = extract_dsml_tool_block(blob)
                if inner:
                    synth = dsml_tool_calls_to_openai_tool_calls(inner)
                    if synth:
                        assistant = dict(result.assistant_message)
                        assistant["tool_calls"] = synth
                        return DeepSeekChatRoundResult(
                            text=result.text,
                            tool_calls=synth,
                            assistant_message=assistant,
                            raw_finish_reason=result.raw_finish_reason,
                            reasoning_content=result.reasoning_content,
                            usage=usage_snap,
                        )
        return DeepSeekChatRoundResult(
            text=result.text,
            tool_calls=result.tool_calls,
            assistant_message=result.assistant_message,
            raw_finish_reason=result.raw_finish_reason,
            reasoning_content=result.reasoning_content,
            usage=usage_snap,
        )

    def _base_payload(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        thinking: Optional[DeepSeekThinkingCompletionOptions],
        model_override: Optional[str],
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        model = (model_override or self._model).strip()
        payload: Dict[str, Any] = {"model": model, "messages": list(messages)}
        if thinking is not None and thinking.enabled:
            payload["reasoning_effort"] = thinking.reasoning_effort
            payload["thinking"] = {"type": "enabled"}
        else:
            payload["temperature"] = 0.7
        if max_tokens is not None:
            payload["max_tokens"] = max(1, int(max_tokens))
        return payload

    def _post_completion(
        self,
        payload: Dict[str, Any],
        *,
        trace_id: str,
        request_id: str,
        message_count: int,
        tools_present: bool,
        thinking_enabled: bool,
    ) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self._base_url}/v1/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("X-Trace-Id", trace_id)
        req.add_header("X-Request-Id", request_id)
        if self._prompt_cache_policy is not None:
            cache_key = self._derive_prompt_cache_key(payload)
            est_tokens = max(1, len(body) // 4)
            stats = self._prompt_cache_policy.record_request(cache_key, est_tokens, 0)
            _LOGGER.info(
                "prompt_cache_recorded",
                trace_id=trace_id,
                request_id=request_id,
                cache_key_prefix=cache_key[:16],
                cache_hit=stats.cache_hit,
                cache_input_tokens_saved=stats.input_tokens_saved,
            )
            if self._prompt_cache_http_header:
                req.add_header("X-Agentium-Prompt-Cache-Key", cache_key)
        _LOGGER.info(
            "deepseek_completion_request",
            trace_id=trace_id,
            request_id=request_id,
            url=url,
            model=payload.get("model"),
            message_count=message_count,
            tools_present=tools_present,
            thinking_enabled=thinking_enabled,
            reasoning_effort=payload.get("reasoning_effort"),
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
        if not isinstance(decoded, dict):
            raise DeepSeekChatCompletionError("invalid_json_response")
        return decoded

    def _post_completion_stream(
        self,
        payload: Dict[str, Any],
        *,
        trace_id: str,
        request_id: str,
        message_count: int,
        tools_present: bool,
        thinking_enabled: bool,
    ) -> Iterator[DeepSeekStreamDelta]:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        body = json.dumps(stream_payload, ensure_ascii=False).encode("utf-8")
        url = f"{self._base_url}/v1/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("X-Trace-Id", trace_id)
        req.add_header("X-Request-Id", request_id)
        req.add_header("Accept", "text/event-stream")
        _LOGGER.info(
            "deepseek_completion_stream_request",
            trace_id=trace_id,
            request_id=request_id,
            url=url,
            model=stream_payload.get("model"),
            message_count=message_count,
            tools_present=tools_present,
            thinking_enabled=thinking_enabled,
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2048]
            _LOGGER.warning(
                "deepseek_stream_http_error",
                status=exc.code,
                trace_id=trace_id,
                request_id=request_id,
                detail_prefix=detail[:200],
            )
            raise DeepSeekChatCompletionError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            _LOGGER.warning("deepseek_stream_url_error", trace_id=trace_id, error=str(exc))
            raise DeepSeekChatCompletionError("network_error") from exc
        try:
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line.partition(":")[2].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                choices = obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                ch0 = choices[0]
                if not isinstance(ch0, dict):
                    continue
                finish = ch0.get("finish_reason")
                fr_s = finish if isinstance(finish, str) else None
                delta = ch0.get("delta")
                text_piece = ""
                reason_piece = ""
                if isinstance(delta, dict):
                    c = delta.get("content")
                    if isinstance(c, str) and c:
                        text_piece = c
                    rc = delta.get("reasoning_content")
                    if isinstance(rc, str) and rc:
                        reason_piece = rc
                if text_piece or reason_piece or fr_s:
                    yield DeepSeekStreamDelta(
                        content=text_piece,
                        reasoning=reason_piece,
                        finish_reason=fr_s,
                    )
        finally:
            resp.close()

    @staticmethod
    def _extract_round(decoded: Dict[str, Any]) -> DeepSeekChatRoundResult:
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekChatCompletionError("missing_choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise DeepSeekChatCompletionError("invalid_choice_shape")
        finish = first.get("finish_reason")
        finish_s = finish if isinstance(finish, str) else None
        message = first.get("message")
        if not isinstance(message, dict):
            raise DeepSeekChatCompletionError("missing_message_content")
        tool_calls = message.get("tool_calls")
        normalized: List[Dict[str, Any]] = []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    normalized.append(tc)
        content_raw = message.get("content")
        text_out: Optional[str]
        if isinstance(content_raw, str) and content_raw.strip():
            text_out = content_raw
        else:
            text_out = None
        reasoning_raw = message.get("reasoning_content")
        reasoning_out: Optional[str] = None
        if isinstance(reasoning_raw, str) and reasoning_raw.strip():
            reasoning_out = reasoning_raw.strip()

        assistant_message: Dict[str, Any] = {"role": "assistant"}
        if isinstance(content_raw, str):
            assistant_message["content"] = content_raw
        if reasoning_out is not None:
            assistant_message["reasoning_content"] = reasoning_out
        if normalized:
            assistant_message["tool_calls"] = normalized

        return DeepSeekChatRoundResult(
            text=text_out,
            tool_calls=normalized,
            assistant_message=assistant_message,
            raw_finish_reason=finish_s,
            reasoning_content=reasoning_out,
        )

    @staticmethod
    def _extract_message(decoded: Dict[str, Any]) -> tuple[str, Optional[str], Optional[str]]:
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekChatCompletionError("missing_choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise DeepSeekChatCompletionError("invalid_choice_shape")
        message = first.get("message")
        reasoning_out: Optional[str] = None
        if isinstance(message, dict):
            rc = message.get("reasoning_content")
            if isinstance(rc, str) and rc.strip():
                reasoning_out = rc.strip()
            content = message.get("content")
            if isinstance(content, str):
                finish = first.get("finish_reason")
                return content, finish if isinstance(finish, str) else None, reasoning_out
        alt = decoded.get("output_text") or first.get("text")
        if isinstance(alt, str):
            finish = first.get("finish_reason")
            return alt, finish if isinstance(finish, str) else None, reasoning_out
        raise DeepSeekChatCompletionError("missing_message_content")
