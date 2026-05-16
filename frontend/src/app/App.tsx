import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { canQueryProfile } from "../core/connection/connectionStore";
import { fetchMe } from "../core/profile/profileApi";
import { useProfileStore } from "../core/profile/profileStore";
import { ApprovalPage } from "../modules/approval/ApprovalPage";
import { BackgroundPage } from "../modules/background/BackgroundPage";
import { BudgetPage } from "../modules/budget/BudgetPage";
import { ScheduledJobsPage } from "../modules/scheduled-jobs/ScheduledJobsPage";
import { ConnectorsPage } from "../modules/connectors/ConnectorsPage";
import { CoordinationPage } from "../modules/coordination/CoordinationPage";
import { DecisionChainPage } from "../modules/decision-chain/DecisionChainPage";
import { DeepResearchPage } from "../modules/deep-research/DeepResearchPage";
import { DegradedPage } from "../modules/degraded/DegradedPage";
import { EvalPage } from "../modules/eval/EvalPage";
import { GovernancePage } from "../modules/governance/GovernancePage";
import { LlmWikiPage } from "../modules/llm-wiki/LlmWikiPage";
import { OverviewPage } from "../modules/overview/OverviewPage";
import { PolicyPage } from "../modules/policy/PolicyPage";
import { RunDetailPage } from "../modules/run-detail/RunDetailPage";
import { SecurityCompliancePage } from "../modules/security-compliance/SecurityCompliancePage";
import { SessionsPage } from "../modules/sessions/SessionsPage";
import { ShellLayout } from "../modules/shell/ShellLayout";
import { SettingsPage } from "../modules/settings/SettingsPage";
import { ToolCatalogPage } from "../modules/tools/ToolCatalogPage";
import { TurnDebugPage } from "../modules/turn-debug/TurnDebugPage";
import { WorkflowIdePage } from "../modules/workflow-ide/WorkflowIdePage";
import { WorkspacePage } from "../modules/workspace/WorkspacePage";

export function App(): React.ReactElement {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<OverviewPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="approval" element={<ApprovalPage />} />
        <Route path="workspace" element={<WorkspacePage />} />
        <Route path="turn-debug" element={<TurnDebugPage />} />
        <Route path="tools" element={<ToolCatalogPage />} />
        <Route path="llm-wiki" element={<LlmWikiPage />} />
        <Route path="budget" element={<BudgetPage />} />
        <Route path="scheduled-jobs" element={<ScheduledJobsPage />} />
        <Route path="policy" element={<PolicyPage />} />
        <Route path="decision-chain" element={<DecisionChainPage />} />
        <Route path="background" element={<BackgroundPage />} />
        <Route path="deep-research" element={<DeepResearchPage />} />
        <Route path="workflow-ide" element={<WorkflowIdePage />} />
        <Route path="coordination" element={<CoordinationPage />} />
        <Route path="eval" element={<EvalPage />} />
        <Route path="security-compliance" element={<SecurityCompliancePage />} />
        <Route path="connectors" element={<ConnectorsPage />} />
        <Route path="governance" element={<GovernancePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function AppLayout(): React.ReactElement {
  const { pathname } = useLocation();
  const onSettings = pathname === "/settings";
  const ready = canQueryProfile();
  const setProfile = useProfileStore((s) => s.setProfile);

  const me = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    enabled: ready,
  });

  React.useEffect(() => {
    if (me.data) {
      setProfile(me.data);
    }
    if (me.isError) {
      setProfile(null);
    }
  }, [me.data, me.isError, setProfile]);

  if (!ready) {
    if (onSettings) {
      return (
        <ShellLayout profile={null} showHealth={false}>
          <Outlet />
        </ShellLayout>
      );
    }
    return (
      <DegradedPage
        title="Connection not configured"
        detail="Set tenant and user (header mode) or bearer token in Settings."
      />
    );
  }

  if (me.isPending && !onSettings) {
    return <div className="p-8 text-slate-600">Loading profile…</div>;
  }

  if (me.isError && !onSettings) {
    const msg = me.error instanceof Error ? me.error.message : String(me.error);
    return <DegradedPage title="Profile unavailable" detail={msg} />;
  }

  return (
    <ShellLayout profile={me.data ?? null} showHealth>
      <Outlet />
    </ShellLayout>
  );
}
