"""DeepResearch, eval gates, and workflow resume/get."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs
from uuid import uuid4

from pydantic import ValidationError

from agentium.api.http.control_plane_schemas import (
    EvalCompareRequest,
    EvolutionTrajectorySubmitRequest,
    ResearchRunRequest,
    WorkflowResumeRequest,
)
from agentium.api.http.handler_constants import admin_scope, cap_granted
from agentium.evaluation.eval_compare import compare_eval_summaries
from agentium.evaluation.release_gates_runner import collect_release_gate_summary
from agentium.governance.evolution_plugin import TrajectoryBatch, TrajectoryEvent
from agentium.governance.evolution_trajectory import sanitize_trajectory_batch
from agentium.models.context import RequestContext
from agentium.shared.errors import PolicyDeniedError


class ResearchEvalWorkflowHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_research_get(self, run_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "research.run"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability research.run required.")
            return
        pipe = self.resources.deep_research_pipeline if self.resources else None
        if pipe is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "research_unavailable", "Research pipeline not configured.")
            return
        snap = pipe.workflow_snapshot_for_http(run_id)
        if snap is None:
            self._write_error(HTTPStatus.NOT_FOUND, "research_run_not_found", "Unknown research run.")
            return
        if snap.get("tenant_id") != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot read another tenant's research run.")
            return
        self._write_json(HTTPStatus.OK, snap)

    def _handle_workflow_get(self, run_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "workflow.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability workflow.read required.")
            return
        pipe = self.resources.deep_research_pipeline if self.resources else None
        if pipe is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "workflow_unavailable", "Workflow orchestrator not available.")
            return
        snap = pipe.workflow_snapshot_for_http(run_id)
        if snap is None:
            self._write_error(HTTPStatus.NOT_FOUND, "workflow_run_not_found", "Unknown workflow run.")
            return
        if snap.get("tenant_id") != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot read another tenant's workflow.")
            return
        self._write_json(HTTPStatus.OK, snap)

    def _handle_research_run(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "research.run"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability research.run required.")
            return
        try:
            payload = ResearchRunRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        pipe = self.resources.deep_research_pipeline if self.resources else None
        if pipe is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "research_unavailable", "Research pipeline not configured.")
            return
        ctx = RequestContext(
            request_id=payload.request_id,
            run_id=payload.run_id,
            tenant_id=headers["tenant_id"],
            user_id=headers["user_id"],
            trace_id=payload.trace_id,
            role=headers["role"],
            deployment_mode=payload.deployment_mode,
        )
        try:
            outcome = pipe.run(
                ctx,
                payload.query,
                extras={"vertical_template": payload.vertical_template},
            )
        except Exception as exc:  # noqa: BLE001
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "research_failed",
                str(exc),
            )
            return
        snap = pipe.workflow_snapshot_for_http(payload.run_id)
        self._write_json(
            HTTPStatus.OK,
            {
                "success": outcome.success,
                "run_id": payload.run_id,
                "artifacts": outcome.artifacts,
                "report": outcome.report,
                "workflow": snap,
            },
        )

    def _handle_eval_gates_post(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "eval.run"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability eval.run required.")
            return
        try:
            summary = collect_release_gate_summary()
        except Exception as exc:  # noqa: BLE001
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "eval_failed",
                str(exc),
            )
            return
        eval_id = str(uuid4())
        estore = self.resources.eval_run_store if self.resources else None
        if estore is not None:
            estore.insert(
                eval_id=eval_id,
                tenant_id=info.tenant_id,
                user_id=info.user_id,
                passed=bool(summary.get("passed")),
                summary=summary,
            )
        payload = dict(summary)
        payload["eval_id"] = eval_id
        self._write_json(HTTPStatus.OK, payload)

    def _handle_eval_runs_list(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "eval.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability eval.read required.")
            return
        estore = self.resources.eval_run_store if self.resources else None
        if estore is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE, "eval_store_unavailable", "Eval persistence not configured."
            )
            return
        params = parse_qs(query, keep_blank_values=False)
        try:
            limit = int(params.get("limit", ["20"])[0] or "20")
            cursor_raw = params.get("cursor", ["0"])[0] or "0"
            after_id = int(cursor_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_query", "limit and cursor must be integers.")
            return
        tenant_id = info.tenant_id if not admin_scope(info.roles) else (
            params.get("tenant_id", [info.tenant_id])[0] or info.tenant_id
        )
        rows, next_c = estore.list_page(tenant_id=tenant_id, after_id=after_id, limit=limit)
        slim = [
            {
                "id": r["id"],
                "eval_id": r["eval_id"],
                "created_at": r["created_at"],
                "passed": r["passed"],
            }
            for r in rows
        ]
        self._write_json(
            HTTPStatus.OK,
            {"count": len(slim), "runs": slim, "next_cursor": next_c},
        )

    def _handle_eval_compare_post(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "eval.compare"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability eval.compare required.")
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            req = EvalCompareRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        estore = self.resources.eval_run_store if self.resources else None
        if estore is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE, "eval_store_unavailable", "Eval persistence not configured."
            )
            return
        base = estore.get(req.baseline_eval_id)
        cand = estore.get(req.candidate_eval_id)
        if base is None or cand is None:
            self._write_error(HTTPStatus.NOT_FOUND, "eval_not_found", "One or both eval_id not found.")
            return
        if base["tenant_id"] != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot compare another tenant's eval runs.")
            return
        if cand["tenant_id"] != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot compare another tenant's eval runs.")
            return
        diff = compare_eval_summaries(base["summary"], cand["summary"])
        self._write_json(
            HTTPStatus.OK,
            {
                "baseline_eval_id": req.baseline_eval_id,
                "candidate_eval_id": req.candidate_eval_id,
                "diff": diff,
            },
        )

    def _handle_evolution_trajectory_post(self) -> None:
        res = self.resources
        if res is None or not getattr(res, "evolution_http_enabled", False):
            self._write_error(
                HTTPStatus.NOT_FOUND,
                "evolution_http_disabled",
                "Evolution HTTP ingestion is disabled (set evolution.http_enabled in plugins YAML).",
            )
            return
        if getattr(res, "evolution_plugin", None) is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "evolution_unavailable",
                "Evolution plugin is not configured on this process.",
            )
            return
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "evolution.submit"):
            self._write_error(
                HTTPStatus.FORBIDDEN,
                "forbidden",
                "Capability evolution.submit required.",
            )
            return
        try:
            payload = EvolutionTrajectorySubmitRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc)
            )
            return
        events = [
            TrajectoryEvent(step_type=e.step_type, payload=dict(e.payload)) for e in payload.events
        ]
        batch = TrajectoryBatch(run_id=payload.run_id, events=events)
        try:
            batch = sanitize_trajectory_batch(batch)
        except ValueError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "trajectory_invalid",
                str(exc),
            )
            return
        ctx = RequestContext(
            request_id=payload.request_id,
            run_id=payload.run_id,
            tenant_id=headers["tenant_id"],
            user_id=headers["user_id"],
            trace_id=payload.trace_id,
            role=headers["role"],
            deployment_mode=payload.deployment_mode,
        )
        try:
            res.evolution_plugin.on_trajectory(ctx, batch)
        except Exception as exc:  # noqa: BLE001
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "evolution_submit_failed",
                str(exc),
            )
            return
        self._write_json(
            HTTPStatus.OK,
            {"accepted": True, "event_count": len(batch.events), "run_id": payload.run_id},
        )

    def _handle_workflow_resume(self, run_id: str) -> None:
        body = self._read_json_body()
        if body is None:
            return
        headers = self._extract_identity_headers()
        if headers is None:
            return
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "workflow.intervene"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability workflow.intervene required.")
            return
        try:
            payload = WorkflowResumeRequest.parse_obj(body)
        except ValidationError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Invalid request body.", detail=str(exc))
            return
        pipe = self.resources.deep_research_pipeline if self.resources else None
        if pipe is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "workflow_unavailable", "Workflow orchestrator not available.")
            return
        rid = self.headers.get("X-Request-Id", "").strip() or f"wf_resume_{run_id}"
        tid = self.headers.get("X-Trace-Id", "").strip() or rid
        ctx = RequestContext(
            request_id=rid,
            run_id=run_id,
            tenant_id=headers["tenant_id"],
            user_id=headers["user_id"],
            trace_id=tid,
            role=headers["role"],
        )
        try:
            pipe.orchestrator.resume(ctx, pipe.spec, payload.approval_id)
        except PolicyDeniedError as exc:
            self._write_error(HTTPStatus.CONFLICT, "workflow_resume_denied", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "workflow_resume_failed",
                str(exc),
            )
            return
        snap = pipe.workflow_snapshot_for_http(run_id)
        self._write_json(HTTPStatus.OK, {"run_id": run_id, "workflow": snap})
