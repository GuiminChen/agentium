"""Policy release and effective policy HTTP handlers."""

from __future__ import annotations

from http import HTTPStatus

from pydantic import ValidationError
from urllib.parse import parse_qs

from agentium.api.http.control_plane_schemas import (
    PolicyReleaseActivateRequest,
    PolicyReleaseApprovalRequest,
    PolicyReleaseRollbackRequest,
    PolicyReleaseSubmitRequest,
)
from agentium.api.http.handler_constants import admin_scope, cap_granted
from agentium.models.context import RequestContext
from agentium.shared.errors import AgentiumError, PolicyDeniedError


class PolicyHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_policy_release_submit(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        try:
            payload = PolicyReleaseSubmitRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        context = RequestContext(
            request_id=payload.request_id,
            run_id=payload.run_id,
            tenant_id=headers["tenant_id"],
            user_id=headers["user_id"],
            trace_id=payload.trace_id,
            role=headers["role"],
            deployment_mode="prod",
        )
        try:
            response = self.api.submit_policy_release(bundle=payload.bundle, context=context)
        except PolicyDeniedError as exc:
            self._write_error(
                HTTPStatus.FORBIDDEN,
                "policy_release_denied",
                str(exc),
            )
            return
        except AgentiumError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "policy_release_error", str(exc))
            return
        self._write_json(HTTPStatus.ACCEPTED, response.dict())

    def _handle_policy_release_approve(self, release_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = PolicyReleaseApprovalRequest.parse_obj(body)
            response = self.api.approve_policy_release(
                release_id=release_id,
                approver_id=payload.approver_id,
                comment=payload.comment,
            )
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        except AgentiumError as exc:
            self._write_error(HTTPStatus.CONFLICT, "policy_release_error", str(exc))
            return
        self._write_json(HTTPStatus.OK, response.dict())

    def _handle_policy_release_activate(self, release_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = PolicyReleaseActivateRequest.parse_obj(body)
            response = self.api.activate_policy_release(
                release_id=release_id,
                tenant_ids=payload.tenant_ids,
                activated_by=payload.activated_by,
            )
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        except AgentiumError as exc:
            self._write_error(HTTPStatus.CONFLICT, "policy_release_error", str(exc))
            return
        self._write_json(HTTPStatus.OK, response.dict())

    def _handle_policy_release_rollback(self, release_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            payload = PolicyReleaseRollbackRequest.parse_obj(body)
            response = self.api.rollback_policy_release(
                release_id=release_id,
                rolled_back_by=payload.rolled_back_by,
            )
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        except AgentiumError as exc:
            self._write_error(HTTPStatus.CONFLICT, "policy_release_error", str(exc))
            return
        self._write_json(HTTPStatus.OK, response.dict())

    def _handle_policy_release_get(self, release_id: str) -> None:
        if not release_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_release_id", "release id is required.")
            return
        try:
            response = self.api.get_policy_release(release_id)
        except AgentiumError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "policy_release_error", str(exc))
            return
        if response is None:
            self._write_error(HTTPStatus.NOT_FOUND, "policy_release_not_found", "Unknown release id.")
            return
        self._write_json(HTTPStatus.OK, response.dict())

    def _handle_policy_effective(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "governance.policy.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability governance.policy.read required.")
            return
        if self.resources is None or self.resources.tool_registry is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "policy_unavailable", "Policy engine not available.")
            return
        pe = self.resources.tool_registry.base_policy_engine
        self._write_json(HTTPStatus.OK, {"effective": pe.summarize_for_http()})

    def _handle_policy_releases_list(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "governance.releases.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability governance.releases.read required.")
            return
        params = parse_qs(query, keep_blank_values=False)
        tenant_q = params.get("tenant_id", [None])[0]
        status = params.get("status", [None])[0]
        limit_raw = params.get("limit", ["100"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        tenant_id = info.tenant_id if not admin_scope(info.roles) else (tenant_q or info.tenant_id)
        rows = self.api.try_list_policy_releases(tenant_id=tenant_id, status=status, limit=limit)
        if rows is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "policy_release_manager_unavailable",
                "Policy release manager is not configured.",
            )
            return
        self._write_json(
            HTTPStatus.OK,
            {"count": len(rows), "releases": [r.dict() for r in rows]},
        )
