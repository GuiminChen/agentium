import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { hmacSha256Hex, stableStringify } from "../../core/crypto/stableJson";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

function shortId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

type ReleaseRow = {
  release_id: string;
  version: string;
  status: string;
  requested_by: string;
  tenant_id: string;
  run_id: string;
  approver_id?: string | null;
  active_tenants?: string[];
};

export function PolicyPage(): React.ReactElement {
  const { t } = useTranslation();
  const can = useHasCapability("governance.policy.read");
  const canRel = useHasCapability("governance.releases.read");
  const canSubmit = useHasCapability("policy.release.submit");
  const [version, setVersion] = React.useState("1.0.0-candidate");
  const [docText, setDocText] = React.useState('{\n  "version": "p0",\n  "default_decision": "deny"\n}');
  const [metaText, setMetaText] = React.useState("{}");
  const [hmacSecret, setHmacSecret] = React.useState("");
  const [wizardError, setWizardError] = React.useState("");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [approverId, setApproverId] = React.useState("release-approver");
  const [approveComment, setApproveComment] = React.useState("");
  const [activateTenants, setActivateTenants] = React.useState("");
  const [activatedBy, setActivatedBy] = React.useState("operator-1");
  const [rollbackBy, setRollbackBy] = React.useState("operator-1");
  const [lifecycleError, setLifecycleError] = React.useState("");

  const qc = useQueryClient();

  const pol = useQuery({
    queryKey: ["policy-effective"],
    queryFn: async () => {
      const r = await apiFetch("/v1/policy/effective", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    enabled: can,
  });

  const releases = useQuery({
    queryKey: ["policy-releases"],
    queryFn: async () => {
      const r = await apiFetch("/v1/policies/releases?limit=50", { method: "GET" });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json() as Promise<{ count: number; releases: ReleaseRow[] }>;
    },
    enabled: canRel,
  });

  const releaseDetail = useQuery({
    queryKey: ["policy-release", selectedId],
    queryFn: async () => {
      if (!selectedId) {
        return null;
      }
      const r = await apiFetch(`/v1/policies/releases/${encodeURIComponent(selectedId)}`, {
        method: "GET",
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return (await r.json()) as ReleaseRow;
    },
    enabled: Boolean(selectedId) && canRel,
  });

  const submitRel = useMutation({
    mutationFn: async () => {
      let policy_document: Record<string, unknown>;
      try {
        const parsed = JSON.parse(docText) as unknown;
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          throw new Error("not_object");
        }
        policy_document = parsed as Record<string, unknown>;
      } catch {
        throw new Error(t("policyRelease.invalidJsonDocument"));
      }
      let metadata: Record<string, unknown>;
      try {
        metadata = JSON.parse(metaText || "{}") as Record<string, unknown>;
      } catch {
        throw new Error(t("policyRelease.invalidJsonMetadata"));
      }
      const secret = hmacSecret.trim();
      if (!secret) {
        throw new Error(t("policyRelease.secretRequired"));
      }
      const canonical = stableStringify({ version: version.trim(), policy_document });
      const signature = await hmacSha256Hex(secret, canonical);
      const run_id = shortId("rel");
      const body = {
        run_id,
        request_id: `${run_id}-req`,
        trace_id: `${run_id}-trace`,
        bundle: {
          version: version.trim(),
          policy_document,
          signature,
          metadata,
        },
      };
      const r = await apiFetch("/v1/policies/releases", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json() as Promise<ReleaseRow>;
    },
    onError: (e: Error) => setWizardError(e.message),
    onSuccess: (data) => {
      setWizardError("");
      void qc.invalidateQueries({ queryKey: ["policy-releases"] });
      if (data?.release_id) {
        setSelectedId(data.release_id);
      }
    },
  });

  const approveMut = useMutation({
    mutationFn: async () => {
      if (!selectedId) {
        throw new Error(t("policyRelease.selectRelease"));
      }
      const r = await apiFetch(`/v1/policies/releases/${encodeURIComponent(selectedId)}/approve`, {
        method: "POST",
        body: JSON.stringify({
          approver_id: approverId.trim(),
          comment: approveComment,
        }),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    onError: (e: Error) => setLifecycleError(e.message),
    onSuccess: () => {
      setLifecycleError("");
      void qc.invalidateQueries({ queryKey: ["policy-releases"] });
      void qc.invalidateQueries({ queryKey: ["policy-release", selectedId] });
    },
  });

  const activateMut = useMutation({
    mutationFn: async () => {
      if (!selectedId) {
        throw new Error(t("policyRelease.selectRelease"));
      }
      const tenants = activateTenants
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!tenants.length) {
        throw new Error(t("policyRelease.tenantsRequired"));
      }
      const r = await apiFetch(`/v1/policies/releases/${encodeURIComponent(selectedId)}/activate`, {
        method: "POST",
        body: JSON.stringify({
          tenant_ids: tenants,
          activated_by: activatedBy.trim(),
        }),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    onError: (e: Error) => setLifecycleError(e.message),
    onSuccess: () => {
      setLifecycleError("");
      void qc.invalidateQueries({ queryKey: ["policy-releases"] });
      void qc.invalidateQueries({ queryKey: ["policy-release", selectedId] });
    },
  });

  const rollbackMut = useMutation({
    mutationFn: async () => {
      if (!selectedId) {
        throw new Error(t("policyRelease.selectRelease"));
      }
      const r = await apiFetch(`/v1/policies/releases/${encodeURIComponent(selectedId)}/rollback`, {
        method: "POST",
        body: JSON.stringify({ rolled_back_by: rollbackBy.trim() }),
      });
      if (!r.ok) {
        throw new Error(await readApiError(r));
      }
      return r.json();
    },
    onError: (e: Error) => setLifecycleError(e.message),
    onSuccess: () => {
      setLifecycleError("");
      void qc.invalidateQueries({ queryKey: ["policy-releases"] });
      void qc.invalidateQueries({ queryKey: ["policy-release", selectedId] });
    },
  });

  const rows = releases.data?.releases ?? [];

  return (
    <div className="space-y-3">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.policy")}</h1>
      <CapabilityGuard need="governance.policy.read" label="Policy">
        {pol.isLoading ? <div>{t("common.loading")}</div> : null}
        {pol.isError ? (
          <div className="text-sm text-red-700">
            {pol.error instanceof Error ? pol.error.message : String(pol.error)}
          </div>
        ) : null}
        {pol.data ? (
          <pre className="max-h-[480px] overflow-auto rounded border border-slate-200 bg-slate-50 p-2 text-xs">
            {JSON.stringify(pol.data, null, 2)}
          </pre>
        ) : null}
      </CapabilityGuard>

      <CapabilityGuard need="governance.releases.read" label="Policy releases">
        {releases.isLoading ? <div>{t("common.loading")}</div> : null}
        {releases.isError ? (
          <div className="mt-2 text-sm text-red-700">
            {releases.error instanceof Error ? releases.error.message : String(releases.error)}
          </div>
        ) : null}
        {releases.data && rows.length > 0 ? (
          <div className="mt-2 overflow-x-auto rounded border border-slate-200">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-slate-100 text-xs text-slate-600">
                <tr>
                  <th scope="col" className="px-2 py-1.5 font-medium">
                    {t("policyRelease.colId")}
                  </th>
                  <th scope="col" className="px-2 py-1.5 font-medium">
                    {t("policyRelease.colVersion")}
                  </th>
                  <th scope="col" className="px-2 py-1.5 font-medium">
                    {t("policyRelease.colStatus")}
                  </th>
                  <th scope="col" className="px-2 py-1.5 font-medium">
                    {t("policyRelease.colTenant")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.release_id}
                    className={
                      selectedId === r.release_id
                        ? "cursor-pointer bg-blue-50"
                        : "cursor-pointer hover:bg-slate-50"
                    }
                    onClick={() => {
                      setSelectedId(r.release_id);
                      setLifecycleError("");
                    }}
                  >
                    <td className="border-t border-slate-100 px-2 py-1.5 font-mono text-xs">
                      {r.release_id}
                    </td>
                    <td className="border-t border-slate-100 px-2 py-1.5">{r.version}</td>
                    <td className="border-t border-slate-100 px-2 py-1.5">{r.status}</td>
                    <td className="border-t border-slate-100 px-2 py-1.5">{r.tenant_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : releases.data ? (
          <p className="text-sm text-slate-600">{t("policyRelease.noReleases")}</p>
        ) : null}

        {selectedId ? (
          <div className="mt-3 rounded border border-slate-200 bg-white p-3">
            <h2 className="text-sm font-semibold text-slate-800">{t("policyRelease.detailTitle")}</h2>
            {releaseDetail.isLoading ? <div className="mt-1">{t("common.loading")}</div> : null}
            {releaseDetail.isError ? (
              <div className="mt-1 text-sm text-red-700">
                {releaseDetail.error instanceof Error ? releaseDetail.error.message : String(releaseDetail.error)}
              </div>
            ) : null}
            {releaseDetail.data ? (
              <pre className="mt-2 max-h-48 overflow-auto rounded border border-slate-100 bg-slate-50 p-2 text-xs">
                {JSON.stringify(releaseDetail.data, null, 2)}
              </pre>
            ) : null}

            <CapabilityGuard need="policy.release.submit" label="Policy release actions">
              {lifecycleError ? (
                <div className="mt-2 text-sm text-red-700" role="alert">
                  {lifecycleError}
                </div>
              ) : null}
              <div className="mt-3 grid gap-3 border-t border-slate-100 pt-3">
                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium text-slate-700">{t("policyRelease.approveLegend")}</legend>
                  <label className="block text-xs">
                    approver_id
                    <input
                      className="mt-0.5 w-full max-w-md rounded border border-slate-300 px-2 py-1"
                      value={approverId}
                      onChange={(e) => setApproverId(e.target.value)}
                    />
                  </label>
                  <label className="block text-xs">
                    comment
                    <input
                      className="mt-0.5 w-full max-w-md rounded border border-slate-300 px-2 py-1"
                      value={approveComment}
                      onChange={(e) => setApproveComment(e.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="rounded bg-emerald-700 px-2 py-1 text-xs text-white disabled:opacity-50"
                    disabled={!canSubmit || approveMut.isPending}
                    onClick={() => void approveMut.mutate()}
                  >
                    {t("policyRelease.approve")}
                  </button>
                </fieldset>
                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium text-slate-700">{t("policyRelease.activateLegend")}</legend>
                  <label className="block text-xs">
                    {t("policyRelease.tenantIdsHint")}
                    <textarea
                      className="mt-0.5 w-full max-w-md rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                      rows={2}
                      value={activateTenants}
                      onChange={(e) => setActivateTenants(e.target.value)}
                      placeholder="tenant-a, tenant-b"
                    />
                  </label>
                  <label className="block text-xs">
                    activated_by
                    <input
                      className="mt-0.5 w-full max-w-md rounded border border-slate-300 px-2 py-1"
                      value={activatedBy}
                      onChange={(e) => setActivatedBy(e.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="rounded bg-blue-700 px-2 py-1 text-xs text-white disabled:opacity-50"
                    disabled={!canSubmit || activateMut.isPending}
                    onClick={() => void activateMut.mutate()}
                  >
                    {t("policyRelease.activate")}
                  </button>
                </fieldset>
                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium text-slate-700">{t("policyRelease.rollbackLegend")}</legend>
                  <label className="block text-xs">
                    rolled_back_by
                    <input
                      className="mt-0.5 w-full max-w-md rounded border border-slate-300 px-2 py-1"
                      value={rollbackBy}
                      onChange={(e) => setRollbackBy(e.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="rounded bg-red-800 px-2 py-1 text-xs text-white disabled:opacity-50"
                    disabled={!canSubmit || rollbackMut.isPending}
                    onClick={() => void rollbackMut.mutate()}
                  >
                    {t("policyRelease.rollback")}
                  </button>
                </fieldset>
              </div>
            </CapabilityGuard>
          </div>
        ) : null}
      </CapabilityGuard>

      <CapabilityGuard need="policy.release.submit" label="Policy release submit">
        <div className="rounded border border-slate-200 bg-white p-3">
          <h2 className="text-sm font-semibold text-slate-800">{t("policyRelease.wizardTitle")}</h2>
          <p className="mt-1 text-xs text-slate-600">{t("policyRelease.wizardHelp")}</p>
          <div className="mt-3 grid max-w-2xl gap-2 text-sm">
            <label className="block">
              {t("policyRelease.version")}
              <input
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                aria-invalid={!version.trim()}
              />
            </label>
            <label className="block">
              {t("policyRelease.documentJson")}
              <textarea
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                rows={8}
                value={docText}
                onChange={(e) => setDocText(e.target.value)}
              />
            </label>
            <label className="block">
              {t("policyRelease.metadataJson")}
              <textarea
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                rows={3}
                value={metaText}
                onChange={(e) => setMetaText(e.target.value)}
              />
            </label>
            <label className="block">
              {t("policyRelease.hmacSecret")}
              <input
                type="password"
                autoComplete="off"
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 font-mono text-xs"
                value={hmacSecret}
                onChange={(e) => setHmacSecret(e.target.value)}
                aria-invalid={!hmacSecret.trim()}
              />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded bg-blue-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              disabled={!canSubmit || submitRel.isPending}
              onClick={() => void submitRel.mutate()}
            >
              {t("policyRelease.submit")}
            </button>
            {submitRel.isPending ? <span className="text-xs text-slate-500">{t("common.loading")}</span> : null}
          </div>
          {wizardError ? (
            <div className="mt-2 text-sm text-red-700" role="alert">
              {wizardError}
            </div>
          ) : null}
          {submitRel.isSuccess && submitRel.data ? (
            <pre className="mt-2 max-h-48 overflow-auto rounded border border-emerald-200 bg-emerald-50 p-2 text-xs">
              {JSON.stringify(submitRel.data, null, 2)}
            </pre>
          ) : null}
        </div>
      </CapabilityGuard>
    </div>
  );
}
