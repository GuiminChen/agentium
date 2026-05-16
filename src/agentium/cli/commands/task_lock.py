"""CLI: task-lock (P2 cross-worker lease, host-local ops)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from agentium.app.logging_setup import setup_logging
from agentium.app.settings import load_settings
from agentium.coordination.task_lock.sqlite_backend import SqliteTaskLockBackend


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentium task-lock")
    parser.add_argument("--tenant", required=True, help="Tenant id for lock scope")
    parser.add_argument("--resource-key", required=True, dest="resource_key", help="Logical resource key")
    parser.add_argument(
        "--holder",
        required=True,
        dest="holder_run_id",
        help="Holder id (e.g. run_id / job_id)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire", help="Try to acquire a lease")
    acq.add_argument("--ttl-seconds", type=float, default=300.0)

    ren = sub.add_parser("renew", help="Renew an existing lease")
    ren.add_argument("--ttl-seconds", type=float, default=300.0)

    sub.add_parser("release", help="Release lease if holder matches")

    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = load_settings()
    setup_logging(settings)
    if not settings.feature_task_lock_enabled:
        sys.stderr.write(
            "task-lock: AGENTIUM_FEATURE_TASK_LOCK is off; enable it to use SQLite locks.\n"
        )
        return 2
    ttl_cap = float(settings.task_lock_max_ttl_seconds)
    be = SqliteTaskLockBackend(path=settings.task_lock_sqlite_path)

    if args.cmd == "acquire":
        ttl = max(1.0, min(float(args.ttl_seconds), ttl_cap))
        lease = be.try_acquire(
            tenant_id=args.tenant,
            resource_key=args.resource_key,
            holder_run_id=args.holder_run_id,
            ttl_seconds=ttl,
        )
        if lease is None:
            sys.stdout.write(json.dumps({"ok": False, "reason": "denied"}) + "\n")
            return 1
        sys.stdout.write(
            json.dumps(
                {
                    "ok": True,
                    "tenant_id": lease.tenant_id,
                    "resource_key": lease.resource_key,
                    "holder_run_id": lease.holder_run_id,
                    "issued_at": lease.issued_at,
                    "expires_at": lease.expires_at,
                }
            )
            + "\n"
        )
        return 0
    if args.cmd == "renew":
        ttl = max(1.0, min(float(args.ttl_seconds), ttl_cap))
        lease = be.renew(
            tenant_id=args.tenant,
            resource_key=args.resource_key,
            holder_run_id=args.holder_run_id,
            ttl_seconds=ttl,
        )
        if lease is None:
            sys.stdout.write(json.dumps({"ok": False, "reason": "not_holder_or_missing"}) + "\n")
            return 1
        sys.stdout.write(
            json.dumps(
                {
                    "ok": True,
                    "expires_at": lease.expires_at,
                }
            )
            + "\n"
        )
        return 0
    if args.cmd == "release":
        ok = be.release(
            tenant_id=args.tenant,
            resource_key=args.resource_key,
            holder_run_id=args.holder_run_id,
        )
        sys.stdout.write(json.dumps({"ok": ok}) + "\n")
        return 0 if ok else 1
    return 1
