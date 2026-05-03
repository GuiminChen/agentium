import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { auditEventCorrelationIssues } from "../../core/contract/contractGuard";
import { AuditEventTable } from "../../core/ui/AuditEventTable";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

export function SecurityCompliancePage(): React.ReactElement {
  const { t } = useTranslation();
  const [needle, setNeedle] = React.useState("dlp");
  const [runId, setRunId] = React.useState("");
  const [tenantId, setTenantId] = React.useState("");
  const [exportRunId, setExportRunId] = React.useState("");
  const [exportRedact, setExportRedact] = React.useState(true);
  const can = useHasCapability("security.events.read");
  const canExport = useHasCapability("export.audit.redacted");

  const q = useQuery({
    queryKey: ["audit-security", needle, runId, tenantId],
    queryFn: async () => {
      const qp = new URLSearchParams({ limit: "100" });
      if (needle.trim()) {
        qp.set("event_type", needle.trim());
      }
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

  const exportMut = useMutation({
    mutationFn: async () => {
      const rid = exportRunId.trim();
      if (!rid) {
        throw new Error("run_id required");
      }
      const qp = new URLSearchParams({ run_id: rid, redact: exportRedact ? "1" : "0" });
      const r = await apiFetch(`/v1/audit/export?${qp.toString()}`, { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.blob();
    },
    onSuccess: (blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `audit-export-${exportRunId.trim() || "run"}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    },
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
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.compliance")}</h1>
      <CapabilityGuard need="security.events.read" label="Compliance audit slice">
        <div className="grid gap-2 md:grid-cols-3">
          <label className="block text-sm">
            {t("compliancePage.eventType")}
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={needle}
              onChange={(e) => setNeedle(e.target.value)}
              aria-label={t("compliancePage.eventType")}
            />
          </label>
          <label className="block text-sm">
            {t("compliancePage.runId")}
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            />
          </label>
          <label className="block text-sm">
            {t("compliancePage.tenantId")}
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
            />
          </label>
        </div>
        <p className="text-xs text-slate-500">{t("compliancePage.hint")}</p>
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
      </CapabilityGuard>

      <div className="rounded border border-slate-200 bg-slate-50/80 p-3 text-sm">
        <h2 className="font-medium text-slate-800">{t("auditExport.title")}</h2>
        <CapabilityGuard need="export.audit.redacted" label="Audit export">
          <div className="mt-2 flex flex-wrap items-end gap-3">
            <label className="block text-sm">
              {t("auditExport.runId")}
              <input
                className="mt-1 w-56 rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                value={exportRunId}
                onChange={(e) => setExportRunId(e.target.value)}
                aria-required
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={exportRedact}
                onChange={(e) => setExportRedact(e.target.checked)}
              />
              {t("auditExport.redact")}
            </label>
            <button
              type="button"
              className="rounded bg-slate-800 px-3 py-1.5 text-xs text-white disabled:opacity-50"
              disabled={!canExport || exportMut.isPending || !exportRunId.trim()}
              onClick={() => void exportMut.mutate()}
            >
              {t("auditExport.download")}
            </button>
          </div>
          {exportMut.isError ? (
            <div className="mt-2 text-xs text-red-700" role="alert">
              {t("auditExport.error")}:{" "}
              {exportMut.error instanceof Error ? exportMut.error.message : String(exportMut.error)}
            </div>
          ) : null}
        </CapabilityGuard>
        {!canExport ? <p className="mt-1 text-xs text-slate-500">{t("auditExport.needCap")}</p> : null}
      </div>
    </div>
  );
}
