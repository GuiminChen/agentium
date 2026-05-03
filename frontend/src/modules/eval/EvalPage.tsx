import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

type GateResultRow = {
  name?: unknown;
  passed?: unknown;
  duration_ms?: unknown;
  detail?: unknown;
  error?: unknown;
};

type EvalGatesResponse = {
  passed?: unknown;
  results?: unknown;
  started_at?: unknown;
  finished_at?: unknown;
};

function isGateRow(x: unknown): x is GateResultRow {
  return typeof x === "object" && x !== null;
}

export function EvalPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("eval.run");
  const run = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/v1/eval/gates", {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json() as Promise<EvalGatesResponse>;
    },
  });

  const rows = React.useMemo(() => {
    const raw = run.data?.results;
    if (!Array.isArray(raw)) {
      return [] as GateResultRow[];
    }
    return raw.filter(isGateRow);
  }, [run.data]);

  const overallPass = run.data?.passed === true;

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.eval")}</h1>
      <CapabilityGuard need="eval.run" label="Eval gates">
        <button
          type="button"
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled={!can || run.isPending}
          onClick={() => void run.mutate()}
        >
          {t("evalGates.runButton")}
        </button>
        {run.isError ? (
          <div className="text-sm text-red-700" role="alert">
            {run.error instanceof Error ? run.error.message : String(run.error)}
          </div>
        ) : null}
        {run.data ? (
          <div className="space-y-2">
            <div
              className={
                overallPass
                  ? "rounded border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900"
                  : "rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900"
              }
              role="status"
            >
              <span className="font-semibold">{overallPass ? t("evalGates.overallPass") : t("evalGates.overallFail")}</span>
              <span className="ml-2 text-xs opacity-80">
                {String(run.data.started_at ?? "")} → {String(run.data.finished_at ?? "")}
              </span>
            </div>
            <div className="overflow-x-auto rounded border border-slate-200">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-slate-100 text-xs text-slate-600">
                  <tr>
                    <th scope="col" className="px-2 py-1.5 font-medium">
                      {t("evalGates.gateName")}
                    </th>
                    <th scope="col" className="px-2 py-1.5 font-medium">
                      {t("evalGates.duration")}
                    </th>
                    <th scope="col" className="px-2 py-1.5 font-medium">
                      {t("evalGates.colResult")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((gr, idx) => {
                    const name = typeof gr.name === "string" ? gr.name : `gate-${idx}`;
                    const ok = gr.passed === true;
                    const dur = typeof gr.duration_ms === "number" ? gr.duration_ms : gr.duration_ms;
                    const err = typeof gr.error === "string" ? gr.error : "";
                    const detail = gr.detail;
                    const hasExtra = Boolean(err) || (detail !== undefined && detail !== null && detail !== "");
                    return (
                      <tr key={name} className="border-t border-slate-100">
                        <td className="px-2 py-1.5 font-mono text-xs">{name}</td>
                        <td className="px-2 py-1.5 font-mono text-xs">{String(dur ?? "—")}</td>
                        <td className="px-2 py-1.5">
                          <span
                            className={
                              ok
                                ? "rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-900"
                                : "rounded bg-red-100 px-2 py-0.5 text-xs text-red-900"
                            }
                          >
                            {ok ? t("evalGates.passed") : t("evalGates.failed")}
                          </span>
                          {hasExtra ? (
                            <details className="mt-1 text-xs">
                              <summary className="cursor-pointer text-slate-600">{t("evalGates.expandDetail")}</summary>
                              {err ? <pre className="mt-1 whitespace-pre-wrap text-red-800">{err}</pre> : null}
                              {detail !== undefined && detail !== null && detail !== "" ? (
                                <pre className="mt-1 max-h-40 overflow-auto rounded bg-slate-50 p-1 text-[11px]">
                                  {typeof detail === "string" ? detail : JSON.stringify(detail, null, 2)}
                                </pre>
                              ) : !err ? (
                                <p className="text-slate-500">{t("evalGates.noDetail")}</p>
                              ) : null}
                            </details>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <details className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
              <summary className="cursor-pointer font-medium text-slate-700">Raw JSON</summary>
              <pre className="mt-2 max-h-[240px] overflow-auto">{JSON.stringify(run.data, null, 2)}</pre>
            </details>
          </div>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
