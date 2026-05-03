"""Main CLI entrypoint."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from typing import Sequence

from agentium.cli.commands.ai_review import main as ai_review_main
from agentium.cli.commands.research import main as research_main


_LOGGER = logging.getLogger("agentium.cli")


def build_parser() -> argparse.ArgumentParser:
    """Build top-level parser with subcommands."""
    parser = argparse.ArgumentParser(prog="agentium", description="Agentium CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ai_review_parser = subparsers.add_parser("ai-review", help="Run AI code review")
    ai_review_parser.add_argument("--pr-number", type=int, required=True)
    ai_review_parser.add_argument("--repo", type=str, required=True)
    ai_review_parser.add_argument("--author", type=str, required=True)

    serve_parser = subparsers.add_parser("serve", help="Start backend HTTP control-plane server")
    serve_parser.add_argument("--host", type=str, default=None, help="Override AGENTIUM_HTTP_HOST")
    serve_parser.add_argument("--port", type=int, default=None, help="Override AGENTIUM_HTTP_PORT")

    subparsers.add_parser("init-db", help="Initialize SQLite governance schemas")
    subparsers.add_parser(
        "run-gates", help="Run release gates (governance/security/stability/eval/recovery)"
    )

    research_parser = subparsers.add_parser(
        "research", help="Run the DeepResearch pipeline"
    )
    research_parser.add_argument("research_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI main function."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ai-review":
        return ai_review_main(
            [
                "--pr-number",
                str(args.pr_number),
                "--repo",
                args.repo,
                "--author",
                args.author,
            ]
        )
    if args.command == "serve":
        return _run_serve(host=args.host, port=args.port)
    if args.command == "init-db":
        return _run_init_db()
    if args.command == "run-gates":
        return _run_release_gates()
    if args.command == "research":
        return research_main(args.research_args)
    parser.print_help()
    return 1


def _run_serve(host: str | None, port: int | None) -> int:
    """Start the HTTP control plane using settings + bootstrap."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from agentium.api.http.resources import HTTPControlPlaneResources
    from agentium.api.http_control_plane import build_http_server
    from agentium.app import build_runtime_container, load_settings

    from agentium.app.identity_factory import build_identity_provider

    settings = load_settings()
    try:
        identity_provider = build_identity_provider(settings)
    except ValueError as exc:
        _LOGGER.error("identity provider configuration invalid: %s", exc)
        return 1
    container = build_runtime_container(settings)
    container.start()

    bound_host = host or settings.host
    bound_port = port if port is not None else settings.port
    ui_links: dict[str, str] = {}
    if settings.grafana_base_url:
        ui_links["grafana"] = settings.grafana_base_url
    if settings.tempo_base_url:
        ui_links["tempo"] = settings.tempo_base_url
    from agentium.infra.db.sqlite_store import SqliteAuditSink

    sqlite_audit = (
        container.audit_sink if isinstance(container.audit_sink, SqliteAuditSink) else None
    )
    resources = HTTPControlPlaneResources(
        tool_registry=container.tool_registry,
        budget_service=container.budget_service,
        background_daemon=container.background_daemon,
        artifact_store=container.artifact_store,
        notify_bridge=container.notify_bridge,
        task_graph=container.task_graph_supervisor,
        deep_research_pipeline=container.deep_research_pipeline,
        emergence_guardrails=container.emergence_guardrails,
        evolution_plugin=container.evolution_plugin,
        evolution_http_enabled=settings.plugins.evolution.http_enabled,
        dev_http_enabled=(settings.profile == "dev"),
        lsp_upstream_configured=bool(settings.lsp_upstream_url),
        ui_links=ui_links or None,
        run_message_store=container.run_message_store,
        chat_session_store=container.chat_session_store,
        chat_turn_service=container.chat_turn_service,
        session_checkpoint_store=container.session_checkpoint_store,
        eval_run_store=container.eval_run_store,
        run_cancel_registry=container.run_cancel_registry,
        lifecycle_manager=container.lifecycle_manager,
        sqlite_audit_sink=sqlite_audit,
        domain_packs_root=settings.domain_packs_root,
    )
    server = build_http_server(
        api=container.api,
        host=bound_host,
        port=bound_port,
        identity_provider=identity_provider,
        identity_mode=settings.identity_mode,
        manifest_policy=container.manifest_policy,
        audit_sink=container.audit_sink,
        state_observer=container.state_observer,
        resources=resources,
    )

    def _graceful_stop(signum, frame):  # noqa: ANN001
        del signum, frame
        _LOGGER.info("agentium serve received shutdown signal")
        try:
            server.shutdown()
        except Exception:
            pass

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _graceful_stop)
            except (ValueError, OSError):
                pass

    _LOGGER.info(
        "agentium serve listening profile=%s host=%s port=%s",
        settings.profile,
        bound_host,
        bound_port,
    )
    try:
        server.serve_forever()
    finally:
        try:
            server.server_close()
        finally:
            container.shutdown()
    return 0


def _run_init_db() -> int:
    """Force-create SQLite tables for approvals, audit, budget."""

    from agentium.app import load_settings
    from agentium.coordination.budget_ledger import TenantBudget
    from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
    from agentium.infra.db.sqlite_store import (
        SqliteApprovalGate,
        SqliteAuditSink,
        SqliteBudgetLedger,
    )

    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = settings.sqlite_db_path
    audit = SqliteAuditSink(db_path)
    gate = SqliteApprovalGate(db_path)
    budget = SqliteBudgetLedger(
        db_path,
        tenant_budgets={
            "_default": TenantBudget(
                token_limit=settings.default_tenant_token_limit,
                cost_limit=settings.default_tenant_cost_limit,
                max_concurrency=settings.default_tenant_max_concurrency,
            )
        },
    )
    audit.close()
    gate.close()
    budget.close()
    chat_sess = SqliteChatSessionStore(db_path)
    chat_sess.close()
    sys.stdout.write(f"initialized sqlite at {db_path}\n")
    return 0


def _run_release_gates() -> int:
    """Run release gate validation script."""

    from agentium.evaluation.release_gates_runner import run_all_gates

    return run_all_gates()


if __name__ == "__main__":
    raise SystemExit(main())
