"""AI gateway router with privacy-first deterministic decision chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from agentium.ai_gateway.content_safety import (
    ContentSafetyDecision,
    ContentSafetyPipeline,
)
from agentium.ai_gateway.prompt_output_policy import (
    PromptOutputPolicy,
    PromptOutputPolicyError,
    assert_prompt_complies,
    assert_response_complies,
)
from agentium.ai_gateway.token_rate_limit import (
    RateLimitDecision,
    TokenRateLimiter,
)
from agentium.governance.audit_lineage import AuditSink
from agentium.models.context import AuditRecord, RequestContext
from agentium.shared.errors import PolicyDeniedError


class PrivacyClass(str, Enum):
    """Data privacy class for routing decisions (lower is more sensitive)."""

    REGULATED = "regulated"
    INTERNAL = "internal"
    PUBLIC = "public"


class CostTier(str, Enum):
    """Coarse cost bucket; lower means cheaper per call."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LatencyTier(str, Enum):
    """Coarse latency bucket; lower means faster."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_PRIVACY_RANK = {PrivacyClass.REGULATED: 0, PrivacyClass.INTERNAL: 1, PrivacyClass.PUBLIC: 2}
_COST_RANK = {CostTier.LOW: 0, CostTier.MEDIUM: 1, CostTier.HIGH: 2}
_LATENCY_RANK = {LatencyTier.LOW: 0, LatencyTier.MEDIUM: 1, LatencyTier.HIGH: 2}


class ModelRoute(BaseModel):
    """One candidate model route considered by the router."""

    model_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    privacy_class: PrivacyClass
    cost_tier: CostTier
    latency_tier: LatencyTier
    capabilities: List[str] = Field(default_factory=list)

    class Config:
        extra = "forbid"


class RouteRequest(BaseModel):
    """Routing request describing data sensitivity and required capabilities."""

    data_privacy_class: PrivacyClass
    required_capabilities: List[str] = Field(default_factory=list)
    max_cost_tier: Optional[CostTier] = None
    max_latency_tier: Optional[LatencyTier] = None

    class Config:
        extra = "forbid"


@dataclass
class RouteDecision:
    """Outcome of one routing call with explanation chain."""

    route: Optional[ModelRoute]
    reason_chain: List[str] = field(default_factory=list)
    rejected_reasons: Dict[str, str] = field(default_factory=dict)


class NullModelClient:
    """No-op model client used for offline tests and gates."""

    def complete(self, prompt: str) -> str:
        return f"[null-model-echo] {prompt[:80]}"


class AIGatewayRouter:
    """Deterministic router applying privacy -> policy -> cost -> latency."""

    def __init__(
        self,
        routes: Sequence[ModelRoute],
        audit_sink: Optional[AuditSink] = None,
        denied_capabilities: Optional[Sequence[str]] = None,
        *,
        rate_limiter: Optional[TokenRateLimiter] = None,
        content_safety: Optional[ContentSafetyPipeline] = None,
        prompt_output_policy: Optional[PromptOutputPolicy] = None,
    ) -> None:
        self._routes = list(routes)
        self._audit_sink = audit_sink
        self._denied = set(denied_capabilities or [])
        self._rate_limiter = rate_limiter
        self._content_safety = content_safety
        self._prompt_output_policy = prompt_output_policy

    def decide_route(
        self,
        context: RequestContext,
        request: RouteRequest,
    ) -> RouteDecision:
        """Pick the lowest-cost, lowest-latency route satisfying invariants."""

        decision = RouteDecision(route=None)
        request_rank = _PRIVACY_RANK[request.data_privacy_class]
        candidates: List[ModelRoute] = []
        for route in self._routes:
            if _PRIVACY_RANK[route.privacy_class] > request_rank:
                decision.rejected_reasons[route.model_name] = "privacy_class_too_open"
                continue
            denied_caps = [cap for cap in request.required_capabilities if cap in self._denied]
            if denied_caps:
                decision.rejected_reasons[route.model_name] = (
                    f"capability_denied:{','.join(sorted(denied_caps))}"
                )
                continue
            missing_caps = [
                cap for cap in request.required_capabilities if cap not in route.capabilities
            ]
            if missing_caps:
                decision.rejected_reasons[route.model_name] = (
                    f"missing_capability:{','.join(sorted(missing_caps))}"
                )
                continue
            if (
                request.max_cost_tier is not None
                and _COST_RANK[route.cost_tier] > _COST_RANK[request.max_cost_tier]
            ):
                decision.rejected_reasons[route.model_name] = "cost_tier_exceeded"
                continue
            if (
                request.max_latency_tier is not None
                and _LATENCY_RANK[route.latency_tier] > _LATENCY_RANK[request.max_latency_tier]
            ):
                decision.rejected_reasons[route.model_name] = "latency_tier_exceeded"
                continue
            candidates.append(route)
        if not candidates:
            decision.reason_chain = [
                f"privacy={request.data_privacy_class.value}",
                "no_candidate_after_filters",
            ]
            self._audit(context, request, decision)
            return decision
        candidates.sort(
            key=lambda r: (
                _PRIVACY_RANK[r.privacy_class],
                _COST_RANK[r.cost_tier],
                _LATENCY_RANK[r.latency_tier],
                r.model_name,
            )
        )
        chosen = candidates[0]
        decision.route = chosen
        decision.reason_chain = [
            f"privacy={request.data_privacy_class.value}->{chosen.privacy_class.value}",
            "policy=passed",
            f"cost={chosen.cost_tier.value}",
            f"latency={chosen.latency_tier.value}",
        ]
        self._audit(context, request, decision)
        return decision

    def complete(
        self,
        context: RequestContext,
        request: RouteRequest,
        prompt: str,
        *,
        client: Optional["NullModelClient"] = None,
        estimated_tokens: int = 256,
    ) -> Dict[str, Any]:
        """Run the full pipeline: route → rate limit → safety → call → safety.

        Args:
            context: governance request context (tenant id, run id, ...).
            request: routing constraints (privacy, capabilities, ...).
            prompt: user-controlled prompt.
            client: optional model client; defaults to :class:`NullModelClient`
                so the gateway is always callable in offline tests.
            estimated_tokens: tokens to charge against the rate limiter.

        Returns:
            A dict with ``status`` (``ok`` / ``blocked`` / ``rate_limited`` /
            ``no_route``), ``response`` (str | None), ``decision``
            (RouteDecision), and ``safety`` (ContentSafetyDecision | None).

        Raises:
            PolicyDeniedError: when the prompt or response violates the
                configured :class:`PromptOutputPolicy`.
        """

        if self._prompt_output_policy is not None:
            try:
                assert_prompt_complies(prompt, self._prompt_output_policy)
            except PromptOutputPolicyError as exc:
                raise PolicyDeniedError(str(exc)) from exc

        rate_decision: Optional[RateLimitDecision] = None
        if self._rate_limiter is not None:
            rate_decision = self._rate_limiter.reserve(
                context.tenant_id, estimated_tokens
            )
            if not rate_decision.allowed:
                return {
                    "status": "rate_limited",
                    "response": None,
                    "decision": None,
                    "safety": None,
                    "rate": rate_decision,
                }

        inbound_safety: Optional[ContentSafetyDecision] = None
        if self._content_safety is not None:
            inbound_safety = self._content_safety.evaluate_inbound(prompt)
            if not inbound_safety.allowed:
                if rate_decision is not None and self._rate_limiter is not None:
                    self._rate_limiter.refund(context.tenant_id, estimated_tokens)
                return {
                    "status": "blocked",
                    "response": None,
                    "decision": None,
                    "safety": inbound_safety,
                    "rate": rate_decision,
                }

        route_decision = self.decide_route(context, request)
        if route_decision.route is None:
            if rate_decision is not None and self._rate_limiter is not None:
                self._rate_limiter.refund(context.tenant_id, estimated_tokens)
            return {
                "status": "no_route",
                "response": None,
                "decision": route_decision,
                "safety": inbound_safety,
                "rate": rate_decision,
            }

        model_client = client or NullModelClient()
        response = model_client.complete(prompt)

        if self._prompt_output_policy is not None:
            try:
                assert_response_complies(response, self._prompt_output_policy)
            except PromptOutputPolicyError as exc:
                raise PolicyDeniedError(str(exc)) from exc

        outbound_safety: Optional[ContentSafetyDecision] = None
        if self._content_safety is not None:
            outbound_safety = self._content_safety.evaluate_outbound(
                response, prompt_for_constitution=prompt
            )
            if not outbound_safety.allowed:
                return {
                    "status": "blocked",
                    "response": outbound_safety.redacted_output,
                    "decision": route_decision,
                    "safety": outbound_safety,
                    "rate": rate_decision,
                }

        return {
            "status": "ok",
            "response": response,
            "decision": route_decision,
            "safety": outbound_safety or inbound_safety,
            "rate": rate_decision,
        }

    def _audit(
        self,
        context: RequestContext,
        request: RouteRequest,
        decision: RouteDecision,
    ) -> None:
        if self._audit_sink is None:
            return
        request_payload = (
            request.model_dump(mode="json") if hasattr(request, "model_dump") else request.dict()
        )
        route_payload = None
        if decision.route is not None:
            route_payload = (
                decision.route.model_dump(mode="json")
                if hasattr(decision.route, "model_dump")
                else decision.route.dict()
            )
        try:
            self._audit_sink.append(
                AuditRecord(
                    event_type="ai_route_decision",
                    tenant_id=context.tenant_id,
                    run_id=context.run_id,
                    payload={
                        "request": request_payload,
                        "route": route_payload,
                        "reason_chain": decision.reason_chain,
                        "rejected_reasons": decision.rejected_reasons,
                    },
                )
            )
        except Exception:
            pass
