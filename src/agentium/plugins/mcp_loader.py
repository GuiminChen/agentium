"""MCP-style external tool loader with contract gating and tenant isolation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from agentium.tools.contract import ToolContract, ToolContractError, assert_contract_valid
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


class McpUnsignedPluginError(Exception):
    """Raised when :class:`McpLoader` requires an attestation digest but none was provided."""


@dataclass
class McpToolDescriptor:
    """Description of an MCP-compatible tool to register."""

    name: str
    capabilities: List[str]
    risk_level: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    contract: ToolContract
    tenant_scope: Optional[str] = None  # if set, tool is exposed only to one tenant
    signature_digest: Optional[str] = None  # non-empty attestation when signature gate enabled


class McpLoader:
    """Load MCP descriptors into a ToolRegistry with contract enforcement."""

    def __init__(
        self,
        registry: ToolRegistry,
        contract_index: Optional[Dict[str, ToolContract]] = None,
        *,
        require_signature: bool = False,
        audit_sink: Any = None,
    ) -> None:
        self._registry = registry
        self._contracts: Dict[str, ToolContract] = contract_index or {}
        self._scopes: Dict[str, Optional[str]] = {}
        self._require_signature = bool(require_signature)
        self._audit_sink = audit_sink

    def register_descriptor(self, descriptor: McpToolDescriptor) -> None:
        """Register one MCP descriptor, enforcing contract validity."""

        assert_contract_valid(descriptor.contract, descriptor.name)
        if self._require_signature and not (descriptor.signature_digest or "").strip():
            if self._audit_sink is not None:
                try:
                    from agentium.models.context import AuditRecord

                    self._audit_sink.append(
                        AuditRecord(
                            event_type="mcp_plugin_unsigned_blocked",
                            tenant_id="_system",
                            run_id="_plugin_load",
                            payload={"tool_name": descriptor.name},
                        )
                    )
                except Exception:
                    pass
            raise McpUnsignedPluginError(
                f"signature_digest required for MCP tool {descriptor.name!r} "
                "when require_signature=True"
            )
        scoped_name = descriptor.name
        if descriptor.tenant_scope:
            scoped_name = f"{descriptor.tenant_scope}::{descriptor.name}"
        self._registry.register(
            ToolSpec(
                name=scoped_name,
                capabilities=descriptor.capabilities,
                risk_level=descriptor.risk_level,
                handler=descriptor.handler,
            )
        )
        self._contracts[scoped_name] = descriptor.contract
        self._scopes[scoped_name] = descriptor.tenant_scope

    def get_contract(self, tool_name: str) -> Optional[ToolContract]:
        """Return the registered contract for a tool name."""

        return self._contracts.get(tool_name)

    def is_visible_to_tenant(self, tool_name: str, tenant_id: str) -> bool:
        """Return whether the tool is reachable from a given tenant."""

        scope = self._scopes.get(tool_name)
        if scope is None:
            return True
        return scope == tenant_id

    def list_tools_for_tenant(self, tenant_id: str) -> List[str]:
        """List visible tool names for a tenant."""

        return [
            name
            for name in self._contracts
            if self.is_visible_to_tenant(name, tenant_id)
        ]


__all__ = [
    "McpLoader",
    "McpToolDescriptor",
    "McpUnsignedPluginError",
    "ToolContract",
    "ToolContractError",
]
