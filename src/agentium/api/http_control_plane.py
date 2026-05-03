"""HTTP control-plane server for runtime and approval operations.

Routers and ``build_http_server`` live here; handlers are split under ``api.http`` mixins.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from agentium.api.control_plane import ControlPlaneAPI
from agentium.api.http.handler_base_mixin import ControlPlaneHTTPHandlerBaseMixin
from agentium.api.http.handlers_approvals_audit import ApprovalsAuditHandlersMixin
from agentium.api.http.handlers_budget_background import BudgetBackgroundHandlersMixin
from agentium.api.http.handlers_chat import ChatHTTPHandlersMixin, route_chat_dispatch
from agentium.api.http.handlers_dev_probe import DevProbeHandlersMixin
from agentium.api.http.handlers_governance_domain_pack import (
    GovernanceDomainPackHandlersMixin,
    parse_domain_pack_bundle_path,
)
from agentium.api.http.handlers_misc_turn import MiscTurnHandlersMixin
from agentium.api.http.handlers_policy import PolicyHandlersMixin
from agentium.api.http.handlers_research_eval_workflow import ResearchEvalWorkflowHandlersMixin
from agentium.api.http.handlers_runs_artifacts import RunsArtifactsHandlersMixin
from agentium.api.http.handlers_session_checkpoint import (
    SessionCheckpointHandlersMixin,
    parse_session_checkpoint_restore_path,
    parse_session_checkpoints_collection_path,
)
from agentium.api.http.handlers_session_timeline import (
    SessionTimelineHandlersMixin,
    parse_run_cancel_path,
)
from agentium.api.http.handlers_tools_catalog import ToolCatalogHandlersMixin
from agentium.api.http.resources import HTTPControlPlaneResources
from agentium.governance.access_control import IdentityProvider
from agentium.models.run_manifest import RunManifestPolicy

# Re-export schemas for callers and documentation that reference this module.
from agentium.api.http.control_plane_schemas import (  # noqa: F401
    EvalCompareRequest,
    ApprovalDecisionRequest,
    EvolutionTrajectorySubmitRequest,
    PolicyReleaseActivateRequest,
    PolicyReleaseApprovalRequest,
    PolicyReleaseRollbackRequest,
    PolicyReleaseSubmitRequest,
    ResearchRunRequest,
    ResumeTurnRequest,
    TurnRequest,
    WorkflowResumeRequest,
)


class ControlPlaneHTTPRequestHandler(
    DevProbeHandlersMixin,
    SessionCheckpointHandlersMixin,
    SessionTimelineHandlersMixin,
    ChatHTTPHandlersMixin,
    GovernanceDomainPackHandlersMixin,
    MiscTurnHandlersMixin,
    ToolCatalogHandlersMixin,
    ApprovalsAuditHandlersMixin,
    PolicyHandlersMixin,
    BudgetBackgroundHandlersMixin,
    RunsArtifactsHandlersMixin,
    ResearchEvalWorkflowHandlersMixin,
    ControlPlaneHTTPHandlerBaseMixin,
    BaseHTTPRequestHandler,
):
    """HTTP handler exposing control-plane endpoints."""

    def do_POST(self) -> None:  # noqa: N802
        """Handle HTTP POST endpoints."""

        parsed = urlparse(self.path)
        if route_chat_dispatch(self, "POST", parsed.path, parsed.query):
            return
        if parsed.path == "/v1/research/run":
            self._handle_research_run()
            return
        if parsed.path == "/v1/eval/gates":
            self._handle_eval_gates_post()
            return
        if parsed.path == "/v1/eval/compare":
            self._handle_eval_compare_post()
            return
        if parsed.path == "/v1/evolution/trajectory":
            self._handle_evolution_trajectory_post()
            return
        wf_resume_run = self._parse_workflow_resume_path(parsed.path)
        if wf_resume_run is not None:
            self._handle_workflow_resume(wf_resume_run)
            return
        if parsed.path == "/v1/background/pause":
            self._handle_background_pause()
            return
        if parsed.path == "/v1/background/resume":
            self._handle_background_resume()
            return
        if parsed.path == "/v1/background/stop":
            self._handle_background_stop()
            return
        cancel_run = parse_run_cancel_path(parsed.path)
        if cancel_run is not None:
            self._handle_run_cancel(cancel_run)
            return
        ck_restore = parse_session_checkpoint_restore_path(parsed.path)
        if ck_restore is not None:
            self._handle_session_checkpoint_restore(ck_restore[0], ck_restore[1])
            return
        ck_create = parse_session_checkpoints_collection_path(parsed.path)
        if ck_create is not None:
            self._handle_session_checkpoint_create(ck_create)
            return
        if parsed.path == "/v1/turn":
            self._handle_run_turn()
            return
        if parsed.path == "/v1/turns/resume":
            self._handle_resume_turn()
            return
        if parsed.path == "/v1/policies/releases":
            self._handle_policy_release_submit()
            return
        policy_release_action = self._parse_policy_release_action(parsed.path)
        if policy_release_action is not None:
            release_id, action = policy_release_action
            if action == "approve":
                self._handle_policy_release_approve(release_id)
                return
            if action == "activate":
                self._handle_policy_release_activate(release_id)
                return
            if action == "rollback":
                self._handle_policy_release_rollback(release_id)
                return
        if parsed.path.startswith("/v1/approvals/") and parsed.path.endswith("/decision"):
            approval_id = parsed.path[len("/v1/approvals/") : -len("/decision")].strip("/")
            self._handle_approval_decision(approval_id)
            return
        self._write_error(HTTPStatus.NOT_FOUND, "endpoint_not_found", "Unknown POST path.")

    def do_GET(self) -> None:  # noqa: N802
        """Handle HTTP GET endpoints."""

        parsed = urlparse(self.path)
        if route_chat_dispatch(self, "GET", parsed.path, parsed.query):
            return
        if parsed.path in ("/v1/healthz", "/healthz"):
            payload = {"status": "ok"}
            status = HTTPStatus.OK
            if self.state_observer is not None:
                snap = self.state_observer.snapshot()
                payload = {
                    "status": snap.status.value,
                    "ready": snap.ready,
                    "started_at": snap.started_at,
                    "observed_at": snap.observed_at,
                    "probes": [
                        {
                            "name": p.name,
                            "status": p.status.value,
                            "detail": dict(p.detail),
                            "error": p.error,
                        }
                        for p in snap.probes
                    ],
                }
                if snap.status.value == "unhealthy":
                    status = HTTPStatus.SERVICE_UNAVAILABLE
            self._write_json(status, payload)
            return
        if parsed.path in ("/v1/readyz", "/readyz"):
            ready = self.api is not None
            if self.state_observer is not None:
                snap = self.state_observer.snapshot()
                ready = ready and snap.ready
            self._write_json(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "ready" if ready else "not_ready"},
            )
            return
        if parsed.path == "/v1/version":
            self._handle_version()
            return
        if parsed.path in ("/v1/me", "/v1/auth/me"):
            self._handle_get_me()
            return
        if parsed.path == "/v1/tools":
            self._handle_tools_catalog()
            return
        if parsed.path == "/v1/config/ui-links":
            self._handle_ui_links()
            return
        if parsed.path == "/v1/dev/lsp-capabilities":
            self._handle_dev_lsp_capabilities()
            return
        if parsed.path == "/v1/policy/effective":
            self._handle_policy_effective()
            return
        if parsed.path == "/v1/policies/releases":
            self._handle_policy_releases_list(parsed.query)
            return
        if parsed.path.startswith("/v1/policies/releases/"):
            release_id = parsed.path[len("/v1/policies/releases/") :].strip("/")
            self._handle_policy_release_get(release_id)
            return
        budget_tenant = self._parse_budget_summary_path(parsed.path)
        if budget_tenant is not None:
            self._handle_budget_summary(budget_tenant)
            return
        if parsed.path == "/v1/background/status":
            self._handle_background_status()
            return
        if parsed.path == "/v1/connectors/notify":
            self._handle_connectors_notify()
            return
        if parsed.path == "/v1/connectors/inbox":
            self._handle_connectors_inbox(parsed.query)
            return
        if parsed.path == "/v1/eval/runs":
            self._handle_eval_runs_list(parsed.query)
            return
        if parsed.path == "/v1/runs/recent":
            self._handle_runs_recent(parsed.query)
            return
        pack_id = parse_domain_pack_bundle_path(parsed.path)
        if pack_id is not None:
            self._handle_domain_pack_bundle(pack_id)
            return
        ck_list = parse_session_checkpoints_collection_path(parsed.path)
        if ck_list is not None:
            self._handle_session_checkpoints_list(ck_list)
            return
        session_id = self._parse_session_messages_path(parsed.path)
        if session_id is not None:
            self._handle_session_messages(session_id, parsed.query)
            return
        run_timeline = self._parse_run_timeline_path(parsed.path)
        if run_timeline is not None:
            self._handle_run_timeline(run_timeline, parsed.query)
            return
        run_artifacts = self._parse_run_artifacts_path(parsed.path)
        if run_artifacts is not None:
            self._handle_run_artifacts(run_artifacts)
            return
        task_graph_run = self._parse_task_graph_path(parsed.path)
        if task_graph_run is not None:
            self._handle_task_graph_get(task_graph_run)
            return
        research_run = self._parse_research_get_path(parsed.path)
        if research_run is not None:
            self._handle_research_get(research_run)
            return
        workflow_run = self._parse_workflow_get_path(parsed.path)
        if workflow_run is not None:
            self._handle_workflow_get(workflow_run)
            return
        if parsed.path == "/v1/audit/export":
            self._handle_audit_export(parsed.query)
            return
        if parsed.path == "/v1/audit/events":
            self._handle_query_audit_events(parsed.query)
            return
        if parsed.path == "/v1/approvals":
            self._handle_list_approvals(parsed.query)
            return
        if parsed.path.startswith("/v1/approvals/"):
            approval_id = parsed.path[len("/v1/approvals/") :].strip("/")
            self._handle_get_approval(approval_id)
            return
        self._write_error(HTTPStatus.NOT_FOUND, "endpoint_not_found", "Unknown GET path.")

    def do_PUT(self) -> None:  # noqa: N802
        """Handle HTTP PUT (chat sessions)."""

        parsed = urlparse(self.path)
        if route_chat_dispatch(self, "PUT", parsed.path, parsed.query):
            return
        self._write_error(HTTPStatus.NOT_FOUND, "endpoint_not_found", "Unknown PUT path.")

    def do_DELETE(self) -> None:  # noqa: N802
        """Handle HTTP DELETE (chat sessions)."""

        parsed = urlparse(self.path)
        if route_chat_dispatch(self, "DELETE", parsed.path, parsed.query):
            return
        self._write_error(HTTPStatus.NOT_FOUND, "endpoint_not_found", "Unknown DELETE path.")


def build_http_server(
    api: ControlPlaneAPI,
    host: str,
    port: int,
    identity_provider: Optional[IdentityProvider] = None,
    identity_mode: str = "hybrid",
    manifest_policy: Optional[RunManifestPolicy] = None,
    audit_sink: Optional[Any] = None,
    state_observer: Optional[Any] = None,
    resources: Optional[HTTPControlPlaneResources] = None,
    policy_bundle_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> ThreadingHTTPServer:
    """Build HTTP server bound to control-plane API instance.

    ``policy_bundle_resolver`` is an optional callable mapping tenant id to the
    expected active policy bundle reference. When configured, ingress writes a
    ``policy_bundle_ref_mismatch`` audit event (warning-level, non-blocking) if
    the manifest-declared ref differs from the active one.

    When ``audit_sink`` is omitted, the handler falls back to ``api``'s internal
    audit sink (if any) so turn/manifest ingress events share the same store as
    the control-plane facade.
    """

    if audit_sink is None:
        audit_sink = getattr(api, "_audit_sink", None)

    class BoundHandler(ControlPlaneHTTPRequestHandler):
        pass

    BoundHandler.api = api
    BoundHandler.identity_provider = identity_provider
    BoundHandler.identity_mode = identity_mode
    BoundHandler.manifest_policy = manifest_policy
    BoundHandler.audit_sink = audit_sink
    BoundHandler.state_observer = state_observer
    BoundHandler.resources = resources
    BoundHandler.policy_bundle_resolver = policy_bundle_resolver
    return ThreadingHTTPServer((host, port), BoundHandler)
