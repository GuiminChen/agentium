#!/usr/bin/env python3
"""Synthetic control-plane latency micro-benchmark for paper § overhead.

Repeated ``ToolRegistry.execute`` calls for a trivial echo tool compare:

- **minimal**: no outbound DLP classifier (``dlp_classifier=None``).
- **with_dlp**: same stack but :class:`~agentium.security.dlp_classifier.DLPClassifier`
  runs on benign outbound payloads.

Uses :func:`time.perf_counter_ns`; reports P50 / P95 / mean in milliseconds.

Run::

    python scripts/ablation_microbench.py --output artifacts/microbench.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parents[1]


def _ensure_src() -> None:
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _percentiles_ms(samples_ns: List[int]) -> dict:
    sorted_s = sorted(samples_ns)
    n = len(sorted_s)

    def pct(p: float) -> float:
        if not sorted_s:
            return 0.0
        if n == 1:
            return sorted_s[0] / 1e6
        k = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return sorted_s[k] / 1e6

    mean_ns = statistics.mean(sorted_s) if sorted_s else 0.0
    return {
        "p50_ms": round(pct(50), 6),
        "p95_ms": round(pct(95), 6),
        "mean_ms": round(mean_ns / 1e6, 6),
    }


def _bench_one(registry: object, *, iterations: int) -> List[int]:
    from agentium.models.context import RequestContext

    ctx = RequestContext(
        request_id="mb",
        run_id="microbench-run",
        tenant_id="t1",
        user_id="u1",
        trace_id="trace-mb",
    )
    payloads: List[int] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        registry.execute(ctx, "echo_micro", {"text": "benchmark payload"})
        payloads.append(time.perf_counter_ns() - t0)
    return payloads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=400, help="Timed iterations after warmup.")
    parser.add_argument("--warmup", type=int, default=40, help="Warmup iterations (not timed).")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path.",
    )
    args = parser.parse_args()

    _ensure_src()
    from agentium.coordination.budget_ledger import BudgetLedger, TenantBudget
    from agentium.governance.audit_lineage import InMemoryAuditSink
    from agentium.governance.policy_engine import PolicyDocument, PolicyEngine, PolicyRule
    from agentium.models.context import DecisionType, RequestContext
    from agentium.security.dlp_classifier import DLPClassifier
    from agentium.tools.tool_registry import ToolRegistry, ToolSpec

    tool_name = "echo_micro"

    policy = PolicyEngine(
        policy=PolicyDocument(
            version="microbench",
            default_decision=DecisionType.DENY,
            default_reason="deny",
            rules=[
                PolicyRule(
                    id="allow-micro",
                    decision=DecisionType.ALLOW,
                    reason="microbench allow",
                    tools={tool_name},
                )
            ],
        )
    )
    ledger = BudgetLedger({"t1": TenantBudget(token_limit=100_000, cost_limit=100.0, max_concurrency=8)})
    audit = InMemoryAuditSink()
    handler = lambda a: {"echo": str(a.get("text", "")), "extra": {"ok": True}}

    minimal = ToolRegistry(
        policy_engine=policy,
        budget_ledger=ledger,
        audit_sink=audit,
        dlp_classifier=None,
    )
    minimal.register(
        ToolSpec(
            name=tool_name,
            capabilities=["benchmark"],
            risk_level="low",
            handler=handler,
        )
    )

    ledger2 = BudgetLedger({"t1": TenantBudget(token_limit=100_000, cost_limit=100.0, max_concurrency=8)})
    audit2 = InMemoryAuditSink()
    with_dlp = ToolRegistry(
        policy_engine=policy,
        budget_ledger=ledger2,
        audit_sink=audit2,
        dlp_classifier=DLPClassifier(),
    )
    with_dlp.register(
        ToolSpec(
            name=tool_name,
            capabilities=["benchmark"],
            risk_level="low",
            handler=handler,
        )
    )

    for reg in (minimal, with_dlp):
        for _ in range(args.warmup):
            ctx = RequestContext(
                request_id="mb-w",
                run_id="microbench-run",
                tenant_id="t1",
                user_id="u1",
                trace_id="trace-mb-w",
            )
            reg.execute(ctx, tool_name, {"text": "warmup"})

    samples_minimal = _bench_one(minimal, iterations=args.iterations)
    samples_dlp = _bench_one(with_dlp, iterations=args.iterations)

    payload = {
        "scenario": "echo_tool_execution_overhead",
        "iterations": args.iterations,
        "warmup": args.warmup,
        "minimal_stack": {"dlp_classifier": False, **_percentiles_ms(samples_minimal)},
        "with_dlp_stack": {"dlp_classifier": True, **_percentiles_ms(samples_dlp)},
    }
    text = json.dumps(payload, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
