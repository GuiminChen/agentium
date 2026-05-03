import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";
import { useProfileStore } from "../../core/profile/profileStore";

export function OverviewPage(): React.ReactElement {
  const { t } = useTranslation();
  const canRuns = useHasCapability("runs.read");
  const canAppr = useHasCapability("approval.read");
  const canBudget = useHasCapability("budget.read");
  const tenantId = useProfileStore((s) => s.profile?.tenant_id ?? "");

  const health = useQuery({
    queryKey: ["overview-healthz"],
    queryFn: async () => {
      const r = await apiFetch("/v1/healthz", { method: "GET" });
      const txt = await r.text();
      return { status: r.status, body: txt };
    },
  });
  const ready = useQuery({
    queryKey: ["overview-readyz"],
    queryFn: async () => {
      const r = await apiFetch("/v1/readyz", { method: "GET" });
      const txt = await r.text();
      return { status: r.status, body: txt };
    },
  });
  const version = useQuery({
    queryKey: ["overview-version"],
    queryFn: async () => {
      const r = await apiFetch("/v1/version", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json() as { git_sha?: string; version?: string };
    },
  });

  const pending = useQuery({
    queryKey: ["overview-pending-approvals"],
    queryFn: async () => {
      const r = await apiFetch("/v1/approvals?status=pending&limit=500", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as { count: number };
    },
    enabled: canAppr,
  });

  const recent = useQuery({
    queryKey: ["overview-recent-runs"],
    queryFn: async () => {
      const r = await apiFetch("/v1/runs/recent?limit=30", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as {
        runs: { run_id: string; last_event_type: string; last_ts: string }[];
      };
    },
    enabled: canRuns,
  });

  const budget = useQuery({
    queryKey: ["overview-budget", tenantId],
    queryFn: async () => {
      const r = await apiFetch(
        `/v1/budget/tenant/${encodeURIComponent(tenantId)}/summary`,
        { method: "GET" },
      );
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as Record<string, unknown>;
    },
    enabled: canBudget && Boolean(tenantId),
  });

  const failedRuns = React.useMemo(() => {
    const runs = recent.data?.runs ?? [];
    return runs.filter((row) => {
      const et = row.last_event_type.toLowerCase();
      return et.includes("fail") || et.includes("blocked") || et.includes("error");
    });
  }, [recent.data]);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.overview")}</h1>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded border border-slate-200 p-3 text-sm">
          <div className="font-medium text-slate-700">{t("overview.healthCard")} · GET /v1/healthz</div>
          <pre className="mt-2 max-h-48 overflow-auto text-xs">
            {health.isLoading
              ? "…"
              : health.isError
                ? String(health.error)
                : JSON.stringify(health.data, null, 2)}
          </pre>
        </div>
        <div className="rounded border border-slate-200 p-3 text-sm">
          <div className="font-medium text-slate-700">{t("overview.readyCard")} · GET /v1/readyz</div>
          <pre className="mt-2 max-h-48 overflow-auto text-xs">
            {ready.isLoading
              ? "…"
              : ready.isError
                ? String(ready.error)
                : JSON.stringify(ready.data, null, 2)}
          </pre>
        </div>
      </div>
      <div className="rounded border border-slate-200 p-3 text-sm">
        <div className="font-medium text-slate-700">{t("overview.versionCard")}</div>
        <pre className="mt-2 text-xs">
          {version.isLoading ? "…" : JSON.stringify(version.data, null, 2)}
        </pre>
      </div>
      {canAppr ? (
        <div className="rounded border border-amber-200 bg-amber-50/50 p-3 text-sm">
          <div className="font-medium text-amber-900">{t("overview.pendingTitle")}</div>
          {pending.isLoading ? <div>{t("common.loading")}</div> : null}
          {pending.data ? (
            <div className="mt-1 text-2xl font-semibold text-amber-900">{pending.data.count}</div>
          ) : null}
          <Link className="text-xs text-blue-700 hover:underline" to="/approval">
            Open approval hub →
          </Link>
        </div>
      ) : null}
      {canBudget ? (
        <div className="rounded border border-slate-200 bg-slate-50/80 p-3 text-sm">
          <div className="font-medium text-slate-800">{t("overview.budgetTitle")}</div>
          {!tenantId ? (
            <p className="mt-1 text-xs text-slate-600">Load profile (/settings) for tenant_id.</p>
          ) : null}
          {budget.isLoading && tenantId ? <div>{t("common.loading")}</div> : null}
          {budget.isError ? (
            <div className="text-xs text-red-700">
              {budget.error instanceof Error ? budget.error.message : String(budget.error)}
            </div>
          ) : null}
          {budget.data ? (
            <pre className="mt-2 max-h-36 overflow-auto rounded border border-slate-200 bg-white p-2 text-xs">
              {JSON.stringify(budget.data, null, 2)}
            </pre>
          ) : null}
          <Link className="mt-2 inline-block text-xs text-blue-700 hover:underline" to="/budget">
            {t("overview.openBudget")}
          </Link>
        </div>
      ) : null}
      {canRuns ? (
        <div className="rounded border border-red-200 bg-red-50/40 p-3 text-sm">
          <div className="font-medium text-red-900">{t("overview.failedRunsTitle")}</div>
          <p className="text-xs text-slate-600">{t("overview.failedRunsHint")}</p>
          {recent.isLoading ? <div>{t("common.loading")}</div> : null}
          <ul className="mt-2 max-h-40 overflow-auto text-xs">
            {failedRuns.map((r) => (
              <li key={r.run_id}>
                <Link className="text-blue-700 hover:underline" to={`/runs/${encodeURIComponent(r.run_id)}`}>
                  {r.run_id}
                </Link>{" "}
                <span className="text-slate-600">({r.last_event_type})</span>
              </li>
            ))}
            {failedRuns.length === 0 ? <li className="text-slate-500">None in recent window.</li> : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
