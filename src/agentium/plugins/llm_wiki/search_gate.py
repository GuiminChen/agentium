"""wiki_search admission checks (explicit job waits + optional session pending gate)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from agentium.app.plugins_config import LlmWikiPluginConfig
from agentium.infra.db.sqlite_wiki_job_store import SqliteWikiIngestJobStore


def wiki_search_gate_violation(
    job_store: SqliteWikiIngestJobStore,
    cfg: LlmWikiPluginConfig,
    *,
    tenant_id: str,
    scope: str,
    session_id: str,
    wait_for_job_ids: Sequence[str],
) -> Optional[Dict[str, Any]]:
    """Return an error payload for ``wiki_search``, or ``None`` when search may proceed."""

    tid = tenant_id.strip()
    scope_norm = (scope or "session").strip().lower()
    sid = (session_id or "").strip()

    wf = _normalize_job_ids(wait_for_job_ids)
    if wf:
        ok, summaries = job_store.verify_jobs_succeeded_for_tenant(tid, wf)
        if not ok:
            return {
                "code": "wiki_wait_for_jobs_not_ready",
                "message": "One or more wait_for_job_ids are missing, wrong tenant, or not succeeded.",
                "jobs": summaries,
            }

    if (
        cfg.wiki_search_block_session_when_jobs_pending
        and scope_norm == "session"
        and sid
    ):
        pending = job_store.list_recent_non_terminal_jobs_for_session(
            tid,
            sid,
            max_age_seconds=cfg.wiki_pending_job_gate_ttl_seconds,
        )
        if pending:
            return {
                "code": "wiki_session_jobs_pending",
                "message": (
                    "Session-scoped wiki_search is blocked while ingest jobs are "
                    "queued or running for this session (within configured TTL)."
                ),
                "blocking_job_ids": [p.job_id for p in pending],
                "blocking_job_statuses": [
                    {"job_id": p.job_id, "status": p.status} for p in pending
                ],
            }

    return None


def _normalize_job_ids(raw: Sequence[str]) -> List[str]:
    out: List[str] = []
    for item in raw:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out
