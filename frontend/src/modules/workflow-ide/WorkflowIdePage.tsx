import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

function extractSpecNodes(
  data: unknown,
): { name: string; depends_on: string[] }[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const d = data as Record<string, unknown>;
  const raw = d.spec_nodes;
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: { name: string; depends_on: string[] }[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const o = item as Record<string, unknown>;
    const name = typeof o.name === "string" ? o.name : "";
    const dep = o.depends_on;
    const depends_on = Array.isArray(dep)
      ? dep.filter((x): x is string => typeof x === "string")
      : [];
    if (name) {
      out.push({ name, depends_on });
    }
  }
  return out;
}

function toGraph(spec: { name: string; depends_on: string[] }[]): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = spec.map((n, i) => ({
    id: n.name,
    position: { x: (i % 4) * 200, y: Math.floor(i / 4) * 120 },
    data: { label: n.name },
  }));
  const edges: Edge[] = [];
  spec.forEach((n) => {
    n.depends_on.forEach((dep) => {
      edges.push({ id: `${dep}->${n.name}`, source: dep, target: n.name });
    });
  });
  return { nodes, edges };
}

export function WorkflowIdePage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("workflow.read");
  const [runId, setRunId] = React.useState("");
  const trimmed = runId.trim();
  const q = useQuery({
    queryKey: ["workflow-ide", trimmed],
    queryFn: async () => {
      const r = await apiFetch(`/v1/workflows/${encodeURIComponent(trimmed)}`, { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as unknown;
    },
    enabled: Boolean(trimmed) && can,
  });

  const spec = React.useMemo(() => extractSpecNodes(q.data ?? null), [q.data]);
  const { nodes, edges } = React.useMemo(() => toGraph(spec), [spec]);
  const showGraph = spec.length > 0 && q.isSuccess;

  return (
    <div className="h-[calc(100vh-8rem)] space-y-2">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.workflowIde")}</h1>
      <CapabilityGuard need="workflow.read" label="Workflow IDE">
        <label className="block text-sm">
          {t("workflowIde.runIdLabel")}
          <input
            className="mt-1 max-w-md rounded border border-slate-300 px-2 py-1 font-mono text-xs"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            aria-label={t("workflowIde.runIdLabel")}
          />
        </label>
        <p className="text-xs text-slate-600">{t("workflowIde.apiNote")}</p>
        {!trimmed ? <p className="text-sm text-slate-600">{t("workflowIde.enterRunId")}</p> : null}
        {q.isLoading && trimmed ? <div>{t("common.loading")}</div> : null}
        {q.isError ? (
          <div className="text-sm text-red-700" role="alert">
            {q.error instanceof Error ? q.error.message : String(q.error)}
          </div>
        ) : null}
        {q.isSuccess && trimmed && spec.length === 0 ? (
          <div className="rounded border border-amber-200 bg-amber-50/80 px-3 py-2 text-sm text-amber-900" role="status">
            {t("workflowIde.noSpecNodes")}
          </div>
        ) : null}
        <div className="mt-2 h-[520px] rounded border border-slate-200 bg-white">
          {showGraph ? (
            <>
              <p className="border-b border-slate-100 px-2 py-1 text-xs text-slate-600">{t("workflowIde.graphTitle")}</p>
              <div className="h-[calc(100%-1.75rem)]">
                <ReactFlow nodes={nodes} edges={edges} fitView>
                  <Background />
                  <Controls />
                </ReactFlow>
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center px-4 text-center text-sm text-slate-500">
              {!trimmed
                ? t("workflowIde.enterRunId")
                : q.isLoading
                  ? t("common.loading")
                  : q.isError
                    ? t("common.error")
                    : "\u00a0"}
            </div>
          )}
        </div>
        {q.isSuccess && q.data !== undefined ? (
          <details className="mt-2 text-xs">
            <summary className="cursor-pointer text-slate-700">{t("workflowIde.rawResponse")}</summary>
            <pre className="mt-1 max-h-40 overflow-auto rounded border border-slate-100 bg-slate-50 p-2">
              {JSON.stringify(q.data, null, 2)}
            </pre>
          </details>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
