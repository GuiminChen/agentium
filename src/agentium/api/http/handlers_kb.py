"""HTTP handlers for contextual KB retrieval (P1-5)."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from agentium.api.http.handler_base_mixin import ControlPlaneHTTPHandlerBaseMixin


class KbHandlersMixin(ControlPlaneHTTPHandlerBaseMixin):
    """Mixin: ``GET|POST /v1/kb/retrieve``."""

    def _handle_kb_retrieve(self, *, query: str, top_k: int) -> None:
        ident = self._resolve_identity()
        if ident is None:
            return
        if self.resources is None or self.resources.contextual_kb_store is None:
            self._write_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "kb_store_unavailable",
                "Contextual KB store is not configured.",
            )
            return
        tenant = ident.tenant_id
        hits = self.resources.contextual_kb_store.search(
            tenant_id=tenant,
            query=query,
            top_k=top_k,
        )
        payload: Dict[str, Any] = {
            "tenant_id": tenant,
            "query": query,
            "top_k": top_k,
            "hits": hits,
            "kb_retrieval_hits": len(hits),
        }
        if self.audit_sink is not None and hasattr(self.audit_sink, "append"):
            try:
                from agentium.models.context import AuditRecord

                self.audit_sink.append(
                    AuditRecord(
                        event_type="kb_retrieve",
                        tenant_id=tenant,
                        run_id="http-kb",
                        policy_version=None,
                        payload={
                            "query_len": len(query),
                            "hits": len(hits),
                            "top_k": top_k,
                        },
                    )
                )
            except Exception:
                pass
        self._write_json(HTTPStatus.OK, payload)

    def _kb_retrieve_from_get(self, parsed_query: str) -> None:
        qs = parse_qs(parsed_query or "")
        qvals = qs.get("query", [])
        query = str(qvals[0]) if qvals else ""
        tk_raw = qs.get("top_k", ["8"])
        try:
            top_k = max(1, min(64, int(tk_raw[0])))
        except (ValueError, TypeError):
            top_k = 8
        if not query.strip():
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_query", "Provide query parameter.")
            return
        self._handle_kb_retrieve(query=query.strip(), top_k=top_k)

    def _kb_retrieve_from_post_body(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        query = str(body.get("query", "")).strip()
        if not query:
            self._write_error(HTTPStatus.BAD_REQUEST, "missing_query", "Field query is required.")
            return
        tk_raw = body.get("top_k", 8)
        try:
            top_k = max(1, min(64, int(tk_raw)))
        except (TypeError, ValueError):
            top_k = 8
        self._handle_kb_retrieve(query=query, top_k=top_k)
