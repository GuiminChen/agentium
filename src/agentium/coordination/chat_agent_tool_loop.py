"""LLM-driven chat tool loop: OpenAI-style tool_calls executed via ControlPlaneAPI.run_turn."""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

import structlog

from agentium.ai_gateway.deepseek_chat import (
    DeepSeekChatCompletionClient,
    DeepSeekChatCompletionError,
    DeepSeekThinkingCompletionOptions,
    LlmUsageSnapshot,
)
from agentium.api.control_plane import ControlPlaneAPI
from agentium.app.settings import AppSettings
from agentium.governance.tool_approval.gate import ToolApprovalGate
from agentium.models.context import RequestContext
from agentium.security.prompt_injection_probe import PromptInjectionProbe
from agentium.tools.tool_registry import ToolRegistry

_LOGGER = structlog.get_logger(__name__)

_UNTRUSTED_TOOL_PREFIXES: tuple[str, ...] = ("web_fetch", "browser_", "http_fetch", "mcp_web")


def aggregate_llm_usage_snapshots(parts: Sequence[Optional[LlmUsageSnapshot]]) -> Optional[LlmUsageSnapshot]:
    """Sum per-round usage snapshots when providers return ``usage`` per completion."""

    sp = sc = st = 0
    has_p = has_c = has_t = False
    for u in parts:
        if u is None:
            continue
        if u.prompt_tokens is not None:
            sp += u.prompt_tokens
            has_p = True
        if u.completion_tokens is not None:
            sc += u.completion_tokens
            has_c = True
        if u.total_tokens is not None:
            st += u.total_tokens
            has_t = True
    if not has_p and not has_c and not has_t:
        return None
    total_out: Optional[int] = st if has_t else None
    if total_out is None and has_p and has_c:
        total_out = sp + sc
    return LlmUsageSnapshot(
        prompt_tokens=sp if has_p else None,
        completion_tokens=sc if has_c else None,
        total_tokens=total_out,
    )


def _latest_user_excerpt(messages: Sequence[Dict[str, Any]], max_chars: int = 4000) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "")
        if text.strip():
            return text[:max_chars]
    return ""


def _wrap_untrusted_tool_payload(tool_name: str, payload_text: str) -> str:
    lowered = tool_name.lower()
    if not any(lowered.startswith(p) for p in _UNTRUSTED_TOOL_PREFIXES):
        return payload_text
    scan = PromptInjectionProbe().scan(f"tool:{tool_name}", payload_text[:50_000])
    banner = (
        "[untrusted_content]\n"
        f"tool={tool_name} pi_risk={scan.risk_level} indicators={scan.indicators}\n---\n"
    )
    return banner + payload_text


class ChatPendingToolApproval(RuntimeError):
    """Raised when chat agent loop hits ``pending_approval`` on a tool execution."""

    def __init__(self, *, approval_id: str, tool_name: str, message: str = "") -> None:
        self.approval_id = approval_id
        self.tool_name = tool_name
        super().__init__(message or "pending_tool_approval")


@dataclass(frozen=True)
class ChatIdentity:
    """Caller identity for RequestContext construction inside chat loops."""

    tenant_id: str
    user_id: str
    role: str


def openai_tools_payload(
    tool_registry: ToolRegistry,
    *,
    name_allowlist: Optional[Sequence[str]] = None,
    description_max_chars: int = 512,
) -> List[Dict[str, Any]]:
    """Map low-risk registry tools to OpenAI ``tools`` entries.

    Args:
        tool_registry: Live registry.
        name_allowlist: When non-empty, restrict to this subset (must already be chat-eligible specs).
        description_max_chars: Truncate per-tool descriptions for provider payloads; logs when shortened.
    """

    allowed_names: Optional[set[str]] = None
    if name_allowlist is not None:
        names = {str(n).strip() for n in name_allowlist if str(n).strip()}
        if names:
            allowed_names = names

    cap = max(32, int(description_max_chars))
    payload: List[Dict[str, Any]] = []
    for spec in tool_registry.list_chat_agent_tool_specs():
        if allowed_names is not None and spec.name not in allowed_names:
            continue
        contract = tool_registry.get_contract(spec.name)
        if contract is not None:
            schema = contract.input_schema
            description = (contract.description or f"Tool `{spec.name}`.").strip()
        else:
            schema = {"type": "object", "additionalProperties": True}
            description = (
                f"Tool `{spec.name}` — send JSON args compatible with control-plane semantics."
            )
        truncated = description[:cap]
        if len(description) > cap:
            _LOGGER.warning(
                "chat_openai_tool_description_truncated",
                tool_name=spec.name,
                original_len=len(description),
                max_chars=cap,
            )
        payload.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": truncated,
                    "parameters": schema,
                },
            }
        )
    return payload


def eligible_base_chat_tool_names(
    tool_registry: ToolRegistry,
    chat_tool_allowlist: Optional[Sequence[str]],
) -> List[str]:
    """Chat-eligible tool names excluding the meta ``tool_search`` tool."""

    specs = tool_registry.list_chat_agent_tool_specs()
    names = [s.name for s in specs if s.name != "tool_search"]
    if chat_tool_allowlist:
        allow = {str(n).strip() for n in chat_tool_allowlist if str(n).strip()}
        if allow:
            names = [n for n in names if n in allow]
    return sorted(names)


def build_openai_tools_for_chat_loop(
    tool_registry: ToolRegistry,
    settings: AppSettings,
    chat_tool_allowlist: Optional[Sequence[str]],
    *,
    deferred_exposed: Optional[Sequence[str]] = None,
) -> tuple[List[Dict[str, Any]], bool, List[str]]:
    """Build OpenAI tool payloads, optionally collapsing to tool_search + Top-K (P1-26)."""

    from agentium.tools.tool_search_index import stable_initial_exposed

    base_names = eligible_base_chat_tool_names(tool_registry, chat_tool_allowlist)
    threshold = settings.chat_tool_defer_loading_threshold
    k = settings.chat_tool_search_max_expose
    cap = max(32, int(settings.chat_tool_description_max_chars))

    if threshold <= 0 or len(base_names) <= threshold:
        allow: Optional[Sequence[str]] = None
        if chat_tool_allowlist:
            filtered = [str(n).strip() for n in chat_tool_allowlist if str(n).strip()]
            if filtered:
                allow = filtered
        payload = openai_tools_payload(
            tool_registry,
            name_allowlist=allow,
            description_max_chars=cap,
        )
        return payload, False, list(base_names)

    has_ts = any(s.name == "tool_search" for s in tool_registry.list_chat_agent_tool_specs())
    if not has_ts:
        _LOGGER.warning(
            "chat_tool_defer_catalog_oversize_missing_tool_search",
            catalog_size=len(base_names),
        )
        allow = None
        if chat_tool_allowlist:
            filtered = [str(n).strip() for n in chat_tool_allowlist if str(n).strip()]
            if filtered:
                allow = filtered
        return openai_tools_payload(
            tool_registry,
            name_allowlist=allow,
            description_max_chars=cap,
        ), False, list(base_names)

    prev = [str(x).strip() for x in (deferred_exposed or []) if str(x).strip()]
    if prev:
        base_set = set(base_names)
        subset = [n for n in prev if n in base_set][:k]
    else:
        subset = stable_initial_exposed(base_names, limit=k)

    ts_payload = openai_tools_payload(
        tool_registry,
        name_allowlist=["tool_search"],
        description_max_chars=cap,
    )
    rest = openai_tools_payload(
        tool_registry,
        name_allowlist=subset,
        description_max_chars=cap,
    )
    return ts_payload + rest, True, subset


def run_chat_agent_tool_loop(
    *,
    deepseek: DeepSeekChatCompletionClient,
    control_plane: ControlPlaneAPI,
    tool_registry: ToolRegistry,
    settings: AppSettings,
    messages: Sequence[Dict[str, Any]],
    trace_id: str,
    base_request_id: str,
    identity: ChatIdentity,
    session_id: str,
    message_disposition: str,
    mcp_execution_tier: str,
    chat_tool_allowlist: Optional[Sequence[str]] = None,
    thinking: Optional[DeepSeekThinkingCompletionOptions] = None,
    model_override: Optional[str] = None,
    dsml_fallback: bool = True,
    steer_drain_hook: Optional[Callable[[], str]] = None,
    tool_approval_gate: Optional[ToolApprovalGate] = None,
) -> tuple[str, List[Dict[str, Any]], str, Optional[str], Optional[LlmUsageSnapshot]]:
    """Execute iterative completions until model stops calling tools or limits hit.

    Args:
        deepseek: Provider client with ``complete_chat_round``.
        control_plane: Facade for ``run_turn``.
        tool_registry: Registry used for OpenAI tool schemas + eligibility filtering.
        settings: Feature flags and ``chat_agent_max_tool_rounds``.
        messages: Initial OpenAI-style messages (must include ``system`` first).
        trace_id: Distributed trace id for upstream headers.
        base_request_id: Prefix for per-round request ids.
        identity: Resolved caller identity.
        session_id: Maps to ``run_id`` / chat session id.
        message_disposition: Ingress disposition mirrored into RequestContext.
        mcp_execution_tier: MCP tier mirrored into RequestContext.
        chat_tool_allowlist: Optional subset of chat-eligible tools exposed to the model.
        thinking: Optional DeepSeek thinking-mode envelope forwarded to the completion client.
        model_override: Optional per-call ``model`` override (OpenAI-compatible field).
        dsml_fallback: When True, parse DSML tool blocks from assistant ``content`` if native
            ``tool_calls`` are absent.

    Returns:
        Tuple of assistant text, structured trace rows, finish_reason string, merged
        ``reasoning_content`` across sub-rounds (when thinking mode returns segments),
        and optional aggregated LLM ``usage`` across completion rounds.

    Raises:
        ChatPendingToolApproval: When governance requires human approval mid-loop.
        DeepSeekChatCompletionError: Provider failures (propagated).
    """

    allow: Optional[Sequence[str]] = None
    if chat_tool_allowlist:
        filtered = [str(n).strip() for n in chat_tool_allowlist if str(n).strip()]
        if filtered:
            allow = filtered

    eligible_base = eligible_base_chat_tool_names(tool_registry, chat_tool_allowlist)
    deferred_subset: List[str] = []
    defer_logged = False

    tools, _, _ = build_openai_tools_for_chat_loop(
        tool_registry,
        settings,
        chat_tool_allowlist,
        deferred_exposed=None,
    )
    merged_reasoning_chunks: List[str] = []

    if not tools:
        result = deepseek.complete_chat(
            list(messages),
            trace_id=trace_id,
            request_id=base_request_id,
            thinking=thinking,
            model_override=model_override,
        )
        rc = (result.reasoning_content or "").strip()
        merged = rc if rc else None
        return result.text.strip(), [], result.raw_finish_reason or "stop", merged, result.usage

    work: List[Dict[str, Any]] = [copy.deepcopy(m) for m in messages]
    trace: List[Dict[str, Any]] = []
    max_rounds = settings.chat_agent_max_tool_rounds
    last_finish = "unknown"
    usage_parts: List[Optional[LlmUsageSnapshot]] = []

    for round_idx in range(max_rounds):
        tools, defer_active, _expose_snapshot = build_openai_tools_for_chat_loop(
            tool_registry,
            settings,
            chat_tool_allowlist,
            deferred_exposed=deferred_subset if deferred_subset else None,
        )
        if defer_active and not defer_logged:
            _LOGGER.info(
                "chat_tool_defer_active",
                catalog_size=len(eligible_base),
                threshold=settings.chat_tool_defer_loading_threshold,
                max_expose=settings.chat_tool_search_max_expose,
            )
            defer_logged = True
        rid = f"{base_request_id}-chat-ag-{round_idx}"
        round_result = deepseek.complete_chat_round(
            work,
            tools=tools,
            trace_id=trace_id,
            request_id=rid,
            thinking=thinking,
            model_override=model_override,
            dsml_fallback=dsml_fallback,
        )
        usage_parts.append(round_result.usage)
        last_finish = round_result.raw_finish_reason or "stop"
        rc_round = (round_result.reasoning_content or "").strip()
        if rc_round:
            merged_reasoning_chunks.append(rc_round)
        if round_result.tool_calls:
            assistant_msg = copy.deepcopy(round_result.assistant_message)
            work.append(assistant_msg)
            excerpt = _latest_user_excerpt(work)
            auto_denies = 0
            for tc in round_result.tool_calls:
                tc_id = str(tc.get("id") or "")
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or "").strip()
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    args_raw = raw_args.strip() or "{}"
                else:
                    args_raw = json.dumps(raw_args or {}, ensure_ascii=False)
                try:
                    args = json.loads(args_raw)
                    if not isinstance(args, dict):
                        args = {"value": args}
                except json.JSONDecodeError:
                    args = {"_raw_arguments": args_raw[:2048]}
                if tool_approval_gate is not None:
                    eval_allow = allow
                    if eval_allow is not None and defer_active:
                        eval_allow = tuple(sorted({*eval_allow, "tool_search"}))
                    decision = tool_approval_gate.evaluate(
                        user_message_excerpt=excerpt,
                        tool_name=name,
                        arguments=args,
                        tool_allowlist=eval_allow,
                        trace_id=trace_id,
                        request_id=rid,
                    )
                    trace.append(
                        {
                            "tool_name": name,
                            "status": "approval_gate",
                            "approval_verdict": decision.verdict,
                            "reason_code": decision.reason_code,
                            "classifier_stage": decision.classifier_stage,
                        }
                    )
                    if decision.verdict == "pending_human":
                        raise ChatPendingToolApproval(
                            approval_id=f"tool_appr:{decision.reason_code}",
                            tool_name=name,
                            message="tool_approval_pending_human",
                        )
                    if decision.verdict == "deny":
                        auto_denies += 1
                        if auto_denies > settings.tool_approval_max_auto_denies_per_turn:
                            raise ChatPendingToolApproval(
                                approval_id="auto_deny_budget_exhausted",
                                tool_name=name,
                                message="tool_approval_deny_budget",
                            )
                        work.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id or f"call-{uuid.uuid4()}",
                                "content": json.dumps(
                                    {
                                        "status": "blocked",
                                        "reason_code": decision.reason_code,
                                        "tool_name": name,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                        continue
                ctx = RequestContext(
                    request_id=f"{rid}-tool-{uuid.uuid4()}",
                    run_id=session_id,
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    trace_id=trace_id,
                    role=identity.role,
                    deployment_mode="prod",
                    message_disposition=message_disposition,  # type: ignore[arg-type]
                    mcp_execution_tier=mcp_execution_tier,  # type: ignore[arg-type]
                    chat_session_id=session_id,
                )
                resp = control_plane.run_turn(context=ctx, tool_name=name, args=args)
                trace.append(
                    {
                        "tool_name": name,
                        "status": resp.status,
                        "approval_id": resp.approval_id,
                        "error_code": resp.error_code,
                    }
                )
                if defer_active and name == "tool_search" and resp.status == "completed":
                    out = resp.output
                    if isinstance(out, dict):
                        hits = out.get("hits")
                        if isinstance(hits, list):
                            base_set = set(eligible_base)
                            picked: List[str] = []
                            for h in hits:
                                if isinstance(h, dict):
                                    nm = str(h.get("name", "")).strip()
                                    if nm in base_set and nm not in picked:
                                        picked.append(nm)
                                if len(picked) >= settings.chat_tool_search_max_expose:
                                    break
                            if picked:
                                deferred_subset = picked
                                _LOGGER.info("chat_tool_search_refresh", exposed=len(picked))
                if resp.status == "pending_approval":
                    raise ChatPendingToolApproval(
                        approval_id=str(resp.approval_id or ""),
                        tool_name=name,
                        message="pending_tool_approval",
                    )
                payload_text = _wrap_untrusted_tool_payload(
                    name,
                    json.dumps(resp.model_dump(mode="json", exclude_none=True), ensure_ascii=False)[
                        :12000
                    ],
                )
                work.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id or f"call-{uuid.uuid4()}",
                        "content": payload_text,
                    }
                )
            steer_extra = (steer_drain_hook() if steer_drain_hook is not None else "").strip()
            if steer_extra:
                work.append({"role": "user", "content": f"[disposition=steer] {steer_extra}"})
            continue

        text_out = (round_result.text or "").strip()
        merged = "\n\n---\n\n".join(merged_reasoning_chunks) if merged_reasoning_chunks else None
        return text_out, trace, last_finish, merged, aggregate_llm_usage_snapshots(usage_parts)

    merged = "\n\n---\n\n".join(merged_reasoning_chunks) if merged_reasoning_chunks else None
    return (
        "Tool loop stopped: maximum chat agent tool rounds exceeded.",
        trace,
        "length",
        merged,
        aggregate_llm_usage_snapshots(usage_parts),
    )
