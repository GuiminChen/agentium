import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { auditEventCorrelationIssues } from "../../core/contract/contractGuard";
import { AuditEventTable } from "../../core/ui/AuditEventTable";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";
import { EffectivePolicyPanel } from "../governance/EffectivePolicyPanel";

export function DecisionChainPage(): React.ReactElement {
  const { t } = useTranslation();
  const [eventType, setEventType] = React.useState("policy_decision");
  const [runId, setRunId] = React.useState("");
  const [tenantId, setTenantId] = React.useState("");
  const can = useHasCapability("audit.read");
  const q = useQuery({
    queryKey: ["audit-decision-chain", eventType, runId, tenantId],
    queryFn: async () => {
      const qp = new URLSearchParams({ limit: "200" });
      qp.set("event_type", eventType);
      if (runId.trim()) {
        qp.set("run_id", runId.trim());
      }
      if (tenantId.trim()) {
        qp.set("tenant_id", tenantId.trim());
      }
      const r = await apiFetch(`/v1/audit/events?${qp.toString()}`, { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as { events: unknown[]; count?: number };
    },
    enabled: can,
  });

  const payloadIssues = React.useMemo(
    () =>
      auditEventCorrelationIssues(q.data?.events ?? [], {
        requirePayloadKeys: ["trace_id"],
      }),
    [q.data],
  );

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.decisionChain")}</h1>
      <CapabilityGuard need="audit.read" label="Decision chain">
        <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
          <EffectivePolicyPanel className="rounded border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 shadow-sm" />
          <div className="min-w-0 space-y-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-800">{t("policyChain.timelineTitle")}</h2>
              <p className="text-xs text-slate-500">{t("policyChain.timelineHint")}</p>
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <label className="block text-sm">
                {t("decisionChain.eventType")}
                <input
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
                  value={eventType}
                  onChange={(e) => setEventType(e.target.value)}
                  aria-label={t("decisionChain.eventType")}
                />
              </label>
              <label className="block text-sm">
                {t("decisionChain.runId")}
                <input
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                  value={runId}
                  onChange={(e) => setRunId(e.target.value)}
                  aria-label={t("decisionChain.runId")}
                />
              </label>
              <label className="block text-sm">
                {t("decisionChain.tenantId")}
                <input
                  className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                  value={tenantId}
                  onChange={(e) => setTenantId(e.target.value)}
                  aria-label={t("decisionChain.tenantId")}
                />
              </label>
            </div>
            {q.isLoading ? <div>{t("common.loading")}</div> : null}
            {q.isError ? (
              <div className="text-sm text-red-700">
                {q.error instanceof Error ? q.error.message : String(q.error)}
              </div>
            ) : null}
            {payloadIssues.length > 0 ? (
              <div
                role="alert"
                className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
              >
                <div className="font-medium">{t("contract.auditPayloadTitle")}</div>
                <p className="mt-1">{t("contract.auditPayloadDetail")}</p>
                <ul className="mt-1 list-inside list-disc">
                  {payloadIssues.slice(0, 6).map((x) => (
                    <li key={x.path}>
                      {x.path}: {x.message}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {q.data ? (
              <div className="space-y-2">
                <p className="text-xs text-slate-500">{q.data.count ?? q.data.events?.length ?? 0} events</p>
                <AuditEventTable events={q.data.events ?? []} />
              </div>
            ) : null}
          </div>
        </div>
      </CapabilityGuard>
    </div>
  );
}
