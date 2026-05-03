"""Runs aggregation, timeline, artifacts, and task-graph endpoints."""

from __future__ import annotations

import json
from http import HTTPStatus

from urllib.parse import parse_qs

from agentium.api.http.handler_constants import admin_scope, cap_granted


class RunsArtifactsHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_runs_recent(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "runs.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability runs.read required.")
            return
        params = parse_qs(query, keep_blank_values=False)
        tenant_q = params.get("tenant_id", [None])[0]
        limit_raw = params.get("limit", ["50"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        tenant_id = info.tenant_id if not admin_scope(info.roles) else (tenant_q or info.tenant_id)
        if not tenant_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_tenant_id", "tenant_id is required.")
            return
        rows = self.api.list_run_summaries(tenant_id=tenant_id, limit=limit)
        self._write_json(HTTPStatus.OK, {"count": len(rows), "runs": rows})

    def _handle_run_timeline(self, run_id: str, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "runs.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability runs.read required.")
            return
        params = parse_qs(query, keep_blank_values=False)
        limit_raw = params.get("limit", ["500"])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        events = self.api.get_run_timeline(run_id=run_id, limit=limit)
        if events and events[0].tenant_id != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot read another tenant's timeline.")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "run_id": run_id,
                "count": len(events),
                "events": [json.loads(event.json()) for event in events],
            },
        )

    def _handle_run_artifacts(self, run_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "artifacts.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability artifacts.read required.")
            return
        store = self.resources.artifact_store if self.resources else None
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "artifacts_unavailable", "Artifact store not configured.")
            return
        arts = store.list_for_run(run_id=run_id)
        if arts and arts[0].tenant_id != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot list another tenant's artifacts.")
            return
        payload = [
            {
                "artifact_id": a.artifact_id,
                "workflow": a.workflow,
                "node": a.node,
                "run_id": a.run_id,
            }
            for a in arts
        ]
        self._write_json(HTTPStatus.OK, {"run_id": run_id, "count": len(payload), "artifacts": payload})

    def _handle_task_graph_get(self, run_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "workflow.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability workflow.read required.")
            return
        tg = self.resources.task_graph if self.resources else None
        if tg is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "task_graph_unavailable", "Task graph not configured.")
            return
        rec = tg.get(run_id)
        if rec is None:
            self._write_error(HTTPStatus.NOT_FOUND, "task_graph_not_found", "Unknown task graph run.")
            return
        if rec.tenant_id != info.tenant_id and not admin_scope(info.roles):
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot read another tenant's task graph.")
            return
        ch = tg.children_of(run_id)
        self._write_json(
            HTTPStatus.OK,
            {
                "run_id": rec.run_id,
                "tenant_id": rec.tenant_id,
                "parent_run_id": rec.parent_run_id,
                "status": rec.status.value,
                "children": [c.run_id for c in ch],
            },
        )
