"""Connector registry for mapping connector plugins to tool handlers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from typing_extensions import Protocol

from agentium.shared.errors import ConfigurationError


class Connector(Protocol):
    """Connector plugin protocol.

    A connector receives a normalized request dictionary and returns
    a structured dictionary result.
    """

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one connector request."""


class ConnectorRegistry:
    """Registry that resolves connector plugins and adapter handlers."""

    def __init__(self) -> None:
        self._connectors: Dict[str, Connector] = {}

    def register(self, name: str, connector: Connector) -> None:
        """Register one connector instance by stable name."""

        if not name:
            raise ConfigurationError("Connector name must not be empty")
        self._connectors[name] = connector

    def resolve(self, name: str) -> Optional[Connector]:
        """Resolve connector by name."""

        return self._connectors.get(name)

    def as_tool_handler(
        self,
        connector_name: str,
        default_operation: Optional[str] = None,
    ) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        """Create a ToolRegistry-compatible handler for one connector.

        Args:
            connector_name: Registered connector name.
            default_operation: Optional default operation when missing in args.
        """

        connector = self.resolve(connector_name)
        if connector is None:
            raise ConfigurationError(f"Connector is not registered: {connector_name}")

        def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
            operation = self._resolve_operation(args=args, default_operation=default_operation)
            request: Dict[str, Any] = {
                "connector_name": connector_name,
                "operation": operation,
                "params": self._as_mapping(args.get("params")),
                "context": self._as_mapping(args.get("context")),
                "metadata": self._as_mapping(args.get("metadata")),
            }
            return connector.execute(request)

        return _handler

    @staticmethod
    def _resolve_operation(args: Dict[str, Any], default_operation: Optional[str]) -> str:
        operation = args.get("operation")
        if isinstance(operation, str) and operation:
            return operation
        if default_operation:
            return default_operation
        return "execute"

    @staticmethod
    def _as_mapping(raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        raise ConfigurationError("Connector args field must be a mapping")
