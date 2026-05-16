"""Background ticker + executor for persisted scheduled jobs (chat_turn MVP)."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Callable, Optional

import structlog

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionError
from agentium.app.settings import AppSettings
from agentium.background.notify_bridge import NotifyBridge, NotifyRequest
from agentium.channels.base import ChannelKind
from agentium.coordination.budget_ledger import BudgetService
from agentium.coordination.chat_agent_tool_loop import ChatPendingToolApproval
from agentium.coordination.chat_turn_service import ChatSendOutcome, ChatTurnService
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_scheduled_job_store import ScheduledJobRow, SqliteScheduledJobStore
from agentium.models.context import AuditRecord, RequestContext
from agentium.models.scheduled_job import validate_chat_turn_payload

_LOGGER = structlog.get_logger(__name__)


class ScheduledJobRunner:
    """Owns optional daemon thread; executes claimed rows via ChatTurnService."""

    def __init__(
        self,
        *,
        store: SqliteScheduledJobStore,
        chat_turn_service: ChatTurnService,
        chat_session_store: SqliteChatSessionStore,
        settings: AppSettings,
        audit_sink: Optional[Callable[[AuditRecord], None]],
        budget_service: Optional[BudgetService] = None,
        notify_bridge: Optional[NotifyBridge] = None,
    ) -> None:
        self._store = store
        self._chat = chat_turn_service
        self._sessions = chat_session_store
        self._settings = settings
        self._audit = audit_sink
        self._budget = budget_service
        self._notify_bridge = notify_bridge
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._exec_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        tick = max(5.0, float(self._settings.scheduled_jobs_tick_seconds))
        self._thread = threading.Thread(
            target=self._loop,
            args=(tick,),
            name="scheduled-job-runner",
            daemon=True,
        )
        self._thread.start()
        _LOGGER.info("scheduled_job_runner_started", tick_seconds=tick)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop(self, tick_seconds: float) -> None:
        while not self._stop.wait(timeout=tick_seconds):
            try:
                row = self._store.try_claim_due_job()
            except Exception as exc:
                _LOGGER.exception("scheduled_job_claim_failed", error=str(exc))
                continue
            if row is None:
                continue
            try:
                self.execute_claimed_row(row)
            except Exception as exc:
                _LOGGER.exception(
                    "scheduled_job_execute_unhandled",
                    job_id=row.job_id,
                    tenant_id=row.tenant_id,
                    error=str(exc),
                )

    def resolve_session_id(self, row: ScheduledJobRow) -> str:
        binding = row.session_binding
        if binding == "pinned_session":
            sid = (row.pinned_session_id or "").strip()
            if not sid:
                raise ValueError("pinned_session_id_missing")
            return sid
        if binding == "named_persistent":
            return f"schedjob-{row.job_id}"
        if binding == "fresh_each_run":
            return str(uuid.uuid4())
        raise ValueError(f"unsupported_session_binding:{binding}")

    def _ensure_chat_session(self, *, tenant_id: str, session_id: str, title: str) -> None:
        existing = self._sessions.try_get_session(tenant_id=tenant_id, session_id=session_id)
        if existing is not None:
            return
        self._sessions.create_session(
            tenant_id=tenant_id,
            session_id=session_id,
            title=title[:512],
            skill=None,
            intro_text=None,
            metadata={"scheduled_job_bootstrap": True},
        )

    def execute_claimed_row(self, row: ScheduledJobRow) -> None:
        """Run one claimed job row (insert ledger + LLM). Serialized via lock."""

        with self._exec_lock:
            self._execute_under_lock(row)

    def execute_manual(self, *, job_id: str, tenant_id: str) -> Optional[str]:
        """Operator/API trigger without advancing cron slot (same execution path)."""

        row = self._store.get_job(job_id=job_id, tenant_id=tenant_id)
        if row is None:
            return None
        with self._exec_lock:
            self._execute_under_lock(row)
        return "ok"

    def _emit_audit(self, *, event_type: str, tenant_id: str, run_id: str, payload: dict) -> None:
        if self._audit is None:
            return
        try:
            self._audit(
                AuditRecord(
                    event_type=event_type,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    payload=payload,
                )
            )
        except Exception as exc:
            _LOGGER.warning("scheduled_job_audit_failed", error=str(exc))

    def _notify_failure(
        self,
        *,
        row: ScheduledJobRow,
        run_id: str,
        err_detail: Optional[str],
    ) -> None:
        if self._notify_bridge is None:
            return
        try:
            self._notify_bridge.notify(
                NotifyRequest(
                    tenant_id=row.tenant_id,
                    title="Scheduled job failed",
                    body=f"{row.name} ({row.job_id}): {err_detail or ''}",
                    recipient=(row.user_id or "").strip() or row.tenant_id,
                    run_id=run_id,
                    kind=ChannelKind.WEB,
                )
            )
        except Exception as exc:
            _LOGGER.warning("scheduled_job_notify_failed", error=str(exc))

    def _execute_under_lock(self, row: ScheduledJobRow) -> None:
        run_id = str(uuid.uuid4())
        trace_id = f"sched-{row.job_id}-{run_id[:8]}"
        request_id = run_id

        payload_dict = json.loads(row.payload_json)
        payload = validate_chat_turn_payload(payload_dict)
        task_kind = row.task_kind
        if task_kind != "chat_turn":
            self._emit_audit(
                event_type="scheduled_job_skipped",
                tenant_id=row.tenant_id,
                run_id=run_id,
                payload={"job_id": row.job_id, "reason": "unsupported_task_kind", "task_kind": task_kind},
            )
            return

        session_id = self.resolve_session_id(row)
        self._ensure_chat_session(
            tenant_id=row.tenant_id,
            session_id=session_id,
            title=f"Job {row.name}",
        )

        raw_est = row.budget_estimate_tokens
        if raw_est is None:
            raw_est = self._settings.scheduled_job_default_budget_estimate_tokens
        estimate_tokens = max(0, int(raw_est))

        budget_ctx = RequestContext(
            request_id=request_id,
            run_id=run_id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            trace_id=trace_id,
            role="user",
            message_disposition=str(payload["message_disposition"]),
            mcp_execution_tier=str(payload["mcp_execution_tier"]),
            chat_session_id=session_id,
        )

        budget_reserved = False
        if self._budget is not None:
            if not self._budget.reserve(budget_ctx, estimate_tokens, 0.0):
                self._store.insert_run(
                    run_id=run_id,
                    job_id=row.job_id,
                    tenant_id=row.tenant_id,
                    status="skipped",
                    attempt_no=1,
                    trace_id=trace_id,
                    session_id=session_id,
                )
                self._store.finish_run(
                    run_id=run_id,
                    status="skipped",
                    error_detail="budget_exceeded",
                )
                self._emit_audit(
                    event_type="scheduled_job_run_end",
                    tenant_id=row.tenant_id,
                    run_id=run_id,
                    payload={
                        "job_id": row.job_id,
                        "trace_id": trace_id,
                        "session_id": session_id,
                        "status": "skipped",
                        "error_detail": "budget_exceeded",
                    },
                )
                return
            budget_reserved = True

        self._store.insert_run(
            run_id=run_id,
            job_id=row.job_id,
            tenant_id=row.tenant_id,
            status="running",
            attempt_no=1,
            trace_id=trace_id,
            session_id=session_id,
        )
        self._emit_audit(
            event_type="scheduled_job_run_begin",
            tenant_id=row.tenant_id,
            run_id=run_id,
            payload={"job_id": row.job_id, "trace_id": trace_id, "session_id": session_id},
        )

        err_detail: Optional[str] = None
        final_status = "succeeded"
        outcome: Optional[ChatSendOutcome] = None
        try:
            outcome = self._chat.send_user_message(
                tenant_id=row.tenant_id,
                session_id=session_id,
                user_id=row.user_id,
                caller_role="user",
                content=str(payload["message_content"]),
                trace_id=trace_id,
                message_disposition=str(payload["message_disposition"]),
                mcp_execution_tier=str(payload["mcp_execution_tier"]),
                request_id=request_id,
                llm_model=payload.get("llm_model"),
                agent_skill_override=payload.get("agent_skill_override"),
                enable_agent_tools=bool(payload.get("enable_agent_tools")),
                auto_ingress=bool(payload.get("auto_ingress")),
            )
            _LOGGER.info(
                "scheduled_job_run_ok",
                job_id=row.job_id,
                run_id=run_id,
                message_id=outcome.message_id,
                status=outcome.status,
            )
        except ChatPendingToolApproval as exc:
            final_status = "failed"
            err_detail = f"pending_tool_approval:{exc}"
        except DeepSeekChatCompletionError as exc:
            final_status = "failed"
            err_detail = f"llm_error:{exc}"
        except KeyError as exc:
            final_status = "failed"
            err_detail = f"session_error:{exc}"
        except Exception as exc:
            final_status = "failed"
            err_detail = f"unexpected:{type(exc).__name__}:{exc}"
        finally:
            if self._budget is not None and budget_reserved:
                if final_status == "succeeded":
                    actual_tokens = estimate_tokens
                    if outcome is not None:
                        if outcome.llm_total_tokens is not None:
                            actual_tokens = outcome.llm_total_tokens
                        elif (
                            outcome.llm_prompt_tokens is not None
                            and outcome.llm_completion_tokens is not None
                        ):
                            actual_tokens = outcome.llm_prompt_tokens + outcome.llm_completion_tokens
                    self._budget.commit(budget_ctx, actual_tokens, 0.0)
                else:
                    self._budget.release(budget_ctx)

        self._store.finish_run(run_id=run_id, status=final_status, error_detail=err_detail)
        self._emit_audit(
            event_type="scheduled_job_run_end",
            tenant_id=row.tenant_id,
            run_id=run_id,
            payload={
                "job_id": row.job_id,
                "trace_id": trace_id,
                "session_id": session_id,
                "status": final_status,
                "error_detail": err_detail,
            },
        )
        if final_status == "failed":
            self._notify_failure(row=row, run_id=run_id, err_detail=err_detail)


__all__ = ["ScheduledJobRunner"]
