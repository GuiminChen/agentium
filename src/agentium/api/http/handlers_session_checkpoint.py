"""HTTP handlers for session checkpoints (MVP: session_id == run_id)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict, Optional, Tuple

from agentium.api.http.handler_constants import cap_granted
from agentium.models.context import AuditRecord


def parse_session_checkpoints_collection_path(path: str) -> Optional[str]:
    """Return session_id for ``GET|POST /v1/sessions/{id}/checkpoints``."""

    prefix = "/v1/sessions/"
    suffix = "/checkpoints"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix) : -len(suffix)].strip("/")
    if not mid or "/" in mid:
        return None
    return mid


def parse_session_checkpoint_restore_path(path: str) -> Optional[Tuple[str, int]]:
    """Return ``(session_id, seq)`` for ``POST .../checkpoints/{seq}/restore``."""

    prefix = "/v1/sessions/"
    if not path.startswith(prefix) or not path.endswith("/restore"):
        return None
    rest = path[len(prefix) : -len("/restore")].strip("/")
    parts = rest.split("/")
    if len(parts) != 3 or parts[1] != "checkpoints":
        return None
    try:
        seq = int(parts[2])
    except ValueError:
        return None
    return parts[0], seq


class SessionCheckpointHandlersMixin:
    """Mixed into :class:`ControlPlaneHTTPRequestHandler`."""

    def _handle_session_checkpoints_list(self, session_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "sessions.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability sessions.read required.")
            return
        res = self.resources
        cp_store = res.session_checkpoint_store if res else None
        if cp_store is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "checkpoints_unavailable",
                "Session checkpoint store not configured.",
            )
            return
        rows = cp_store.list_for_session(session_id=session_id, tenant_id=info.tenant_id)
        self._write_json(
            HTTPStatus.OK,
            {"session_id": session_id, "count": len(rows), "checkpoints": rows},
        )

    def _handle_session_checkpoint_create(self, session_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "sessions.checkpoint"):
            self._write_error(
                HTTPStatus.FORBIDDEN, "forbidden", "Capability sessions.checkpoint required."
            )
            return
        res = self.resources
        cp_store = res.session_checkpoint_store if res else None
        if cp_store is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "checkpoints_unavailable",
                "Session checkpoint store not configured.",
            )
            return
        raw = self._read_json_body()
        if raw is None:
            return
        if not isinstance(raw, dict):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_payload", "Body must be a JSON object.")
            return
        label = str(raw.get("label", "") or "")
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            self._write_error(
                HTTPStatus.BAD_REQUEST, "invalid_payload", "Field 'payload' must be an object.",
            )
            return
        seq = cp_store.append(
            session_id=session_id,
            tenant_id=info.tenant_id,
            label=label,
            payload=payload,
        )
        if self.audit_sink is not None:
            self.audit_sink.append(
                AuditRecord(
                    event_type="session_checkpoint_created",
                    tenant_id=info.tenant_id,
                    run_id=session_id,
                    payload={"seq": seq, "label": label},
                )
            )
        self._write_json(
            HTTPStatus.CREATED,
            {"session_id": session_id, "seq": seq, "label": label},
        )

    def _handle_session_checkpoint_restore(self, session_id: str, seq: int) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "sessions.checkpoint"):
            self._write_error(
                HTTPStatus.FORBIDDEN, "forbidden", "Capability sessions.checkpoint required."
            )
            return
        res = self.resources
        cp_store = res.session_checkpoint_store if res else None
        msg_store = res.run_message_store if res else None
        if cp_store is None or msg_store is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "checkpoints_unavailable",
                "Checkpoint or message store not configured.",
            )
            return
        row = cp_store.get(session_id=session_id, tenant_id=info.tenant_id, seq=seq)
        if row is None:
            self._write_error(HTTPStatus.NOT_FOUND, "checkpoint_not_found", "No such checkpoint.")
            return
        summary = {
            "checkpoint_seq": seq,
            "label": row["label"],
            "snapshot": row["payload"],
        }
        msg_store.append(
            run_id=session_id,
            tenant_id=info.tenant_id,
            role="system",
            kind="checkpoint_restore",
            body=summary,
            request_id=f"restore-{seq}",
        )
        if self.audit_sink is not None:
            self.audit_sink.append(
                AuditRecord(
                    event_type="session_checkpoint_restored",
                    tenant_id=info.tenant_id,
                    run_id=session_id,
                    payload={"seq": seq},
                )
            )
        self._write_json(HTTPStatus.OK, {"session_id": session_id, "restored_seq": seq})


__all__ = [
    "SessionCheckpointHandlersMixin",
    "parse_session_checkpoint_restore_path",
    "parse_session_checkpoints_collection_path",
]
