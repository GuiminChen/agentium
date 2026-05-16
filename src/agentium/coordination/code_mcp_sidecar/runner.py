"""Code + MCP sidecar orchestration stub (P1-12 MVP).

Runs no arbitrary code by default; records egress policy for audit/compliance hooks.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional
from urllib.parse import urlparse


def run_python_tool_orchestration_stub(
    *,
    code: str,
    allowed_mcp_endpoints: Optional[tuple[str, ...]] = None,
    net_allow_hosts: FrozenSet[str] = frozenset(),
    egress_probe_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministic stub: measures payload size and echoes network allowlist policy.

    Args:
        code: Python source (not executed in this MVP stub).
        allowed_mcp_endpoints: Declared MCP endpoints the orchestration may target.
        net_allow_hosts: Hostnames allowed when egress is restricted.
        egress_probe_url: When set, host from this URL must appear in ``net_allow_hosts``
            (case-insensitive); otherwise the stub returns ``egress_denied`` without execution.

    Returns:
        Structured sidecar envelope suitable for DLP/post-filters before model ingest.
    """

    if egress_probe_url:
        host = urlparse(egress_probe_url).hostname
        allowed = {h.lower() for h in net_allow_hosts}
        if not host or host.lower() not in allowed:
            endpoints = list(allowed_mcp_endpoints or ())
            return {
                "status": "egress_denied",
                "code_bytes": len(code.encode("utf-8")),
                "allowed_mcp_endpoints": endpoints,
                "sidecar_net_policy": {
                    "egress_allow_hosts": sorted(net_allow_hosts),
                },
                "egress_probe_url": egress_probe_url,
            }

    endpoints = list(allowed_mcp_endpoints or ())
    return {
        "status": "stub_no_code_execution",
        "code_bytes": len(code.encode("utf-8")),
        "allowed_mcp_endpoints": endpoints,
        "sidecar_net_policy": {
            "egress_allow_hosts": sorted(net_allow_hosts),
        },
    }
