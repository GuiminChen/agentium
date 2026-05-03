"""``agentium research`` CLI: drive the DeepResearch pipeline.

Subcommands:

- ``run``: run the canonical research workflow with stub handlers and
  print the final report + artifact ids as JSON.

The CLI deliberately keeps external surface narrow; production
deployments wire real handlers via Python entrypoints, not this CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Sequence

from agentium.coordination.artifact_store import ArtifactStore
from agentium.models.context import RequestContext
from agentium.runtime.deepresearch_pipeline import (
    DeepResearchPipeline,
    stub_handlers,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentium research",
        description="Run the DeepResearch pipeline.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="Run a single research workflow")
    run.add_argument("--query", required=True, help="research query")
    run.add_argument("--tenant-id", default="cli-tenant", help="caller tenant")
    run.add_argument("--user-id", default="cli-user", help="caller user")
    run.add_argument(
        "--persist",
        default=None,
        help="optional path for artifact JSONL ledger",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action != "run":
        parser.print_help()
        return 1

    artifact_store = ArtifactStore(persist_path=args.persist)
    pipeline = DeepResearchPipeline(handlers=stub_handlers(), artifact_store=artifact_store)

    run_id = f"cli-research-{uuid.uuid4().hex[:8]}"
    context = RequestContext(
        request_id=run_id,
        run_id=run_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        trace_id=run_id,
    )
    outcome = pipeline.run(context=context, query=args.query)
    payload = {
        "success": outcome.success,
        "run_id": run_id,
        "artifacts": outcome.artifacts,
        "report": outcome.report,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if outcome.success else 2


__all__ = ["build_parser", "main"]
