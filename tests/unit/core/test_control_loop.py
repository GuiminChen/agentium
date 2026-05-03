from __future__ import annotations

from agentium.core.control_loop import (
    ActuatorAction,
    ControlLoopRegistry,
    ControlSignal,
    ControlStructure,
    DisturbanceType,
)


def test_control_structure_emits_trace_aligned_fields() -> None:
    structure = ControlStructure(
        controlled_object="tenant_queue",
        setpoint_name="queue_depth_slo",
        setpoint_value=10.0,
        sensor_name="queue_depth",
        controller_name="admission_rule",
        actuator_name="reject_or_degrade",
    )

    signal = structure.evaluate(
        observed_value=12.0,
        disturbance_type=DisturbanceType.LOAD,
        trace_id="trace-control-1",
    )

    assert signal.action == ActuatorAction.DEGRADE
    assert signal.trace_attributes["controlled_object"] == "tenant_queue"
    assert signal.trace_attributes["setpoint_name"] == "queue_depth_slo"
    assert signal.trace_attributes["disturbance_type"] == "load"


def test_control_loop_registry_records_last_signal() -> None:
    registry = ControlLoopRegistry()
    structure = ControlStructure(
        controlled_object="cost_rate",
        setpoint_name="budget_rate",
        setpoint_value=1.0,
        sensor_name="cost_per_second",
        controller_name="budget_controller",
        actuator_name="route_or_reject",
    )
    registry.register("budget", structure)

    signal = registry.evaluate(
        "budget",
        observed_value=0.5,
        disturbance_type=DisturbanceType.ENVIRONMENT,
        trace_id="trace-control-2",
    )

    assert isinstance(signal, ControlSignal)
    assert signal.action == ActuatorAction.ALLOW
    assert registry.last_signal("budget") == signal
