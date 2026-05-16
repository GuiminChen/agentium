"""TradeAgent-style chat REST handlers (sessions + messages)."""

from __future__ import annotations

import re
import sqlite3
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import uuid

from pydantic import ValidationError

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionError
from agentium.api.http.chat_schemas import (
    ChatMessageSendRequest,
    ChatSessionCreateRequest,
    ChatSessionUpdateRequest,
)
from agentium.api.http.chat_workspace_agent import (
    allowed_skill_tag_set,
    serialize_workspace_agent_for_storage,
)
from agentium.api.http.chat_memory_scopes import parse_memory_scopes_query
from agentium.api.http.handler_constants import cap_granted
from agentium.coordination.chat_agent_tool_loop import ChatPendingToolApproval
from agentium.coordination.chat_ingress.exceptions import ChatIngressDeferred
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.coordination.persona_templates import load_persona_templates
from agentium.infra.db.sqlite_chat_session_store import ChatSessionRecord
from agentium.memory.types import MemoryLayer, MemoryRecord
from agentium.models.context import RequestContext
from agentium.skills.catalog import load_merged_skill_manifests

_SESSION_ID_SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_SKILL_TAG_SAFE = _SESSION_ID_SAFE

_WORKSPACE_AGENT_SKILL_OPTION = {
    "id": "workspace_agent",
    "kind": "builtin",
    "description": "Session-scoped workbench persona for /workspace Agent chat (UI default).",
}


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


def parse_chat_session_memory_path(path: str) -> Optional[str]:
    """Return ``session_id`` for ``/v1/chat/sessions/{id}/memory``."""

    suffix = "/memory"
    prefix = "/v1/chat/sessions/"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix) : -len(suffix)].strip("/")
    if not mid or "/" in mid:
        return None
    return mid


class ChatHTTPHandlersMixin:
    """Mounted on :class:`ControlPlaneHTTPRequestHandler`; requires chat resources."""

    def _chat_workspace_agent_context(self) -> tuple[Optional[Any], Optional[Any]]:
        """Return ``(settings, tool_registry)`` from HTTP resources."""

        res = self.resources
        if res is None:
            return None, None
        return getattr(res, "settings", None), getattr(res, "tool_registry", None)

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

    def _chat_handle_skill_options(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        items: List[Dict[str, Any]] = [dict(_WORKSPACE_AGENT_SKILL_OPTION)]
        res = self.resources
        settings = getattr(res, "settings", None) if res else None
        if settings is not None:
            try:
                for manifest in load_merged_skill_manifests(settings):
                    items.append(
                        {
                            "id": manifest.name,
                            "kind": "pack",
                            "description": manifest.description,
                        }
                    )
            except (OSError, RuntimeError, ValueError):
                pass
        self._write_json(HTTPStatus.OK, {"items": items})

    def _chat_handle_persona_templates(self) -> None:
        """Return bundled plus optional overlay persona templates for the workbench."""

        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.sessions.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.sessions.manage required.")
            return
        res = self.resources
        settings = getattr(res, "settings", None) if res else None
        if settings is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "settings_unavailable",
                "Server settings not available for persona templates.",
            )
            return
        items = [row.as_public_dict() for row in load_persona_templates(settings)]
        self._write_json(HTTPStatus.OK, {"items": items})

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
        settings, tool_registry = self._chat_workspace_agent_context()
        md = dict(req.metadata or {})
        md["orchestration_mode"] = req.orchestration_mode
        if req.policy_pack_id is not None:
            pp = req.policy_pack_id.strip()
            if pp:
                md["policy_pack_id"] = pp
        if req.workspace_agent is not None:
            if settings is None:
                self._write_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "settings_unavailable",
                    "Server settings not available for workspace_agent validation.",
                )
                return
            allowed_tags = allowed_skill_tag_set(settings)
            try:
                md["workspace_agent"] = serialize_workspace_agent_for_storage(
                    req.workspace_agent,
                    settings=settings,
                    tool_registry=tool_registry,
                    allowed_skill_tags=allowed_tags,
                )
            except ValueError as exc:
                self._write_error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_workspace_agent",
                    str(exc),
                )
                return
        skill_val: Optional[str] = None
        if req.skill is not None:
            stripped = req.skill.strip()
            if stripped:
                if not _SKILL_TAG_SAFE.match(stripped):
                    self._write_error(HTTPStatus.BAD_REQUEST, "invalid_skill", "skill has invalid characters.")
                    return
                skill_val = stripped
        if skill_val is None and req.workspace_agent is not None and req.workspace_agent.skill_tags:
            skill_val = req.workspace_agent.skill_tags[0]
        if req.session_id and not _SESSION_ID_SAFE.match(req.session_id.strip()):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_session_id", "session_id has invalid characters.")
            return
        try:
            rec = store.create_session(
                tenant_id=info.tenant_id,
                session_id=(req.session_id.strip() if req.session_id else None),
                title=req.title,
                skill=skill_val,
                intro_text=req.intro_text,
                metadata=md,
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
            existing = store.get_session(tenant_id=info.tenant_id, session_id=session_id)
        except KeyError:
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown or deleted session.")
            return
        new_title = req.title if req.title is not None else existing.title
        new_skill = existing.skill
        if req.skill is not None:
            stripped = req.skill.strip()
            if stripped:
                if not _SKILL_TAG_SAFE.match(stripped):
                    self._write_error(HTTPStatus.BAD_REQUEST, "invalid_skill", "skill has invalid characters.")
                    return
                new_skill = stripped
            else:
                new_skill = None
        elif req.workspace_agent is not None and req.workspace_agent.skill_tags:
            new_skill = req.workspace_agent.skill_tags[0]

        title_explicit = isinstance(raw, dict) and isinstance(raw.get("title"), str)

        merged_md = dict(existing.metadata)
        touched_md = False
        if req.workspace_agent is not None:
            settings, tool_registry = self._chat_workspace_agent_context()
            if settings is None:
                self._write_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "settings_unavailable",
                    "Server settings not available for workspace_agent validation.",
                )
                return
            allowed_tags = allowed_skill_tag_set(settings)
            try:
                merged_md["workspace_agent"] = serialize_workspace_agent_for_storage(
                    req.workspace_agent,
                    settings=settings,
                    tool_registry=tool_registry,
                    allowed_skill_tags=allowed_tags,
                )
            except ValueError as exc:
                self._write_error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_workspace_agent",
                    str(exc),
                )
                return
            touched_md = True

        if title_explicit:
            merged_md["session_title_source"] = "user"
            merged_md["session_title_auto_status"] = "skipped"
            touched_md = True

        if req.orchestration_mode is not None:
            merged_md["orchestration_mode"] = req.orchestration_mode
            touched_md = True

        meta_payload: Optional[Dict[str, Any]] = merged_md if touched_md else None

        try:
            rec = store.update_session(
                tenant_id=info.tenant_id,
                session_id=session_id,
                title=new_title,
                skill=new_skill,
                metadata=meta_payload,
            )
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

    def _chat_handle_session_memory(self, session_id: str, query: str) -> None:
        """Return layered memory rows; optionally merge user-scoped (LONG) rows."""

        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "chat.messages.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability chat.messages.read required.")
            return
        res = self.resources
        router = getattr(res, "chat_memory_lane_router", None) if res else None
        mem = router.resolve(tenant_id=info.tenant_id, session_id=session_id) if router is not None else None
        if mem is None and res:
            mem = getattr(res, "memory_service", None)
        if mem is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "memory_unavailable",
                "Memory service not configured on control plane.",
            )
            return
        qs = parse_qs(query, keep_blank_values=False)
        layers: List[MemoryLayer] = []
        raw_layers = (qs.get("layers", ["short,mid"])[0] or "short,mid").strip()
        for part in raw_layers.split(","):
            token = part.strip().lower()
            if token in ("short", "mid", "long"):
                layers.append(MemoryLayer(token))
        if not layers:
            layers = [MemoryLayer.SHORT, MemoryLayer.MID]
        try:
            limit = max(1, min(100, int(qs.get("limit", ["50"])[0] or "50")))
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_query", "limit must be an integer.")
            return

        scopes_raw = (qs.get("scopes", ["session"])[0] or "session").strip()
        include_session, include_user = parse_memory_scopes_query(scopes_raw)

        sess = self._chat_session_store()
        if sess is None or not sess.session_exists(tenant_id=info.tenant_id, session_id=session_id):
            # UX / race: memory is an overlay; unknown session behaves like "no rows yet" (not 404).
            self._write_json(
                HTTPStatus.OK,
                {"session_id": session_id, "items": [], "layers": [lay.value for lay in layers]},
            )
            return

        req_id = str(uuid.uuid4())
        trace_hdr = self.headers.get("X-Trace-Id", "").strip() or req_id
        ctx = RequestContext(
            request_id=req_id,
            run_id=session_id,
            tenant_id=info.tenant_id,
            user_id=info.user_id,
            trace_id=trace_hdr,
            role=(info.role or "user").strip(),
        )
        uid_expect = str(info.user_id or "").strip()
        items_out: List[Dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        def _append_rows(rows: List[MemoryRecord]) -> None:
            for r in rows:
                triple = (r.layer.value, r.key, r.created_at.isoformat())
                if triple in seen_keys:
                    continue
                seen_keys.add(triple)
                items_out.append(
                    {
                        "layer": r.layer.value,
                        "key": r.key,
                        "payload": dict(r.payload),
                        "created_at": r.created_at.isoformat(),
                    }
                )

        wide_cap = min(500, max(limit, limit * 25))

        for layer in layers:
            merged: List[MemoryRecord] = []
            if include_session:
                merged.extend(mem.recall(context=ctx, layer=layer, limit=limit, run_id_filter=session_id))
            if include_user:
                candidates = mem.recall(context=ctx, layer=layer, limit=wide_cap, run_id_filter=None)
                for rec in candidates:
                    payload = rec.payload or {}
                    if str(payload.get("memory_scope") or "") != "user":
                        continue
                    if str(payload.get("user_id") or "").strip() != uid_expect:
                        continue
                    merged.append(rec)
            _append_rows(merged)

        items_out.sort(key=lambda row: row.get("created_at") or "")
        self._write_json(
            HTTPStatus.OK,
            {
                "session_id": session_id,
                "items": items_out,
                "layers": [lay.value for lay in layers],
                "scopes": {"session": include_session, "user": include_user},
            },
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
        if req.orchestration_mode is not None:
            sess_store = self._chat_session_store()
            if sess_store is None:
                self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat session store not configured.")
                return
            try:
                sess_store.merge_session_metadata(
                    tenant_id=info.tenant_id,
                    session_id=(req.session_id or "").strip(),
                    patch={"orchestration_mode": req.orchestration_mode},
                )
            except KeyError:
                self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
                return
        svc = self._chat_service()
        if svc is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "chat_unavailable", "Chat service not configured.")
            return
        request_id = str(uuid.uuid4())
        trace_hdr = self.headers.get("X-Trace-Id", "").strip() or request_id
        agent_skill_raw = (req.agent_skill or "").strip()
        agent_skill_arg = agent_skill_raw if agent_skill_raw else None
        if req.stream:
            sse_iter = svc.iter_send_user_message_sse(
                tenant_id=info.tenant_id,
                session_id=req.session_id,
                user_id=info.user_id,
                caller_role=info.role,
                content=req.content,
                trace_id=trace_hdr,
                message_disposition=req.message_disposition,
                mcp_execution_tier=req.mcp_execution_tier,
                request_id=request_id,
                llm_model=req.llm_model,
                agent_skill_override=agent_skill_arg,
                enable_agent_tools=req.enable_agent_tools,
                deepseek_thinking_enabled=req.deepseek_thinking_enabled,
                deepseek_reasoning_effort=req.deepseek_reasoning_effort,
                auto_ingress=req.auto_ingress,
                regenerate_from_message_id=req.regenerate_from_message_id,
            )
            try:
                first_event = next(sse_iter)
            except StopIteration:
                self._write_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "stream_empty",
                    "Chat streaming produced no events.",
                )
                return
            except KeyError as exc:
                key = str(exc.args[0]) if exc.args else ""
                if key == "regenerate_target_not_found":
                    self._write_error(
                        HTTPStatus.NOT_FOUND,
                        "regenerate_target_not_found",
                        "No assistant message found to regenerate for this id.",
                    )
                    return
                if key == "regenerate_user_missing":
                    self._write_error(
                        HTTPStatus.NOT_FOUND,
                        "regenerate_user_missing",
                        "User row missing for regenerate target.",
                    )
                    return
                if key == "session_not_found":
                    self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
                    return
                self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
                return
            except ValueError as exc:
                if str(exc) == "stream_with_tools_unsupported":
                    self._write_error(
                        HTTPStatus.NOT_IMPLEMENTED,
                        "stream_with_tools_unsupported",
                        "stream=true requires enable_agent_tools=false (agent tool loop cannot stream yet).",
                    )
                    return
                if str(exc) == "regenerate_empty_user_content":
                    self._write_error(
                        HTTPStatus.BAD_REQUEST,
                        "regenerate_empty_user_content",
                        "Stored user message is empty; cannot regenerate.",
                    )
                    return
                raise
            except RuntimeError:
                self._write_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "deepseek_not_configured",
                    "DeepSeek completion is not configured (set AGENTIUM_DEEPSEEK_API_KEY).",
                )
                return
            except ChatIngressDeferred as exc:
                payload: Dict[str, Any] = {
                    "status": "queued",
                    "ingress_kind": exc.kind,
                    "queue_depth": exc.queue_depth,
                }
                if exc.collect_flush_after_ms is not None:
                    payload["collect_flush_after_ms"] = exc.collect_flush_after_ms
                self._write_json(HTTPStatus.ACCEPTED, payload)
                return
            try:
                self._begin_sse_response()
                self._write_sse_json_event(first_event)
                for evt in sse_iter:
                    self._write_sse_json_event(evt)
            except BrokenPipeError:
                return
            return

        try:
            outcome = svc.send_user_message(
                tenant_id=info.tenant_id,
                session_id=req.session_id,
                user_id=info.user_id,
                caller_role=info.role,
                content=req.content,
                trace_id=trace_hdr,
                message_disposition=req.message_disposition,
                mcp_execution_tier=req.mcp_execution_tier,
                request_id=request_id,
                llm_model=req.llm_model,
                agent_skill_override=agent_skill_arg,
                enable_agent_tools=req.enable_agent_tools,
                deepseek_thinking_enabled=req.deepseek_thinking_enabled,
                deepseek_reasoning_effort=req.deepseek_reasoning_effort,
                auto_ingress=req.auto_ingress,
                regenerate_from_message_id=req.regenerate_from_message_id,
            )
        except KeyError as exc:
            key = str(exc.args[0]) if exc.args else ""
            if key == "regenerate_target_not_found":
                self._write_error(
                    HTTPStatus.NOT_FOUND,
                    "regenerate_target_not_found",
                    "No assistant message found to regenerate for this id.",
                )
                return
            if key == "regenerate_user_missing":
                self._write_error(
                    HTTPStatus.NOT_FOUND,
                    "regenerate_user_missing",
                    "User row missing for regenerate target.",
                )
                return
            self._write_error(HTTPStatus.NOT_FOUND, "session_not_found", "Unknown session.")
            return
        except ValueError as exc:
            if str(exc) == "regenerate_empty_user_content":
                self._write_error(
                    HTTPStatus.BAD_REQUEST,
                    "regenerate_empty_user_content",
                    "Stored user message is empty; cannot regenerate.",
                )
                return
            raise
        except RuntimeError:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "deepseek_not_configured",
                "DeepSeek completion is not configured (set AGENTIUM_DEEPSEEK_API_KEY).",
            )
            return
        except ChatIngressDeferred as exc:
            qpayload: Dict[str, Any] = {
                "status": "queued",
                "ingress_kind": exc.kind,
                "queue_depth": exc.queue_depth,
            }
            if exc.collect_flush_after_ms is not None:
                qpayload["collect_flush_after_ms"] = exc.collect_flush_after_ms
            self._write_json(HTTPStatus.ACCEPTED, qpayload)
            return
        except ChatPendingToolApproval as pend:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "pending_tool_approval",
                "Chat agent tool execution requires approval before continuing.",
                detail={"approval_id": pend.approval_id, "tool_name": pend.tool_name},
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
        payload: Dict[str, Any] = {
            "type": "Answer",
            "message_id": outcome.message_id,
            "status": outcome.status,
            "content_blocks": outcome.content_blocks,
            "answer": outcome.answer_preview,
        }
        if outcome.tool_trace:
            payload["chat_agent_tool_trace"] = outcome.tool_trace
        if outcome.reasoning_content:
            payload["reasoning_content"] = outcome.reasoning_content
        self._write_json(HTTPStatus.OK, payload)


def route_chat_dispatch(
    handler: Any,
    method: str,
    path: str,
    query: str,
) -> bool:
    """Return ``True`` if the request was handled."""

    parsed_path = urlparse(path).path
    if parsed_path == "/v1/chat/skill-options":
        if method == "GET":
            handler._chat_handle_skill_options()
            return True
        return False
    if parsed_path == "/v1/chat/persona-templates":
        if method == "GET":
            handler._chat_handle_persona_templates()
            return True
        return False
    if parsed_path == "/v1/chat/sessions":
        if method == "GET":
            handler._chat_handle_list_sessions(query)
            return True
        if method == "POST":
            handler._chat_handle_create_session()
            return True
        return False
    sid_memory = parse_chat_session_memory_path(parsed_path)
    if sid_memory is not None:
        if method == "GET":
            handler._chat_handle_session_memory(sid_memory, query)
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
