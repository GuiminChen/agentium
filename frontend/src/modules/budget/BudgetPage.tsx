import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useProfileStore } from "../../core/profile/profileStore";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";

export function BudgetPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("budget.read");
  const tenantId = useProfileStore((s) => s.profile?.tenant_id ?? "");
  const q = useQuery({
    queryKey: ["budget-summary", tenantId],
    queryFn: async () => {
      const r = await apiFetch(
        `/v1/budget/tenant/${encodeURIComponent(tenantId)}/summary`,
        { method: "GET" },
      );
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json();
    },
    enabled: Boolean(tenantId) && can,
  });

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">Budget</h1>
      <CapabilityGuard need="budget.read" label="Budget">
        {!tenantId ? <div className="text-sm text-slate-600">Load profile first.</div> : null}
        {q.isLoading ? <div>{t("common.loading")}</div> : null}
        {q.isError ? (
          <div className="text-sm text-red-700">
            {q.error instanceof Error ? q.error.message : String(q.error)}
          </div>
        ) : null}
        {q.data ? (
          <pre className="rounded border bg-slate-50 p-2 text-xs">{JSON.stringify(q.data, null, 2)}</pre>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
