import * as React from "react";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

export function TurnDebugPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("turn.execute");
  const canCatalog = useHasCapability("tools.read");
  const [toolNameOptions, setToolNameOptions] = React.useState<string[]>([]);
  const [toolName, setToolName] = React.useState("echo_tool");
  const [argsJson, setArgsJson] = React.useState('{"text":"hi"}');
  const [runId, setRunId] = React.useState("run-ui-1");
  const [requestId, setRequestId] = React.useState("req-ui-1");
  const [traceId, setTraceId] = React.useState("trace-ui-1");
  const [manifestText, setManifestText] = React.useState("");
  const [messageDisposition, setMessageDisposition] = React.useState<
    "collect" | "followup" | "steer"
  >("collect");
  const [mcpTier, setMcpTier] = React.useState<"direct-tool" | "code-exec-mcp">("direct-tool");
  const [result, setResult] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  async function submit(): Promise<void> {
    setLoading(true);
    setResult("");
    try {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(argsJson) as Record<string, unknown>;
      } catch {
        setResult("Invalid JSON in args");
        setLoading(false);
        return;
      }
      let run_manifest: Record<string, unknown> | undefined;
      if (manifestText.trim()) {
        try {
          const m = JSON.parse(manifestText) as unknown;
          if (typeof m !== "object" || m === null || Array.isArray(m)) {
            setResult(t("manifest.invalidJson"));
            setLoading(false);
            return;
          }
          run_manifest = m as Record<string, unknown>;
        } catch {
          setResult(t("manifest.invalidJson"));
          setLoading(false);
          return;
        }
      }
      const body: Record<string, unknown> = {
        tool_name: toolName,
        args,
        run_id: runId,
        request_id: requestId,
        trace_id: traceId,
        deployment_mode: "prod",
        message_disposition: messageDisposition,
        mcp_execution_tier: mcpTier,
      };
      if (run_manifest) {
        body.run_manifest = run_manifest;
      }
      const res = await apiFetch("/v1/turn", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const text = await res.text();
      if (!res.ok) {
        let msg = text;
        try {
          msg = await readApiError(new Response(text, { status: res.status, statusText: res.statusText }));
        } catch {
          /* use text */
        }
        setResult(`${res.status}\n${msg}`);
      } else {
        setResult(`${res.status}\n${text}`);
      }
    } catch (e) {
      setResult(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => {
    if (!canCatalog) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiFetch("/v1/tools", { method: "GET" });
        if (!res.ok || cancelled) {
          return;
        }
        const j = (await res.json()) as { tools?: Array<{ name: string }> };
        if (Array.isArray(j.tools)) {
          setToolNameOptions(j.tools.map((row) => row.name));
        }
      } catch {
        /* catalog optional */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canCatalog]);

  const form = (
    <div className="max-w-xl space-y-3">
      <label className="block text-sm">
        <span className="text-slate-600">tool_name</span>
        <input
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
          value={toolName}
          onChange={(e) => setToolName(e.target.value)}
          aria-label="tool name"
          list={canCatalog && toolNameOptions.length > 0 ? "turn-debug-tool-names" : undefined}
        />
        {canCatalog && toolNameOptions.length > 0 ? (
          <datalist id="turn-debug-tool-names">
            {toolNameOptions.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
        ) : null}
      </label>
      <label className="block text-sm">
        <span className="text-slate-600">args (JSON)</span>
        <textarea
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
          rows={4}
          value={argsJson}
          onChange={(e) => setArgsJson(e.target.value)}
          aria-label="tool args json"
        />
      </label>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <label className="block text-sm">
          <span className="text-slate-600">run_id</span>
          <input
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            aria-label="run id"
          />
        </label>
        <label className="block text-sm">
          <span className="text-slate-600">request_id</span>
          <input
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={requestId}
            onChange={(e) => setRequestId(e.target.value)}
            aria-label="request id"
          />
        </label>
        <label className="block text-sm">
          <span className="text-slate-600">trace_id</span>
          <input
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={traceId}
            onChange={(e) => setTraceId(e.target.value)}
            aria-label="trace id"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex flex-col gap-0.5 text-xs text-slate-600">
          <span>{t("workspace.dispositionLabel")}</span>
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1 text-slate-800"
            value={messageDisposition}
            onChange={(e) =>
              setMessageDisposition(e.target.value as "collect" | "followup" | "steer")
            }
            aria-label={t("workspace.dispositionLabel")}
          >
            <option value="collect">{t("workspace.dispositionCollect")}</option>
            <option value="followup">{t("workspace.dispositionFollowup")}</option>
            <option value="steer">{t("workspace.dispositionSteer")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5 text-xs text-slate-600">
          <span>{t("workspace.tierLabel")}</span>
          <select
            className="rounded border border-slate-300 bg-white px-2 py-1 text-slate-800"
            value={mcpTier}
            onChange={(e) => setMcpTier(e.target.value as "direct-tool" | "code-exec-mcp")}
            aria-label={t("workspace.tierLabel")}
          >
            <option value="direct-tool">{t("workspace.tierDirect")}</option>
            <option value="code-exec-mcp">{t("workspace.tierCodeMcp")}</option>
          </select>
        </label>
      </div>
      <details className="rounded border border-slate-200 bg-slate-50/80 p-2 text-sm">
        <summary className="cursor-pointer font-medium text-slate-800">{t("manifest.advancedTitle")}</summary>
        <p className="mt-2 text-xs text-slate-600">{t("manifest.optionalHint")}</p>
        <label className="mt-2 block">
          <span className="text-slate-600">run_manifest (JSON)</span>
          <textarea
            className="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1 font-mono text-xs"
            rows={5}
            placeholder={t("manifest.placeholder")}
            value={manifestText}
            onChange={(e) => setManifestText(e.target.value)}
            aria-label={t("manifest.advancedTitle")}
          />
        </label>
      </details>
      <button
        type="button"
        className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        disabled={loading || !can}
        onClick={() => void submit()}
      >
        POST /v1/turn
      </button>
    </div>
  );

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.turnDebug")}</h1>
      <CapabilityGuard need="turn.execute" label="Turn">
        {form}
      </CapabilityGuard>
      {!can ? (
        <p className="text-sm text-slate-600">Your profile lacks capability `turn.execute`.</p>
      ) : null}
      {result ? (
        <pre
          className="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs"
          aria-live="polite"
        >
          {result}
        </pre>
      ) : null}
    </div>
  );
}
