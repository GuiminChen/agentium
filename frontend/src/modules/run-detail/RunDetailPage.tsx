import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { auditEventCorrelationIssues } from "../../core/contract/contractGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { expandObservabilityUrl } from "../../core/http/expandObservabilityUrl";
import { readApiError } from "../../core/http/readApiError";

export function RunDetailPage(): React.ReactElement {
  const { runId = "" } = useParams();
  const { t } = useTranslation();
  const decoded = decodeURIComponent(runId);
  const canObs = useHasCapability("observability.read");

  const timeline = useQuery({
    queryKey: ["run-timeline", decoded],
    queryFn: async () => {
      const r = await apiFetch(
        `/v1/runs/${encodeURIComponent(decoded)}/timeline?limit=500`,
        { method: "GET" },
      );
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as { events: Record<string, unknown>[] };
    },
    enabled: Boolean(decoded),
  });

  const uiLinks = useQuery({
    queryKey: ["run-ui-links"],
    queryFn: async () => {
      const r = await apiFetch("/v1/config/ui-links", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as { links: Record<string, string> };
    },
    enabled: canObs,
  });

  const derivedTraceId = React.useMemo(() => {
    const ev = timeline.data?.events ?? [];
    for (const row of ev) {
      const pl = row.payload;
      if (typeof pl === "object" && pl !== null && !Array.isArray(pl)) {
        const tid = (pl as Record<string, unknown>).trace_id;
        if (typeof tid === "string" && tid) {
          return tid;
        }
      }
    }
    return "";
  }, [timeline.data]);

  const issues = React.useMemo(() => {
    const ev = timeline.data?.events ?? [];
    return auditEventCorrelationIssues(ev, {
      requirePayloadKeys: ["trace_id"],
    });
  }, [timeline.data]);

  const linkVars = React.useMemo(
    () => ({ run_id: decoded, trace_id: derivedTraceId }),
    [decoded, derivedTraceId],
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold text-slate-800">Run: {decoded || "—"}</h1>
        {decoded ? (
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50"
            onClick={() => void navigator.clipboard.writeText(decoded)}
            aria-label={t("runDetail.copyRunId")}
          >
            {t("common.copy")} run_id
          </button>
        ) : null}
        {derivedTraceId ? (
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50"
            onClick={() => void navigator.clipboard.writeText(derivedTraceId)}
            aria-label={t("runDetail.copyTraceId")}
          >
            {t("common.copy")} trace_id
          </button>
        ) : null}
      </div>
      {timeline.data && timeline.data.events.length > 0 && !derivedTraceId ? (
        <div
          role="alert"
          className="rounded border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900"
        >
          <div className="font-medium">{t("contract.ob03NoTraceTitle")}</div>
          <p className="mt-1 text-xs">{t("contract.ob03NoTraceBody")}</p>
        </div>
      ) : null}
      {canObs && uiLinks.data?.links && Object.keys(uiLinks.data.links).length > 0 ? (
        <div className="flex flex-wrap gap-2 rounded border border-slate-200 bg-slate-50 px-2 py-2 text-sm">
          <span className="font-medium text-slate-700">{t("runDetail.observability")}:</span>
          {Object.entries(uiLinks.data.links).map(([k, url]) => {
            const href = expandObservabilityUrl(url, linkVars);
            return (
              <a
                key={k}
                className="rounded bg-white px-2 py-0.5 text-xs text-blue-700 shadow-sm hover:underline"
                href={href}
                rel="noreferrer"
                target="_blank"
              >
                {k}
              </a>
            );
          })}
        </div>
      ) : null}
      {issues.length > 0 ? (
        <div
          role="alert"
          className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <div className="font-medium">{t("contract.auditPayloadTitle")}</div>
          <p className="mt-1 text-xs">{t("contract.auditPayloadDetail")}</p>
          <ul className="mt-2 list-inside list-disc text-xs">
            {issues.slice(0, 8).map((x) => (
              <li key={x.path}>
                {x.path}: {x.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {timeline.isLoading ? <div>{t("common.loading")}</div> : null}
      {timeline.isError ? (
        <div className="text-sm text-red-700">
          {timeline.error instanceof Error ? timeline.error.message : String(timeline.error)}
        </div>
      ) : null}
      {timeline.data ? (
        <div className="space-y-2">
          <ol aria-label={t("runDetail.timelineLabel")} className="list-decimal space-y-2 pl-5 text-sm">
            {timeline.data.events.map((ev, idx) => {
              const pl = ev.payload as Record<string, unknown> | undefined;
              const toolUse =
                pl && typeof pl.tool_use_id === "string" ? pl.tool_use_id : null;
              return (
                <li key={idx} className="rounded border border-slate-200 bg-white px-2 py-1.5">
                  <div className="font-mono text-xs text-slate-700">
                    {String(ev.event_type ?? "—")} · {String(ev.run_id ?? "")}
                  </div>
                  {toolUse ? (
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                      <span className="text-slate-600">{t("runDetail.toolUseId")}</span>
                      <code className="rounded bg-slate-100 px-1 py-0.5 font-mono">{toolUse}</code>
                      <button
                        type="button"
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-slate-700 hover:bg-slate-50"
                        onClick={() => void navigator.clipboard.writeText(toolUse)}
                      >
                        {t("common.copy")}
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
          <pre
            aria-label={t("runDetail.rawTimeline")}
            className="max-h-48 overflow-auto rounded border border-slate-200 bg-slate-50 p-2 text-xs"
          >
            {JSON.stringify(timeline.data.events, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
