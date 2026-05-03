"""Budget and background daemon HTTP handlers."""

from __future__ import annotations

from http import HTTPStatus

from agentium.api.http.handler_constants import admin_scope, cap_granted


class BudgetBackgroundHandlersMixin:
    """Mixed into ``ControlPlaneHTTPRequestHandler``."""

    def _handle_budget_summary(self, tenant_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "budget.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability budget.read required.")
            return
        if not admin_scope(info.roles) and tenant_id != info.tenant_id:
            self._write_error(HTTPStatus.FORBIDDEN, "tenant_mismatch", "Cannot read another tenant's budget.")
            return
        if self.resources is None or self.resources.budget_service is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "budget_unavailable", "Budget service not configured.")
            return
        bs = self.resources.budget_service
        summary = getattr(bs, "tenant_budget_summary", lambda _tid: None)(tenant_id)
        if summary is None:
            self._write_error(HTTPStatus.NOT_FOUND, "budget_not_found", "No budget record for tenant.")
            return
        self._write_json(HTTPStatus.OK, summary)

    def _handle_background_status(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "background.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability background.read required.")
            return
        daemon = self.resources.background_daemon if self.resources else None
        if daemon is None:
            self._write_json(
                HTTPStatus.OK,
                {"enabled": False, "paused": False, "thread_alive": False},
            )
            return
        thread = getattr(daemon, "_thread", None)
        alive = bool(thread is not None and thread.is_alive())
        paused = bool(getattr(daemon, "paused", False))
        self._write_json(
            HTTPStatus.OK,
            {"enabled": True, "paused": paused, "thread_alive": alive},
        )

    def _handle_background_pause(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "background.control"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability background.control required.")
            return
        daemon = self.resources.background_daemon if self.resources else None
        if daemon is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "background_disabled", "Background daemon not enabled.")
            return
        daemon.pause()
        self._write_json(HTTPStatus.OK, {"paused": True})

    def _handle_background_resume(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "background.control"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability background.control required.")
            return
        daemon = self.resources.background_daemon if self.resources else None
        if daemon is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "background_disabled", "Background daemon not enabled.")
            return
        daemon.resume()
        self._write_json(HTTPStatus.OK, {"paused": False})

    def _handle_background_stop(self) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "background.control"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability background.control required.")
            return
        daemon = self.resources.background_daemon if self.resources else None
        if daemon is None:
            self._write_error(HTTPStatus.SERVICE_UNAVAILABLE, "background_disabled", "Background daemon not enabled.")
            return
        daemon.stop()
        self._write_json(HTTPStatus.OK, {"stopped": True})
