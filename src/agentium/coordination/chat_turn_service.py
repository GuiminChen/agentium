"""Orchestrate TradeAgent-style chat turns: persist rows, call DeepSeek, audit hooks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

import structlog

from agentium.ai_gateway.deepseek_chat import (
    DeepSeekChatCompletionClient,
    DeepSeekChatCompletionError,
    DeepSeekCompletionResult,
)
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_store import SqliteRunMessageStore
from agentium.models.context import AuditRecord
from agentium.shared.chat_timeline import CHAT_KIND_ASSISTANT, CHAT_KIND_USER

_LOGGER = structlog.get_logger(__name__)


class AuditAppendFn(Protocol):
    """Sink for non-blocking chat audit events."""

    def __call__(self, record: AuditRecord) -> None:
        ...


@dataclass(frozen=True)
class ChatSendOutcome:
    """HTTP-facing result for one ``POST /v1/chat/messages`` call."""

    message_id: str
    content_blocks: List[Dict[str, Any]]
    answer_preview: str
    status: str


class ChatTurnService:
    """Kernel-adjacent service: maps chat intents to persisted timeline + outbound LLM."""

    def __init__(
        self,
        *,
        run_message_store: SqliteRunMessageStore,
        chat_session_store: SqliteChatSessionStore,
        deepseek_client: Optional[DeepSeekChatCompletionClient],
        audit_sink: Optional[Callable[[AuditRecord], None]],
    ) -> None:
        self._messages = run_message_store
        self._sessions = chat_session_store
        self._deepseek = deepseek_client
        self._audit = audit_sink

    def send_user_message(
        self,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        content: str,
        trace_id: str,
        message_disposition: str,
        request_id: str,
        llm_model: Optional[str],
    ) -> ChatSendOutcome:
        """Append user assistant exchange; requires active session and configured DeepSeek client."""

        if not self._sessions.session_exists(tenant_id=tenant_id, session_id=session_id):
            raise KeyError("session_not_found")
        if self._deepseek is None:
            raise RuntimeError("deepseek_not_configured")
        pair_id = str(uuid.uuid4())
        public_message_id = pair_id
        user_body: Dict[str, Any] = {
            "message_pair_id": pair_id,
            "message_id": public_message_id,
            "content": content,
            "message_disposition": message_disposition,
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
        self._emit_audit(
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            event_type="chat_message_ingress",
            payload={
                "message_disposition": message_disposition,
                "content_len": len(content),
                "llm_model_requested": llm_model,
            },
        )
        history = self._build_openai_messages(session_id=session_id, tenant_id=tenant_id)
        try:
            result = self._deepseek.complete_chat(history, trace_id=trace_id, request_id=request_id)
        except DeepSeekChatCompletionError as exc:
            _LOGGER.warning(
                "chat_turn_llm_failed",
                session_id=session_id,
                tenant_id=tenant_id,
                error=str(exc),
            )
            raise
        blocks, preview = self._to_content_blocks(result)
        assistant_body: Dict[str, Any] = {
            "message_pair_id": pair_id,
            "message_id": public_message_id,
            "content_blocks": blocks,
            "answer": preview,
            "status": "finished",
        }
        self._messages.append(
            run_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            kind=CHAT_KIND_ASSISTANT,
            body=assistant_body,
            status="finished",
            request_id=request_id,
        )
        self._sessions.touch_updated_at(tenant_id=tenant_id, session_id=session_id)
        self._emit_audit(
            tenant_id=tenant_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            event_type="chat_message_completed",
            payload={"answer_len": len(preview), "finish_reason": result.raw_finish_reason},
        )
        return ChatSendOutcome(
            message_id=public_message_id,
            content_blocks=blocks,
            answer_preview=preview,
            status="finished",
        )

    def _build_openai_messages(self, *, session_id: str, tenant_id: str) -> List[Dict[str, str]]:
        """Assemble recent chat rows into OpenAI-style role/content messages."""

        session = self._sessions.try_get_session(tenant_id=tenant_id, session_id=session_id)
        skill_note = ""
        if session and session.skill:
            skill_note = f" Bound skill: {session.skill}."
        system = (
            "You are Agentium control-plane chat assistant. Follow tenant safety defaults; "
            "do not request secrets or bypass policy."
            + skill_note
        )
        rows = self._messages.list_recent_chat_rows(
            run_id=session_id, tenant_id=tenant_id, limit_rows=40
        )
        out: List[Dict[str, str]] = [{"role": "system", "content": system}]
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
                out.append({"role": "assistant", "content": preview})
        return out

    @staticmethod
    def _to_content_blocks(result: DeepSeekCompletionResult) -> tuple[List[Dict[str, Any]], str]:
        text = (result.text or "").strip()
        preview = text[:512]
        return ([{"type": "text", "text": text}], preview)

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
