import * as React from "react";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";

type ToolContractDTO = {
  version?: string;
  description?: string;
  input_schema?: Record<string, unknown>;
};

type ToolCatalogEntry = {
  name: string;
  capabilities: string[];
  risk_level: string;
  has_contract: boolean;
  contract?: ToolContractDTO;
};

type ToolsResponse = {
  count: number;
  tools: ToolCatalogEntry[];
};

export function ToolCatalogPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("tools.read");
  const [data, setData] = React.useState<ToolsResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const load = React.useCallback(async () => {
    if (!can) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/v1/tools", { method: "GET" });
      const raw = await res.text();
      if (!res.ok) {
        setError(`${res.status} ${raw.slice(0, 200)}`);
        setData(null);
        return;
      }
      setData(JSON.parse(raw) as ToolsResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [can]);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("toolsCatalog.title")}</h1>
      <p className="text-sm text-slate-600">{t("toolsCatalog.hint")}</p>
      <CapabilityGuard need="tools.read" label="Tools">
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
            disabled={loading}
            onClick={() => void load()}
          >
            {t("common.reload")}
          </button>
        </div>
        {error ? <p className="text-sm text-red-700">{error}</p> : null}
        {loading && !data ? <p className="text-sm text-slate-500">{t("common.loading")}</p> : null}
        {data ? (
          <div className="overflow-auto rounded border border-slate-200">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-600">
                <tr>
                  <th className="px-2 py-1.5">{t("toolsCatalog.colName")}</th>
                  <th className="px-2 py-1.5">{t("toolsCatalog.colRisk")}</th>
                  <th className="px-2 py-1.5">{t("toolsCatalog.colCaps")}</th>
                  <th className="px-2 py-1.5">{t("toolsCatalog.colContract")}</th>
                </tr>
              </thead>
              <tbody>
                {data.tools.map((row) => (
                  <tr key={row.name} className="border-t border-slate-100">
                    <td className="px-2 py-1.5 font-mono text-xs">{row.name}</td>
                    <td className="px-2 py-1.5 text-xs">{row.risk_level}</td>
                    <td className="px-2 py-1.5 text-xs">{row.capabilities.join(", ")}</td>
                    <td className="px-2 py-1.5 text-xs">
                      {row.has_contract && row.contract ? (
                        <details>
                          <summary className="cursor-pointer text-blue-700">
                            {row.contract.version ?? "—"}
                          </summary>
                          <p className="mt-1 max-w-md text-slate-700">{row.contract.description}</p>
                          <pre className="mt-1 max-h-40 max-w-xl overflow-auto rounded bg-slate-50 p-1 text-[10px]">
                            {JSON.stringify(row.contract.input_schema ?? {}, null, 2)}
                          </pre>
                        </details>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-slate-100 px-2 py-1 text-xs text-slate-500">
              {t("toolsCatalog.countLabel", { count: data.count })}
            </p>
          </div>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
