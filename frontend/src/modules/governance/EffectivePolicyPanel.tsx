import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

export type EffectivePolicyResponse = {
  effective: Record<string, unknown>;
};

/** Read-only snapshot from GET /v1/policy/effective (governance.policy.read). */
export function EffectivePolicyPanel(props: {
  className?: string;
}): React.ReactElement {
  const { className } = props;
  const { t } = useTranslation();
  const can = useHasCapability("governance.policy.read");
  const q = useQuery({
    queryKey: ["policy-effective"],
    queryFn: async () => {
      const r = await apiFetch("/v1/policy/effective", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as EffectivePolicyResponse;
    },
    enabled: can,
  });

  return (
    <CapabilityGuard need="governance.policy.read" label={t("policyChain.effectiveTitle")}>
      <section
        className={
          className ??
          "rounded border border-slate-200 bg-white px-3 py-2 text-xs text-slate-800 shadow-sm"
        }
      >
        <h2 className="text-sm font-semibold text-slate-800">{t("policyChain.effectiveTitle")}</h2>
        <p className="mt-1 text-xs text-slate-500">{t("policyChain.effectiveHint")}</p>
        {q.isLoading ? <div className="mt-2">{t("common.loading")}</div> : null}
        {q.isError ? (
          <div className="mt-2 text-red-700">
            {q.error instanceof Error ? q.error.message : String(q.error)}
          </div>
        ) : null}
        {q.data?.effective ? (
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-[11px] leading-snug">
            {JSON.stringify(q.data.effective, null, 2)}
          </pre>
        ) : null}
      </section>
    </CapabilityGuard>
  );
}
