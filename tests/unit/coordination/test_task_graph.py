from __future__ import annotations

from agentium.coordination.task_graph import (
    OrphanPolicy,
    TaskGraphSupervisor,
    TaskRunStatus,
)


def test_orphan_policy_fail_marks_child_failed_when_parent_terminates() -> None:
    supervisor = TaskGraphSupervisor()
    supervisor.register_run(run_id="parent", tenant_id="tenant-a")
    supervisor.register_run(
        run_id="child",
        tenant_id="tenant-a",
        parent_run_id="parent",
        orphan_policy=OrphanPolicy.FAIL,
    )

    result = supervisor.terminate_run("parent")

    assert result["child"].status == TaskRunStatus.FAILED
    assert result["child"].orphaned is True


def test_orphan_policy_adopt_clears_parent_and_keeps_child_active() -> None:
    supervisor = TaskGraphSupervisor()
    supervisor.register_run(run_id="parent", tenant_id="tenant-a")
    supervisor.register_run(
        run_id="child",
        tenant_id="tenant-a",
        parent_run_id="parent",
        orphan_policy=OrphanPolicy.ADOPT,
    )

    result = supervisor.terminate_run("parent", adopter_run_id="supervisor")

    child = result["child"]
    assert child.status == TaskRunStatus.ADOPTED
    assert child.parent_run_id == "supervisor"
    assert child.orphaned is True


def test_orphan_policy_cancel_marks_descendant_cancelled() -> None:
    supervisor = TaskGraphSupervisor()
    supervisor.register_run(run_id="parent", tenant_id="tenant-a")
    supervisor.register_run(
        run_id="child",
        tenant_id="tenant-a",
        parent_run_id="parent",
        orphan_policy=OrphanPolicy.CANCEL,
    )

    result = supervisor.terminate_run("parent")

    assert result["child"].status == TaskRunStatus.CANCELLED
