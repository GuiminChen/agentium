"""HTTP handlers for LLM-Wiki (ingest jobs + search)."""

from __future__ import annotations

import base64
import binascii
import re
from http import HTTPStatus
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote

from pydantic import ValidationError

from agentium.api.http.handler_base_mixin import ControlPlaneHTTPHandlerBaseMixin
from agentium.api.http.handler_constants import cap_granted
from agentium.api.http.wiki_http_schemas import WikiSessionUploadRequest

_WIKI_JOB_RE = re.compile(r"^/v1/wiki/ingest-jobs/([^/]+)/?$")

_LLM_WIKI_503_MESSAGE = (
    "LLM-Wiki service is not running in this process (disabled in config, crate import failed, "
    "or wiki DB connection not configured)."
)

_LLM_WIKI_503_DETAIL = (
    "Fix: ensure llm_wiki.enabled=true in plugins YAML (or AGENTIUM_LLM_WIKI_ENABLED=1) and restart; "
    "install crate with the same Python as Agentium (`pip install -e ./crate`); "
    "if wiki_db.backend=postgresql, set the env variable named by postgresql_conninfo_from_env. "
    "Note: missing wiki.read returns HTTP 403, not this 503."
)


class LlmWikiHandlersMixin(ControlPlaneHTTPHandlerBaseMixin):
    """Mixin: wiki ingest jobs, session uploads, search, pages/graph."""

    def _parse_wiki_ingest_job_id(self, path: str) -> Optional[str]:
        m = _WIKI_JOB_RE.match(path or "")
        if m is None:
            return None
        return str(m.group(1)).strip()

    def _write_llm_wiki_service_unavailable(self) -> None:
        """Unified 503 body so clients can show accurate troubleshooting."""

        self._write_error(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "llm_wiki_unavailable",
            _LLM_WIKI_503_MESSAGE,
            detail=_LLM_WIKI_503_DETAIL,
        )

    def _handle_wiki_ping_get(self) -> None:
        """Lightweight diagnostics; auth required, ``wiki.read`` not."""

        ident = self._resolve_identity()
        if ident is None:
            return
        if self.resources is None or self.resources.settings is None:
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "server_misconfigured",
                "HTTP resources.settings is unavailable.",
            )
            return
        svc = getattr(self.resources, "llm_wiki_service", None)

        from agentium.plugins.llm_wiki.service import build_llm_wiki_wire_ping_payload

        payload = build_llm_wiki_wire_ping_payload(
            self.resources.settings,
            service_wired=svc is not None,
        )
        payload["tenant_id"] = ident.tenant_id
        self._write_json(HTTPStatus.OK, payload)

    def _handle_wiki_ingest_job_post(self) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        body = self._read_json_body()
        if body is None:
            return
        blob_key = str(body.get("blob_key", "")).strip()
        if not blob_key:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_blob_key", "blob_key is required.")
            return
        session_id = str(body.get("session_id", "")).strip()
        svc = self.resources.llm_wiki_service
        sink = getattr(self.resources, "deferred_task_sink", None)
        jid = svc.enqueue_ingest_job(
            tenant_id=ident.tenant_id,
            blob_key=blob_key,
            session_id=session_id,
            deferred_sink=sink,
        )
        self._write_json(HTTPStatus.ACCEPTED, {"job_id": jid, "status": "queued"})

    def _handle_wiki_session_upload_post(self) -> None:
        """JSON upload: writes RawBlobStore + queues ingest (same shape as ingest-jobs response tail)."""

        ident = self._resolve_identity()
        if ident is None:
            return
        if not cap_granted(ident.roles, "chat.messages.send"):
            self._write_error(
                HTTPStatus.FORBIDDEN,
                "forbidden",
                "Capability chat.messages.send required.",
            )
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        body = self._read_json_body()
        if body is None:
            return
        try:
            req = WikiSessionUploadRequest.model_validate(body)
        except ValidationError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_body",
                "Request body failed validation.",
                detail=exc.errors(),
            )
            return
        try:
            raw = base64.b64decode(req.content_base64.strip(), validate=True)
        except binascii.Error:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_base64",
                "content_base64 is not valid standard Base64.",
            )
            return

        svc = self.resources.llm_wiki_service
        sink = getattr(self.resources, "deferred_task_sink", None)
        from agentium.plugins.llm_wiki.session_material import SessionMaterialUploadError

        try:
            blob_key, jid = svc.enqueue_session_material(
                tenant_id=ident.tenant_id,
                session_id=req.session_id,
                filename=req.filename,
                raw_bytes=raw,
                deferred_sink=sink,
            )
        except SessionMaterialUploadError as exc:
            self._write_error(HTTPStatus.BAD_REQUEST, exc.code, exc.message)
            return

        self._write_json(
            HTTPStatus.ACCEPTED,
            {"job_id": jid, "blob_key": blob_key, "status": "queued"},
        )

    def _handle_wiki_ingest_job_get(self, job_id: str) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        rec = self.resources.llm_wiki_service.job_store.get_job(job_id)
        if rec is None:
            self._write_error(HTTPStatus.NOT_FOUND, "job_not_found", "Unknown job_id.")
            return
        if rec.tenant_id != ident.tenant_id:
            self._write_error(HTTPStatus.FORBIDDEN, "job_forbidden", "Job belongs to another tenant.")
            return
        payload: Dict[str, Any] = {
            "job_id": rec.job_id,
            "tenant_id": rec.tenant_id,
            "session_id": rec.session_id,
            "blob_key": rec.blob_key,
            "status": rec.status,
            "error": rec.error,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "wiki_scope": "session" if rec.session_id.strip() else "tenant",
        }
        if rec.status == "succeeded":
            try:
                from crate.stores.wiki_paths import wiki_logical_path_for_blob

                payload["indexed_logical_path"] = wiki_logical_path_for_blob(
                    blob_key=rec.blob_key,
                    session_id=rec.session_id,
                )
            except Exception:
                payload["indexed_logical_path"] = ""
        self._write_json(HTTPStatus.OK, payload)

    def _handle_wiki_search_get(self, query_string: str) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if not cap_granted(ident.roles, "wiki.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability wiki.read required.")
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        qs = parse_qs(query_string or "")
        qvals = qs.get("q", [])
        query = str(qvals[0]) if qvals else ""
        if not query.strip():
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_q", "Provide q= query parameter.")
            return
        lim_raw = qs.get("limit", ["10"])
        try:
            limit = max(1, min(50, int(lim_raw[0])))
        except (ValueError, TypeError):
            limit = 10
        scope_raw = (qs.get("scope", ["session"])[0] or "session").strip().lower()
        if scope_raw not in ("session", "tenant"):
            scope_raw = "session"
        sess_raw = (qs.get("session_id", [""])[0] or "").strip()
        if scope_raw == "session" and not sess_raw:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "missing_session_id",
                "scope=session requires session_id query parameter.",
            )
            return
        wait_ids: list[str] = []
        for w in qs.get("wait_for_job_id", []):
            s = str(w).strip()
            if s:
                wait_ids.append(s)
        wj = qs.get("wait_for_job_ids", [])
        if wj:
            for part in str(wj[0]).split(","):
                s = part.strip()
                if s:
                    wait_ids.append(s)
        svc = self.resources.llm_wiki_service
        gate = svc.wiki_search_precheck(
            tenant_id=ident.tenant_id,
            scope=scope_raw,
            session_id=sess_raw,
            wait_for_job_ids=wait_ids,
        )
        if gate is not None:
            self._write_json(
                HTTPStatus.CONFLICT,
                {"tenant_id": ident.tenant_id, "query": query, "scope": scope_raw, **gate},
            )
            return
        out = svc.host.search(
            ident.tenant_id,
            query.strip(),
            literal=True,
            semantic=False,
            limit=limit,
            scope=scope_raw,
            chat_session_id=sess_raw,
        )
        self._write_json(
            HTTPStatus.OK,
            {"tenant_id": ident.tenant_id, "query": query, "scope": scope_raw, **out},
        )

    def _handle_wiki_pages_list_get(self, query_string: str) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if not cap_granted(ident.roles, "wiki.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability wiki.read required.")
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        qs = parse_qs(query_string or "")
        prefix_raw = (qs.get("prefix", [""])[0] or "").strip()
        prefix = prefix_raw if prefix_raw else None
        try:
            limit = max(1, min(500, int(qs.get("limit", ["100"])[0])))
        except (TypeError, ValueError):
            limit = 100
        try:
            offset = max(0, int(qs.get("offset", ["0"])[0]))
        except (TypeError, ValueError):
            offset = 0
        svc = self.resources.llm_wiki_service
        try:
            rows = svc.host.list_page_summaries(
                ident.tenant_id,
                path_prefix=prefix,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_prefix",
                str(exc)[:512],
            )
            return
        items = [
            {
                "logical_path": r.logical_path,
                "updated_at": r.updated_at,
                "content_sha256": r.content_sha256,
            }
            for r in rows
        ]
        self._write_json(
            HTTPStatus.OK,
            {"tenant_id": ident.tenant_id, "items": items, "limit": limit, "offset": offset},
        )

    def _handle_wiki_page_get(self, query_string: str) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if not cap_granted(ident.roles, "wiki.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability wiki.read required.")
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        qs = parse_qs(query_string or "")
        pvals = qs.get("path", [])
        raw_path = unquote(str(pvals[0])) if pvals else ""
        logical_path = str(raw_path).strip()
        if not logical_path:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_path", "path= query parameter is required.")
            return
        svc = self.resources.llm_wiki_service
        try:
            rec = svc.host.get_page(ident.tenant_id, logical_path)
        except ValueError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_path",
                str(exc)[:512],
            )
            return
        if rec is None:
            self._write_error(HTTPStatus.NOT_FOUND, "page_not_found", "Unknown wiki page path.")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "tenant_id": rec.tenant_id,
                "logical_path": rec.logical_path,
                "body_md": rec.body_md,
                "content_sha256": rec.content_sha256,
                "updated_at": rec.updated_at,
            },
        )

    def _handle_wiki_graph_get(self, query_string: str) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if not cap_granted(ident.roles, "wiki.read"):
            self._write_error(HTTPStatus.FORBIDDEN, "forbidden", "Capability wiki.read required.")
            return
        if self.resources is None or self.resources.llm_wiki_service is None:
            self._write_llm_wiki_service_unavailable()
            return
        from crate.stores.wiki_paths import session_path_prefix

        from agentium.plugins.llm_wiki.wikilink_graph import build_wiki_graph_payload

        qs = parse_qs(query_string or "")
        scope_raw = (qs.get("scope", ["tenant"])[0] or "tenant").strip().lower()
        if scope_raw not in ("session", "tenant"):
            scope_raw = "tenant"
        sess_raw = (qs.get("session_id", [""])[0] or "").strip()
        if scope_raw == "session" and not sess_raw:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "missing_session_id",
                "scope=session requires session_id query parameter.",
            )
            return
        try:
            max_pages = max(1, min(500, int(qs.get("max_pages", ["200"])[0])))
        except (TypeError, ValueError):
            max_pages = 200

        path_prefix: Optional[str]
        try:
            if scope_raw == "session":
                path_prefix = session_path_prefix(sess_raw)
            else:
                path_prefix = None
        except ValueError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_session_id",
                str(exc)[:256],
            )
            return

        svc = self.resources.llm_wiki_service
        try:
            summaries = svc.host.list_page_summaries(
                ident.tenant_id,
                path_prefix=path_prefix,
                limit=max_pages,
                offset=0,
            )
        except ValueError as exc:
            self._write_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_graph_prefix",
                str(exc)[:512],
            )
            return

        loaded: list[Any] = []
        for row in summaries:
            full = svc.host.get_page(ident.tenant_id, row.logical_path)
            if full is not None:
                loaded.append(full)

        graph = build_wiki_graph_payload(pages=loaded)
        self._write_json(
            HTTPStatus.OK,
            {
                "tenant_id": ident.tenant_id,
                "scope": scope_raw,
                "session_id": sess_raw if scope_raw == "session" else "",
                "max_pages": max_pages,
                **graph,
            },
        )
