import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

function humanizeStatus(data: unknown): string[] {
  if (typeof data !== "object" || data === null) {
    return [];
  }
  const o = data as Record<string, unknown>;
  const lines: string[] = [];
  for (const key of ["status", "state", "running", "paused", "message", "detail"]) {
    if (key in o && o[key] !== undefined && o[key] !== null && o[key] !== "") {
      lines.push(`${key}: ${JSON.stringify(o[key])}`);
    }
  }
  return lines.slice(0, 6);
}

export function BackgroundPage(): React.ReactElement {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const canRead = useHasCapability("background.read");
  const canCtl = useHasCapability("background.control");
  const status = useQuery({
    queryKey: ["background-status"],
    queryFn: async () => {
      const r = await apiFetch("/v1/background/status", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    enabled: canRead,
  });

  const pause = useMutation({
    mutationFn: async () =>
      apiFetch("/v1/background/pause", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["background-status"] }),
  });
  const resume = useMutation({
    mutationFn: async () =>
      apiFetch("/v1/background/resume", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["background-status"] }),
  });
  const stop = useMutation({
    mutationFn: async () =>
      apiFetch("/v1/background/stop", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["background-status"] }),
  });

  const hints = React.useMemo(() => humanizeStatus(status.data ?? null), [status.data]);

  function requestStop(): void {
    if (typeof window !== "undefined" && !window.confirm(t("background.stopConfirm"))) {
      return;
    }
    void stop.mutate();
  }

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.background")}</h1>
      <CapabilityGuard need="background.read" label="Background status">
        {status.isLoading ? <div>{t("common.loading")}</div> : null}
        {hints.length > 0 ? (
          <ul className="mb-2 list-inside list-disc rounded border border-slate-200 bg-white p-2 text-xs text-slate-700">
            {hints.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs text-slate-500">{t("background.statusHint")}</p>
        {status.data ? (
          <pre className="rounded border border-slate-200 bg-slate-50 p-2 text-xs">
            {JSON.stringify(status.data, null, 2)}
          </pre>
        ) : null}
      </CapabilityGuard>
      <CapabilityGuard need="background.control" label="Background control">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={!canCtl || pause.isPending}
            onClick={() => void pause.mutate()}
            aria-label={t("background.pause")}
          >
            {t("background.pause")}
          </button>
          <button
            type="button"
            className="rounded bg-slate-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={!canCtl || resume.isPending}
            onClick={() => void resume.mutate()}
            aria-label={t("background.resume")}
          >
            {t("background.resume")}
          </button>
          <button
            type="button"
            className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={!canCtl || stop.isPending}
            onClick={requestStop}
            aria-label={t("background.stop")}
          >
            {t("background.stop")}
          </button>
        </div>
      </CapabilityGuard>
    </div>
  );
}
