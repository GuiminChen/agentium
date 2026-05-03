import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";

export function ConnectorsPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("connectors.read");
  const q = useQuery({
    queryKey: ["connectors-notify"],
    queryFn: async () => {
      const r = await apiFetch("/v1/connectors/notify", { method: "GET" });
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json();
    },
    enabled: can,
  });

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.connectors")}</h1>
      <CapabilityGuard need="connectors.read" label="Connectors">
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
