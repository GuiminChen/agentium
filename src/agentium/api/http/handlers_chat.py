"""TradeAgent-style chat REST handlers (sessions + messages)."""

from __future__ import annotations

import re
import sqlite3
from http import HTTPStatus
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import uuid

from pydantic import ValidationError

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionError
from agentium.api.http.chat_schemas import (
    ChatMessageSendRequest,
    ChatSessionCreateRequest,
    ChatSessionUpdateRequest,
)
from agentium.api.http.handler_constants import cap_granted
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.infra.db.sqlite_chat_session_store import ChatSessionRecord

_SESSION_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _session_as_dict(record: ChatSessionRecord) -> Dict[str, Any]:
    return {
        "session_id": record.session_id,
        "run_id": record.session_id,
        "note": "session_id maps to run_id for MVP storage.",
        "title": record.title,
        "skill": record.skill,
        "intro_text": record.intro_text,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def parse_chat_session_detail_path(path: str) -> Optional[str]:
    """Return ``session_id`` for ``/v1/chat/sessions/{session_id}`` (no trailing subpath)."""

    prefix = "/v1/chat/sessions/"
    if not path.startswith(prefix):
        return None
    tail = path[len(prefix) :].strip("/")
    if not tail or "/" in tail:
        return None
    return tail


def parse_chat_session_messages_path(path: str) -> Optional[str]:
    """Return ``session_id`` for ``/v1/chat/sessions/{id}/messages``."""

    suffix = "/messages"
    prefix = "/v1/chat/sessions/"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix) : -len(suffix)].strip("/")
    if not mid or "/" in mid:
        return None
    return mid


class ChatHTTPHandlersMixin:
    """Mounted on :class:`ControlPlaneHTTPRequestHandler`; requires chat resources."""

    def _chat_service(self) -> Optional[ChatTurnService]:
        res = self.resources
        if res is None or getattr(res, "chat_turn_service", None) is None:
            return None
        svc = res.chat_turn_service
        assert isinstance(svc, ChatTurnService)
        return svc

    def _chat_session_store(self):
        res = self.resources
        if res is None:
            return None
        return getattr(res, "chat_session_store", None)

    # --- Routes invoked from ``do_GET`` / ``do_POST`` / ``do_PUT`` / ``do_DELETE`` ---

    def _chat_handle_list_sessions(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        store = self._chat_session_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
            return
        qs = parse_qs(query, keep_blank_values=False)
        try:
            page = max(1, int(qs.get("page", ["1"])[0] or "1"))
            page_size = max(1, min(100, int(qs.get("page_size", ["20"])[0] or "20")))
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_query", "page and page_size must be integers.")
            return
        rows, pagination = store.list_sessions_page(tenant_id=info.tenant_id, page=page, page_size=page_size)
        payload = {"items": [_session_as_dict(r) for r in rows], "pagination": pagination}
        self._write_json(HTTPStatus.OK, payload)

    def _chat_handle_create_session(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        store = self._chat_session_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
            return
        raw = self._read_json_body()
        if raw is None:
            return
        try:
            req = ChatSessionCreateRequest.parse_obj(raw)
        except ValidationError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_payload",
                "Invalid request body.",
                detail=str(exc),
            )
            return
        if req.session_id and not _SESSION_ID_SAFE.match(req.session_id.strip()):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_session_id", "session_id has invalid characters.")
            return
        try:
            rec = store.create_session(
                tenant_id=info.tenant_id,
                session_id=(req.session_id.strip() if req.session_id else None),
                title=req.title,
                skill=req.skill,
                intro_text=req.intro_text,
                metadata=req.metadata,
            )
        except sqlite3.IntegrityError:
            self._write_error(
                HTTPStatus.CONFLICT,
                "SESSION_ID_EXISTS",
                "session_id already exists for this tenant.",
            )
            return
        self._write_json(HTTPStatus.CREATED, {"session": _session_as_dict(rec)})

    def _chat_handle_get_session(self, session_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        store = self._chat_session_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
            return
        try:
            rec = store.get_session(tenant_id=info.tenant_id, session_id=session_id)
        except KeyError:
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
            return
        self._write_json(HTTPStatus.OK, {"session": _session_as_dict(rec)})

    def _chat_handle_update_session(self, session_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        store = self._chat_session_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
            return
        raw = self._read_json_body()
        if raw is None:
            return
        try:
            req = ChatSessionUpdateRequest.parse_obj(raw)
        except ValidationError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_payload",
                "Invalid request body.",
                detail=str(exc),
            )
            return
        try:
            rec = store.update_session(tenant_id=info.tenant_id, session_id=session_id, title=req.title)
        except KeyError:
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown or deleted session.")
            return
        self._write_json(HTTPStatus.OK, {"session": _session_as_dict(rec)})

    def _chat_handle_delete_session(self, session_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        store = self._chat_session_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
            return
        try:
            store.soft_delete(tenant_id=info.tenant_id, session_id=session_id)
        except KeyError:
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
            return
        self._write_json(HTTPStatus.OK, {"deleted": True, "session_id": session_id})

    def _chat_handle_session_messages(self, session_id: str, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.messages.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.messages.read required.")
            return
        res = self.resources
        store = getattr(res, "run_message_store", None) if res else None
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Run message store not configured.")
            return
        sess = self._chat_session_store()
        if sess is None or not sess.session_exists(tenant_id=info.tenant_id, session_id=session_id):
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
            return
        qs = parse_qs(query, keep_blank_values=False)
        try:
            page = max(1, int(qs.get("page", ["1"])[0] or "1"))
            page_size = max(1, min(100, int(qs.get("page_size", ["50"])[0] or "50")))
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_query", "page and page_size must be integers.")
            return
        items, pagination = store.list_chat_messages_page(
            run_id=session_id,
            tenant_id=info.tenant_id,
            page=page,
            page_size=page_size,
        )
        self._write_json(
            HTTPStatus.OK,
            {"session_id": session_id, "items": items, "pagination": pagination},
        )

    def _chat_handle_send_message(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.messages.send"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.messages.send required.")
            return
        raw = self._read_json_body()
        if raw is None:
            return
        try:
            req = ChatMessageSendRequest.parse_obj(raw)
        except ValidationError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_payload",
                "Invalid request body.",
                detail=str(exc),
            )
            return
        if req.stream:
            self._write_error(
                HTTPStatus.NOT_IMPLEMENTED,
                "stream_not_supported",
                "stream=true is not implemented; use stream=false.",
            )
            return
        svc = self._chat_service()
        if svc is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat service not configured.")
            return
        request_id = str(uuid.uuid4())
        trace_hdr = self.headers.get("X-Trace-Id", "").strip() or request_id
        try:
            outcome = svc.send_user_message(
                tenant_id=info.tenant_id,
                session_id=req.session_id,
                user_id=info.user_id,
                content=req.content,
                trace_id=trace_hdr,
                message_disposition=req.message_disposition,
                request_id=request_id,
                llm_model=req.llm_model,
            )
        except KeyError:
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
            return
        except RuntimeError:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "deepseek_not_configured",
                "DeepSeek completion is not configured (set AGENTIUM_DEEPSEEK_API_KEY).",
            )
            return
        except DeepSeekChatCompletionError as exc:
            self._write_error(
                HTTPStatus.BAD_GATEWAY,
                "upstream_llm_failed",
                "Upstream chat completion failed.",
                detail=str(exc),
            )
            return
        payload = {
            "type": "Answer",
            "message_id": outcome.message_id,
            "status": outcome.status,
            "content_blocks": outcome.content_blocks,
            "answer": outcome.answer_preview,
        }
        self._write_json(HTTPStatus.OK, payload)


def route_chat_dispatch(
    handler: Any,
    method: str,
    path: str,
    query: str,
) -> bool:
    """Return ``True`` if the request was handled."""

    parsed_path = urlparse(path).path
    if parsed_path == "/v1/chat/sessions":
        if method == "GET":
            handler._chat_handle_list_sessions(query)
            return True
        if method == "POST":
            handler._chat_handle_create_session()
            return True
        return False
    sid_messages = parse_chat_session_messages_path(parsed_path)
    if sid_messages is not None:
        if method == "GET":
            handler._chat_handle_session_messages(sid_messages, query)
            return True
        return False
    sid_detail = parse_chat_session_detail_path(parsed_path)
    if sid_detail is not None:
        if method == "GET":
            handler._chat_handle_get_session(sid_detail)
            return True
        if method == "PUT":
            handler._chat_handle_update_session(sid_detail)
            return True
        if method == "DELETE":
            handler._chat_handle_delete_session(sid_detail)
            return True
        return False
    if parsed_path == "/v1/chat/messages" and method == "POST":
        handler._chat_handle_send_message()
        return True
    return False
