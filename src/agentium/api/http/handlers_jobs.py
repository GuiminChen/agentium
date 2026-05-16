"""HTTP handlers for persisted scheduled jobs (MVP)."""

from __future__ import annotations

import hashlib
import hmac
import json
from http import HTTPStatus
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

import structlog

from agentium.api.http.handler_constants import cap_granted
from agentium.infra.db.sqlite_scheduled_job_store import compute_initial_next_run_at_unix_ms
from agentium.models.context import DecisionType, RequestContext
from agentium.models.scheduled_job import (
    ScheduledJobCreateRequest,
    ScheduledJobPatchRequest,
    validate_chat_turn_payload,
    validate_trigger_dict,
)

_LOGGER = structlog.get_logger(__name__)


def parse_scheduled_job_collection_path(path: str) -> bool:
    """True if ``GET/POST /v1/jobs`` (exact)."""

    p = path.rstrip("/") or "/"
    return p == "/v1/jobs"


def parse_scheduled_job_webhook_path(path: str) -> bool:
    return path.rstrip("/") == "/v1/jobs/webhook-trigger"


def parse_scheduled_job_subresource(path: str) -> Optional[Tuple[str, str]]:
    """Return ``("job", id)`` or ``("runs", id)`` for ``/v1/jobs/...``."""

    prefix = "/v1/jobs/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :].strip("/")
    if not rest:
        return None
    parts = [x for x in rest.split("/") if x]
    if len(parts) == 1:
        return ("job", parts[0])
    if len(parts) == 2 and parts[1] == "runs":
        return ("runs", parts[0])
    return None


def parse_scheduled_job_trigger_path(path: str) -> Optional[str]:
    """Return ``job_id`` for ``POST /v1/jobs/{job_id}/trigger``."""

    p = path.rstrip("/")
    suffix = "/trigger"
    if not p.startswith("/v1/jobs/") or not p.endswith(suffix):
        return None
    job_id = p[len("/v1/jobs/") : -len(suffix)].strip("/")
    return job_id or None


class ScheduledJobsHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _scheduled_jobs_policy_allows_manage(self, info) -> bool:  # type: ignore[no-untyped-def]
        """Optional YAML policy gate on ``scheduled_job.manage`` (tool-shaped hook)."""

        settings = self.resources.settings if self.resources else None
        if settings is None or not getattr(settings, "scheduled_jobs_policy_gate_enabled", False):
            return True
        engine = self.resources.policy_engine if self.resources else None
        if engine is None and self.resources is not None:
            tr = self.resources.tool_registry
            if tr is not None and hasattr(tr, "base_policy_engine"):
                engine = tr.base_policy_engine
        if engine is None:
            return True
        ctx = RequestContext(
            request_id="_scheduled_job_http",
            run_id="_scheduled_job_http",
            tenant_id=info.tenant_id,
            user_id=info.user_id,
            trace_id="_scheduled_job_http",
            role=info.role,
        )
        dec = engine.decide_tool_call(ctx, "scheduled_job.manage")
        return dec.decision != DecisionType.DENY

    def _scheduled_jobs_store(self):  # type: ignore[no-untyped-def]
        if self.resources is None or self.resources.scheduled_job_store is None:
            return None
        return self.resources.scheduled_job_store

    def _scheduled_jobs_runner(self):  # type: ignore[no-untyped-def]
        if self.resources is None:
            return None
        return self.resources.scheduled_job_runner

    def _utc_ms_now(self) -> int:
        from datetime import datetime, timezone

        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def _handle_jobs_list(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.read required.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        qs = parse_qs(query)
        page = int(qs.get("page", ["1"])[0] or 1)
        page_size = int(qs.get("page_size", ["20"])[0] or 20)
        rows, total = store.list_jobs(tenant_id=info.tenant_id, page=page, page_size=page_size)
        items = [r.as_public_dict() for r in rows]
        self._write_json(
            HTTPStatus.OK,
            {
                "items": items,
                "pagination": {"page": page, "page_size": page_size, "total": total},
            },
        )

    def _handle_jobs_create(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.manage required.")
            return
        if not self._scheduled_jobs_policy_allows_manage(info):
            self._write_error(HTTPStatus.FORBIDDEN, "policy_denied", "Policy denied scheduled job management.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            req = ScheduledJobCreateRequest.model_validate(body)
            trig = validate_trigger_dict(dict(req.trigger))
            payload = validate_chat_turn_payload(dict(req.payload))
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_job_body", str(exc))
            return
        try:
            now_ms = self._utc_ms_now()
            next_run = compute_initial_next_run_at_unix_ms(trig, now_ms)
            row = store.insert_job(
                tenant_id=info.tenant_id,
                user_id=info.user_id,
                name=req.name,
                enabled=req.enabled,
                task_kind=req.task_kind,
                trigger=trig,
                session_binding=req.session_binding,
                pinned_session_id=(
                    (req.pinned_session_id or "").strip() if req.session_binding == "pinned_session" else None
                ),
                payload=payload,
                policy_bundle_ref=req.policy_bundle_ref,
                budget_estimate_tokens=req.budget_estimate_tokens,
                max_retries=req.max_retries,
                timeout_seconds=req.timeout_seconds,
                next_run_at_unix_ms=next_run if req.enabled else None,
            )
        except Exception as exc:
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, "job_create_failed", str(exc))
            return
        self._write_json(HTTPStatus.CREATED, row.as_public_dict())

    def _handle_job_get(self, job_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.read required.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        row = store.get_job(job_id=job_id, tenant_id=info.tenant_id)
        if row is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        self._write_json(HTTPStatus.OK, row.as_public_dict())

    def _handle_job_put(self, job_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.manage required.")
            return
        if not self._scheduled_jobs_policy_allows_manage(info):
            self._write_error(HTTPStatus.FORBIDDEN, "policy_denied", "Policy denied scheduled job management.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            patch = ScheduledJobPatchRequest.model_validate(body)
        except Exception as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_job_patch", str(exc))
            return

        current = store.get_job(job_id=job_id, tenant_id=info.tenant_id)
        if current is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return

        updates: Dict[str, Any] = {}
        if patch.name is not None:
            updates["name"] = patch.name
        if patch.enabled is not None:
            updates["enabled"] = patch.enabled
        trig_payload: Optional[Dict[str, Any]] = None
        if patch.trigger is not None:
            trig_payload = validate_trigger_dict(dict(patch.trigger))
            updates["trigger"] = trig_payload
        if patch.session_binding is not None:
            updates["session_binding"] = patch.session_binding
        if patch.pinned_session_id is not None:
            updates["pinned_session_id"] = patch.pinned_session_id.strip() or None
        if patch.payload is not None:
            updates["payload"] = validate_chat_turn_payload(dict(patch.payload))
        if patch.policy_bundle_ref is not None:
            updates["policy_bundle_ref"] = patch.policy_bundle_ref
        patch_dump = patch.model_dump(exclude_unset=True)
        if "budget_estimate_tokens" in patch_dump:
            updates["budget_estimate_tokens"] = patch.budget_estimate_tokens
        if patch.max_retries is not None:
            updates["max_retries"] = patch.max_retries
        if patch.timeout_seconds is not None:
            updates["timeout_seconds"] = patch.timeout_seconds

        eff_binding = (
            patch.session_binding if patch.session_binding is not None else current.session_binding
        )
        if patch.pinned_session_id is not None:
            eff_pinned = patch.pinned_session_id.strip() or None
        else:
            eff_pinned = current.pinned_session_id
        if eff_binding == "pinned_session" and not (eff_pinned or "").strip():
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_job_patch",
                "pinned_session required when session_binding is pinned_session.",
            )
            return

        merged_trigger = trig_payload if trig_payload is not None else json.loads(current.trigger_json)
        if patch.enabled is True or (
            patch.trigger is not None and current.enabled and patch.enabled is not False
        ):
            updates["next_run_at_unix_ms"] = compute_initial_next_run_at_unix_ms(
                merged_trigger,
                self._utc_ms_now(),
            )
        if patch.enabled is False:
            updates["next_run_at_unix_ms"] = None

        row = store.patch_job(job_id=job_id, tenant_id=info.tenant_id, updates=updates)
        if row is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        self._write_json(HTTPStatus.OK, row.as_public_dict())

    def _handle_job_delete(self, job_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.manage"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.manage required.")
            return
        if not self._scheduled_jobs_policy_allows_manage(info):
            self._write_error(HTTPStatus.FORBIDDEN, "policy_denied", "Policy denied scheduled job management.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        ok = store.delete_job(job_id=job_id, tenant_id=info.tenant_id)
        if not ok:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        self._write_json(HTTPStatus.OK, {"deleted": True, "job_id": job_id})

    def _handle_job_runs_list(self, job_id: str, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.read required.")
            return
        store = self._scheduled_jobs_store()
        if store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Scheduled job store off.")
            return
        if store.get_job(job_id=job_id, tenant_id=info.tenant_id) is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        qs = parse_qs(query)
        page = int(qs.get("page", ["1"])[0] or 1)
        page_size = int(qs.get("page_size", ["20"])[0] or 20)
        status_filter = qs.get("status", [None])[0] or None
        started_after = qs.get("started_after", [None])[0] or None
        started_before = qs.get("started_before", [None])[0] or None
        rows, total = store.list_runs(
            tenant_id=info.tenant_id,
            job_id=job_id,
            page=page,
            page_size=page_size,
            status_filter=status_filter,
            started_after=started_after,
            started_before=started_before,
        )
        items = [r.as_public_dict() for r in rows]
        self._write_json(
            HTTPStatus.OK,
            {"items": items, "pagination": {"page": page, "page_size": page_size, "total": total}},
        )

    def _handle_job_trigger(self, job_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "jobs.trigger"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability jobs.trigger required.")
            return
        if not self._scheduled_jobs_policy_allows_manage(info):
            self._write_error(HTTPStatus.FORBIDDEN, "policy_denied", "Policy denied scheduled job management.")
            return
        runner = self._scheduled_jobs_runner()
        if runner is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_runner_unavailable", "Runner not configured.")
            return
        try:
            result = runner.execute_manual(job_id=job_id, tenant_id=info.tenant_id)
        except Exception as exc:
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, "job_trigger_failed", str(exc))
            return
        if result is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        self._write_json(HTTPStatus.ACCEPTED, {"accepted": True, "job_id": job_id})

    def _handle_jobs_webhook_trigger(self) -> None:
        settings = self.resources.settings if self.resources else None
        secret = getattr(settings, "scheduled_jobs_webhook_secret", None) if settings else None
        content_length_header = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_header)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length.")
            return
        if content_length <= 0:
            self._write_error(HTTPStatus.BAD_REQUEST, "empty_body", "Body required.")
            return
        raw_body = self.rfile.read(content_length)
        if secret:
            sig_hdr = (
                self.headers.get("X-Agentium-Job-Signature")
                or self.headers.get("X-Agentium-Signature")
                or ""
            ).strip()
            mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_hdr, mac):
                self._write_error(HTTPStatus.UNAUTHORIZED, "invalid_signature", "Invalid webhook signature.")
                return
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json", "Body must be JSON.")
            return
        if not isinstance(body, dict):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json_payload", "JSON root must be an object.")
            return
        job_id = str(body.get("job_id") or "").strip()
        tenant_id = str(body.get("tenant_id") or "").strip()
        if not job_id or not tenant_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_fields", "job_id and tenant_id required.")
            return
        runner = self._scheduled_jobs_runner()
        store = self._scheduled_jobs_store()
        if runner is None or store is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "jobs_unavailable", "Jobs subsystem unavailable.")
            return
        if secret is None:
            self._write_error(HTTPStatus.NOT_FOUND, "webhook_disabled", "Webhook secret not configured.")
            return
        row = store.get_job(job_id=job_id, tenant_id=tenant_id)
        if row is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Job not found.")
            return
        idem_hdr = (self.headers.get("Idempotency-Key") or "").strip()
        if idem_hdr:
            if not store.claim_webhook_idempotency_key(
                tenant_id=tenant_id,
                job_id=job_id,
                idempotency_key=idem_hdr,
                received_unix_ms=self._utc_ms_now(),
            ):
                _LOGGER.info(
                    "scheduled_job_webhook_idempotent_hit",
                    tenant_id=tenant_id,
                    job_id=job_id,
                )
                self._write_json(
                    HTTPStatus.ACCEPTED,
                    {"accepted": True, "job_id": job_id, "deduplicated": True},
                )
                return
        try:
            runner.execute_manual(job_id=job_id, tenant_id=tenant_id)
        except Exception as exc:
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, "job_trigger_failed", str(exc))
            return
        self._write_json(HTTPStatus.ACCEPTED, {"accepted": True, "job_id": job_id})
