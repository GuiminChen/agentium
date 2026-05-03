import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { canQueryProfile, useConnectionStore } from "../../core/connection/connectionStore";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch, apiPath } from "../../core/http/client";
import { setAgentiumLanguage } from "../../core/i18n/i18n";
import { fetchMe } from "../../core/profile/profileApi";

function looksLikeBrowserFetchFailure(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }
  const m = err.message.toLowerCase();
  return (
    m.includes("failed to fetch") ||
    m.includes("network request failed") ||
    m.includes("load failed") ||
    (err.name === "TypeError" && m.includes("fetch"))
  );
}

export function SettingsPage(): React.ReactElement {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const canObs = useHasCapability("observability.read");
  const identityReady = canQueryProfile();
  const apiBaseUrlTrimmed = useConnectionStore((s) => s.apiBaseUrl.trim());
  const apiBaseUrl = useConnectionStore((s) => s.apiBaseUrl);
  const identityMode = useConnectionStore((s) => s.identityMode);
  const tenantId = useConnectionStore((s) => s.tenantId);
  const userId = useConnectionStore((s) => s.userId);
  const role = useConnectionStore((s) => s.role);
  const bearerToken = useConnectionStore((s) => s.bearerToken);
  const setApiBaseUrl = useConnectionStore((s) => s.setApiBaseUrl);
  const setIdentityMode = useConnectionStore((s) => s.setIdentityMode);
  const setTenantId = useConnectionStore((s) => s.setTenantId);
  const setUserId = useConnectionStore((s) => s.setUserId);
  const setRole = useConnectionStore((s) => s.setRole);
  const setBearerToken = useConnectionStore((s) => s.setBearerToken);

  const [applyBusy, setApplyBusy] = React.useState(false);
  const [applyBanner, setApplyBanner] = React.useState<{ tone: "ok" | "err"; text: string } | null>(
    null,
  );

  const uiLinks = useQuery({
    queryKey: ["ui-links"],
    queryFn: async () => {
      const r = await apiFetch("/v1/config/ui-links", { method: "GET" });
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json() as Promise<{ links: Record<string, string> }>;
    },
    enabled: canObs && identityReady,
  });

  async function applyConnection(): Promise<void> {
    if (!identityReady) {
      setApplyBanner({ tone: "err", text: t("settings.applyNeedIdentity") });
      return;
    }
    setApplyBusy(true);
    setApplyBanner(null);
    try {
      await qc.fetchQuery({ queryKey: ["me"], queryFn: fetchMe });
      setApplyBanner({ tone: "ok", text: t("settings.applyOk") });
      await qc.invalidateQueries({ queryKey: ["ui-links"] });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      let text = msg;
      if (looksLikeBrowserFetchFailure(e)) {
        const hint = t("settings.applyNetworkFailed");
        const devUrl =
          import.meta.env.DEV && typeof window !== "undefined"
            ? `\nGET ${window.location.origin}${apiPath("/v1/me")}`
            : apiBaseUrlTrimmed
              ? `\nGET ${apiBaseUrlTrimmed.replace(/\/$/, "")}/v1/me`
              : "";
        text = `${msg}\n\n${hint}${devUrl}`;
      }
      setApplyBanner({ tone: "err", text });
    } finally {
      setApplyBusy(false);
    }
  }

  const uiLinksErrText =
    uiLinks.error instanceof Error ? uiLinks.error.message : uiLinks.error ? String(uiLinks.error) : "";

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">{t("nav.settings")}</h1>
      <div className="rounded border border-slate-200 p-3 text-sm">
        <label className="block">
          {t("settings.language")}
          <select
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={i18n.language}
            onChange={(e) => setAgentiumLanguage(e.target.value)}
            aria-label={t("settings.language")}
          >
            <option value="en">English</option>
            <option value="zh-CN">中文（简体）</option>
          </select>
        </label>
      </div>
      <p className="text-sm text-slate-600">
        Values persist in <code className="rounded bg-slate-100 px-1">sessionStorage</code> (no
        secrets beyond bearer). Empty API base uses same-origin <code>/v1</code> (Vite proxy in
        dev).
      </p>
      <details className="rounded border border-slate-200 p-3 text-sm">
        <summary className="cursor-pointer font-medium text-slate-700">Run manifest template (JSON)</summary>
        <pre className="mt-2 overflow-auto rounded bg-slate-50 p-2 text-xs">{`{
  "profile": "dev",
  "build_id": "local",
  "declared_tools": ["echo_tool"]
}`}</pre>
        <p className="mt-2 text-slate-600">
          <Trans
            i18nKey="settings.runManifestRm01"
            components={{
              code: <code className="rounded bg-slate-100 px-1 text-xs" />,
              turnDebug: <Link className="text-blue-700 underline hover:text-blue-800" to="/turn-debug" />,
              workspace: <Link className="text-blue-700 underline hover:text-blue-800" to="/workspace" />,
            }}
          />
        </p>
      </details>
      <div className="space-y-3 rounded border border-slate-200 p-3">
        <label className="block text-sm">
          <span className="text-slate-600">API base URL (optional)</span>
          <input
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            placeholder="e.g. http://127.0.0.1:8765"
            value={apiBaseUrl}
            onChange={(e) => setApiBaseUrl(e.target.value)}
          />
        </label>
        {apiBaseUrlTrimmed ? (
          <p className="text-xs text-amber-800">{t("settings.apiBaseHint")}</p>
        ) : null}
        <label className="block text-sm">
          <span className="text-slate-600">Identity mode</span>
          <select
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
            value={identityMode}
            onChange={(e) => setIdentityMode(e.target.value as "header" | "bearer")}
          >
            <option value="header">header (X-Tenant-Id / X-User-Id)</option>
            <option value="bearer">bearer</option>
          </select>
        </label>
        {identityMode === "header" ? (
          <>
            <label className="block text-sm">
              <span className="text-slate-600">X-Tenant-Id</span>
              <input
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">X-User-Id</span>
              <input
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">X-Role</span>
              <input
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              />
            </label>
          </>
        ) : (
          <label className="block text-sm">
            <span className="text-slate-600">Bearer token</span>
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1"
              type="password"
              autoComplete="off"
              value={bearerToken}
              onChange={(e) => setBearerToken(e.target.value)}
            />
          </label>
        )}
        <button
          type="button"
          className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white disabled:opacity-60"
          disabled={applyBusy || !identityReady}
          onClick={() => void applyConnection()}
        >
          {applyBusy ? t("settings.applyRunning") : t("settings.applyReload")}
        </button>
        {!identityReady ? (
          <p className="text-xs text-amber-800">{t("settings.applyNeedIdentity")}</p>
        ) : null}
        {applyBanner ? (
          <div
            role="status"
            className={
              applyBanner.tone === "ok"
                ? "rounded border border-emerald-200 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-900"
                : "whitespace-pre-wrap rounded border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-900"
            }
          >
            {applyBanner.text}
          </div>
        ) : null}
      </div>
      {canObs ? (
        <div className="space-y-2 rounded border border-slate-200 p-3 text-sm">
          <div className="font-medium text-slate-700">{t("settings.observabilityLinks")}</div>
          {uiLinks.isLoading ? <div>{t("common.loading")}</div> : null}
          {uiLinks.isError ? (
            <div className="space-y-1 text-xs text-slate-600">
              <div className="whitespace-pre-wrap">{uiLinksErrText}</div>
              {uiLinksErrText.includes("missing_identity_headers") ? (
                <p className="text-amber-900">{t("settings.observabilityCorsHint")}</p>
              ) : null}
              {uiLinksErrText.toLowerCase().includes("failed to fetch") ? (
                <p className="text-amber-900">{t("settings.applyNetworkFailed")}</p>
              ) : null}
            </div>
          ) : null}
          {uiLinks.data?.links
            ? Object.entries(uiLinks.data.links).map(([k, v]) => (
                <div key={k}>
                  <a className="text-blue-700 hover:underline" href={v} rel="noreferrer" target="_blank">
                    {k}
                  </a>
                </div>
              ))
            : null}
        </div>
      ) : (
        <p className="text-xs text-slate-500">
          Observability URLs require capability <code className="rounded bg-slate-100 px-1">observability.read</code>.
        </p>
      )}
    </div>
  );
}
