import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiFetch } from "../../core/http/client";
import type { MeResponse } from "../../core/profile/profileTypes";
import { NavCapLink } from "./NavCapLink";

export function ShellLayout({
  profile,
  showHealth,
  children,
}: {
  profile: MeResponse | null;
  showHealth: boolean;
  children: React.ReactNode;
}): React.ReactElement {
  const { t } = useTranslation();
  const runtimeTarget =
    typeof window !== "undefined" && window.agentium?.getRuntimeTarget
      ? window.agentium.getRuntimeTarget()
      : "web";

  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async () => {
      const r = await apiFetch("/v1/healthz", { method: "GET" });
      return { status: r.status, text: await r.text() };
    },
    refetchInterval: showHealth ? 15_000 : false,
    enabled: showHealth,
  });

  async function exportDiagnostics(): Promise<void> {
    if (window.agentium?.exportDiagnostics) {
      const pack = await window.agentium.exportDiagnostics();
      const blob = new Blob([pack.json], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = pack.filename;
      a.click();
      URL.revokeObjectURL(a.href);
      return;
    }
    const ver = await apiFetch("/v1/version", { method: "GET" }).then((r) => r.json());
    const blob = new Blob([JSON.stringify({ target: "web", version: ver, redacted: true })], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "agentium-diagnostics-web.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden">
      <header className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
        <div className="font-semibold text-slate-800">Agentium</div>
        <div className="flex items-center gap-3 text-xs text-slate-600">
          {showHealth ? (
            <span className="rounded px-2 py-0.5" title={health.data?.text}>
              health:{" "}
              {health.isFetching ? "…" : health.data ? `${health.data.status}` : "—"}
            </span>
          ) : null}
          <span className="rounded bg-slate-100 px-2 py-0.5">{runtimeTarget}</span>
          {profile ? (
            <span title={profile.capabilities.join(", ")} className="max-w-xs truncate">
              {profile.tenant_id} / {profile.user_id} ({profile.role})
            </span>
          ) : (
            <span className="text-amber-700">no profile</span>
          )}
          {runtimeTarget === "desktop" ? (
            <button
              type="button"
              className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
              onClick={() => void exportDiagnostics()}
            >
              Export diagnostics
            </button>
          ) : null}
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="w-52 shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50 p-3 text-sm">
          <nav className="flex flex-col gap-1" aria-label="Primary">
            <NavCapLink to="/" end>
              {t("nav.overview")}
            </NavCapLink>
            <NavCapLink to="/sessions" need="runs.read">
              {t("nav.sessions")}
            </NavCapLink>
            <NavCapLink to="/approval" need="approval.read">
              {t("nav.approvals")}
            </NavCapLink>
            <NavCapLink to="/workspace" need="chat.sessions.manage">
              {t("nav.workspace")}
            </NavCapLink>
            <NavCapLink to="/tools" need="tools.read">
              {t("nav.toolsCatalog")}
            </NavCapLink>
            <NavCapLink to="/llm-wiki" need="wiki.read">
              {t("nav.llmWiki")}
            </NavCapLink>
            <NavCapLink to="/turn-debug" need="turn.execute">
              {t("nav.turnDebug")}
            </NavCapLink>
            <NavCapLink to="/budget" need="budget.read">
              {t("nav.budget")}
            </NavCapLink>
            <NavCapLink to="/scheduled-jobs" need="jobs.read">
              {t("nav.scheduledJobs")}
            </NavCapLink>
            <NavCapLink to="/policy" need="governance.policy.read">
              {t("nav.policy")}
            </NavCapLink>
            <NavCapLink to="/settings">{t("nav.settings")}</NavCapLink>
            <NavCapLink to="/decision-chain" need="audit.read">
              {t("nav.decisionChain")}
            </NavCapLink>
            <NavCapLink to="/background" need="background.read">
              {t("nav.background")}
            </NavCapLink>
            <NavCapLink to="/deep-research" need="research.run">
              {t("nav.deepResearch")}
            </NavCapLink>
            <NavCapLink to="/workflow-ide" need="workflow.read">
              {t("nav.workflowIde")}
            </NavCapLink>
            <NavCapLink to="/coordination" need="artifacts.read">
              {t("nav.coordination")}
            </NavCapLink>
            <NavCapLink to="/eval" need="eval.run">
              {t("nav.eval")}
            </NavCapLink>
            <NavCapLink to="/security-compliance" need="security.events.read">
              {t("nav.compliance")}
            </NavCapLink>
            <NavCapLink to="/connectors" need="connectors.read">
              {t("nav.connectors")}
            </NavCapLink>
            <NavCapLink to="/governance">{t("nav.governance")}</NavCapLink>
          </nav>
        </aside>
        <main className="min-h-0 flex-1 overflow-y-auto p-4">{children}</main>
      </div>
    </div>
  );
}
