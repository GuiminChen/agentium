"""Profile, version, UI links, connectors, turn / resume handlers."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from typing import Any, Dict

from pydantic import ValidationError

from agentium.api.http.capabilities import (
    capabilities_for_roles,
    deployment_mode_from_env,
    ui_profile_for_roles,
)
from agentium.api.http.control_plane_schemas import ResumeTurnRequest, TurnRequest
from agentium.api.http.handler_constants import MANIFEST_REJECTED, admin_scope, cap_granted


class MiscTurnHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_get_me(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        roles_list = list(info.roles)
        payload = {
            "user_id": info.user_id,
            "tenant_id": info.tenant_id,
            "role": info.role,
            "roles": roles_list,
            "deployment_mode": deployment_mode_from_env(),
            "capabilities": capabilities_for_roles(roles_list),
            "ui_profile": ui_profile_for_roles(roles_list),
        }
        self._write_json(HTTPStatus.OK, payload)

    def _handle_version(self) -> None:
        sha = os.environ.get("GIT_SHA", os.environ.get("AGENTIUM_GIT_SHA", "")).strip()
        self._write_json(
            HTTPStatus.OK,
            {"service": "agentium", "version": "0.1.0", "git_sha": sha or "unknown"},
        )

    def _handle_ui_links(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "observability.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability observability.read required.")
            return
        links: Dict[str, str] = {}
        if self.resources and self.resources.ui_links:
            links = dict(self.resources.ui_links)
        self._write_json(HTTPStatus.OK, {"links": links})

    def _handle_connectors_notify(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "connectors.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability connectors.read required.")
            return
        bridge = self.resources.notify_bridge if self.resources else None
        if bridge is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "notify_unavailable", "Notify bridge not configured.")
            return
        orch = getattr(bridge, "_orchestrator", None)
        self._write_json(
            HTTPStatus.OK,
            {
                "notify_bridge": "active",
                "orchestrator": type(orch).__name__ if orch is not None else None,
            },
        )

    def _handle_run_turn(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        try:
            payload = TurnRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        ingress = self._validate_run_manifest(payload.run_manifest, headers["tenant_id"])
        if ingress is MANIFEST_REJECTED:
            return
        manifest_sha, manifest_declared_tools = ingress
        context = self._build_context(
            payload=payload,
            identity=headers,
            manifest_sha=manifest_sha,
            manifest_declared_tools=manifest_declared_tools,
        )
        self._audit_turn_ingress(
            tenant_id=headers["tenant_id"],
            run_id=payload.run_id,
            request_id=payload.request_id,
            trace_id=payload.trace_id,
            message_disposition=payload.message_disposition,
            mcp_execution_tier=payload.mcp_execution_tier,
        )
        response = self.api.run_turn(context=context, tool_name=payload.tool_name, args=payload.args)
        status = HTTPStatus.ACCEPTED if response.status == "pending_approval" else HTTPStatus.OK
        body = response.dict()
        self._record_turn_messages(
            tenant_id=headers["tenant_id"],
            run_id=payload.run_id,
            request_id=payload.request_id,
            tool_name=payload.tool_name,
            args=payload.args,
            response_dict=body,
        )
        self._write_json(status, body)

    def _handle_resume_turn(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        try:
            payload = ResumeTurnRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        ingress = self._validate_run_manifest(payload.run_manifest, headers["tenant_id"])
        if ingress is MANIFEST_REJECTED:
            return
        manifest_sha, manifest_declared_tools = ingress
        context = self._build_context(
            payload=payload,
            identity=headers,
            manifest_sha=manifest_sha,
            manifest_declared_tools=manifest_declared_tools,
        )
        self._audit_turn_ingress(
            tenant_id=headers["tenant_id"],
            run_id=payload.run_id,
            request_id=payload.request_id,
            trace_id=payload.trace_id,
            message_disposition=payload.message_disposition,
            mcp_execution_tier=payload.mcp_execution_tier,
        )
        response = self.api.resume_turn(
            context=context,
            tool_name=payload.tool_name,
            approval_id=payload.approval_id,
            args=payload.args,
        )
        body = response.dict()
        self._record_turn_messages(
            tenant_id=headers["tenant_id"],
            run_id=payload.run_id,
            request_id=payload.request_id,
            tool_name=payload.tool_name,
            args=payload.args,
            response_dict=body,
        )
        self._write_json(HTTPStatus.OK, body)
