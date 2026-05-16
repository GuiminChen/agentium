"""CLI: research-job (P1-24 library-facing hook)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from agentium.coordination.research_job import ResearchJobService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentium research-job")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create", help="Create a local research job (in-memory demo)")
    create.add_argument("--query", required=True)
    create.add_argument("--tenant", default="cli-tenant")
    create.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "create":
        svc = ResearchJobService()
        rec = svc.create_job(tenant_id=args.tenant, query=args.query, max_workers=args.max_workers)
        sys.stdout.write(json.dumps(svc.to_http_dict(rec), ensure_ascii=False) + "\n")
        return 0
    return 1
