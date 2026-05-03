#!/usr/bin/env python3
"""Emit a JSON summary for the three governance profiles (paper §10: none / weak / full)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _ensure_src() -> None:
    src = _ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _ensure_src()
    from agentium.governance.policy_engine import PolicyEngine
    from agentium.models.context import RequestContext

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON (default: stdout only).",
    )
    args = parser.parse_args()

    profiles = [
        ("none", _ROOT / "configs" / "paper" / "policy_none.yaml"),
        ("weak", _ROOT / "configs" / "paper" / "policy_weak.yaml"),
        ("full", _ROOT / "configs" / "paper" / "policy_full.yaml"),
    ]

    ctx = RequestContext(
        request_id="paper",
        run_id="paper",
        tenant_id="t",
        user_id="u",
        trace_id="tr",
        role="analyst",
    )
    rows = []
    for name, path in profiles:
        if not path.is_file():
            print(f"missing policy file: {path}", file=sys.stderr)
            return 2
        pe = PolicyEngine.load(path)
        decision = pe.decide_tool_call(ctx, "paper_probe", {})
        rows.append(
            {
                "profile": name,
                "policy_path": str(path.relative_to(_ROOT)),
                "decision": decision.decision.value,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
            }
        )

    payload = {"governance_profiles": rows}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
