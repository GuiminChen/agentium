"""Session timeline (run_messages) and cooperative run cancel."""

from __future__ import annotations

import hashlib
import json
import re
from http import HTTPStatus
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from pydantic import ValidationError

from agentium.api.http.control_plane_schemas import RunCancelRequest
from agentium.api.http.handler_constants import admin_scope, cap_granted
from agentium.core.agent_lifecycle import AgentLifecycleError
from agentium.models.context import AuditRecord

_PACK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def parse_run_cancel_path(path: str) -> Optional[str]:
    prefix = "/v1/runs/"
    suffix = "/cancel"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix) : -len(suffix)].strip("/")
    if not mid or "/" in mid:
        return None
    return mid


def _args_fingerprint(args: Dict[str, Any]) -> str:
    blob = json.dumps(args or {}, sort_keys=True, default=str, ensure_ascii=False)
    if len(blob) > 512:
        blob = blob[:512]
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class SessionTimelineHandlersMixin:
    """Session messages + run cancel; mixed into ControlPlaneHTTPRequestHandler."""

    def _record_turn_messages(
        self,
        *,
        tenant_id: str,
        run_id: str,
        request_id: str,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        response_dict: Dict[str, Any],
    ) -> None:
        res = self.resources
        store = res.run_message_store if res else None
        if store is None:
            return
        fp = _args_fingerprint(dict(args or {}))
        store.append(
            run_id=run_id,
            tenant_id=tenant_id,
            role="user",
            kind="turn_request",
            body={"tool_name": tool_name, "args_fingerprint": fp},
            tool_name=tool_name,
            request_id=request_id,
        )
        store.append(
            run_id=run_id,
            tenant_id=tenant_id,
            role="assistant",
            kind="turn_result",
            body={
                "status": response_dict.get("status"),
                "error_code": response_dict.get("error_code"),
                "tool_use_id": response_dict.get("tool_use_id"),
                "approval_id": response_dict.get("approval_id"),
                "message": (response_dict.get("message") or "")[:512],
            },
            tool_name=tool_name,
            status=str(response_dict.get("status") or ""),
            request_id=request_id,
        )

    def _parse_session_messages_path(self, path: str) -> Optional[str]:
        prefix = "/v1/sessions/"
        suffix = "/messages"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        mid = path[len(prefix) : -len(suffix)].strip("/")
        if not mid or "/" in mid:
            return None
        return mid

    def _handle_session_messages(self, session_id: str, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "sessions.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability sessions.read required.")
            return
        res = self.resources
        store = res.run_message_store if res else None
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "sessions_unavailable", "Run message store not configured.")
            return
        run_id = session_id
        params = parse_qs(query, keep_blank_values=False)
        limit_raw = params.get("limit", ["50"])[0]
        cursor_raw = params.get("cursor", ["0"])[0]
        try:
            limit = int(limit_raw or "50")
            after_seq = int(cursor_raw or "0")
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_query", "limit and cursor must be integers.")
            return
        items, next_cursor = store.list_page(
            run_id=run_id, tenant_id=info.tenant_id, after_seq=after_seq, limit=limit
        )
        payload = {
            "session_id": session_id,
            "run_id": run_id,
            "note": "session_id is the run_id for this MVP mapping.",
            "count": len(items),
            "messages": items,
            "next_cursor": next_cursor,
        }
        self._write_json(HTTPStatus.OK, payload)

    def _handle_run_cancel(self, run_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "runs.cancel"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability runs.cancel required.")
            return
        res = self.resources
        if res is None or res.run_cancel_registry is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "cancel_unavailable", "Run cancel is not configured.")
            return
        force = False
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length > 0:
            raw = self._read_json_body()
            if raw is None:
                return
            try:
                body = RunCancelRequest.parse_obj(raw if isinstance(raw, dict) else {})
            except ValidationError as exc:
                self._write_error(
                    HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc)
                )
                return
            force = body.force
        res.run_cancel_registry.cancel(run_id)
        if res.lifecycle_manager is not None:
            try:
                if force:
                    res.lifecycle_manager.kill(run_id, reason="http_cancel_force")
                else:
                    res.lifecycle_manager.stop(run_id, reason="http_cancel")
            except AgentLifecycleError:
                pass
        if res.task_graph is not None:
            res.task_graph.terminate_run(run_id)
        if self.audit_sink is not None:
            self.audit_sink.append(
                AuditRecord(
                    event_type="run_cancel_requested",
                    tenant_id=info.tenant_id,
                    run_id=run_id,
                    policy_version=None,
                    payload={"force": force, "user_id": info.user_id},
                )
            )
        self._write_json(HTTPStatus.OK, {"run_id": run_id, "cancelled": True, "force": force})


__all__ = ["SessionTimelineHandlersMixin", "_args_fingerprint", "_PACK_ID_RE", "parse_run_cancel_path"]
