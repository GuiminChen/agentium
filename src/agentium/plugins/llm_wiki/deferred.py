"""Register deferred handler for LLM-Wiki ingest jobs."""

from __future__ import annotations

from typing import Any, Mapping

import structlog

from agentium.coordination.deferred_tasks.registry import register_deferred_handler

_LOGGER = structlog.get_logger(__name__)

KIND_LLM_WIKI_INGEST_COMPILE = "llm_wiki.ingest_compile"


def _fail_wiki_job_when_worker_unavailable(job_id: str, reason: str) -> None:
    """Persist terminal failure when the plugin service handle is gone (shutdown race)."""

    try:
        from agentium.app.settings import load_settings
        from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore

        settings = load_settings()
        store = SqliteWikiIngestJobStore(settings.sqlite_db_path)
        store.update_status(job_id, status="failed", error=reason[:2048])
    except Exception:
        _LOGGER.exception("wiki_job_fail_fallback_failed", job_id=job_id)


def _handle_llm_wiki_ingest_compile(payload: Mapping[str, Any]) -> None:
    from agentium.plugins.llm_wiki.runtime_bridge import get_llm_wiki_service

    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        _LOGGER.warning("llm_wiki_deferred_missing_job_id")
        return
    svc = get_llm_wiki_service()
    if svc is None:
        _LOGGER.warning("llm_wiki_service_unavailable", job_id=job_id)
        _fail_wiki_job_when_worker_unavailable(job_id, "llm_wiki_service_unavailable")
        return
    svc.run_ingest_job(job_id)


register_deferred_handler(KIND_LLM_WIKI_INGEST_COMPILE, _handle_llm_wiki_ingest_compile)
