"""Unit tests for AIGatewayRouter."""

from __future__ import annotations

from agentium.ai_gateway.router import (
    AIGatewayRouter,
    CostTier,
    LatencyTier,
    ModelRoute,
    PrivacyClass,
    RouteRequest,
)
from agentium.governance.audit_lineage import InMemoryAuditSink
from agentium.models.context import RequestContext


def _ctx() -> RequestContext:
    return RequestContext(
        request_id="r",
        run_id="run-1",
        tenant_id="tenant-a",
        user_id="user-a",
        trace_id="trace",
    )


def test_router_picks_lowest_cost() -> None:
    routes = [
        ModelRoute(
            model_name="m-high",
            provider="p",
            privacy_class=PrivacyClass.PUBLIC,
            cost_tier=CostTier.HIGH,
            latency_tier=LatencyTier.LOW,
            capabilities=["chat"],
        ),
        ModelRoute(
            model_name="m-low",
            provider="p",
            privacy_class=PrivacyClass.PUBLIC,
            cost_tier=CostTier.LOW,
            latency_tier=LatencyTier.LOW,
            capabilities=["chat"],
        ),
    ]
    router = AIGatewayRouter(routes=routes)
    decision = router.decide_route(
        context=_ctx(),
        request=RouteRequest(
            data_privacy_class=PrivacyClass.PUBLIC,
            required_capabilities=["chat"],
        ),
    )
    assert decision.route is not None
    assert decision.route.model_name == "m-low"
    assert decision.reason_chain[0].startswith("privacy=")


def test_router_privacy_filter() -> None:
    routes = [
        ModelRoute(
            model_name="m-public",
            provider="p",
            privacy_class=PrivacyClass.PUBLIC,
            cost_tier=CostTier.LOW,
            latency_tier=LatencyTier.LOW,
            capabilities=["chat"],
        )
    ]
    sink = InMemoryAuditSink()
    router = AIGatewayRouter(routes=routes, audit_sink=sink)
    decision = router.decide_route(
        context=_ctx(),
        request=RouteRequest(
            data_privacy_class=PrivacyClass.REGULATED,
            required_capabilities=["chat"],
        ),
    )
    assert decision.route is None
    assert "no_candidate_after_filters" in decision.reason_chain
    assert any(e.event_type == "ai_route_decision" for e in sink.query())


def test_router_capability_filter() -> None:
    routes = [
        ModelRoute(
            model_name="m1",
            provider="p",
            privacy_class=PrivacyClass.PUBLIC,
            cost_tier=CostTier.LOW,
            latency_tier=LatencyTier.LOW,
            capabilities=["chat"],
        )
    ]
    router = AIGatewayRouter(routes=routes)
    decision = router.decide_route(
        context=_ctx(),
        request=RouteRequest(
            data_privacy_class=PrivacyClass.PUBLIC,
            required_capabilities=["vision"],
        ),
    )
    assert decision.route is None
    assert decision.rejected_reasons["m1"].startswith("missing_capability")
