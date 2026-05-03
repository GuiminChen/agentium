import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { auditEventToolUseId, auditEventTraceId } from "./auditEventHelpers";

export type AuditEventRow = {
  event_type?: unknown;
  timestamp?: unknown;
  tenant_id?: unknown;
  run_id?: unknown;
  payload?: unknown;
};

function str(v: unknown): string {
  if (v === null || v === undefined) {
    return "";
  }
  return String(v);
}

export function AuditEventTable(props: { events: unknown[] }): React.ReactElement {
  const { events } = props;
  const { t } = useTranslation();

  const rows = React.useMemo(() => {
    return events.filter((e): e is AuditEventRow => typeof e === "object" && e !== null);
  }, [events]);

  return (
    <div className="overflow-x-auto rounded border border-slate-200">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-slate-100 text-xs text-slate-600">
          <tr>
            <th scope="col" className="px-2 py-1.5 font-medium">
              {t("auditTable.colTime")}
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              {t("auditTable.colType")}
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              tenant_id
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              run_id
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              trace_id
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              {t("auditTable.colActions")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((ev, idx) => {
            const runId = str(ev.run_id);
            const traceId = auditEventTraceId(ev.payload);
            const toolUse = auditEventToolUseId(ev.payload);
            return (
              <tr key={`${runId}-${idx}`} className="border-t border-slate-100">
                <td className="whitespace-nowrap px-2 py-1.5 font-mono text-xs text-slate-700">
                  {str(ev.timestamp)}
                </td>
                <td className="px-2 py-1.5 font-mono text-xs">{str(ev.event_type)}</td>
                <td className="px-2 py-1.5 font-mono text-xs">{str(ev.tenant_id)}</td>
                <td className="px-2 py-1.5 font-mono text-xs">
                  {runId ? (
                    <Link className="text-blue-700 hover:underline" to={`/runs/${encodeURIComponent(runId)}`}>
                      {runId}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="max-w-[12rem] truncate px-2 py-1.5 font-mono text-xs">
                  {traceId ?? "—"}
                </td>
                <td className="space-x-1 px-2 py-1.5 text-xs">
                  {traceId ? (
                    <button
                      type="button"
                      className="rounded border border-slate-300 px-1 py-0.5 hover:bg-slate-50"
                      onClick={() => void navigator.clipboard.writeText(traceId)}
                      aria-label={t("auditTable.copyTrace")}
                    >
                      trace
                    </button>
                  ) : null}
                  {toolUse ? (
                    <button
                      type="button"
                      className="rounded border border-slate-300 px-1 py-0.5 hover:bg-slate-50"
                      onClick={() => void navigator.clipboard.writeText(toolUse)}
                      aria-label={t("auditTable.copyToolUse")}
                    >
                      tool
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
