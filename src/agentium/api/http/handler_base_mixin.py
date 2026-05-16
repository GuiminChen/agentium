"""Shared handler infrastructure: JSON I/O, identity, manifest ingress."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.control_plane_schemas import ResumeTurnRequest, TurnRequest
from agentium.api.http.handler_constants import MANIFEST_REJECTED, TENANT_BAD_CHARS
from agentium.api.http.identity import IdentityInfo, make_identity_info
from agentium.api.http.json_errors import error_payload
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.governance.access_control import IdentityProvider, Principal
from agentium.models.context import AuditRecord, RequestContext
from agentium.models.run_manifest import RunManifestPolicy, parse_run_manifest_payload


class ControlPlaneHTTPHandlerBaseMixin:
    """Mixin: must precede ``BaseHTTPRequestHandler`` in MRO."""

    api: ControlPlaneAPI = None  # type: ignore[assignment]
    identity_provider: Optional[IdentityProvider] = None
    identity_mode: str = "hybrid"
    manifest_policy: Optional[RunManifestPolicy] = None
    audit_sink: Any = None
    state_observer: Any = None
    resources: Optional[HTTPControlPlaneResources] = None  # type: ignore[assignment]
    # Optional resolver returning the active policy bundle ref for a tenant.
    # When set, ingress writes a warning-level ``policy_bundle_ref_mismatch``
    # audit event if the manifest-declared ref diverges from the resolver's
    # value. The request still proceeds; enforcement remains with PolicyEngine.
    policy_bundle_resolver: Optional[Callable[[str], Optional[str]]] = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Silence default stdout logging for test-friendly behavior."""

    def _write_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        assert isinstance(self, BaseHTTPRequestHandler)
        self.wfile.write(encoded)

    def _begin_sse_response(self) -> None:
        """Start a UTF-8 Server-Sent Events response (newline-delimited ``data:`` JSON chunks)."""

        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        assert isinstance(self, BaseHTTPRequestHandler)
        self.end_headers()

    def _write_sse_json_event(self, payload: Dict[str, Any]) -> None:
        assert isinstance(self, BaseHTTPRequestHandler)
        chunk = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str = "",
        detail: Any = None,
    ) -> None:
        self._write_json(status, error_payload(code, message, detail))

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        content_length_header = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_header)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length header.")
            return None
        if content_length <= 0:
            self._write_error(HTTPStatus.BAD_REQUEST, "empty_body", "Request body is required.")
            return None
        assert isinstance(self, BaseHTTPRequestHandler)
        raw_body = self.rfile.read(content_length)
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json", "Body must be JSON.")
            return None
        if not isinstance(parsed, dict):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json_payload", "JSON root must be an object.")
            return None
        return parsed

    def _principal_to_identity_info(self, principal: Principal) -> IdentityInfo:
        roles = sorted(principal.roles) if principal.roles else ["user"]
        return make_identity_info(principal.tenant_id, principal.subject, roles)

    def _resolve_identity(self) -> Optional[IdentityInfo]:
        """Resolve caller identity; on failure write HTTP response and return None."""

        auth_header = self.headers.get("Authorization", "").strip()
        mode = self.identity_mode
        if mode == "bearer":
            principal = self._authenticate_bearer_principal(auth_header=auth_header)
            if principal is None:
                if not auth_header:
                    self._write_error(HTTPStatus.UNAUTHORIZED, "missing_bearer_token", "Bearer token required.")
                return None
            return self._principal_to_identity_info(principal)
        if mode == "hybrid":
            principal = self._authenticate_bearer_principal(auth_header=auth_header)
            if auth_header and principal is None:
                return None
            if principal is not None:
                return self._principal_to_identity_info(principal)
        elif mode != "header":
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "invalid_identity_mode",
                "Server identity_mode is misconfigured.",
            )
            return None

        tenant_id = self.headers.get("X-Tenant-Id", "").strip()
        user_id = self.headers.get("X-User-Id", "").strip()
        role = self.headers.get("X-Role", "").strip() or "user"
        if not tenant_id or not user_id:
            self._write_error(
                HTTPStatus.UNAUTHORIZED,
                "missing_identity_headers",
                "X-Tenant-Id and X-User-Id are required.",
                detail={"required": ["X-Tenant-Id", "X-User-Id"]},
            )
            return None
        if any(ch in TENANT_BAD_CHARS for ch in tenant_id):
            self._audit_manifest_event(tenant_id, "tenant_id_invalid_chars")
            self._write_error(HTTPStatus.BAD_REQUEST, "tenant_id_invalid_chars", "Tenant id contains invalid characters.")
            return None
        return make_identity_info(tenant_id, user_id, [role])

    def _extract_identity_headers(self) -> Optional[Dict[str, str]]:
        info = self._resolve_identity()
        if info is None:
            return None
        return {"tenant_id": info.tenant_id, "user_id": info.user_id, "role": info.role}

    def _authenticate_bearer_principal(self, auth_header: str):
        if not auth_header:
            return None
        if not auth_header.startswith("Bearer "):
            self._write_error(HTTPStatus.UNAUTHORIZED, "invalid_authorization_header", "Expected Bearer token.")
            return None
        if self.identity_provider is None:
            self._write_error(
                HTTPStatus.UNAUTHORIZED,
                "identity_provider_not_configured",
                "Bearer authentication is not configured.",
            )
            return None
        token = auth_header[len("Bearer ") :].strip()
        if not token:
            self._write_error(HTTPStatus.UNAUTHORIZED, "empty_bearer_token", "Bearer token is empty.")
            return None
        principal = self.identity_provider.authenticate(token)
        if principal is None:
            self._write_error(HTTPStatus.UNAUTHORIZED, "invalid_bearer_token", "Token rejected.")
            return None
        return principal

    def _build_context(
        self,
        payload: Union[TurnRequest, ResumeTurnRequest],
        identity: Dict[str, str],
        manifest_sha: Optional[str] = None,
        manifest_declared_tools: Optional[List[str]] = None,
    ) -> RequestContext:
        return RequestContext(
            request_id=payload.request_id,
            run_id=payload.run_id,
            tenant_id=identity["tenant_id"],
            user_id=identity["user_id"],
            trace_id=payload.trace_id,
            role=identity["role"],
            deployment_mode=payload.deployment_mode,
            run_manifest_sha256=manifest_sha,
            manifest_declared_tools=manifest_declared_tools,
            message_disposition=payload.message_disposition,
            mcp_execution_tier=payload.mcp_execution_tier,
        )

    def _audit_turn_ingress(
        self,
        *,
        tenant_id: str,
        run_id: str,
        request_id: str,
        trace_id: str,
        message_disposition: str,
        mcp_execution_tier: str,
    ) -> None:
        """Record turn ingress fields for PRD §3.5.1 / §3.9.2 traceability."""

        if self.audit_sink is None:
            return
        try:
            self.audit_sink.append(
                AuditRecord(
                    event_type="turn_ingress",
                    tenant_id=tenant_id,
                    run_id=run_id,
                    payload={
                        "message_disposition": message_disposition,
                        "mcp_execution_tier": mcp_execution_tier,
                        "request_id": request_id,
                        "trace_id": trace_id,
                    },
                )
            )
        except Exception:
            pass

    def _validate_run_manifest(
        self, raw_manifest: Optional[Dict[str, Any]], tenant_id: str
    ) -> Any:
        manifest, parse_error = parse_run_manifest_payload(raw_manifest)
        if parse_error is not None:
            self._audit_manifest_event(tenant_id, parse_error)
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                error_payload(parse_error, "Run manifest rejected."),
            )
            return MANIFEST_REJECTED
        if self.manifest_policy is None:
            if manifest is None:
                return (None, None)
            self._audit_policy_bundle_ref_if_mismatch(manifest, tenant_id)
            return (manifest.content_sha256(), manifest.declared_tools)
        accepted, validate_error = self.manifest_policy.validate_manifest(manifest)
        if validate_error is not None:
            self._audit_manifest_event(tenant_id, validate_error)
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                error_payload(validate_error, "Run manifest rejected."),
            )
            return MANIFEST_REJECTED
        if accepted is None:
            return (None, None)
        self._audit_policy_bundle_ref_if_mismatch(accepted, tenant_id)
        return (accepted.content_sha256(), accepted.declared_tools)

    def _audit_policy_bundle_ref_if_mismatch(
        self, manifest: Any, tenant_id: str
    ) -> None:
        """Emit ``policy_bundle_ref_mismatch`` when manifest ref differs from active.

        The check is warning-level: no exception, no response override. It
        records divergence so operators can correlate replayable runs with the
        policy version that was actually active at ingress time.
        """

        if self.audit_sink is None or self.policy_bundle_resolver is None:
            return
        declared_ref = getattr(manifest, "policy_bundle_ref", None)
        if declared_ref is None:
            return
        try:
            active_ref = self.policy_bundle_resolver(tenant_id)
        except Exception:
            return
        if active_ref is None:
            return
        if declared_ref == active_ref:
            return
        try:
            self.audit_sink.append(
                AuditRecord(
                    event_type="policy_bundle_ref_mismatch",
                    tenant_id=tenant_id or "_unknown",
                    run_id="_ingress",
                    policy_version=active_ref,
                    payload={
                        "declared_ref": declared_ref,
                        "active_ref": active_ref,
                    },
                )
            )
        except Exception:
            pass

    def _audit_manifest_event(self, tenant_id: str, error_code: str) -> None:
        if self.audit_sink is None:
            return
        try:
            self.audit_sink.append(
                AuditRecord(
                    event_type="run_manifest_rejected",
                    tenant_id=tenant_id or "_unknown",
                    run_id="_ingress",
                    payload={"error_code": error_code},
                )
            )
        except Exception:
            pass

    @staticmethod
    def _parse_policy_release_action(path: str) -> Optional[Tuple[str, str]]:
        prefix = "/v1/policies/releases/"
        if not path.startswith(prefix):
            return None
        remaining = path[len(prefix) :].strip("/")
        parts = remaining.split("/")
        if len(parts) != 2:
            return None
        release_id, action = parts
        if action not in {"approve", "activate", "rollback"}:
            return None
        return release_id, action

    @staticmethod
    def _parse_budget_summary_path(path: str) -> Optional[str]:
        prefix = "/v1/budget/tenant/"
        suffix = "/summary"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        return path[len(prefix) : -len(suffix)].strip("/") or None

    @staticmethod
    def _parse_run_timeline_path(path: str) -> Optional[str]:
        suffix = "/timeline"
        if not path.startswith("/v1/runs/") or not path.endswith(suffix):
            return None
        mid = path[len("/v1/runs/") : -len(suffix)].strip("/")
        if not mid or "/" in mid:
            return None
        return mid

    @staticmethod
    def _parse_run_artifacts_path(path: str) -> Optional[str]:
        suffix = "/artifacts"
        if not path.startswith("/v1/runs/") or not path.endswith(suffix):
            return None
        mid = path[len("/v1/runs/") : -len(suffix)].strip("/")
        if not mid or "/" in mid:
            return None
        return mid

    @staticmethod
    def _parse_task_graph_path(path: str) -> Optional[str]:
        prefix = "/v1/task-graph/"
        if not path.startswith(prefix):
            return None
        rid = path[len(prefix) :].strip("/")
        return rid or None

    @staticmethod
    def _parse_research_get_path(path: str) -> Optional[str]:
        prefix = "/v1/research/"
        if not path.startswith(prefix):
            return None
        rid = path[len(prefix) :].strip("/")
        if not rid or "/" in rid:
            return None
        if rid == "jobs":
            return None
        return rid

    @staticmethod
    def _parse_research_job_detail_path(path: str) -> Optional[str]:
        prefix = "/v1/research/jobs/"
        if not path.startswith(prefix):
            return None
        jid = path[len(prefix) :].strip("/")
        if not jid or "/" in jid:
            return None
        return jid

    @staticmethod
    def _parse_workflow_get_path(path: str) -> Optional[str]:
        if not path.startswith("/v1/workflows/") or path.endswith("/resume"):
            return None
        rid = path[len("/v1/workflows/") :].strip("/")
        if not rid or "/" in rid:
            return None
        return rid

    @staticmethod
    def _parse_workflow_resume_path(path: str) -> Optional[str]:
        prefix = "/v1/workflows/"
        suffix = "/resume"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        rid = path[len(prefix) : -len(suffix)].strip("/")
        return rid or None


__all__ = ["ControlPlaneHTTPHandlerBaseMixin"]
