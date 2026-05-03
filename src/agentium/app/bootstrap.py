"""Backend service bootstrap and dependency wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agentium.ai_gateway.deepseek_chat import DeepSeekChatCompletionClient
from agentium.api.control_plane import ControlPlaneAPI
from agentium.app.settings import AppSettings
from agentium.background.event_ingestor import EventIngestor
from agentium.background.memory_consolidator import MemoryConsolidator
from agentium.background.notify_bridge import NotifyBridge
from agentium.background.trigger_planner import TriggerPlanner, TriggerRule
from agentium.channels import (
    ChannelAdapter,
    NullChannelAdapter,
    OutboundOrchestrator,
    RateLimit,
)
from agentium.coordination.chat_turn_service import ChatTurnService
from agentium.coordination.artifact_store import ArtifactStore
from agentium.coordination.budget_ledger import (
    BudgetLedger,
    BudgetService,
    ResourceLimitController,
    TenantBudget,
)
from agentium.coordination.emergence_guardrails import (
    EmergenceGuardrails,
    GuardrailLimit,
)
from agentium.coordination.task_graph import TaskGraphSupervisor
from agentium.core.agent_lifecycle import AgentLifecycleManager
from agentium.core.agent_runtime import AgentRuntime
from agentium.core.run_cancellation import RunCancelRegistry
from agentium.core.scheduler import TenantFairScheduler
from agentium.core.state_observer import HealthStatus, ProbeReport, StateObserver
from agentium.evaluation.eval_contamination_guard import EvalContaminationGuard
from agentium.infra.mq.inproc_bus import InprocBus
from agentium.sandbox import ResourceManager, SafetySandbox
from agentium.sandbox.safety_sandbox import SandboxProfile
from agentium.app.plugin_runtime_factory import build_plugin_runtime
from agentium.governance.evolution_plugin import EvolutionPlugin
from agentium.governance.proposal_queue import ProposalQueue
from agentium.memory.memory_service import MemoryService
from agentium.models.context import RequestContext
from agentium.governance.approval_gate import ApprovalGate, ApprovalService
from agentium.governance.audit_lineage import (
    AuditSink,
    InMemoryAuditSink,
    JsonlAuditSink,
)
from agentium.governance.policy_engine import PolicyEngine
from agentium.governance.policy_release import HMACPolicySigner
from agentium.governance.policy_release_manager import PolicyReleaseManager
from agentium.infra.db.sqlite_chat_session_store import SqliteChatSessionStore
from agentium.infra.db.sqlite_store import (
    SqliteApprovalGate,
    SqliteAuditSink,
    SqliteBudgetLedger,
    SqliteEvalRunStore,
    SqliteRunMessageStore,
    SqliteSessionCheckpointStore,
)
from agentium.infra.telemetry import NullTelemetry, OTelTelemetry, RuntimeTelemetry
from agentium.models.run_manifest import RunManifestPolicy
from agentium.runtime.deepresearch_pipeline import DeepResearchPipeline, stub_handlers
from agentium.security.constitutional_guard import ConstitutionalGuard
from agentium.security.dlp_classifier import DLPClassifier
from agentium.security.misuse_detector import MisuseDetector
from agentium.security.prompt_injection_probe import PromptInjectionProbe
from agentium.security.secret_leak_guard import SecretLeakGuard
from agentium.security.social_engineering_guard import SocialEngineeringGuard
from agentium.tools.builtin_skill import register_skill_tools
from agentium.tools.builtin_defaults import register_builtin_tools
from agentium.tools.tool_registry import ToolRegistry, ToolSpec


@dataclass
class RuntimeContainer:
    """Resolved set of dependencies for a backend process.

    Attributes:
        settings: Resolved application settings.
        api: Control-plane API surface.
        runtime: Underlying agent runtime.
        approval_service: Approval gate service.
        audit_sink: Audit lineage sink.
        budget_service: Budget ledger service.
        manifest_policy: Run manifest acceptance policy.
        telemetry: Telemetry adapter.
        scheduler: Tenant-aware scheduler for admission and backpressure.
        lifecycle_manager: Lifecycle state machine for run cleanup and stop semantics.
        resource_controller: Resource soft/hard limit evaluator.
        background_daemon: Optional background-plane daemon if enabled.
        tool_registry: Tool registry instance for late tool registration.
        proposal_queue: Async change proposals from evolution / operators (governed).
        evolution_plugin: Pluggable learning loop (submits proposals only).
        plugins_fingerprint: Non-secret snapshot of YAML plugin selections (observability / REPRO).
    """

    settings: AppSettings
    api: ControlPlaneAPI
    runtime: AgentRuntime
    approval_service: ApprovalService
    audit_sink: AuditSink
    budget_service: BudgetService
    manifest_policy: RunManifestPolicy
    telemetry: RuntimeTelemetry
    tool_registry: ToolRegistry
    state_observer: StateObserver
    safety_sandbox: SafetySandbox
    resource_manager: ResourceManager
    ipc_bus: InprocBus
    artifact_store: ArtifactStore
    emergence_guardrails: EmergenceGuardrails
    outbound_orchestrator: OutboundOrchestrator
    notify_bridge: NotifyBridge
    memory_service: MemoryService
    event_ingestor: EventIngestor
    trigger_planner: TriggerPlanner
    memory_consolidator: MemoryConsolidator
    scheduler: TenantFairScheduler
    lifecycle_manager: AgentLifecycleManager
    resource_controller: ResourceLimitController
    task_graph_supervisor: TaskGraphSupervisor
    deep_research_pipeline: DeepResearchPipeline
    proposal_queue: ProposalQueue
    evolution_plugin: EvolutionPlugin
    plugins_fingerprint: dict
    run_message_store: SqliteRunMessageStore
    chat_session_store: SqliteChatSessionStore
    chat_turn_service: ChatTurnService
    session_checkpoint_store: SqliteSessionCheckpointStore
    eval_run_store: SqliteEvalRunStore
    run_cancel_registry: RunCancelRegistry
    background_daemon: Optional[object] = None
    _shutdown_callbacks: list = None  # type: ignore[assignment]

    def start(self) -> None:
        """Start background workers (background plane daemon, etc.) when configured."""

        if self.background_daemon is not None and hasattr(self.background_daemon, "start"):
            self.background_daemon.start()

    def shutdown(self) -> None:
        """Release resources: stop daemons, close persistent stores."""

        if self.background_daemon is not None and hasattr(self.background_daemon, "stop"):
            try:
                self.background_daemon.stop()
            except Exception:
                pass
        if self._shutdown_callbacks:
            for callback in reversed(self._shutdown_callbacks):
                try:
                    callback()
                except Exception:
                    pass


def _build_audit_sink(settings: AppSettings) -> tuple[AuditSink, list]:
    callbacks: list = []
    if settings.audit_backend == "sqlite":
        sink = SqliteAuditSink(settings.sqlite_db_path)
        callbacks.append(sink.close)
        return sink, callbacks
    if settings.audit_backend == "jsonl":
        return JsonlAuditSink(settings.audit_jsonl_path), callbacks
    return InMemoryAuditSink(), callbacks


def _build_approval_service(settings: AppSettings) -> tuple[ApprovalService, list]:
    callbacks: list = []
    if settings.approval_backend == "sqlite":
        gate = SqliteApprovalGate(settings.sqlite_db_path)
        callbacks.append(gate.close)
        return gate, callbacks
    return ApprovalGate(), callbacks


def _build_default_tenant_budget(settings: AppSettings) -> TenantBudget:
    return TenantBudget(
        token_limit=settings.default_tenant_token_limit,
        cost_limit=settings.default_tenant_cost_limit,
        max_concurrency=settings.default_tenant_max_concurrency,
    )


def _build_budget_service(settings: AppSettings) -> BudgetService:
    default_budget = _build_default_tenant_budget(settings)
    return BudgetLedger(tenant_budgets={}, default_budget=default_budget)


def _build_telemetry(settings: AppSettings) -> RuntimeTelemetry:
    if settings.telemetry_mode == "otel":
        return OTelTelemetry.from_env(service_name="agentium")
    return NullTelemetry()


def build_deepseek_client_from_settings(settings: AppSettings) -> Optional[DeepSeekChatCompletionClient]:
    """Instantiate DeepSeek REST client when API key env is present."""

    if not settings.deepseek_api_key:
        return None
    return DeepSeekChatCompletionClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.chat_completion_model,
        timeout_seconds=float(settings.chat_completion_timeout_seconds),
    )


def build_runtime_container(
    settings: AppSettings,
    extra_tools: Optional[list[ToolSpec]] = None,
) -> RuntimeContainer:
    """Wire all backend dependencies according to settings.

    Args:
        settings: Resolved application settings.
        extra_tools: Optional extra ToolSpec list to register at startup.
    """

    if not settings.policy_path.exists():
        raise FileNotFoundError(
            f"Policy file not found: {settings.policy_path}. Set AGENTIUM_POLICY_PATH "
            "or create the default config."
        )

    settings.data_dir.mkdir(parents=True, exist_ok=True)

    policy_engine = PolicyEngine.load(settings.policy_path)
    audit_sink, audit_callbacks = _build_audit_sink(settings)
    approval_service, approval_callbacks = _build_approval_service(settings)
    run_message_store = SqliteRunMessageStore(settings.sqlite_db_path)
    chat_session_store = SqliteChatSessionStore(settings.sqlite_db_path)
    session_checkpoint_store = SqliteSessionCheckpointStore(settings.sqlite_db_path)
    eval_run_store = SqliteEvalRunStore(settings.sqlite_db_path)
    store_callbacks = [
        run_message_store.close,
        chat_session_store.close,
        session_checkpoint_store.close,
        eval_run_store.close,
    ]
    deepseek_chat = build_deepseek_client_from_settings(settings)
    chat_turn_service = ChatTurnService(
        run_message_store=run_message_store,
        chat_session_store=chat_session_store,
        deepseek_client=deepseek_chat,
        audit_sink=getattr(audit_sink, "append", None),
    )
    run_cancel_registry = RunCancelRegistry()
    budget_service = _build_budget_service(settings)
    resource_controller = ResourceLimitController(
        tenant_budgets={}, default_budget=_build_default_tenant_budget(settings)
    )
    scheduler = TenantFairScheduler(
        max_concurrency_per_tenant=settings.default_tenant_max_concurrency,
        global_max_concurrency=max(1, settings.default_tenant_max_concurrency * 4),
    )
    lifecycle_manager = AgentLifecycleManager()
    telemetry = _build_telemetry(settings)

    tool_registry = ToolRegistry(
        policy_engine=policy_engine,
        budget_ledger=budget_service,
        audit_sink=audit_sink,
        approval_gate=approval_service,
        access_controller=None,
        telemetry=telemetry,
        resource_controller=resource_controller,
        prompt_injection_probe=PromptInjectionProbe(),
        constitutional_guard=ConstitutionalGuard(),
        misuse_detector=MisuseDetector(),
        prompt_cache_policy=None,
        eval_contamination_guard=EvalContaminationGuard(),
        dlp_classifier=DLPClassifier(),
        secret_leak_guard=SecretLeakGuard(),
        social_engineering_guard=SocialEngineeringGuard(),
        require_contract=False,
        default_approval_ttl_seconds=settings.sqlite_approval_ttl_seconds,
    )
    register_builtin_tools(tool_registry, settings)
    if extra_tools:
        for spec in extra_tools:
            tool_registry.register(spec)

    runtime = AgentRuntime(
        tool_registry=tool_registry,
        telemetry=telemetry,
        lifecycle_manager=lifecycle_manager,
        run_cancel_registry=run_cancel_registry,
    )
    policy_release_manager: Optional[PolicyReleaseManager] = None
    if settings.policy_release_hmac_secret:
        policy_release_manager = PolicyReleaseManager(
            HMACPolicySigner(settings.policy_release_hmac_secret),
            audit_sink,
        )
    api = ControlPlaneAPI(
        runtime=runtime,
        approval_service=approval_service,
        audit_sink=audit_sink,
        scheduler=scheduler,
        telemetry=telemetry,
        policy_release_manager=policy_release_manager,
    )

    manifest_policy = RunManifestPolicy(
        expected_profile=settings.profile,
        expected_sha256=settings.expected_run_manifest_sha256,
        require_manifest=settings.require_run_manifest,
    )

    safety_sandbox = SafetySandbox()
    safety_sandbox.register_profile(
        "*",
        "skill_invoke",
        SandboxProfile(
            allowed_capabilities=frozenset(["skill.subprocess"]),
            max_wall_seconds=120.0,
            max_output_bytes=2_000_000,
        ),
    )
    register_skill_tools(tool_registry, settings, policy_engine, safety_sandbox)
    resource_manager = ResourceManager()
    ipc_bus = InprocBus()
    state_observer = StateObserver()
    artifact_store = ArtifactStore(persist_path=settings.data_dir / "artifacts.jsonl")
    emergence_guardrails = EmergenceGuardrails(
        limits={
            "workflow.node_completed": GuardrailLimit(
                warn_threshold=settings.emergence_node_warn,
                hard_limit=settings.emergence_node_hard,
            ),
            "channel.outbound": GuardrailLimit(
                warn_threshold=settings.emergence_outbound_warn,
                hard_limit=settings.emergence_outbound_hard,
                window_seconds=60.0,
            ),
        },
    )

    task_graph_supervisor = TaskGraphSupervisor()
    handlers = stub_handlers()
    plugin_rt = build_plugin_runtime(
        settings,
        handlers=handlers.to_handler_map(),
        audit_sink=audit_sink,
        artifact_store=artifact_store,
        guardrails=emergence_guardrails,
        task_graph=task_graph_supervisor,
        run_cancel_registry=run_cancel_registry,
    )
    memory_service = MemoryService(
        backend=plugin_rt.memory_backend, audit_sink=audit_sink
    )
    deep_research_pipeline = DeepResearchPipeline(
        handlers,
        artifact_store=artifact_store,
        guardrails=emergence_guardrails,
        audit_sink=audit_sink,
        task_graph=task_graph_supervisor,
        orchestration_engine=plugin_rt.orchestration_engine,
        evolution_plugin=plugin_rt.evolution_plugin,
        run_cancel_registry=run_cancel_registry,
    )

    event_ingestor = EventIngestor()
    trigger_planner = TriggerPlanner(
        rules=[
            TriggerRule(
                topic="memory.fresh_item",
                action="background.notify_memory_update",
                risk="low",
                description="New memory item available; surface to operator.",
            ),
            TriggerRule(
                topic="approval.expired",
                action="background.notify_approval_expired",
                risk="low",
                description="Pending approval expired; notify owner.",
            ),
            TriggerRule(
                topic="channel.delivery_failed",
                action="background.escalate_channel_failure",
                risk="high",
                description="Outbound delivery failure; escalate for review.",
            ),
        ]
    )
    memory_consolidator = MemoryConsolidator(memory_service=memory_service)

    state_observer.register_probe(
        "policy",
        lambda: ProbeReport(
            name="policy",
            status=HealthStatus.HEALTHY,
            detail={"rules": len(getattr(policy_engine, "policy", []))},
        ),
    )
    state_observer.register_probe(
        "audit",
        lambda: ProbeReport(
            name="audit", status=HealthStatus.HEALTHY, detail={"sink": type(audit_sink).__name__}
        ),
    )
    state_observer.register_probe(
        "ipc_bus",
        lambda: ProbeReport(
            name="ipc_bus",
            status=HealthStatus.HEALTHY,
            detail=dict(ipc_bus.counters()),
        ),
    )
    state_observer.register_probe(
        "plugins",
        lambda: ProbeReport(
            name="plugins",
            status=HealthStatus.HEALTHY,
            detail=plugin_rt.fingerprint,
        ),
    )

    null_channel: ChannelAdapter = NullChannelAdapter()
    outbound_orchestrator = OutboundOrchestrator(
        adapters={null_channel.name: null_channel},
        audit_sink=audit_sink,
        telemetry=telemetry,
        rate_limit=RateLimit(
            max_per_window=settings.outbound_rate_limit_per_minute,
            window_seconds=60.0,
        ),
        dlp_classifier=DLPClassifier(),
        secret_leak_guard=SecretLeakGuard(),
        social_engineering_guard=SocialEngineeringGuard(),
        emergence_guardrails=emergence_guardrails,
    )
    notify_bridge = NotifyBridge(orchestrator=outbound_orchestrator)

    background_daemon = None
    if settings.background_enabled:
        from agentium.background.background_daemon import BackgroundDaemon

        def _consolidation_ctx() -> RequestContext:
            return RequestContext(
                request_id="_background_consolidation",
                run_id="_background_consolidation",
                tenant_id="_background",
                user_id="_background",
                trace_id="_background_trace",
            )

        background_daemon = BackgroundDaemon(
            approval_service=approval_service,
            audit_sink=audit_sink,
            policy_engine=policy_engine,
            interval_seconds=settings.background_interval_seconds,
            event_ingestor=event_ingestor,
            trigger_planner=trigger_planner,
            memory_consolidator=memory_consolidator,
            notify_bridge=notify_bridge,
            consolidation_context_factory=_consolidation_ctx,
            noise_rps_pause=(
                settings.background_noise_rps_pause
                if settings.background_noise_rps_pause > 0
                else None
            ),
        )

    container = RuntimeContainer(
        settings=settings,
        api=api,
        runtime=runtime,
        approval_service=approval_service,
        audit_sink=audit_sink,
        budget_service=budget_service,
        manifest_policy=manifest_policy,
        telemetry=telemetry,
        tool_registry=tool_registry,
        state_observer=state_observer,
        safety_sandbox=safety_sandbox,
        resource_manager=resource_manager,
        ipc_bus=ipc_bus,
        artifact_store=artifact_store,
        emergence_guardrails=emergence_guardrails,
        outbound_orchestrator=outbound_orchestrator,
        notify_bridge=notify_bridge,
        memory_service=memory_service,
        event_ingestor=event_ingestor,
        trigger_planner=trigger_planner,
        memory_consolidator=memory_consolidator,
        scheduler=scheduler,
        lifecycle_manager=lifecycle_manager,
        resource_controller=resource_controller,
        background_daemon=background_daemon,
        task_graph_supervisor=task_graph_supervisor,
        deep_research_pipeline=deep_research_pipeline,
        proposal_queue=plugin_rt.proposal_queue,
        evolution_plugin=plugin_rt.evolution_plugin,
        plugins_fingerprint=plugin_rt.fingerprint,
        run_message_store=run_message_store,
        chat_session_store=chat_session_store,
        chat_turn_service=chat_turn_service,
        session_checkpoint_store=session_checkpoint_store,
        eval_run_store=eval_run_store,
        run_cancel_registry=run_cancel_registry,
    )
    container._shutdown_callbacks = (
        approval_callbacks + audit_callbacks + store_callbacks
    )
    return container
