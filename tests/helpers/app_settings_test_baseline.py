"""Extra :class:`AppSettings` fields introduced in P0; merge into manual test constructors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

# Keys must match :class:`agentium.app.settings.AppSettings` (frozen dataclass).
APP_SETTINGS_EXTENDED_BASELINE: Dict[str, Any] = {
    "chat_tool_description_max_chars": 512,
    "tool_contract_min_description_chars": 12,
    "chat_context_budget_enabled": True,
    "chat_context_soft_token_limit": 24000,
    "chat_context_hard_token_limit": 48000,
    "chat_context_safe_degrade": True,
    "prompt_cache_telemetry_enabled": True,
    "prompt_cache_http_header": False,
    "tool_approval_auto_enabled": False,
    "tool_approval_classifier_model": None,
    "tool_approval_max_auto_denies_per_turn": 3,
    "tool_approval_on_fault": "pending_human",
    "tool_approval_rule_deny_tools": (),
    "tool_approval_deny_shell_pattern": None,
    "sandbox_path_allowlist_prefixes": (),
    "sandbox_egress_deny_by_default": False,
    "sandbox_egress_allow_hosts": (),
    "chat_tool_defer_loading_threshold": 24,
    "chat_tool_search_max_expose": 8,
    "kb_contextual_sqlite_path": None,
    "classifier_stage_order": ("constitutional", "dlp", "tool_approval"),
    "code_sidecar_egress_allow_hosts": (),
    "feature_task_lock_enabled": False,
    "task_lock_max_ttl_seconds": 3600,
    "harness_oracle_enabled": False,
    "harness_parallel_workers_max": 8,
    "strict_harness_handoff_enabled": False,
    "execution_tier_high_risk_gate_enabled": False,
    "scheduled_jobs_enabled": False,
    "scheduled_jobs_tick_seconds": 30.0,
    "scheduled_jobs_webhook_secret": None,
    "scheduled_jobs_policy_gate_enabled": False,
    "scheduled_job_default_budget_estimate_tokens": 0,
}


def app_settings_extended_dict_for_data_dir(data_dir: Path) -> Dict[str, Any]:
    """Return baseline plus ``task_lock_sqlite_path`` under ``data_dir``."""

    out = dict(APP_SETTINGS_EXTENDED_BASELINE)
    out["task_lock_sqlite_path"] = (data_dir / "task_lock.db").resolve()
    return out
