import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { validateRecordKeys } from "../../core/contract/contractGuard";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

type ApprovalRow = {
  approval_id: string;
  status: string;
  run_id: string;
  tenant_id: string;
  tool_name: string;
  reason: string;
  args_hash?: string | null;
};

export function ApprovalPage(): React.ReactElement {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const canRead = useHasCapability("approval.read");
  const canDecide = useHasCapability("approval.decide");
  const [id, setId] = React.useState("");
  const [out, setOut] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [decisionComment, setDecisionComment] = React.useState("");

  const listQ = useQuery({
    queryKey: ["approvals-list", "pending"],
    queryFn: async () => {
      const r = await apiFetch("/v1/approvals?status=pending&limit=100", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as { approvals: ApprovalRow[] };
    },
    enabled: canRead,
  });

  const contractIssues = React.useMemo(() => {
    const rows = listQ.data?.approvals ?? [];
    const issues: { path: string; message: string }[] = [];
    rows.forEach((row, i) => {
      issues.push(...validateRecordKeys(row, i, ["approval_id", "run_id"]));
    });
    return issues;
  }, [listQ.data]);

  async function load(): Promise<void> {
    if (!id.trim()) {
      return;
    }
    setLoading(true);
    setOut("");
    try {
      const res = await apiFetch(`/v1/approvals/${encodeURIComponent(id.trim())}`, {
        method: "GET",
      });
      const text = await res.text();
      setOut(`${res.status}\n${text}`);
    } catch (e) {
      setOut(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const decide = useMutation({
    mutationFn: async (decision: "approve" | "reject") => {
      if (!id.trim()) {
        throw new Error("approval_id required");
      }
      const r = await apiFetch(`/v1/approvals/${encodeURIComponent(id.trim())}/decision`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          approver_id: "ui-operator",
          comment: decisionComment,
        }),
      });
      const text = await r.text();
      if (!r.ok) {
        throw new Error(`${r.status} ${text}`);
      }
      return text;
    },
    onSuccess: (text) => {
      setOut(text);
      void qc.invalidateQueries({ queryKey: ["approvals-list"] });
    },
  });

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.approvals")}</h1>

      {contractIssues.length > 0 ? (
        <div
          role="alert"
          className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          <div className="font-medium">{t("contract.violationTitle")}</div>
          <p className="mt-1">{t("contract.violationDetail")}</p>
          <ul className="mt-1 list-inside list-disc">
            {contractIssues.slice(0, 6).map((x) => (
              <li key={x.path}>
                {x.path}: {x.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <CapabilityGuard need="approval.read" label="Approval list">
        <div className="text-sm font-medium text-slate-700">Pending (GET /v1/approvals)</div>
        {listQ.isLoading ? <div>{t("common.loading")}</div> : null}
        {listQ.isError ? (
          <div className="text-sm text-red-700">
            {listQ.error instanceof Error ? listQ.error.message : String(listQ.error)}
          </div>
        ) : null}
        {listQ.data ? (
          <ul
            className="max-h-48 divide-y divide-slate-200 overflow-auto rounded border border-slate-200 text-sm"
            aria-label={t("approval.pendingListAria")}
          >
            {listQ.data.approvals.map((a) => (
              <li key={a.approval_id}>
                <div className="flex items-start gap-2 px-2 py-1.5 hover:bg-slate-50">
                  <button
                    type="button"
                    className="min-w-0 flex-1 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                    onClick={() => setId(a.approval_id)}
                  >
                    <span className="font-mono text-xs">{a.approval_id}</span>
                    <span className="ml-2 text-slate-600">{a.tool_name}</span>
                    {a.args_hash ? (
                      <span className="mt-0.5 block font-mono text-[10px] text-slate-500">
                        {t("approval.argsFingerprint")}: {a.args_hash.slice(0, 16)}…
                      </span>
                    ) : null}
                  </button>
                  {a.args_hash ? (
                    <button
                      type="button"
                      className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                      onClick={() => void navigator.clipboard.writeText(a.args_hash ?? "")}
                      aria-label={t("approval.copyFingerprint")}
                    >
                      {t("approval.copyFingerprint")}
                    </button>
                  ) : null}
                  <Link
                    className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-blue-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                    to={`/runs/${encodeURIComponent(a.run_id)}`}
                    aria-label={t("approval.runLinkAria")}
                  >
                    {t("approval.openRunDetail")}
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </CapabilityGuard>

      <CapabilityGuard need="approval.read" label="Approval lookup">
        <div className="flex max-w-xl flex-col gap-2">
          <label className="text-sm">
            <span className="text-slate-600">approval_id</span>
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              value={id}
              onChange={(e) => setId(e.target.value)}
              aria-label="approval id"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="w-fit rounded bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              disabled={loading || !canRead}
              onClick={() => void load()}
            >
              GET /v1/approvals/{"{id}"}
            </button>
          </div>
        </div>
      </CapabilityGuard>

      <CapabilityGuard need="approval.decide" label="Approval decision">
        <label className="block max-w-xl text-sm">
          comment
          <input
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={decisionComment}
            onChange={(e) => setDecisionComment(e.target.value)}
          />
        </label>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            className="rounded bg-emerald-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={!canDecide || decide.isPending || !id.trim()}
            onClick={() => void decide.mutate("approve")}
          >
            Approve
          </button>
          <button
            type="button"
            className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={!canDecide || decide.isPending || !id.trim()}
            onClick={() => void decide.mutate("reject")}
          >
            Reject
          </button>
        </div>
      </CapabilityGuard>

      {!canRead ? (
        <p className="text-sm text-slate-600">Capability `approval.read` required.</p>
      ) : null}
      {out ? (
        <pre className="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs">
          {out}
        </pre>
      ) : null}
    </div>
  );
}
