"""Built-in demo and Phase-2-scale tool specs registered at bootstrap (no I/O side effects by default).

Prod profile registers a minimal safe set; dev/staging registers an extended catalog (20+ tools)
for phased-delivery scale targets without loading third-party MCP.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from typing import List, Protocol, Set

from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class SupportsBuiltinProfile(Protocol):
    """Minimal surface used by :func:`register_builtin_tools`."""

    profile: str


def _echo_handler(args: dict) -> dict:
    return {"echo": args.get("text", "")}


def _noop_handler(args: dict) -> dict:
    return {"ok": True, "args_keys": sorted(args.keys())}


def _calc_handler(args: dict) -> dict:
    a = float(args.get("a", 0))
    b = float(args.get("b", 0))
    op = str(args.get("op", "add"))
    if op == "add":
        return {"result": a + b}
    if op == "mul":
        return {"result": a * b}
    if op == "sub":
        return {"result": a - b}
    return {"result": None, "error": "unsupported_op", "op": op}


def _hash_handler(args: dict) -> dict:
    text = str(args.get("text", ""))
    algo = str(args.get("algorithm", "sha256")).lower()
    if algo == "sha256":
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return {"digest": h, "algorithm": algo}


def _wc_handler(args: dict) -> dict:
    text = str(args.get("text", ""))
    return {"words": len(text.split()), "chars": len(text)}


def _json_parse_handler(args: dict) -> dict:
    raw = args.get("payload", "{}")
    if isinstance(raw, dict):
        return {"parsed": raw}
    return {"parsed": json.loads(str(raw))}


def _now_handler(args: dict) -> dict:
    del args
    return {"unix": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _random_choice_handler(args: dict) -> dict:
    choices = args.get("choices") or ["a", "b"]
    if not isinstance(choices, list) or not choices:
        return {"error": "choices_must_be_non_empty_list"}
    return {"pick": random.choice(choices)}


def _reverse_handler(args: dict) -> dict:
    text = str(args.get("text", ""))
    return {"reversed": text[::-1]}


def _web_search_stub(args: dict) -> dict:
    q = str(args.get("query", ""))
    return {"results": [], "query": q, "note": "stub_no_network"}


def _db_export_stub(args: dict) -> dict:
    return {"ok": True, "dataset": str(args.get("dataset", "")), "rows": 0}


def _list_files_stub(args: dict) -> dict:
    del args
    return {"files": [], "note": "stub_no_fs"}


def _env_snapshot_stub(args: dict) -> dict:
    del args
    return {"keys_sample": ["PATH", "AGENTIUM_PROFILE"], "note": "stub_no_environ_dump"}


def _http_probe_stub(args: dict) -> dict:
    return {"reachable": False, "url": str(args.get("url", "")), "note": "stub_no_network"}


def _delay_ms_handler(args: dict) -> dict:
    ms = min(float(args.get("ms", 0)), 500.0)
    time.sleep(ms / 1000.0)
    return {"slept_ms": ms}


def _identity_handler(args: dict) -> dict:
    return {"value": args.get("value")}


def _merge_dict_handler(args: dict) -> dict:
    a = args.get("a") if isinstance(args.get("a"), dict) else {}
    b = args.get("b") if isinstance(args.get("b"), dict) else {}
    merged = {**a, **b}
    return {"merged": merged}


def _pick_field_handler(args: dict) -> dict:
    obj = args.get("obj") if isinstance(args.get("obj"), dict) else {}
    key = str(args.get("field", ""))
    return {"field": key, "value": obj.get(key)}


def _template_stub(args: dict) -> dict:
    return {"rendered": str(args.get("template", "")).format(**(args.get("ctx") or {}))}


def _table_stub(args: dict) -> dict:
    rows = args.get("rows") or []
    return {"markdown": "|stub|\n|---|\n" + "\n".join(f"|{r}|" for r in rows[:20])}


def _summarize_stub(args: dict) -> dict:
    text = str(args.get("text", ""))
    return {"summary": text[:200], "truncated": len(text) > 200}


def _mcp_stub_handler(args: dict) -> dict:
    """Return a deterministic mock MCP envelope keyed off ingress ``mcp_execution_tier``.

    Lets UI/E2E verify disposition + tier wiring without a real MCP server (design intent:
    observability-only stub; no outbound MCP).

    Args:
        args: Optional ``action`` (default ``list_tools``) and ``query`` echoed for humans.

    Returns:
        Structured payload including ``mock_kind``, ``tier``, ``message_disposition``, and
        a ``simulated`` subtree that differs for ``code-exec-mcp`` vs ``direct-tool``.
    """

    from agentium.shared.request_context import get_request_context

    ctx = get_request_context()
    tenant_id = ctx.tenant_id
    tenant_preview = f"{tenant_id[:8]}..." if len(tenant_id) > 8 else tenant_id
    action = str(args.get("action", "list_tools")).strip() or "list_tools"
    query_hint = str(args.get("query", "")).strip()
    tier = ctx.mcp_execution_tier
    disposition = ctx.message_disposition
    if tier == "code-exec-mcp":
        return {
            "mock_kind": "mcp_code_exec",
            "tier": tier,
            "message_disposition": disposition,
            "simulated": {
                "sandbox_id": "mock-sbx-001",
                "action": action,
                "stderr_tail": "",
            },
            "echo_query": query_hint,
            "tenant_preview": tenant_preview,
            "trace_id": ctx.trace_id,
        }
    return {
        "mock_kind": "mcp_direct",
        "tier": tier,
        "message_disposition": disposition,
        "simulated": {"proto": "jsonrpc-ish", "method": action, "ok": True},
        "echo_query": query_hint,
        "tenant_preview": tenant_preview,
        "trace_id": ctx.trace_id,
    }


def _alert_stub(args: dict) -> dict:
    return {"alert_id": "stub", "severity": str(args.get("severity", "info"))}


_ALL_BUILTIN: List[ToolSpec] = [
    ToolSpec(
        name="echo_tool", capabilities=["echo"], risk_level="low", handler=_echo_handler
    ),
    ToolSpec(name="noop", capabilities=["debug"], risk_level="low", handler=_noop_handler),
    ToolSpec(
        name="calculator",
        capabilities=["math"],
        risk_level="low",
        handler=_calc_handler,
    ),
    ToolSpec(
        name="hash_text",
        capabilities=["transform"],
        risk_level="low",
        handler=_hash_handler,
    ),
    ToolSpec(
        name="word_count",
        capabilities=["nlp_stub"],
        risk_level="low",
        handler=_wc_handler,
    ),
    ToolSpec(
        name="db_export",
        capabilities=["db.export"],
        risk_level="high",
        handler=_db_export_stub,
    ),
    ToolSpec(
        name="web_search",
        capabilities=["network_stub"],
        risk_level="medium",
        handler=_web_search_stub,
    ),
    ToolSpec(
        name="json_parse",
        capabilities=["transform"],
        risk_level="low",
        handler=_json_parse_handler,
    ),
    ToolSpec(name="timestamp_now", capabilities=["time"], risk_level="low", handler=_now_handler),
    ToolSpec(
        name="random_choice",
        capabilities=["rng"],
        risk_level="low",
        handler=_random_choice_handler,
    ),
    ToolSpec(
        name="string_reverse",
        capabilities=["transform"],
        risk_level="low",
        handler=_reverse_handler,
    ),
    ToolSpec(
        name="list_files_stub",
        capabilities=["fs_stub"],
        risk_level="low",
        handler=_list_files_stub,
    ),
    ToolSpec(
        name="env_snapshot_stub",
        capabilities=["env_stub"],
        risk_level="medium",
        handler=_env_snapshot_stub,
    ),
    ToolSpec(
        name="http_probe_stub",
        capabilities=["network_stub"],
        risk_level="medium",
        handler=_http_probe_stub,
    ),
    ToolSpec(
        name="delay_ms",
        capabilities=["scheduling"],
        risk_level="low",
        handler=_delay_ms_handler,
    ),
    ToolSpec(
        name="identity_pass",
        capabilities=["transform"],
        risk_level="low",
        handler=_identity_handler,
    ),
    ToolSpec(
        name="merge_dict",
        capabilities=["transform"],
        risk_level="low",
        handler=_merge_dict_handler,
    ),
    ToolSpec(
        name="pick_field",
        capabilities=["transform"],
        risk_level="low",
        handler=_pick_field_handler,
    ),
    ToolSpec(
        name="template_render_stub",
        capabilities=["template"],
        risk_level="low",
        handler=_template_stub,
    ),
    ToolSpec(
        name="format_table_stub",
        capabilities=["format"],
        risk_level="low",
        handler=_table_stub,
    ),
    ToolSpec(
        name="summarize_stub",
        capabilities=["nlp_stub"],
        risk_level="low",
        handler=_summarize_stub,
    ),
    ToolSpec(
        name="mcp_stub",
        capabilities=["mcp.mock"],
        risk_level="low",
        handler=_mcp_stub_handler,
    ),
    ToolSpec(
        name="alert_stub",
        capabilities=["notify_stub"],
        risk_level="medium",
        handler=_alert_stub,
    ),
]

_PROD_BUILTIN_NAMES: Set[str] = {
    "echo_tool",
    "noop",
    "calculator",
    "hash_text",
    "word_count",
    "db_export",
}


def builtin_tool_specs_for_profile(profile: str) -> List[ToolSpec]:
    """Return ToolSpecs to register for the given deployment profile."""

    if profile == "prod":
        return [s for s in _ALL_BUILTIN if s.name in _PROD_BUILTIN_NAMES]
    return list(_ALL_BUILTIN)


def register_builtin_tools(registry: ToolRegistry, settings: SupportsBuiltinProfile) -> None:
    """Register built-in tools according to `settings.profile`."""

    for spec in builtin_tool_specs_for_profile(settings.profile):
        registry.register(spec)


__all__ = [
    "builtin_tool_specs_for_profile",
    "register_builtin_tools",
    "SupportsBuiltinProfile",
]
