"""Engineering-control primitives for traceable runtime feedback loops."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class DisturbanceType(str, Enum):
    """Disturbance taxonomy used in operations and policy traces."""

    LOAD = "load"
    ADVERSARY = "adversary"
    ENVIRONMENT = "environment"


class ActuatorAction(str, Enum):
    """Control action selected by a controller."""

    ALLOW = "allow"
    DEGRADE = "degrade"
    REJECT = "reject"
    ROUTE = "route"


@dataclass(frozen=True)
class ControlSignal:
    """Result of one control-loop evaluation."""

    action: ActuatorAction
    observed_value: float
    setpoint_value: float
    trace_attributes: Dict[str, object]


@dataclass(frozen=True)
class ControlStructure:
    """Standard control-theoretic structure for one runtime loop."""

    controlled_object: str
    setpoint_name: str
    setpoint_value: float
    sensor_name: str
    controller_name: str
    actuator_name: str

    def evaluate(
        self,
        observed_value: float,
        disturbance_type: DisturbanceType,
        trace_id: str,
    ) -> ControlSignal:
        """Evaluate observed value against setpoint and emit trace fields."""

        action = (
            ActuatorAction.DEGRADE
            if observed_value > self.setpoint_value
            else ActuatorAction.ALLOW
        )
        return ControlSignal(
            action=action,
            observed_value=observed_value,
            setpoint_value=self.setpoint_value,
            trace_attributes={
                "trace_id": trace_id,
                "controlled_object": self.controlled_object,
                "setpoint_name": self.setpoint_name,
                "setpoint_value": self.setpoint_value,
                "sensor_name": self.sensor_name,
                "observed_value": observed_value,
                "controller_name": self.controller_name,
                "actuator_name": self.actuator_name,
                "action": action.value,
                "disturbance_type": disturbance_type.value,
            },
        )


class ControlLoopRegistry:
    """Registry for named control structures and their latest signals."""

    def __init__(self) -> None:
        self._structures: Dict[str, ControlStructure] = {}
        self._signals: Dict[str, ControlSignal] = {}

    def register(self, name: str, structure: ControlStructure) -> None:
        """Register one named control-loop structure."""

        self._structures[name] = structure

    def evaluate(
        self,
        name: str,
        observed_value: float,
        disturbance_type: DisturbanceType,
        trace_id: str,
    ) -> ControlSignal:
        """Evaluate a named control loop and store its latest signal."""

        structure = self._structures[name]
        signal = structure.evaluate(
            observed_value=observed_value,
            disturbance_type=disturbance_type,
            trace_id=trace_id,
        )
        self._signals[name] = signal
        return signal

    def last_signal(self, name: str) -> Optional[ControlSignal]:
        """Return the most recent signal for a named loop."""

        return self._signals.get(name)
