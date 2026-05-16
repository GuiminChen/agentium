"""Orchestrate TradeAgent-style chat turns: persist rows, call DeepSeek, audit hooks."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Sequence

import structlog

from agentium.ai_gateway.deepseek_chat import (
    DeepSeekChatCompletionClient,
    DeepSeekChatCompletionError,
    DeepSeekCompletionResult,
    DeepSeekStreamDelta,
    DeepSeekThinkingCompletionOptions,
    LlmUsageSnapshot,
)
from agentium.ai_gateway.deepseek_v4_agent.dsml import (
    build_dsml_tool_system_addon,
    format_tool_definitions_markdown,
)
from agentium.ai_gateway.deepseek_v4_agent.model_gate import is_deepseek_v4_series_model
from agentium.ai_gateway.deepseek_v4_agent.think_max import THINK_MAX_SYSTEM_INSTRUCTION
from agentium.ai_gateway.deepseek_v4_agent.thinking import normalize_reasoning_effort
from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.chat_schemas import WorkspaceAgentConfig
from agentium.app.settings import AppSettings
from agentium.coordination.deferred_tasks import (
    DeferredTaskSink,
    KIND_CHAT_GENERATE_SESSION_TITLE,
    LANE_CHAT,
)
from agentium.coordination.chat_agent_tool_loop import (
    ChatIdentity,
    ChatPendingToolApproval,
    openai_tools_payload,
    run_chat_agent_tool_loop,
)
from agentium.coordination.chat_ingress import ChatIngressCoordinator
from agentium.coordination.chat_ingress.exceptions import ChatIngressDeferred
from agentium.coordination.chat_ingress.types import session_ingress_key
from agentium.coordination.chat_ingress_classification import classify_chat_ingress
from agentium.governance.tool_approval.gate import ToolApprovalGate
from agentium.coordination.chat_mid_semantic_memory import (
    extract_mid_term_memories,
    extract_session_running_summary,
    normalize_fact_key,
    stable_fact_hash,
    stable_user_fact_hash,
)
from agentium.memory.chat_memory_lane_router import ChatMemoryLaneRouter
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_store import SqliteRunMessageStore
from agentium.memory.memory_service import MemoryService
from agentium.memory.types import MemoryLayer
from agentium.models.context import AuditRecord, RequestContext
from agentium.shared.chat_timeline import CHAT_KIND_ASSISTANT, CHAT_KIND_USER
from agentium.tools.tool_registry import ToolRegistry

_LOGGER = structlog.get_logger(__name__)


class AuditAppendFn(Protocol):
    """Sink for non-blocking chat audit events."""

    def __call__(self, record: AuditRecord) -> None:
        ...


SkillAddonFn = Callable[[str], str]


@dataclass(frozen=True)
class ChatSendOutcome:
    """HTTP-facing result for one ``POST /v1/chat/messages`` call."""

    message_id: str
    content_blocks: List[Dict[str, Any]]
    answer_preview: str  # full assistant text (JSON field ``answer``); name kept for callers
    status: str
    tool_trace: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None
    llm_prompt_tokens: Optional[int] = None
    llm_completion_tokens: Optional[int] = None
    llm_total_tokens: Optional[int] = None


@dataclass(frozen=True)
class ChatLlmTurnContext:
    """Intermediate state after user ingress and history assembly (before assistant completion)."""

    tenant_id: str
    session_id: str
    user_id: str
    caller_role: str
    trace_id: str
    request_id: str
    pair_id: str
    public_message_id: str
    working_content: str
    effective_disp: str
    effective_tier: str
    model_override: Optional[str]
    effective_completion_model: str
    use_v4_adapter: bool
    use_tools: bool
    thinking_opts: Optional[DeepSeekThinkingCompletionOptions]
    history: List[Dict[str, Any]]
    chat_tool_allowlist_arg: Optional[List[str]]


class ChatTurnService:
    """Kernel-adjacent service: maps chat intents to persisted timeline + outbound LLM."""

    def __init__(
        self,
        *,
        run_message_store: SqliteRunMessageStore,
        chat_session_store: SqliteChatSessionStore,
        deepseek_client: Optional[DeepSeekChatCompletionClient],
        audit_sink: Optional[Callable[[AuditRecord], None]],
        skill_addon: SkillAddonFn,
        settings: AppSettings,
        control_plane_api: Optional[ControlPlaneAPI] = None,
        tool_registry: Optional[ToolRegistry] = None,
        memory_lane_router: Optional[ChatMemoryLaneRouter] = None,
        memory_service: Optional[MemoryService] = None,
        deferred_task_sink: Optional[DeferredTaskSink] = None,
        ingress_coordinator: Optional[ChatIngressCoordinator] = None,
        tool_approval_gate: Optional[ToolApprovalGate] = None,
    ) -> None:
        self._messages = run_message_store
        self._sessions = chat_session_store
        self._deepseek = deepseek_client
        self._audit = audit_sink
        self._skill_addon = skill_addon
        self._settings = settings
        self._control_plane = control_plane_api
        self._tool_registry = tool_registry
        self._memory_router = memory_lane_router
        self._memory_legacy = memory_service
        self._deferred_task_sink = deferred_task_sink
        self._ingress = ingress_coordinator
        self._tool_approval_gate = tool_approval_gate

    @staticmethod
    def _resolved_orchestration_mode(metadata: Optional[Dict[str, Any]]) -> str:
        """Normalize stored session metadata to a known orchestration label."""

        raw = (metadata or {}).get("orchestration_mode")
        if raw in ("workflow", "agentic", "research"):
            return raw  # type: ignore[return-value]
        return "agentic"

    def _log_chat_turn_begin(
        self,
        *,
        tenant_id: str,
        session_id: str,
        trace_id: str,
        request_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
        enable_agent_tools: bool,
    ) -> None:
        row = self._sessions.try_get_session(tenant_id=tenant_id, session_id=session_id)
        md = dict(row.metadata) if row is not None else {}
        orch = self._resolved_orchestration_mode(md)
        policy_pack = md.get("policy_pack_id")
        _LOGGER.info(
            "chat_turn_begin",
            tenant_id=tenant_id,
            session_id=session_id,
            trace_id=trace_id,
            request_id=request_id,
            orchestration_mode=orch,
            policy_pack_id=policy_pack,
            enable_agent_tools=enable_agent_tools,
            mcp_execution_tier=mcp_execution_tier,
            message_disposition=message_disposition,
        )

    def _memory_for_chat(self, *, tenant_id: str, session_id: str) -> Optional[MemoryService]:
        if self._memory_router is not None:
            return self._memory_router.resolve(tenant_id=tenant_id, session_id=session_id)
        return self._memory_legacy

    @staticmethod
    def _preview_effective_disposition_for_ingress(
        content: str,
        message_disposition: str,
        mcp_execution_tier: str,
        auto_ingress: bool,
        regenerate_from_message_id: Optional[str],
    ) -> tuple[str, str, str]:
        """Classify disposition without touching stores (regenerate skips auto-ingress here)."""

        if (regenerate_from_message_id or "").strip():
            return "", message_disposition, mcp_execution_tier
        working = (content or "").strip()
        eff_disp = message_disposition
        eff_tier = mcp_execution_tier
        if auto_ingress:
            eff_disp, eff_tier = classify_chat_ingress(working)
        return working, eff_disp, eff_tier

    def _ingress_begin_or_raise(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        content: str,
        trace_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
        llm_model: Optional[str],
        agent_skill_override: Optional[str],
        enable_agent_tools: bool,
        deepseek_thinking_enabled: Optional[bool],
        deepseek_reasoning_effort: Optional[str],
        auto_ingress: bool,
        regenerate_from_message_id: Optional[str],
        ingress_from_drain: bool,
        ingress_bypass_collect: bool,
    ) -> tuple[str, Optional[str], str]:
        """Return ``(admission_working, lease_token, session_key)`` or raise :class:`ChatIngressDeferred`."""

        sk = session_ingress_key(tenant_id, session_id)
        preview_working, eff_disp, _ = self._preview_effective_disposition_for_ingress(
            content,
            message_disposition,
            mcp_execution_tier,
            auto_ingress,
            regenerate_from_message_id,
        )
        regen = bool((regenerate_from_message_id or "").strip())
        admission_working = (content or "").strip()
        lease_token: Optional[str] = None
        if self._ingress is None:
            return admission_working, None, sk

        followup_blob: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "user_id": user_id,
            "caller_role": caller_role,
            "content": content,
            "trace_id": trace_id,
            "message_disposition": message_disposition,
            "mcp_execution_tier": mcp_execution_tier,
            "llm_model": llm_model,
            "agent_skill_override": agent_skill_override,
            "enable_agent_tools": enable_agent_tools,
            "deepseek_thinking_enabled": deepseek_thinking_enabled,
            "deepseek_reasoning_effort": deepseek_reasoning_effort,
            "auto_ingress": auto_ingress,
            "regenerate_from_message_id": regenerate_from_message_id,
        }

        def _collect_flush(_sk_inner: str, merged: str) -> None:
            self.send_user_message(
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=user_id,
                caller_role=caller_role,
                content=merged,
                trace_id=str(uuid.uuid4()),
                message_disposition="collect",
                mcp_execution_tier=mcp_execution_tier,
                request_id=str(uuid.uuid4()),
                llm_model=llm_model,
                agent_skill_override=agent_skill_override,
                enable_agent_tools=enable_agent_tools,
                deepseek_thinking_enabled=deepseek_thinking_enabled,
                deepseek_reasoning_effort=deepseek_reasoning_effort,
                auto_ingress=False,
                regenerate_from_message_id=None,
                ingress_from_drain=False,
                ingress_bypass_collect=True,
            )

        admission = self._ingress.admit_user_turn(
            session_key=sk,
            effective_disposition=eff_disp,
            working_content=preview_working if not regen else admission_working,
            regenerate=regen,
            followup_payload=followup_blob,
            collect_flush=_collect_flush if not ingress_bypass_collect else None,
            from_drain=ingress_from_drain,
            bypass_collect_buffer=ingress_bypass_collect,
        )
        return admission.working_content, admission.lease_token, sk

    def _maybe_enqueue_auto_session_title(
        self,
        *,
        ctx: ChatLlmTurnContext,
        assistant_preview: str,
    ) -> None:
        """After first assistant turn completes, enqueue async LLM title when configured."""

        if self._deferred_task_sink is None or not self._settings.chat_auto_session_title_enabled:
            return
        count_asst = self._messages.count_chat_assistant_rows(
            run_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
        )
        if count_asst != 1:
            return
        row = self._sessions.try_get_session(tenant_id=ctx.tenant_id, session_id=ctx.session_id)
        if row is None:
            return
        md = dict(row.metadata or {})
        if md.get("session_title_source") == "user":
            return
        status = str(md.get("session_title_auto_status") or "")
        if status in {"scheduled", "complete"}:
            return
        try:
            self._sessions.merge_session_metadata(
                tenant_id=ctx.tenant_id,
                session_id=ctx.session_id,
                patch={"session_title_auto_status": "scheduled"},
            )
        except KeyError:
            return
        self._deferred_task_sink.enqueue(
            KIND_CHAT_GENERATE_SESSION_TITLE,
            {
                "tenant_id": ctx.tenant_id,
                "session_id": ctx.session_id,
                "user_excerpt": (ctx.working_content or "").strip(),
                "assistant_excerpt": (assistant_preview or "").strip(),
            },
            lane=LANE_CHAT,
        )

    @staticmethod
    def _openai_tools_to_dsml_specs(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for item in tools:
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            specs.append(
                {
                    "name": name,
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters"),
                }
            )
        return specs

    def _resolve_deepseek_thinking(
        self,
        *,
        thinking_enabled_override: Optional[bool],
        reasoning_effort_override: Optional[str],
    ) -> Optional[DeepSeekThinkingCompletionOptions]:
        enabled = (
            self._settings.deepseek_thinking_enabled
            if thinking_enabled_override is None
            else thinking_enabled_override
        )
        if not enabled:
            return None
        raw_effort = (
            self._settings.deepseek_reasoning_effort
            if reasoning_effort_override is None
            else reasoning_effort_override
        )
        api_effort = normalize_reasoning_effort(raw_effort)
        return DeepSeekThinkingCompletionOptions(enabled=True, reasoning_effort=api_effort)

    def _prepare_llm_turn_context(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        content: str,
        trace_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
        request_id: str,
        llm_model: Optional[str],
        agent_skill_override: Optional[str] = None,
        enable_agent_tools: bool = False,
        deepseek_thinking_enabled: Optional[bool] = None,
        deepseek_reasoning_effort: Optional[str] = None,
        auto_ingress: bool = False,
        regenerate_from_message_id: Optional[str] = None,
    ) -> ChatLlmTurnContext:
        """Persist user row, emit ingress audit, and assemble chat history."""

        model_override = (llm_model or "").strip() or None
        effective_completion_model = (model_override or self._settings.chat_completion_model).strip()
        use_v4_adapter = is_deepseek_v4_series_model(effective_completion_model)

        regen_mid = (regenerate_from_message_id or "").strip()
        working_content = (content or "").strip()
        skip_user_append = False

        if regen_mid:
            deleted = self._messages.delete_latest_chat_assistant_for_message_id(
                run_id=session_id,
                tenant_id=tenant_id,
                message_id=regen_mid,
            )
            if deleted is None:
                raise KeyError("regenerate_target_not_found")
            user_snapshot = self._messages.fetch_chat_user_body_for_pair(
                run_id=session_id,
                tenant_id=tenant_id,
                message_pair_id=regen_mid,
            )
            if user_snapshot is None:
                raise KeyError("regenerate_user_missing")
            working_content = str(user_snapshot.get("content") or "").strip()
            if not working_content:
                raise ValueError("regenerate_empty_user_content")
            pair_id = regen_mid
            public_message_id = regen_mid
            skip_user_append = True
        else:
            pair_id = str(uuid.uuid4())
            public_message_id = pair_id

        effective_disp = message_disposition
        effective_tier = mcp_execution_tier
        if auto_ingress:
            effective_disp, effective_tier = classify_chat_ingress(working_content)

        if not skip_user_append:
            user_body: Dict[str, Any] = {
                "message_pair_id": pair_id,
                "message_id": public_message_id,
                "content": working_content,
                "message_disposition": effective_disp,
                "mcp_execution_tier": effective_tier,
                "request_id": request_id,
                "user_id": user_id,
            }
            self._messages.append(
                run_id=session_id,
                tenant_id=tenant_id,
                role="user",
                kind=CHAT_KIND_USER,
                body=user_body,
                request_id=request_id,
            )
            self._persist_chat_memory_snippet(
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=user_id,
                caller_role=caller_role,
                trace_id=trace_id,
                request_id=request_id,
                pair_id=pair_id,
                role_label="user",
                preview=working_content,
            )
        self._emit_audit(
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            event_type="chat_message_ingress",
            payload={
                "message_disposition": effective_disp,
                "mcp_execution_tier": effective_tier,
                "client_message_disposition": message_disposition,
                "client_mcp_execution_tier": mcp_execution_tier,
                "auto_ingress": auto_ingress,
                "regenerate": bool(regen_mid),
                "content_len": len(working_content),
                "llm_model_requested": llm_model,
                "enable_agent_tools": enable_agent_tools,
                "deepseek_thinking_enabled_override": deepseek_thinking_enabled,
                "deepseek_reasoning_effort_override": deepseek_reasoning_effort,
                "effective_llm_model": effective_completion_model,
                "deepseek_v4_adapter": use_v4_adapter,
            },
        )
        session_row = self._sessions.try_get_session(tenant_id=tenant_id, session_id=session_id)
        workspace_agent: Optional[WorkspaceAgentConfig] = None
        if session_row is not None:
            raw_wa = session_row.metadata.get("workspace_agent")
            if isinstance(raw_wa, dict):
                try:
                    workspace_agent = WorkspaceAgentConfig.model_validate(raw_wa)
                except Exception:
                    workspace_agent = None

        override = (agent_skill_override or "").strip()
        primary_skill = ""
        if override:
            primary_skill = override
        elif workspace_agent is not None and workspace_agent.skill_tags:
            primary_skill = workspace_agent.skill_tags[0]
        elif session_row is not None:
            primary_skill = (session_row.skill or "").strip()

        extra_skill_tags: List[str] = []
        if workspace_agent is not None and len(workspace_agent.skill_tags) > 1:
            for tag in workspace_agent.skill_tags[1:]:
                t = (tag or "").strip()
                if t and t != primary_skill:
                    extra_skill_tags.append(t)

        chat_tool_allowlist_arg: Optional[List[str]] = None
        if workspace_agent is not None and workspace_agent.chat_tool_allowlist:
            chat_tool_allowlist_arg = list(workspace_agent.chat_tool_allowlist)

        use_tools = (
            enable_agent_tools
            and self._settings.chat_agent_tools_enabled
            and self._control_plane is not None
            and self._tool_registry is not None
        )

        thinking_opts = self._resolve_deepseek_thinking(
            thinking_enabled_override=deepseek_thinking_enabled,
            reasoning_effort_override=deepseek_reasoning_effort,
        )
        if not use_v4_adapter:
            thinking_opts = None

        system_suffix_parts: List[str] = []
        if (
            use_v4_adapter
            and thinking_opts is not None
            and thinking_opts.reasoning_effort == "max"
            and self._settings.deepseek_inject_think_max_instruction
        ):
            system_suffix_parts.append(THINK_MAX_SYSTEM_INSTRUCTION.rstrip())

        if (
            use_v4_adapter
            and use_tools
            and self._settings.deepseek_dsml_tool_prompt_enabled
            and self._tool_registry is not None
        ):
            tools_payload = openai_tools_payload(
                self._tool_registry,
                name_allowlist=chat_tool_allowlist_arg,
                description_max_chars=self._settings.chat_tool_description_max_chars,
            )
            specs = self._openai_tools_to_dsml_specs(tools_payload)
            if specs:
                system_suffix_parts.append(
                    build_dsml_tool_system_addon(format_tool_definitions_markdown(specs)).rstrip()
                )

        system_prompt_suffix = "\n\n".join(p for p in system_suffix_parts if p.strip())

        history = self._build_openai_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            primary_skill=primary_skill,
            extra_skill_tags=extra_skill_tags,
            workspace_agent=workspace_agent,
            system_prompt_suffix=system_prompt_suffix,
        )

        return ChatLlmTurnContext(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            caller_role=caller_role,
            trace_id=trace_id,
            request_id=request_id,
            pair_id=pair_id,
            public_message_id=public_message_id,
            working_content=working_content,
            effective_disp=effective_disp,
            effective_tier=effective_tier,
            model_override=model_override,
            effective_completion_model=effective_completion_model,
            use_v4_adapter=use_v4_adapter,
            use_tools=use_tools,
            thinking_opts=thinking_opts,
            history=history,
            chat_tool_allowlist_arg=chat_tool_allowlist_arg,
        )

    def _finalize_chat_turn_with_result(
        self,
        ctx: ChatLlmTurnContext,
        result: DeepSeekCompletionResult,
        *,
        tool_trace: Optional[List[Dict[str, Any]]],
        llm_usage: Optional[LlmUsageSnapshot] = None,
    ) -> ChatSendOutcome:
        """Persist assistant row, audits, memory; return outbound payload."""

        usage_eff = llm_usage if llm_usage is not None else result.usage
        blocks, preview = self._to_content_blocks(result)
        assistant_body: Dict[str, Any] = {
            "message_pair_id": ctx.pair_id,
            "message_id": ctx.public_message_id,
            "content_blocks": blocks,
            "answer": preview,
            "status": "finished",
        }
        if tool_trace:
            assistant_body["chat_agent_tool_trace"] = tool_trace
        rc_store = (result.reasoning_content or "").strip()
        if rc_store:
            assistant_body["reasoning_content"] = rc_store
        self._messages.append(
            run_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            role="assistant",
            kind=CHAT_KIND_ASSISTANT,
            body=assistant_body,
            status="finished",
            request_id=ctx.request_id,
        )
        self._sessions.touch_updated_at(tenant_id=ctx.tenant_id, session_id=ctx.session_id)
        reasoning_audit = rc_store
        self._emit_audit(
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            event_type="chat_message_completed",
            payload={
                "answer_len": len(preview),
                "finish_reason": result.raw_finish_reason,
                "chat_agent_tools": bool(tool_trace),
                "reasoning_content_len": len(reasoning_audit),
                "reasoning_content": reasoning_audit,
                "effective_llm_model": ctx.effective_completion_model,
                "deepseek_v4_adapter": ctx.use_v4_adapter,
                "message_disposition": ctx.effective_disp,
                "mcp_execution_tier": ctx.effective_tier,
            },
        )
        self._persist_chat_memory_snippet(
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            caller_role=ctx.caller_role,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            pair_id=ctx.pair_id,
            role_label="assistant",
            preview=preview,
        )
        self._persist_mid_semantic_memories(
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            caller_role=ctx.caller_role,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            pair_id=ctx.pair_id,
            user_turn_text=ctx.working_content,
            assistant_turn_text=preview,
            model_override=ctx.model_override,
        )
        self._persist_session_running_summary(
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            caller_role=ctx.caller_role,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
            pair_id=ctx.pair_id,
            user_turn_text=ctx.working_content,
            assistant_turn_text=preview,
            model_override=ctx.model_override,
        )
        self._maybe_enqueue_auto_session_title(ctx=ctx, assistant_preview=preview)
        return ChatSendOutcome(
            message_id=ctx.public_message_id,
            content_blocks=blocks,
            answer_preview=preview,
            status="finished",
            tool_trace=tool_trace,
            reasoning_content=rc_store if rc_store else None,
            llm_prompt_tokens=usage_eff.prompt_tokens if usage_eff else None,
            llm_completion_tokens=usage_eff.completion_tokens if usage_eff else None,
            llm_total_tokens=usage_eff.total_tokens if usage_eff else None,
        )

    def iter_send_user_message_sse(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        content: str,
        trace_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
        request_id: str,
        llm_model: Optional[str],
        agent_skill_override: Optional[str] = None,
        enable_agent_tools: bool = False,
        deepseek_thinking_enabled: Optional[bool] = None,
        deepseek_reasoning_effort: Optional[str] = None,
        auto_ingress: bool = False,
        regenerate_from_message_id: Optional[str] = None,
        ingress_from_drain: bool = False,
        ingress_bypass_collect: bool = False,
    ) -> Iterator[Dict[str, Any]]:
        """Yield SSE-shaped dicts for ``POST /v1/chat/messages`` with ``stream=true`` (tool loop unsupported)."""

        if not self._sessions.session_exists(tenant_id=tenant_id, session_id=session_id):
            raise KeyError("session_not_found")
        if self._deepseek is None:
            raise RuntimeError("deepseek_not_configured")

        self._log_chat_turn_begin(
            tenant_id=tenant_id,
            session_id=session_id,
            trace_id=trace_id,
            request_id=request_id,
            message_disposition=message_disposition,
            mcp_execution_tier=mcp_execution_tier,
            enable_agent_tools=enable_agent_tools,
        )

        admission_working, lease_token, sk = self._ingress_begin_or_raise(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            caller_role=caller_role,
            content=content,
            trace_id=trace_id,
            message_disposition=message_disposition,
            mcp_execution_tier=mcp_execution_tier,
            llm_model=llm_model,
            agent_skill_override=agent_skill_override,
            enable_agent_tools=enable_agent_tools,
            deepseek_thinking_enabled=deepseek_thinking_enabled,
            deepseek_reasoning_effort=deepseek_reasoning_effort,
            auto_ingress=auto_ingress,
            regenerate_from_message_id=regenerate_from_message_id,
            ingress_from_drain=ingress_from_drain,
            ingress_bypass_collect=ingress_bypass_collect,
        )

        ctx = self._prepare_llm_turn_context(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            caller_role=caller_role,
            content=admission_working,
            trace_id=trace_id,
            message_disposition=message_disposition,
            mcp_execution_tier=mcp_execution_tier,
            request_id=request_id,
            llm_model=llm_model,
            agent_skill_override=agent_skill_override,
            enable_agent_tools=enable_agent_tools,
            deepseek_thinking_enabled=deepseek_thinking_enabled,
            deepseek_reasoning_effort=deepseek_reasoning_effort,
            auto_ingress=auto_ingress,
            regenerate_from_message_id=regenerate_from_message_id,
        )

        if ctx.use_tools:
            raise ValueError("stream_with_tools_unsupported")

        yield {
            "event": "start",
            "message_id": ctx.public_message_id,
            "session_id": ctx.session_id,
            "effective_message_disposition": ctx.effective_disp,
            "client_message_disposition": message_disposition,
            "mcp_execution_tier": ctx.effective_tier,
            "ingress_active": bool(self._ingress),
        }

        acc_text: List[str] = []
        acc_reason: List[str] = []
        last_finish: Optional[str] = None
        delta_count = 0
        try:
            for delta in self._deepseek.iter_complete_chat(
                ctx.history,
                trace_id=ctx.trace_id,
                request_id=ctx.request_id,
                thinking=ctx.thinking_opts,
                model_override=ctx.model_override,
            ):
                if delta.content:
                    acc_text.append(delta.content)
                    yield {"event": "delta", "text": delta.content}
                if delta.reasoning:
                    acc_reason.append(delta.reasoning)
                    yield {"event": "reasoning_delta", "text": delta.reasoning}
                if delta.finish_reason:
                    last_finish = delta.finish_reason
                delta_count += 1
                if (
                    self._ingress is not None
                    and lease_token is not None
                    and delta_count % 20 == 0
                ):
                    self._ingress.renew_lease_for_stream(session_key=sk, lease_token=lease_token)
        except DeepSeekChatCompletionError as exc:
            if self._ingress is not None and lease_token is not None:
                self._ingress.renew_or_release_after_error(session_key=sk, lease_token=lease_token)
            yield {"event": "error", "code": "upstream_llm_failed", "message": str(exc)}
            return

        full_reasoning = "".join(acc_reason).strip()
        result = DeepSeekCompletionResult(
            text="".join(acc_text),
            raw_finish_reason=last_finish,
            reasoning_content=full_reasoning if full_reasoning else None,
        )
        try:
            outcome = self._finalize_chat_turn_with_result(ctx, result, tool_trace=None)
        except BaseException:
            if self._ingress is not None and lease_token is not None:
                self._ingress.renew_or_release_after_error(session_key=sk, lease_token=lease_token)
            raise

        done_payload: Dict[str, Any] = {
            "event": "done",
            "message_id": outcome.message_id,
            "status": outcome.status,
            "content_blocks": outcome.content_blocks,
            "answer": outcome.answer_preview,
        }
        if outcome.reasoning_content:
            done_payload["reasoning_content"] = outcome.reasoning_content
        yield done_payload

        if self._ingress is not None and lease_token is not None:

            def _drain_one(payload: Dict[str, Any]) -> None:
                rid = str(uuid.uuid4())
                tid = str(payload.get("tenant_id") or tenant_id)
                sid = str(payload.get("session_id") or session_id)
                self.send_user_message(
                    tenant_id=tid,
                    session_id=sid,
                    user_id=str(payload.get("user_id") or user_id),
                    caller_role=str(payload.get("caller_role") or caller_role),
                    content=str(payload.get("content") or ""),
                    trace_id=str(payload.get("trace_id") or trace_id),
                    message_disposition=str(payload.get("message_disposition") or "collect"),
                    mcp_execution_tier=str(payload.get("mcp_execution_tier") or "direct-tool"),
                    request_id=rid,
                    llm_model=payload.get("llm_model") if payload.get("llm_model") else None,
                    agent_skill_override=(
                        str(payload["agent_skill_override"]).strip()
                        if payload.get("agent_skill_override")
                        else None
                    ),
                    enable_agent_tools=bool(payload.get("enable_agent_tools") is True),
                    deepseek_thinking_enabled=(
                        payload.get("deepseek_thinking_enabled")
                        if "deepseek_thinking_enabled" in payload
                        else None
                    ),
                    deepseek_reasoning_effort=(
                        str(payload["deepseek_reasoning_effort"])
                        if payload.get("deepseek_reasoning_effort")
                        else None
                    ),
                    auto_ingress=bool(payload.get("auto_ingress") is True),
                    regenerate_from_message_id=(
                        str(payload["regenerate_from_message_id"]).strip()
                        if payload.get("regenerate_from_message_id")
                        else None
                    ),
                    ingress_from_drain=True,
                    ingress_bypass_collect=False,
                )

            self._ingress.finish_turn_release_and_drain(
                session_key=sk,
                lease_token=lease_token,
                drain_fn=_drain_one,
            )

    def send_user_message(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        content: str,
        trace_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
        request_id: str,
        llm_model: Optional[str],
        agent_skill_override: Optional[str] = None,
        enable_agent_tools: bool = False,
        deepseek_thinking_enabled: Optional[bool] = None,
        deepseek_reasoning_effort: Optional[str] = None,
        auto_ingress: bool = False,
        regenerate_from_message_id: Optional[str] = None,
        ingress_from_drain: bool = False,
        ingress_bypass_collect: bool = False,
    ) -> ChatSendOutcome:
        """Append user assistant exchange; requires active session and configured DeepSeek client.

        Args:
            tenant_id: Tenant isolation key.
            session_id: Chat session id (MVP maps to ``run_id`` in message store).
            user_id: Caller identity key.
            caller_role: Primary role string for nested ``run_turn`` contexts.
            content: User-visible message body sent to the model after history assembly.
            trace_id: Trace correlation id for audit.
            message_disposition: Ingress disposition forwarded into audit and persisted user row.
            mcp_execution_tier: MCP path grade for observability (audit + user row).
            request_id: Unique id for this HTTP submission.
            llm_model: Optional model hint from client.
            agent_skill_override: Optional per-message skill tag overriding session skill for prompts.
            enable_agent_tools: Client asks for chat tool loop when server policy allows.
            deepseek_thinking_enabled: When set, overrides default thinking toggle for this turn
                (sent on the wire only for ``deepseek-v4-*`` completion models).
            deepseek_reasoning_effort: When set, overrides reasoning-effort string for this turn
                (applied only when the resolved completion model is a ``deepseek-v4-*`` id).
            auto_ingress: When true, derive disposition and MCP tier from ``content`` (after regen load).
            regenerate_from_message_id: When set, delete last assistant row for this id and reuse user text.

        Returns:
            ChatSendOutcome: Assistant-facing identifiers and rendered blocks for HTTP.

        Raises:
            KeyError: ``session_not_found`` when chat session row missing.
            KeyError: ``regenerate_target_not_found`` / ``regenerate_user_missing`` for bad regen ids.
            ValueError: Empty stored user content on regenerate.
            RuntimeError: ``deepseek_not_configured`` when LLM client absent.
            DeepSeekChatCompletionError: Upstream LLM failures.
            ChatPendingToolApproval: Tool execution paused for human approval (caller maps to HTTP).
        """

        if not self._sessions.session_exists(tenant_id=tenant_id, session_id=session_id):
            raise KeyError("session_not_found")
        if self._deepseek is None:
            raise RuntimeError("deepseek_not_configured")

        self._log_chat_turn_begin(
            tenant_id=tenant_id,
            session_id=session_id,
            trace_id=trace_id,
            request_id=request_id,
            message_disposition=message_disposition,
            mcp_execution_tier=mcp_execution_tier,
            enable_agent_tools=enable_agent_tools,
        )

        admission_working, lease_token, sk = self._ingress_begin_or_raise(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            caller_role=caller_role,
            content=content,
            trace_id=trace_id,
            message_disposition=message_disposition,
            mcp_execution_tier=mcp_execution_tier,
            llm_model=llm_model,
            agent_skill_override=agent_skill_override,
            enable_agent_tools=enable_agent_tools,
            deepseek_thinking_enabled=deepseek_thinking_enabled,
            deepseek_reasoning_effort=deepseek_reasoning_effort,
            auto_ingress=auto_ingress,
            regenerate_from_message_id=regenerate_from_message_id,
            ingress_from_drain=ingress_from_drain,
            ingress_bypass_collect=ingress_bypass_collect,
        )

        steer_hook: Optional[Callable[[], str]] = None
        if self._ingress is not None:

            def _steer_hook() -> str:
                merged = self._ingress.backend.steer_drain(sk)  # type: ignore[union-attr]
                text = (merged or "").strip()
                if text:
                    self._emit_audit(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        event_type="steer_injected",
                        payload={
                            "session_id": session_id,
                            "steer_chars": len(text),
                        },
                    )
                return merged

            steer_hook = _steer_hook

        try:
            ctx = self._prepare_llm_turn_context(
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=user_id,
                caller_role=caller_role,
                content=admission_working,
                trace_id=trace_id,
                message_disposition=message_disposition,
                mcp_execution_tier=mcp_execution_tier,
                request_id=request_id,
                llm_model=llm_model,
                agent_skill_override=agent_skill_override,
                enable_agent_tools=enable_agent_tools,
                deepseek_thinking_enabled=deepseek_thinking_enabled,
                deepseek_reasoning_effort=deepseek_reasoning_effort,
                auto_ingress=auto_ingress,
                regenerate_from_message_id=regenerate_from_message_id,
            )

            tool_trace: Optional[List[Dict[str, Any]]] = None
            result: DeepSeekCompletionResult

            if ctx.use_tools:
                identity = ChatIdentity(
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    role=(ctx.caller_role or "user").strip(),
                )
                assert self._control_plane is not None and self._tool_registry is not None
                result_text, tool_trace, raw_finish, merged_reasoning, loop_usage = run_chat_agent_tool_loop(
                    deepseek=self._deepseek,
                    control_plane=self._control_plane,
                    tool_registry=self._tool_registry,
                    settings=self._settings,
                    messages=ctx.history,
                    trace_id=ctx.trace_id,
                    base_request_id=ctx.request_id,
                    identity=identity,
                    session_id=ctx.session_id,
                    message_disposition=ctx.effective_disp,
                    mcp_execution_tier=ctx.effective_tier,
                    chat_tool_allowlist=ctx.chat_tool_allowlist_arg,
                    thinking=ctx.thinking_opts,
                    model_override=ctx.model_override,
                    dsml_fallback=ctx.use_v4_adapter,
                    steer_drain_hook=steer_hook,
                    tool_approval_gate=self._tool_approval_gate,
                )
                result = DeepSeekCompletionResult(
                    text=result_text,
                    raw_finish_reason=raw_finish,
                    reasoning_content=merged_reasoning,
                    usage=loop_usage,
                )
            else:
                try:
                    result = self._deepseek.complete_chat(
                        ctx.history,
                        trace_id=ctx.trace_id,
                        request_id=ctx.request_id,
                        thinking=ctx.thinking_opts,
                        model_override=ctx.model_override,
                    )
                except DeepSeekChatCompletionError as exc:
                    _LOGGER.warning(
                        "chat_turn_llm_failed",
                        session_id=ctx.session_id,
                        tenant_id=ctx.tenant_id,
                        error=str(exc),
                    )
                    raise

            outcome = self._finalize_chat_turn_with_result(ctx, result, tool_trace=tool_trace)
        except BaseException:
            if self._ingress is not None and lease_token is not None:
                self._ingress.renew_or_release_after_error(session_key=sk, lease_token=lease_token)
            raise

        if self._ingress is not None and lease_token is not None:

            def _drain_one(payload: Dict[str, Any]) -> None:
                rid = str(uuid.uuid4())
                tid = str(payload.get("tenant_id") or tenant_id)
                sid = str(payload.get("session_id") or session_id)
                self.send_user_message(
                    tenant_id=tid,
                    session_id=sid,
                    user_id=str(payload.get("user_id") or user_id),
                    caller_role=str(payload.get("caller_role") or caller_role),
                    content=str(payload.get("content") or ""),
                    trace_id=str(payload.get("trace_id") or trace_id),
                    message_disposition=str(payload.get("message_disposition") or "collect"),
                    mcp_execution_tier=str(payload.get("mcp_execution_tier") or "direct-tool"),
                    request_id=rid,
                    llm_model=payload.get("llm_model") if payload.get("llm_model") else None,
                    agent_skill_override=(
                        str(payload["agent_skill_override"]).strip()
                        if payload.get("agent_skill_override")
                        else None
                    ),
                    enable_agent_tools=bool(payload.get("enable_agent_tools") is True),
                    deepseek_thinking_enabled=(
                        bool(payload["deepseek_thinking_enabled"])
                        if "deepseek_thinking_enabled" in payload
                        else None
                    ),
                    deepseek_reasoning_effort=(
                        str(payload["deepseek_reasoning_effort"])
                        if payload.get("deepseek_reasoning_effort")
                        else None
                    ),
                    auto_ingress=bool(payload.get("auto_ingress") is True),
                    regenerate_from_message_id=(
                        str(payload["regenerate_from_message_id"]).strip()
                        if payload.get("regenerate_from_message_id")
                        else None
                    ),
                    ingress_from_drain=True,
                    ingress_bypass_collect=False,
                )

            self._ingress.finish_turn_release_and_drain(
                session_key=sk,
                lease_token=lease_token,
                drain_fn=_drain_one,
            )

        return outcome

    def _build_openai_messages(
        self,
        *,
        session_id: str,
        tenant_id: str,
        primary_skill: str,
        extra_skill_tags: Sequence[str],
        workspace_agent: Optional[WorkspaceAgentConfig],
        system_prompt_suffix: str = "",
    ) -> List[Dict[str, Any]]:
        """Assemble recent chat rows into OpenAI-style role/content messages."""

        skill_note = f" Bound skill: {primary_skill}." if primary_skill else ""
        addon_primary = self._skill_addon(primary_skill) if primary_skill else ""
        addon_extra_parts: List[str] = []
        for tag in extra_skill_tags:
            piece = self._skill_addon(tag)
            if piece:
                addon_extra_parts.append(piece)
        addon_extra = "".join(addon_extra_parts)

        persona_chunks: List[str] = []
        if workspace_agent is not None:
            if workspace_agent.persona_identity_md:
                persona_chunks.append(
                    "\n\n### Persona: Identity\n\n" + workspace_agent.persona_identity_md.strip()
                )
            if workspace_agent.persona_soul_md:
                persona_chunks.append("\n\n### Persona: Style\n\n" + workspace_agent.persona_soul_md.strip())
            if workspace_agent.persona_tools_md:
                persona_chunks.append(
                    "\n\n### Persona: Tools usage\n\n" + workspace_agent.persona_tools_md.strip()
                )
            if workspace_agent.persona_user_md:
                persona_chunks.append(
                    "\n\n### Persona: User preferences\n\n" + workspace_agent.persona_user_md.strip()
                )

        system = (
            "You are Agentium control-plane chat assistant. Follow tenant safety defaults; "
            "do not request secrets or bypass policy."
            + skill_note
            + addon_primary
            + addon_extra
            + "".join(persona_chunks)
        )
        suffix = system_prompt_suffix.strip()
        if suffix:
            system = f"{system.rstrip()}\n\n{suffix}"
        rows = self._messages.list_recent_chat_rows(
            run_id=session_id, tenant_id=tenant_id, limit_rows=40
        )
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for row in rows:
            kind = row.get("kind")
            body = row.get("body") or {}
            if kind == CHAT_KIND_USER:
                text = str(body.get("content") or "")
                disp = str(body.get("message_disposition") or "collect")
                if disp != "collect":
                    text = f"[disposition={disp}] {text}"
                out.append({"role": "user", "content": text})
            elif kind == CHAT_KIND_ASSISTANT:
                preview = str(body.get("answer") or "")
                blocks = body.get("content_blocks")
                if isinstance(blocks, list) and blocks:
                    first = blocks[0]
                    if isinstance(first, dict) and first.get("type") == "text":
                        t = first.get("text")
                        if isinstance(t, str) and t.strip():
                            preview = t
                asst: Dict[str, Any] = {"role": "assistant", "content": preview}
                rc_hist = body.get("reasoning_content")
                if isinstance(rc_hist, str) and rc_hist.strip():
                    asst["reasoning_content"] = rc_hist.strip()
                out.append(asst)
        return self._apply_chat_context_budget(out)

    def _estimate_message_tokens(self, message: Dict[str, Any]) -> int:
        return max(1, len(json.dumps(message, ensure_ascii=False)) // 4)

    def _apply_chat_context_budget(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self._settings.chat_context_budget_enabled:
            return messages
        if len(messages) <= 1:
            return messages
        soft = self._settings.chat_context_soft_token_limit
        hard = self._settings.chat_context_hard_token_limit
        safe = self._settings.chat_context_safe_degrade
        system_msg = messages[0]
        rest = list(messages[1:])
        total = self._estimate_message_tokens(system_msg) + sum(
            self._estimate_message_tokens(m) for m in rest
        )
        if total <= soft:
            return messages
        dropped = 0
        min_keep = 2
        while len(rest) > min_keep and total > soft:
            removed = rest.pop(0)
            total -= self._estimate_message_tokens(removed)
            dropped += 1
        if total <= hard:
            if dropped:
                _LOGGER.warning(
                    "chat_context_budget_soft_trim",
                    dropped_messages=dropped,
                    est_tokens_remaining=total,
                )
            return [system_msg] + rest
        if safe:
            _LOGGER.warning(
                "chat_context_budget_safe_degrade",
                est_tokens=total,
                hard_limit=hard,
            )
            tail = rest[-4:] if len(rest) > 4 else rest
            notice = {
                "role": "user",
                "content": (
                    "[context budget] Earlier transcript turns were omitted to stay within limits."
                ),
            }
            return [system_msg, notice] + tail
        _LOGGER.warning("chat_context_budget_hard_trim", est_tokens=total, hard_limit=hard)
        return [system_msg] + rest

    @staticmethod
    def _to_content_blocks(result: DeepSeekCompletionResult) -> tuple[List[Dict[str, Any]], str]:
        """Split completion into blocks and the HTTP ``answer`` string.

        The ``answer`` field must mirror the full assistant text: clients (workbench)
        display ``answer`` first; a legacy 512-char cap caused visibly truncated tables.
        """

        text = (result.text or "").strip()
        return ([{"type": "text", "text": text}], text)

    def _persist_chat_memory_snippet(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        trace_id: str,
        request_id: str,
        pair_id: str,
        role_label: str,
        preview: str,
    ) -> None:
        """Append one SHORT-layer chat excerpt for session-scoped recall (``payload.run_id``)."""

        mem = self._memory_for_chat(tenant_id=tenant_id, session_id=session_id)
        if mem is None:
            return
        try:
            ctx = RequestContext(
                request_id=request_id,
                run_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                role=(caller_role or "user").strip(),
            )
            cap = 512
            clipped = preview if len(preview) <= cap else preview[:cap]
            mem.remember(
                context=ctx,
                layer=MemoryLayer.SHORT,
                key=f"chat:{session_id}:{pair_id}:{role_label}",
                payload={
                    "run_id": session_id,
                    "role": role_label,
                    "text_preview": clipped,
                    "message_pair_id": pair_id,
                },
            )
        except Exception as exc:
            _LOGGER.warning(
                "chat_memory_short_persist_failed",
                session_id=session_id,
                tenant_id=tenant_id,
                pair_id=pair_id,
                memory_role=role_label,
                error=str(exc),
            )

    def _persist_mid_semantic_memories(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        trace_id: str,
        request_id: str,
        pair_id: str,
        user_turn_text: str,
        assistant_turn_text: str,
        model_override: Optional[str],
    ) -> None:
        """Append MID-layer Mem0-style memories via LLM extraction (distinct from SHORT excerpts)."""

        mem = self._memory_for_chat(tenant_id=tenant_id, session_id=session_id)
        if mem is None:
            return
        if not self._settings.chat_mid_semantic_memory_enabled:
            return
        if self._deepseek is None:
            return
        try:
            ctx = RequestContext(
                request_id=request_id,
                run_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                role=(caller_role or "user").strip(),
            )
            scoped = extract_mid_term_memories(
                self._deepseek,
                user_turn_text=user_turn_text,
                assistant_turn_text=assistant_turn_text,
                trace_id=trace_id,
                request_id=f"{request_id}:midmem",
                model_override=model_override,
            )
            if not scoped:
                return
            existing_mid = mem.recall(
                ctx,
                MemoryLayer.MID,
                limit=200,
                run_id_filter=session_id,
            )
            seen_session: set[str] = set()
            for rec in existing_mid:
                payload = rec.payload or {}
                if str(payload.get("kind") or "") != "extracted_memory":
                    continue
                if str(payload.get("memory_scope") or "session") == "user":
                    continue
                nk = normalize_fact_key(str(payload.get("text") or ""))
                if nk:
                    seen_session.add(nk)

            existing_long = mem.recall(ctx, MemoryLayer.LONG, limit=400, run_id_filter=None)
            seen_user: set[str] = set()
            for rec in existing_long:
                payload = rec.payload or {}
                if str(payload.get("kind") or "") != "extracted_memory":
                    continue
                if str(payload.get("memory_scope") or "") != "user":
                    continue
                if str(payload.get("user_id") or "").strip() != str(user_id).strip():
                    continue
                nk = normalize_fact_key(str(payload.get("text") or ""))
                if nk:
                    seen_user.add(nk)

            for fact, scope in scoped:
                nk = normalize_fact_key(fact)
                if not nk:
                    continue
                if scope == "session":
                    if nk in seen_session:
                        continue
                    seen_session.add(nk)
                    digest = stable_fact_hash(session_id, nk)
                    mem.remember(
                        context=ctx,
                        layer=MemoryLayer.MID,
                        key=f"chat_mem:{session_id}:{digest}",
                        payload={
                            "kind": "extracted_memory",
                            "memory_scope": "session",
                            "run_id": session_id,
                            "user_id": user_id,
                            "message_pair_id": pair_id,
                            "request_id": request_id,
                            "text": fact,
                            "source": "llm_mem0_style_extract",
                        },
                    )
                else:
                    if nk in seen_user:
                        continue
                    seen_user.add(nk)
                    digest = stable_user_fact_hash(tenant_id, user_id, nk)
                    mem.remember(
                        context=ctx,
                        layer=MemoryLayer.LONG,
                        key=f"chat_user_mem:{digest}",
                        payload={
                            "kind": "extracted_memory",
                            "memory_scope": "user",
                            "user_id": user_id,
                            "message_pair_id": pair_id,
                            "request_id": request_id,
                            "text": fact,
                            "source": "llm_mem0_style_extract",
                        },
                    )
        except Exception as exc:
            _LOGGER.warning(
                "chat_mid_semantic_persist_failed",
                session_id=session_id,
                tenant_id=tenant_id,
                pair_id=pair_id,
                error=str(exc),
            )

    def _persist_session_running_summary(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        caller_role: str,
        trace_id: str,
        request_id: str,
        pair_id: str,
        user_turn_text: str,
        assistant_turn_text: str,
        model_override: Optional[str],
    ) -> None:
        """Persist Hermes-style running Markdown summary for this exchange (MID, session scope)."""

        mem = self._memory_for_chat(tenant_id=tenant_id, session_id=session_id)
        if mem is None:
            return
        if not self._settings.chat_session_running_summary_enabled:
            return
        if self._deepseek is None:
            return
        try:
            blob = extract_session_running_summary(
                self._deepseek,
                user_turn_text=user_turn_text,
                assistant_turn_text=assistant_turn_text,
                trace_id=trace_id,
                request_id=f"{request_id}:sessum",
                model_override=model_override,
            ).strip()
            if not blob:
                return
            ctx = RequestContext(
                request_id=request_id,
                run_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                trace_id=trace_id,
                role=(caller_role or "user").strip(),
            )
            mem.remember(
                context=ctx,
                layer=MemoryLayer.MID,
                key=f"chat_sess_summary:{session_id}:{request_id}",
                payload={
                    "kind": "session_running_summary",
                    "memory_scope": "session",
                    "run_id": session_id,
                    "user_id": user_id,
                    "message_pair_id": pair_id,
                    "request_id": request_id,
                    "summary": blob,
                    "source": "llm_session_summary",
                },
            )
        except Exception as exc:
            _LOGGER.warning(
                "chat_session_summary_persist_failed",
                session_id=session_id,
                tenant_id=tenant_id,
                pair_id=pair_id,
                error=str(exc),
            )

    def _emit_audit(
        self,
        *,
        tenant_id: str,
        session_id: str,
        request_id: str,
        trace_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        if self._audit is None:
            return
        try:
            body = dict(payload)
            body["trace_id"] = trace_id
            body["request_id"] = request_id
            self._audit(
                AuditRecord(
                    event_type=event_type,
                    tenant_id=tenant_id,
                    run_id=session_id,
                    payload=body,
                )
            )
        except Exception:
            pass
