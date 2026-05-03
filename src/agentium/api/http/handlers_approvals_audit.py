"""Approvals list/detail/decision and audit query/export."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, Dict, List

from pydantic import ValidationError
from urllib.parse import parse_qs

from agentium.api.http.control_plane_schemas import ApprovalDecisionRequest
from agentium.api.http.handler_constants import admin_scope, cap_granted


class ApprovalsAuditHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_get_approval(self, approval_id: str) -> None:
        if not approval_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_approval_id", "approval_id is required.")
            return
        state = self.api.get_approval(approval_id)
        if state is None:
            self._write_error(HTTPStatus.NOT_FOUND, "approval_not_found", "Unknown approval id.")
            return
        self._write_json(HTTPStatus.OK, state.dict())

    def _handle_approval_decision(self, approval_id: str) -> None:
        if not approval_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_approval_id", "approval_id is required.")
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = ApprovalDecisionRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        response = self.api.decide_approval(
            approval_id=approval_id,
            decision=payload.decision,
            approver_id=payload.approver_id,
            comment=payload.comment,
        )
        if not response.applied:
            self._write_json(HTTPStatus.CONFLICT, response.dict())
            return
        self._write_json(HTTPStatus.OK, response.dict())

    def _handle_query_audit_events(self, query: str) -> None:
        params = parse_qs(query, keep_blank_values=False)
        limit_raw = params.get("limit", ["100"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        if limit <= 0:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be positive.")
            return

        run_id = params.get("run_id", [None])[0]
        tenant_id = params.get("tenant_id", [None])[0]
        event_type = params.get("event_type", [None])[0]
        events = self.api.get_audit_events(
            run_id=run_id,
            tenant_id=tenant_id,
            event_type=event_type,
            limit=limit,
        )
        self._write_json(
            HTTPStatus.OK,
            {"count": len(events), "events": [json.loads(event.json()) for event in events]},
        )

    def _handle_list_approvals(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "approval.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability approval.read required.")
            return
        params = parse_qs(query, keep_blank_values=False)
        status = params.get("status", [None])[0]
        tenant_q = params.get("tenant_id", [None])[0]
        limit_raw = params.get("limit", ["100"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        tenant_id = info.tenant_id if not admin_scope(info.roles) else (tenant_q or info.tenant_id)
        rows = self.api.list_approval_states(tenant_id=tenant_id, status=status, limit=limit)
        self._write_json(HTTPStatus.OK, {"count": len(rows), "approvals": [r.dict() for r in rows]})

    def _handle_audit_export(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "export.audit.redacted"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability export.audit.redacted required.")
            return
        params = parse_qs(query, keep_blank_values=False)
        run_id = params.get("run_id", [None])[0]
        if not run_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_run_id", "run_id query parameter is required.")
            return
        redact = params.get("redact", ["1"])[0] in {"1", "true", "yes"}
        events = self.api.get_audit_events(run_id=run_id, limit=10_000)
        if events and events[0].tenant_id != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot export another tenant's audit trail.")
            return
        out: List[Dict[str, Any]] = []
        for e in events:
            d = json.loads(e.json())
            if redact and isinstance(d.get("payload"), dict):
                d["payload"] = {k: "[REDACTED]" for k in d["payload"]}
            out.append(d)
        self._write_json(HTTPStatus.OK, {"run_id": run_id, "count": len(out), "events": out})
