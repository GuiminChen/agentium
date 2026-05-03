"""OpenTelemetry adapters for traces, metrics, and logs."""

from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any, ContextManager, Dict, Iterable, Optional
from typing_extensions import Protocol


class RuntimeTelemetry(Protocol):
    """Telemetry adapter protocol for runtime and tool execution hooks."""

    def start_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> ContextManager[None]:
        """Start a span context manager."""

    def record_tool_execution(
        self, tool_name: str, status: str, latency_ms: int, attributes: Dict[str, Any]
    ) -> None:
        """Record tool execution metrics and logs."""

    def record_runtime_turn(
        self, status: str, error_code: Optional[str], attributes: Dict[str, Any]
    ) -> None:
        """Record runtime turn metrics and logs."""

    def record_event(self, name: str, attributes: Dict[str, Any]) -> None:
        """Record generic telemetry event."""

    def record_quota_hard_limit_trigger(self, attributes: Dict[str, Any]) -> None:
        """Record a budget / hard-quota refusal (maps to phased-delivery quota counter)."""


class NullTelemetry:
    """No-op telemetry adapter."""

    def start_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> ContextManager[None]:
        del name, attributes
        return nullcontext()

    def record_tool_execution(
        self, tool_name: str, status: str, latency_ms: int, attributes: Dict[str, Any]
    ) -> None:
        del tool_name, status, latency_ms, attributes

    def record_runtime_turn(
        self, status: str, error_code: Optional[str], attributes: Dict[str, Any]
    ) -> None:
        del status, error_code, attributes

    def record_event(self, name: str, attributes: Dict[str, Any]) -> None:
        del name, attributes

    def record_quota_hard_limit_trigger(self, attributes: Dict[str, Any]) -> None:
        del attributes


class OTelTelemetry:
    """OpenTelemetry-based adapter for traces, metrics, and logs."""

    def __init__(
        self,
        service_name: str = "agentium",
        enable_console_export: bool = True,
        otlp_endpoint: Optional[str] = None,
        otlp_insecure: bool = True,
        metric_export_interval_ms: int = 30000,
        extra_span_exporters: Optional[Iterable[Any]] = None,
    ):
        self._logger = logging.getLogger("agentium.telemetry")
        self._enabled = False
        self._tool_counter = None
        self._runtime_counter = None
        self._tool_latency = None
        self._event_counter = None
        self._quota_hard_counter = None
        self._tracer = None
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
        except ImportError:
            return

        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        if enable_console_export:
            tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        for exporter in extra_span_exporters or ():
            tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
        self._configure_otlp_trace_exporter(
            tracer_provider=tracer_provider,
            otlp_endpoint=otlp_endpoint,
            otlp_insecure=otlp_insecure,
        )
        trace.set_tracer_provider(tracer_provider)

        metric_readers = []
        if enable_console_export:
            from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

            metric_readers.append(
                PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=30000)
            )
        self._configure_otlp_metric_reader(
            metric_readers=metric_readers,
            otlp_endpoint=otlp_endpoint,
            otlp_insecure=otlp_insecure,
            metric_export_interval_ms=metric_export_interval_ms,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        metrics.set_meter_provider(meter_provider)

        meter = metrics.get_meter("agentium.runtime")
        self._tracer = trace.get_tracer("agentium.runtime")
        self._tool_counter = meter.create_counter(
            "agentium_tool_executions_total",
            description="Total tool executions grouped by status",
        )
        self._runtime_counter = meter.create_counter(
            "agentium_runtime_turns_total",
            description="Total runtime turns grouped by status",
        )
        self._tool_latency = meter.create_histogram(
            "agentium_tool_latency_ms",
            unit="ms",
            description="Tool execution latency in milliseconds",
        )
        self._event_counter = meter.create_counter(
            "agentium_events_total",
            description="Security and governance event count",
        )
        self._quota_hard_counter = meter.create_counter(
            "agentium_quota_hard_limit_triggers_total",
            description="Hard budget or quota ceiling refusals at turn execution",
        )
        self._enabled = True

    @classmethod
    def from_env(cls, service_name: str = "agentium") -> "OTelTelemetry":
        """Build telemetry instance from OTEL-related environment variables."""

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        insecure = cls._parse_bool(os.getenv("OTEL_EXPORTER_OTLP_INSECURE"), default=True)
        enable_console = cls._parse_bool(os.getenv("AGENTIUM_OTEL_CONSOLE_EXPORT"), default=True)
        interval_ms = cls._parse_int(
            raw_value=os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS"), default=30000
        )
        return cls(
            service_name=service_name,
            enable_console_export=enable_console,
            otlp_endpoint=endpoint,
            otlp_insecure=insecure,
            metric_export_interval_ms=interval_ms,
        )

    @staticmethod
    def _parse_bool(raw_value: Optional[str], default: bool) -> bool:
        if raw_value is None:
            return default
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _parse_int(raw_value: Optional[str], default: int) -> int:
        if raw_value is None:
            return default
        try:
            return int(raw_value)
        except ValueError:
            return default

    def _configure_otlp_trace_exporter(
        self,
        tracer_provider: Any,
        otlp_endpoint: Optional[str],
        otlp_insecure: bool,
    ) -> None:
        if not otlp_endpoint:
            return
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        except ImportError:
            self._logger.warning("otlp trace exporter unavailable", extra={"endpoint": otlp_endpoint})
            return
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=otlp_insecure))
        )

    def _configure_otlp_metric_reader(
        self,
        metric_readers: Any,
        otlp_endpoint: Optional[str],
        otlp_insecure: bool,
        metric_export_interval_ms: int,
    ) -> None:
        if not otlp_endpoint:
            return
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        except ImportError:
            self._logger.warning("otlp metric exporter unavailable", extra={"endpoint": otlp_endpoint})
            return
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=otlp_endpoint, insecure=otlp_insecure),
                export_interval_millis=metric_export_interval_ms,
            )
        )

    def start_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None
    ) -> ContextManager[None]:
        if not self._enabled or self._tracer is None:
            return nullcontext()
        return self._tracer.start_as_current_span(name, attributes=attributes or {})

    def record_tool_execution(
        self, tool_name: str, status: str, latency_ms: int, attributes: Dict[str, Any]
    ) -> None:
        payload = {"tool_name": tool_name, "status": status}
        payload.update(attributes)
        if self._enabled and self._tool_counter is not None and self._tool_latency is not None:
            self._tool_counter.add(1, payload)
            self._tool_latency.record(max(0, latency_ms), payload)
        self._logger.info("otel.tool_execution", extra={"payload": payload})

    def record_runtime_turn(
        self, status: str, error_code: Optional[str], attributes: Dict[str, Any]
    ) -> None:
        payload = {"status": status, "error_code": error_code or "none"}
        payload.update(attributes)
        if self._enabled and self._runtime_counter is not None:
            self._runtime_counter.add(1, payload)
        self._logger.info("otel.runtime_turn", extra={"payload": payload})

    def record_event(self, name: str, attributes: Dict[str, Any]) -> None:
        payload = {"event_name": name}
        payload.update(attributes)
        if self._enabled and self._event_counter is not None:
            self._event_counter.add(1, payload)
        self._logger.info("otel.event", extra={"payload": payload})

    def record_quota_hard_limit_trigger(self, attributes: Dict[str, Any]) -> None:
        payload = dict(attributes)
        if self._enabled and self._quota_hard_counter is not None:
            self._quota_hard_counter.add(1, payload)
        self._logger.info("otel.quota_hard_limit", extra={"payload": payload})

