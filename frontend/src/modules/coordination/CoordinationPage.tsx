import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

type ArtifactRow = {
  artifact_id?: unknown;
  workflow?: unknown;
  node?: unknown;
  run_id?: unknown;
};

export function CoordinationPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("artifacts.read");
  const [params] = useSearchParams();
  const [runId, setRunId] = React.useState("");

  React.useEffect(() => {
    const pre = params.get("run_id");
    if (pre) {
      setRunId(pre);
    }
  }, [params]);

  const q = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: async () => {
      const r = await apiFetch(`/v1/runs/${encodeURIComponent(runId)}/artifacts`, {
        method: "GET",
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json() as { artifacts?: ArtifactRow[]; count?: number };
    },
    enabled: Boolean(runId) && can,
  });

  const artifacts = q.data?.artifacts ?? [];

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.coordination")}</h1>
      <CapabilityGuard need="artifacts.read" label="Artifacts">
        <label className="block text-sm">
          run_id
          <input
            className="mt-1 max-w-md rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            aria-label="run id"
          />
        </label>
        {q.isLoading ? <div>{t("common.loading")}</div> : null}
        {q.isError ? (
          <div className="text-sm text-red-700">
            {q.error instanceof Error ? q.error.message : String(q.error)}
          </div>
        ) : null}
        {q.data ? (
          <div className="space-y-2">
            <p className="text-xs text-slate-600">
              {t("coordination.artifactsTitle")}: {q.data.count ?? artifacts.length}
            </p>
            <div className="grid gap-2 md:grid-cols-2">
              {artifacts.map((a, idx) => {
                const aid = typeof a.artifact_id === "string" ? a.artifact_id : `artifact-${idx}`;
                const wf = typeof a.workflow === "string" ? a.workflow : "—";
                const node = typeof a.node === "string" ? a.node : "—";
                return (
                  <div
                    key={`${aid}-${idx}`}
                    className="rounded border border-slate-200 bg-white p-3 text-sm shadow-sm"
                  >
                    <div className="font-mono text-xs font-semibold text-slate-800">{aid}</div>
                    <div className="mt-1 text-xs text-slate-600">
                      workflow: {wf}
                      <br />
                      node: {node}
                    </div>
                    <button
                      type="button"
                      className="mt-2 rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                      onClick={() => void navigator.clipboard.writeText(aid)}
                      aria-label={t("coordination.copyId")}
                    >
                      {t("common.copy")} artifact_id
                    </button>
                  </div>
                );
              })}
            </div>
            {runId ? (
              <Link className="text-xs text-blue-700 hover:underline" to={`/runs/${encodeURIComponent(runId)}`}>
                → Run detail
              </Link>
            ) : null}
            <details className="rounded border border-slate-100 bg-slate-50 p-2 text-xs">
              <summary className="cursor-pointer">Raw JSON</summary>
              <pre className="mt-2 max-h-48 overflow-auto">{JSON.stringify(q.data, null, 2)}</pre>
            </details>
          </div>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
