"""Tool registry integrating policy, budget, and audit controls."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import structlog

from agentium.coordination.budget_ledger import (
    BudgetService,
    ResourceDemand,
    ResourceLimitController,
)
from agentium.evaluation.ablation_mode import bypass_manifest_allowlist, coerce_policy_allow
from agentium.evaluation.eval_contamination_guard import EvalContaminationGuard
from agentium.governance.approval_gate import ApprovalService, ApprovalStatus
from agentium.governance.access_control import IAMAccessController
from agentium.governance.audit_lineage import AuditSink
from agentium.governance.policy_engine import PolicyEngine
from agentium.infra.telemetry import NullTelemetry, RuntimeTelemetry
from agentium.models.context import AuditRecord, Decision, DecisionType, RequestContext, ToolCallRecord
from agentium.runtime.prompt_cache_policy import PromptCachePolicy
from agentium.security.constitutional_guard import ConstitutionalGuard
from agentium.security.dlp_audit_stage import DLP_AUDIT_STAGE_TOOL_OUTPUT_POST
from agentium.security.dlp_classifier import DLPClassifier
from agentium.security.misuse_detector import MisuseDetector
from agentium.security.prompt_injection_probe import PromptInjectionProbe
from agentium.security.secret_leak_guard import SecretLeakGuard
from agentium.security.social_engineering_guard import SocialEngineeringGuard
from agentium.shared.errors import ApprovalRequiredError, BudgetExceededError, PolicyDeniedError
from agentium.tools.contract import ToolContract, ToolContractError, assert_contract_valid

_LOGGER = structlog.get_logger(__name__)


ToolHandler = Callable[[Dict[str, Any]], Dict[str, Any]]
PolicySelector = Callable[[RequestContext], PolicyEngine]

# Roles allowed to invoke high-risk tools on ``code-exec-mcp`` when tier gate is enabled.
_CODE_EXEC_PRIVILEGED_HIGH_RISK_ROLES: frozenset[str] = frozenset(
    {"admin", "tenant_admin", "platform_ops"}
)


@dataclass(frozen=True)
class ToolSpec:
    """Registered tool metadata and handler.

    Attributes:
        supply_origin: Provenance label for observability (``builtin``, ``mcp``, ``web``, ...).
    """

    name: str
    capabilities: List[str]
    risk_level: str
    handler: ToolHandler
    supply_origin: str = "builtin"


@dataclass(frozen=True)
class ToolExecutionResult:
    """Result produced by controlled tool execution."""

    output: Dict[str, Any]
    call_record: ToolCallRecord


class ToolRegistry:
    """Registry and controlled executor for tool handlers."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        budget_ledger: BudgetService,
        audit_sink: AuditSink,
        policy_selector: Optional[PolicySelector] = None,
        approval_gate: Optional[ApprovalService] = None,
        access_controller: Optional[IAMAccessController] = None,
        telemetry: Optional[RuntimeTelemetry] = None,
        resource_controller: Optional[ResourceLimitController] = None,
        prompt_injection_probe: Optional[PromptInjectionProbe] = None,
        constitutional_guard: Optional[ConstitutionalGuard] = None,
        misuse_detector: Optional[MisuseDetector] = None,
        prompt_cache_policy: Optional[PromptCachePolicy] = None,
        eval_contamination_guard: Optional[EvalContaminationGuard] = None,
        dlp_classifier: Optional[DLPClassifier] = None,
        secret_leak_guard: Optional[SecretLeakGuard] = None,
        social_engineering_guard: Optional[SocialEngineeringGuard] = None,
        require_contract: bool = False,
        default_estimated_tokens: int = 50,
        default_estimated_cost: float = 0.01,
        default_approval_ttl_seconds: Optional[int] = None,
        tool_contract_min_description_chars: int = 12,
        deny_high_risk_tools_under_code_exec_tier: bool = False,
    ) -> None:
        self._policy_engine = policy_engine
        self._policy_selector = policy_selector
        self._budget_ledger = budget_ledger
        self._audit_sink = audit_sink
        self._approval_gate = approval_gate
        self._access_controller = access_controller
        self._telemetry: RuntimeTelemetry = telemetry or NullTelemetry()
        self._resource_controller = resource_controller
        self._prompt_injection_probe = prompt_injection_probe
        self._constitutional_guard = constitutional_guard
        self._misuse_detector = misuse_detector
        self._prompt_cache_policy = prompt_cache_policy
        self._eval_contamination_guard = eval_contamination_guard
        self._dlp_classifier = dlp_classifier
        self._secret_leak_guard = secret_leak_guard
        self._social_engineering_guard = social_engineering_guard
        self._require_contract = require_contract
        self._default_estimated_tokens = default_estimated_tokens
        self._default_estimated_cost = default_estimated_cost
        self._default_approval_ttl_seconds = default_approval_ttl_seconds
        self._tool_contract_min_description_chars = max(1, int(tool_contract_min_description_chars))
        self._deny_high_risk_tools_under_code_exec_tier = deny_high_risk_tools_under_code_exec_tier
        self._tools: Dict[str, ToolSpec] = {}
        self._contracts: Dict[str, ToolContract] = {}

    @property
    def tool_contract_min_description_chars(self) -> int:
        """Minimum stripped description length enforced for registered contracts."""

        return self._tool_contract_min_description_chars

    @property
    def base_policy_engine(self) -> PolicyEngine:
        """Static policy engine from bootstrap (before release-based selector)."""

        return self._policy_engine

    def register(
        self, spec: ToolSpec, contract: Optional[ToolContract] = None
    ) -> None:
        """Register one tool specification (and optional contract)."""

        if spec.name in self._tools:
            raise ToolContractError(f"tool_name_conflict:{spec.name}")
        eff_min = self._tool_contract_min_description_chars
        if self._require_contract:
            assert_contract_valid(contract, spec.name, min_description_chars=eff_min)
        if contract is not None:
            assert_contract_valid(contract, spec.name, min_description_chars=eff_min)
            self._contracts[spec.name] = contract
        self._tools[spec.name] = spec

    def get_contract(self, name: str) -> Optional[ToolContract]:
        """Return registered contract for one tool, when available."""

        return self._contracts.get(name)

    CHAT_AGENT_TOOL_BLOCKLIST: frozenset[str] = frozenset({"db_export", "skill_invoke", "skill_run"})

    def list_chat_agent_tool_specs(self) -> List[ToolSpec]:
        """Low-risk tools eligible for LLM-driven chat loops (PRD guardrail subset).

        Excludes explicit blocklist names regardless of declared risk level.

        Returns:
            Sorted stable list suitable for OpenAI ``tools`` exposure when paired with contracts.
        """

        out: List[ToolSpec] = []
        for name in sorted(self._tools.keys()):
            if name in self.CHAT_AGENT_TOOL_BLOCKLIST:
                continue
            spec = self._tools[name]
            if spec.risk_level != "low":
                continue
            out.append(spec)
        return out

    def list_catalog_entries(self) -> List[Dict[str, Any]]:
        """Return JSON-serializable tool metadata for HTTP catalog (no handlers)."""

        entries: List[Dict[str, Any]] = []
        for name in sorted(self._tools.keys()):
            spec = self._tools[name]
            row: Dict[str, Any] = {
                "name": spec.name,
                "capabilities": list(spec.capabilities),
                "risk_level": spec.risk_level,
                "supply_origin": spec.supply_origin,
                "has_contract": name in self._contracts,
            }
            contract = self._contracts.get(name)
            if contract is not None:
                row["contract"] = {
                    "version": contract.version,
                    "description": contract.description,
                    "input_schema": contract.input_schema,
                }
            entries.append(row)
        return entries

    def _otel_attrs(
        self,
        context: RequestContext,
        tool_name: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build telemetry attributes carrying the trace/tenant/run/tool_use tuple."""

        attrs: Dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "trace_id": context.trace_id,
        }
        if tool_name is not None:
            attrs["tool_name"] = tool_name
        if tool_use_id is not None:
            attrs["tool_use_id"] = tool_use_id
        if extra:
            attrs.update(extra)
        return attrs

    def resolve(self, name: str) -> Optional[ToolSpec]:
        """Resolve a tool by name."""

        return self._tools.get(name)

    def _select_policy_engine(self, context: RequestContext) -> PolicyEngine:
        if self._policy_selector is None:
            return self._policy_engine
        return self._policy_selector(context)

    def execute(
        self,
        context: RequestContext,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        approval_id: Optional[str] = None,
    ) -> ToolExecutionResult:
        """Execute tool call through policy, budget, and audit pipeline."""

        call_args = args or {}
        self._telemetry.record_event(
            name="tool_execute_started",
            attributes={
                "tenant_id": context.tenant_id,
                "run_id": context.run_id,
                "trace_id": context.trace_id,
                "tool_name": name,
            },
        )
        self._run_access_control(context=context, tool_name=name, call_args=call_args)
        self._run_pre_execution_security(context=context, tool_name=name, call_args=call_args)

        tool_spec = self.resolve(name)
        if tool_spec is None:
            raise PolicyDeniedError("Tool is not registered and cannot be executed")

        if (
            self._deny_high_risk_tools_under_code_exec_tier
            and not coerce_policy_allow()
            and context.mcp_execution_tier == "code-exec-mcp"
            and tool_spec.risk_level == "high"
            and context.role not in _CODE_EXEC_PRIVILEGED_HIGH_RISK_ROLES
        ):
            self._audit_sink.append(
                AuditRecord(
                    event_type="execution_plane_tool_denied",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=self._policy_engine.version,
                    payload={
                        "tool_name": name,
                        "trace_id": context.trace_id,
                        "mcp_execution_tier": context.mcp_execution_tier,
                        "risk_level": tool_spec.risk_level,
                        "role": context.role,
                    },
                )
            )
            self._telemetry.record_event(
                name="execution_plane_tool_denied",
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": name,
                },
            )
            raise PolicyDeniedError(
                "High-risk tools are not permitted on code-exec-mcp tier for this role"
            )

        if context.manifest_declared_tools is not None and not bypass_manifest_allowlist():
            if name not in context.manifest_declared_tools:
                self._audit_sink.append(
                    AuditRecord(
                        event_type="run_manifest_tool_denied",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": name,
                            "trace_id": context.trace_id,
                            "run_manifest_sha256": context.run_manifest_sha256,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="run_manifest_tool_denied",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": name,
                    },
                )
                raise PolicyDeniedError(
                    f"Tool {name!r} is not declared in the run manifest allowlist"
                )

        policy_engine = self._select_policy_engine(context)
        decision = policy_engine.decide_tool_call(context, name, call_args)
        if coerce_policy_allow():
            decision = Decision(
                decision=DecisionType.ALLOW,
                reason="evaluation_ablation_permissive_coerce",
                rule_id=None,
            )
        self._audit_sink.append(
            AuditRecord(
                event_type="policy_decision",
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                policy_version=policy_engine.version,
                payload={
                    "tool_name": name,
                    "decision": decision.decision.value,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                },
            )
        )
        if decision.decision == DecisionType.DENY:
            raise PolicyDeniedError(decision.reason)
        if decision.decision == DecisionType.REQUIRE_APPROVAL:
            approval_id = self._ensure_approval(
                context=context,
                tool_name=name,
                call_args=call_args,
                reason=decision.reason,
                approval_id=approval_id,
            )
            if approval_id is not None:
                self._audit_sink.append(
                    AuditRecord(
                        event_type="approval_checked",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=policy_engine.version,
                        payload={"tool_name": name, "approval_id": approval_id},
                    )
                )

        self._enforce_resource_limits(context=context, tool_name=name, call_args=call_args)
        reserved = self._budget_ledger.reserve(
            context=context,
            estimated_tokens=self._default_estimated_tokens,
            estimated_cost=self._default_estimated_cost,
        )
        if not reserved:
            self._audit_sink.append(
                AuditRecord(
                    event_type="budget_rejected",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=policy_engine.version,
                    payload={"tool_name": name},
                )
            )
            raise BudgetExceededError("Budget reservation rejected for tool execution")

        started_at = time.monotonic()
        tool_use_id = str(uuid4())
        args_hash = self._hash_args(call_args)
        try:
            output = tool_spec.handler(call_args)
            latency_ms = int((time.monotonic() - started_at) * 1000)
            _LOGGER.info(
                "tool_execute_supply_origin",
                tool_name=name,
                supply_origin=tool_spec.supply_origin,
                tenant_id=context.tenant_id,
                run_id=context.run_id,
            )
            self._run_post_execution_security(
                context=context, tool_name=name, call_args=call_args, output=output
            )
            call_record = ToolCallRecord(
                tool_name=name,
                tool_use_id=tool_use_id,
                args_hash=args_hash,
                status="success",
                latency_ms=latency_ms,
            )
            self._budget_ledger.commit(
                context=context,
                actual_tokens=self._default_estimated_tokens,
                actual_cost=self._default_estimated_cost,
            )
            self._audit_sink.append(
                AuditRecord(
                    event_type="tool_executed",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=policy_engine.version,
                    payload={
                        "tool_name": name,
                        "tool_use_id": tool_use_id,
                        "latency_ms": latency_ms,
                        "status": "success",
                        "supply_origin": tool_spec.supply_origin,
                    },
                )
            )
            self._telemetry.record_tool_execution(
                tool_name=name,
                status="success",
                latency_ms=latency_ms,
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_use_id": tool_use_id,
                },
            )
            return ToolExecutionResult(output=output, call_record=call_record)
        except Exception:
            self._budget_ledger.release(context)
            self._audit_sink.append(
                AuditRecord(
                    event_type="tool_failed",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=policy_engine.version,
                    payload={"tool_name": name, "tool_use_id": tool_use_id},
                )
            )
            self._telemetry.record_tool_execution(
                tool_name=name,
                status="failed",
                latency_ms=0,
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_use_id": tool_use_id,
                },
            )
            raise

    def _run_access_control(
        self, context: RequestContext, tool_name: str, call_args: Dict[str, Any]
    ) -> None:
        if self._access_controller is None:
            return
        access_context = {
            "tenant_id": context.tenant_id,
            "deployment_mode": context.deployment_mode,
            "tool_args": call_args,
        }
        decision = self._access_controller.authorize_context(
            request_context=context,
            action=f"tool.execute.{tool_name}",
            resource=f"tool:{tool_name}",
            context=access_context,
        )
        for event in self._access_controller.collect_policy_events():
            event_type = str(event.get("event_type", "access_policy_event"))
            payload = dict(event)
            payload.pop("event_type", None)
            self._audit_sink.append(
                AuditRecord(
                    event_type=event_type,
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=self._policy_engine.version,
                    payload=payload,
                )
            )
            self._telemetry.record_event(
                name=event_type,
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                },
            )
        self._audit_sink.append(
            AuditRecord(
                event_type="access_decision",
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                policy_version=self._policy_engine.version,
                payload={
                    "tool_name": tool_name,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "policy_id": decision.policy_id,
                },
            )
        )
        self._telemetry.record_event(
            name="access_decision",
            attributes={
                "tenant_id": context.tenant_id,
                "run_id": context.run_id,
                "trace_id": context.trace_id,
                "tool_name": tool_name,
                "allowed": decision.allowed,
            },
        )
        if not decision.allowed:
            raise PolicyDeniedError(f"Access denied: {decision.reason}")

    def _enforce_resource_limits(
        self, context: RequestContext, tool_name: str, call_args: Dict[str, Any]
    ) -> None:
        if self._resource_controller is None:
            return
        raw_demand = call_args.get("resource_demand")
        if not isinstance(raw_demand, dict):
            return
        demand = ResourceDemand(
            memory_mb=self._optional_int(raw_demand.get("memory_mb")),
            cpu_millis=self._optional_int(raw_demand.get("cpu_millis")),
            tool_slots=self._optional_int(raw_demand.get("tool_slots")),
            outbound_rps=self._optional_float(raw_demand.get("outbound_rps")),
        )
        decision = self._resource_controller.evaluate(context=context, demand=demand)
        payload = {
            "tool_name": tool_name,
            "allowed": decision.allowed,
            "action": decision.action.value,
            "degraded": decision.degraded,
            "limit_name": decision.limit_name,
            "observed_value": decision.observed_value,
            "limit_value": decision.limit_value,
            "degrade_steps": list(decision.degrade_steps),
        }
        self._audit_sink.append(
            AuditRecord(
                event_type="resource_limit_decision",
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                policy_version=self._policy_engine.version,
                payload=payload,
            )
        )
        self._telemetry.record_event(
            name="resource_limit_decision",
            attributes={
                "tenant_id": context.tenant_id,
                "run_id": context.run_id,
                "trace_id": context.trace_id,
                "tool_name": tool_name,
                "allowed": decision.allowed,
                "action": decision.action.value,
                "limit_name": decision.limit_name or "",
            },
        )
        if not decision.allowed:
            raise BudgetExceededError(
                f"Resource limit rejected for {decision.limit_name}: {decision.action.value}"
            )

    def _run_pre_execution_security(
        self, context: RequestContext, tool_name: str, call_args: Dict[str, Any]
    ) -> None:
        text_payload = self._payload_text(call_args)
        if self._prompt_injection_probe is not None and text_payload:
            scan_result = self._prompt_injection_probe.scan(
                source="tool_output", content=text_payload
            )
            if scan_result.blocked:
                self._audit_sink.append(
                    AuditRecord(
                        event_type="prompt_injection_blocked",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "risk_level": scan_result.risk_level,
                            "indicators": scan_result.indicators,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="prompt_injection_blocked",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                    },
                )
                raise PolicyDeniedError("Prompt injection risk blocked")

        if self._eval_contamination_guard is not None:
            task_prompt = call_args.get("task_prompt")
            transcript = call_args.get("transcript")
            if isinstance(task_prompt, str) and isinstance(transcript, str):
                contamination = self._eval_contamination_guard.inspect_transcript(
                    task_prompt=task_prompt, transcript=transcript
                )
                if contamination.suspected:
                    self._audit_sink.append(
                        AuditRecord(
                            event_type="eval_contamination_blocked",
                            tenant_id=context.tenant_id,
                            run_id=context.run_id,
                            policy_version=self._policy_engine.version,
                            payload={
                                "tool_name": tool_name,
                                "reasons": contamination.reasons,
                            },
                        )
                    )
                    self._telemetry.record_event(
                        name="eval_contamination_blocked",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                    },
                )
                raise PolicyDeniedError("Eval contamination suspected")

        if self._misuse_detector is not None and text_payload:
            signals = self._misuse_detector.detect(text_payload)
            for signal in signals:
                self._audit_sink.append(
                    AuditRecord(
                        event_type="misuse_signal_detected",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "signal_type": signal.signal_type,
                            "confidence": signal.confidence,
                            "action": signal.action,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="misuse_signal_detected",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                        "signal_type": signal.signal_type,
                    },
                )

        if self._prompt_cache_policy is not None:
            cache_key = call_args.get("cache_key")
            if isinstance(cache_key, str) and cache_key:
                cache_stats = self._prompt_cache_policy.record_request(
                    cache_key=cache_key,
                    input_tokens=self._default_estimated_tokens,
                    latency_ms=100,
                )
                self._audit_sink.append(
                    AuditRecord(
                        event_type="prompt_cache_recorded",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "cache_key": cache_key,
                            "cache_hit": cache_stats.cache_hit,
                            "input_tokens_saved": cache_stats.input_tokens_saved,
                            "latency_ms_saved": cache_stats.latency_ms_saved,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="prompt_cache_recorded",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                        "cache_hit": cache_stats.cache_hit,
                    },
                )

    def _run_post_execution_security(
        self,
        context: RequestContext,
        tool_name: str,
        call_args: Dict[str, Any],
        output: Dict[str, Any],
    ) -> None:
        if self._dlp_classifier is not None:
            decision = self._dlp_classifier.classify_payload(output)
            if decision.hits:
                hit_labels = sorted({hit.label for hit in decision.hits})
                self._audit_sink.append(
                    AuditRecord(
                        event_type="dlp_hits_detected",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "labels": hit_labels,
                            "blocked": decision.blocked,
                            "dlp_stage": DLP_AUDIT_STAGE_TOOL_OUTPUT_POST,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="dlp_hits_detected",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                        "blocked": decision.blocked,
                    },
                )
                if decision.blocked:
                    self._audit_sink.append(
                        AuditRecord(
                            event_type="dlp_blocked",
                            tenant_id=context.tenant_id,
                            run_id=context.run_id,
                            policy_version=self._policy_engine.version,
                            payload={
                                "tool_name": tool_name,
                                "labels": hit_labels,
                                "dlp_stage": DLP_AUDIT_STAGE_TOOL_OUTPUT_POST,
                            },
                        )
                    )
                    raise PolicyDeniedError("DLP classifier blocked outbound payload")
        if self._secret_leak_guard is not None:
            leak = self._secret_leak_guard.scan_payload(output)
            if leak.hits:
                self._audit_sink.append(
                    AuditRecord(
                        event_type="secret_leak_detected",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "hit_count": len(leak.hits),
                            "blocked": leak.blocked,
                            "locations": sorted({h.location for h in leak.hits}),
                        },
                    )
                )
                self._telemetry.record_event(
                    name="secret_leak_detected",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                        "blocked": leak.blocked,
                    },
                )
                if leak.blocked:
                    raise PolicyDeniedError(
                        "Secret leak guard blocked outbound payload"
                    )

        if self._social_engineering_guard is not None:
            inbound_text = self._payload_text(call_args)
            outbound_text = self._payload_text(output)
            for direction, text in (("inbound", inbound_text), ("outbound", outbound_text)):
                se_decision = self._social_engineering_guard.classify(text)
                if not se_decision.hits:
                    continue
                self._audit_sink.append(
                    AuditRecord(
                        event_type="social_engineering_detected",
                        tenant_id=context.tenant_id,
                        run_id=context.run_id,
                        policy_version=self._policy_engine.version,
                        payload={
                            "tool_name": tool_name,
                            "direction": direction,
                            "severity": se_decision.severity,
                            "labels": sorted({h.label for h in se_decision.hits}),
                            "blocked": se_decision.blocked,
                        },
                    )
                )
                self._telemetry.record_event(
                    name="social_engineering_detected",
                    attributes={
                        "tenant_id": context.tenant_id,
                        "run_id": context.run_id,
                        "trace_id": context.trace_id,
                        "tool_name": tool_name,
                        "direction": direction,
                        "blocked": se_decision.blocked,
                    },
                )
                if se_decision.blocked:
                    raise PolicyDeniedError(
                        f"Social engineering guard blocked {direction} payload"
                    )

        if self._constitutional_guard is None:
            return
        decision = self._constitutional_guard.evaluate_exchange(
            input_text=self._payload_text(call_args),
            output_text=self._payload_text(output),
        )
        if decision.output_blocked or decision.input_blocked:
            self._audit_sink.append(
                AuditRecord(
                    event_type="constitutional_guard_blocked",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=self._policy_engine.version,
                    payload={
                        "tool_name": tool_name,
                        "policy_label": decision.policy_label,
                        "input_blocked": decision.input_blocked,
                        "output_blocked": decision.output_blocked,
                        "fallback_mode": decision.fallback_mode,
                    },
                )
            )
            self._telemetry.record_event(
                name="constitutional_guard_blocked",
                attributes={
                    "tenant_id": context.tenant_id,
                    "run_id": context.run_id,
                    "trace_id": context.trace_id,
                    "tool_name": tool_name,
                },
            )
            raise PolicyDeniedError("Constitutional guard blocked unsafe exchange")

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        try:
            return json.dumps(payload, sort_keys=True, ensure_ascii=False)
        except TypeError:
            return str(payload)

    @staticmethod
    def _hash_args(args: Dict[str, Any]) -> str:
        raw = json.dumps(args, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def search_chat_tools_for_agent_query(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Return ranked tool catalog hits for ``tool_search`` (P1-26)."""

        from agentium.tools.tool_search_index import rank_tool_rows

        rows: List[Tuple[str, str, str]] = []
        for spec in self.list_chat_agent_tool_specs():
            if spec.name == "tool_search":
                continue
            contract = self.get_contract(spec.name)
            desc = (contract.description if contract else "") or ""
            hay = f"{spec.name} {desc}"
            rows.append((spec.name, desc, hay))
        ranked = rank_tool_rows(rows, query=query, limit=limit)
        return [{"name": h.name, "score": h.score, "snippet": h.snippet} for h in ranked]

    def register_tool_search_meta(self) -> None:
        """Register the meta ``tool_search`` tool (idempotent)."""

        if "tool_search" in self._tools:
            return
        reg = self

        def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
            q = str(args.get("query", "")).strip()
            lim_raw = args.get("limit", 8)
            try:
                lim = max(1, min(32, int(lim_raw)))
            except (TypeError, ValueError):
                lim = 8
            hits = reg.search_chat_tools_for_agent_query(q, lim)
            return {"hits": hits, "query": q}

        contract = ToolContract(
            name="tool_search",
            version="v1",
            description=(
                "Search low-risk chat-eligible tools by keywords over tool names and "
                "descriptions; returns ranked hits for defer_loading discovery."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to match against the tool catalog.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 32,
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
            examples=[{"query": "calculator", "limit": 5}],
        )
        self.register(
            ToolSpec(
                name="tool_search",
                capabilities=["catalog.search"],
                risk_level="low",
                handler=_handler,
            ),
            contract=contract,
        )

    def execute_after_approval(
        self,
        context: RequestContext,
        name: str,
        approval_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> ToolExecutionResult:
        """Resume tool execution after a separate approval decision."""

        return self.execute(
            context=context,
            name=name,
            args=args,
            approval_id=approval_id,
        )

    def _ensure_approval(
        self,
        context: RequestContext,
        tool_name: str,
        call_args: Dict[str, Any],
        reason: str,
        approval_id: Optional[str],
    ) -> Optional[str]:
        if self._approval_gate is None:
            raise ApprovalRequiredError(reason)
        args_hash = self._hash_args(call_args)
        if approval_id is None:
            try:
                request = self._approval_gate.request_approval(
                    context=context,
                    tool_name=tool_name,
                    reason=reason,
                    args_hash=args_hash,
                    ttl_seconds=self._default_approval_ttl_seconds,
                )
            except TypeError:
                request = self._approval_gate.request_approval(
                    context=context,
                    tool_name=tool_name,
                    reason=reason,
                    args_hash=args_hash,
                )
            self._audit_sink.append(
                AuditRecord(
                    event_type="approval_requested",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    policy_version=self._policy_engine.version,
                    payload={
                        "tool_name": tool_name,
                        "approval_id": request.approval_id,
                        "reason": reason,
                    },
                )
            )
            raise ApprovalRequiredError(reason, approval_id=request.approval_id)

        request = self._approval_gate.get_request(approval_id)
        if request is None:
            raise ApprovalRequiredError("Approval request not found", approval_id=approval_id)
        if request.run_id != context.run_id or request.tool_name != tool_name:
            raise ApprovalRequiredError(
                "Approval request does not match current execution",
                approval_id=approval_id,
            )
        if request.args_hash != args_hash:
            raise ApprovalRequiredError(
                "Approval request args mismatch",
                approval_id=approval_id,
            )
        if request.status == ApprovalStatus.PENDING:
            raise ApprovalRequiredError(
                "Approval request is still pending",
                approval_id=approval_id,
            )
        if request.status == ApprovalStatus.REJECTED:
            raise PolicyDeniedError("Approval request was rejected")
        if request.status == ApprovalStatus.EXPIRED:
            raise PolicyDeniedError("Approval request expired before resume")
        return approval_id
