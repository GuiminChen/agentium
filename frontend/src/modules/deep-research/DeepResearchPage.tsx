import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

type WorkflowSnap = {
  workflow_name?: unknown;
  run_id?: unknown;
  pending_node?: unknown;
  spec_nodes?: Array<{ name?: string; depends_on?: string[] }>;
  completed_nodes?: Array<{ node?: string; status?: string; error?: string }>;
};

function WorkflowProgress({ snap }: { snap: WorkflowSnap | null }): React.ReactElement | null {
  const { t } = useTranslation();
  if (!snap) {
    return null;
  }
  const spec = snap.spec_nodes ?? [];
  const completed = snap.completed_nodes ?? [];
  const done = new Set(
    completed.map((c) => (typeof c.node === "string" ? c.node : "")).filter(Boolean),
  );
  const pending =
    typeof snap.pending_node === "string" ? snap.pending_node : String(snap.pending_node ?? "—");

  return (
    <div className="rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="font-medium text-slate-800">{t("deepResearch.workflowTitle")}</div>
      <p className="mt-1 text-xs text-slate-600">
        {String(snap.workflow_name ?? "")} · {String(snap.run_id ?? "")}
      </p>
      <p className="mt-2 text-xs">
        <span className="font-medium text-slate-700">{t("deepResearch.pendingNode")}:</span>{" "}
        <code className="rounded bg-slate-100 px-1">{pending}</code>
      </p>
      <p className="mt-2 text-xs font-medium text-slate-700">{t("deepResearch.specProgress")}</p>
      <ul className="mt-1 max-h-48 space-y-1 overflow-auto text-xs">
        {spec.map((n) => {
          const name = typeof n.name === "string" ? n.name : "—";
          const ok = done.has(name);
          const row = completed.find((c) => c.node === name);
          return (
            <li
              key={name}
              className={`flex items-center justify-between rounded border px-2 py-1 ${
                ok ? "border-emerald-200 bg-emerald-50/60" : "border-slate-100 bg-slate-50"
              }`}
            >
              <span className="font-mono">{name}</span>
              <span className="text-slate-600">
                {ok ? (row?.status ?? "done") : "pending"}
                {row?.error ? ` — ${row.error}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function asWorkflowSnap(x: unknown): WorkflowSnap | null {
  if (typeof x !== "object" || x === null) {
    return null;
  }
  return x as WorkflowSnap;
}

export function DeepResearchPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("research.run");
  const [query, setQuery] = React.useState("stub topic");
  const [runId, setRunId] = React.useState(() => `run-${Date.now()}`);
  const [reqId, setReqId] = React.useState("req-dr-1");
  const [traceId, setTraceId] = React.useState("trace-dr-1");

  const poll = useQuery({
    queryKey: ["research-get", runId],
    queryFn: async () => {
      const r = await apiFetch(`/v1/research/${encodeURIComponent(runId)}`, { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    enabled: false,
  });

  const run = useMutation({
    mutationFn: async () => {
      const r = await apiFetch("/v1/research/run", {
        method: "POST",
        body: JSON.stringify({
          query,
          run_id: runId,
          request_id: reqId,
          trace_id: traceId,
          deployment_mode: "prod",
        }),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as Record<string, unknown>;
    },
    onSuccess: () => void poll.refetch(),
  });

  const workflowSnap = React.useMemo(() => {
    const fromPoll = asWorkflowSnap(poll.data ?? null);
    if (fromPoll?.spec_nodes?.length) {
      return fromPoll;
    }
    const w = run.data?.workflow;
    return asWorkflowSnap(w ?? null) ?? fromPoll;
  }, [poll.data, run.data]);

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.deepResearch")}</h1>
      <CapabilityGuard need="research.run" label="Deep research">
        <div className="grid max-w-xl gap-2 text-sm">
          <label>
            query
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <label>
            run_id
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            />
          </label>
          <label>
            request_id
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={reqId}
              onChange={(e) => setReqId(e.target.value)}
            />
          </label>
          <label>
            trace_id
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="w-fit rounded bg-blue-700 px-3 py-1.5 text-white disabled:opacity-50"
            disabled={!can || run.isPending}
            onClick={() => void run.mutate()}
          >
            {t("deepResearch.postRun")}
          </button>
        </div>
        {run.isError ? (
          <div className="text-sm text-red-700">
            {run.error instanceof Error ? run.error.message : String(run.error)}
          </div>
        ) : null}
        {run.data ? (
          <pre className="max-h-48 overflow-auto rounded border bg-slate-50 p-2 text-xs">
            {JSON.stringify(run.data, null, 2)}
          </pre>
        ) : null}
        <WorkflowProgress snap={workflowSnap} />
        <button
          type="button"
          className="mt-2 text-sm text-blue-700 underline"
          onClick={() => void poll.refetch()}
        >
          {t("deepResearch.reloadSnapshot")}
        </button>
        {poll.isError ? (
          <div className="text-sm text-red-700">
            {poll.error instanceof Error ? poll.error.message : String(poll.error)}
          </div>
        ) : null}
        {poll.data && !workflowSnap?.spec_nodes?.length ? (
          <pre className="max-h-48 overflow-auto rounded border bg-slate-50 p-2 text-xs">
            {JSON.stringify(poll.data, null, 2)}
          </pre>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
