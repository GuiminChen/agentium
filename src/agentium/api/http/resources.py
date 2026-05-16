"""Optional dependencies exposed to HTTP handlers (beyond ControlPlaneAPI)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from agentium.app.settings import AppSettings


@dataclass
class HTTPControlPlaneResources:
    """Narrow facade for read/write control actions that are not on ControlPlaneAPI."""

    tool_registry: Optional[Any] = None
    policy_engine: Optional[Any] = None
    budget_service: Optional[Any] = None
    background_daemon: Optional[Any] = None
    artifact_store: Optional[Any] = None
    notify_bridge: Optional[Any] = None
    task_graph: Optional[Any] = None
    deep_research_pipeline: Optional[Any] = None
    research_job_service: Optional[Any] = None
    policy_release_manager: Optional[Any] = None
    emergence_guardrails: Optional[Any] = None
    evolution_plugin: Optional[Any] = None
    evolution_http_enabled: bool = False
    dev_http_enabled: bool = False
    lsp_upstream_configured: bool = False
    ui_links: Optional[Dict[str, str]] = None
    run_message_store: Optional[Any] = None
    session_checkpoint_store: Optional[Any] = None
    eval_run_store: Optional[Any] = None
    run_cancel_registry: Optional[Any] = None
    lifecycle_manager: Optional[Any] = None
    sqlite_audit_sink: Optional[Any] = None
    domain_packs_root: Optional[Path] = None
    chat_session_store: Optional[Any] = None
    chat_turn_service: Optional[Any] = None
    chat_memory_lane_router: Optional[Any] = None
    memory_service: Optional[Any] = None
    contextual_kb_store: Optional[Any] = None
    settings: Optional["AppSettings"] = None
    llm_wiki_service: Optional[Any] = None
    deferred_task_sink: Optional[Any] = None
    scheduled_job_store: Optional[Any] = None
    scheduled_job_runner: Optional[Any] = None
