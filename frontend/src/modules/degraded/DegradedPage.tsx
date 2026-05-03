import * as React from "react";
import { Link } from "react-router-dom";
import { useConnectionStore } from "../../core/connection/connectionStore";
import { apiFetch } from "../../core/http/client";

export function DegradedPage({
  title,
  detail,
}: {
  title: string;
  detail?: string;
}): React.ReactElement {
  const mode = useConnectionStore((s) => s.identityMode);

  return (
    <div className="mx-auto max-w-lg space-y-4 p-8">
      <h1 className="text-xl font-semibold text-amber-800">{title}</h1>
      {detail ? (
        <pre className="overflow-x-auto rounded border border-amber-200 bg-amber-50 p-3 text-sm">
          {detail}
        </pre>
      ) : null}
      <p className="text-sm text-slate-600">
        Configure API URL and identity ({mode}), then retry. Health check below does not require
        auth.
      </p>
      <Link className="text-blue-600 underline" to="/settings">
        Open settings
      </Link>
      <HealthSnippet />
    </div>
  );
}

function HealthSnippet(): React.ReactElement {
  const [text, setText] = React.useState<string>("…");
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await apiFetch("/v1/healthz", { method: "GET" });
        const body = await res.text();
        if (!cancelled) {
          setText(`${res.status} ${body}`);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="rounded border border-slate-200 p-3 text-sm">
      <div className="font-medium text-slate-700">GET /v1/healthz</div>
      {err ? <div className="text-red-600">{err}</div> : <pre className="mt-1">{text}</pre>}
    </div>
  );
}
