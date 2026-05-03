"""Domain pack bundle download and connectors inbox (SQLite audit)."""

from __future__ import annotations

import io
import zipfile
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from agentium.api.http.handler_constants import admin_scope, cap_granted
from agentium.api.http.handlers_session_timeline import _PACK_ID_RE
from agentium.governance.domain_pack_loader import DomainPackError, verify_pack_directory_integrity
from agentium.infra.db.sqlite_store import SqliteAuditSink
from agentium.models.context import AuditRecord


def parse_domain_pack_bundle_path(path: str) -> Optional[str]:
    prefix = "/v1/governance/domain-packs/"
    suffix = "/bundle"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    mid = path[len(prefix) : -len(suffix)].strip("/")
    if not mid or "/" in mid:
        return None
    return mid


class GovernanceDomainPackHandlersMixin:
    """GET domain pack bundle + connectors inbox."""

    def _handle_domain_pack_bundle(self, pack_id: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "governance.packs.read"):
            self._write_error(
                HTTPStatus.FORBIDDEN, "forbidden", "Capability governance.packs.read required."
            )
            return
        res = self.resources
        root = res.domain_packs_root if res else None
        if root is None or not isinstance(root, Path):
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "domain_packs_unavailable",
                "AGENTIUM_DOMAIN_PACKS_ROOT is not set.",
            )
            return
        if not _PACK_ID_RE.match(pack_id):
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_pack_id", "Invalid pack id.")
            return
        pack_dir = (root / pack_id).resolve()
        try:
            root_resolved = root.resolve()
            pack_dir.relative_to(root_resolved)
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_pack_path", "Path traversal rejected.")
            return
        if not pack_dir.is_dir():
            self._write_error(HTTPStatus.NOT_FOUND, "pack_not_found", "Domain pack not found.")
            return
        try:
            manifest = verify_pack_directory_integrity(pack_dir)
        except DomainPackError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "pack_load_failed",
                "Pack failed verification.",
                detail=str(exc)[:512],
            )
            return
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(pack_dir.rglob("*")):
                if p.is_file():
                    arc = str(p.relative_to(pack_dir)).replace("\\", "/")
                    zf.write(p, arcname=arc)
        data = buf.getvalue()
        checksum = manifest.policy_sha256 or manifest.id
        if self.audit_sink is not None:
            self.audit_sink.append(
                AuditRecord(
                    event_type="domain_pack_downloaded",
                    tenant_id=info.tenant_id,
                    run_id="_governance",
                    policy_version=None,
                    payload={
                        "pack_id": manifest.id,
                        "pack_version": manifest.version,
                        "user_id": info.user_id,
                    },
                )
            )
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Agentium-Pack-Id", manifest.id)
        self.send_header("X-Agentium-Pack-Version", manifest.version)
        self.send_header("X-Agentium-Pack-Checksum", str(checksum))
        self.end_headers()
        self.wfile.write(data)

    def _handle_connectors_inbox(self, query: str) -> None:
        info = self._resolve_identity()
        if info is None:
            return
        if not cap_granted(info.roles, "connectors.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability connectors.read required.")
            return
        res = self.resources
        sink: Optional[SqliteAuditSink] = None
        if res and res.sqlite_audit_sink is not None:
            candidate = res.sqlite_audit_sink
            if isinstance(candidate, SqliteAuditSink):
                sink = candidate
        if sink is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "inbox_unavailable",
                "Connectors inbox requires SQLite audit backend.",
            )
            return
        params = parse_qs(query, keep_blank_values=False)
        limit_raw = params.get("limit", ["50"])[0]
        try:
            limit = int(limit_raw or "50")
        except ValueError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "limit must be an integer.")
            return
        tenant_q = params.get("tenant_id", [None])[0]
        tenant_id = info.tenant_id if not admin_scope(info.roles) else (tenant_q or info.tenant_id)
        if not tenant_id:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_tenant_id", "tenant_id is required.")
            return
        events = sink.fetch_recent_channel_events(tenant_id=tenant_id, limit=limit)
        self._write_json(HTTPStatus.OK, {"count": len(events), "events": events})


__all__ = ["GovernanceDomainPackHandlersMixin", "parse_domain_pack_bundle_path"]
