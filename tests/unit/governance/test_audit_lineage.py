from __future__ import annotations

from pathlib import Path

from agentium.governance.audit_lineage import InMemoryAuditSink, JsonlAuditSink
from agentium.models.context import AuditRecord


def test_audit_sink_append_and_query() -> None:
    sink = InMemoryAuditSink()
    sink.append(
        AuditRecord(
            event_type="tool_executed",
            tenant_id="tenant-a",
            run_id="run-1",
            payload={"tool": "web_search"},
        )
    )
    sink.append(
        AuditRecord(
            event_type="tool_executed",
            tenant_id="tenant-b",
            run_id="run-2",
            payload={"tool": "web_search"},
        )
    )

    records = sink.query(tenant_id="tenant-a")

    assert len(records) == 1
    assert records[0].tenant_id == "tenant-a"
    assert records[0].run_id == "run-1"


def test_jsonl_audit_sink_persists_and_queries(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path / "audit" / "events.jsonl")
    sink.append(
        AuditRecord(
            event_type="policy_decision",
            tenant_id="tenant-a",
            run_id="run-10",
            payload={"decision": "allow"},
        )
    )
    sink.append(
        AuditRecord(
            event_type="policy_decision",
            tenant_id="tenant-b",
            run_id="run-11",
            payload={"decision": "deny"},
        )
    )

    records = sink.query(run_id="run-10")

    assert len(records) == 1
    assert records[0].tenant_id == "tenant-a"
