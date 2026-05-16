import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CapabilityGuard } from "../../core/featureFlags/capabilityGuard";
import { useHasCapability } from "../../core/featureFlags/useHasCapability";
import { apiFetch } from "../../core/http/client";

type JobRow = {
  job_id: string;
  name: string;
  enabled: boolean;
  task_kind: string;
  trigger: Record<string, unknown>;
  session_binding: string;
  pinned_session_id?: string | null;
  payload?: Record<string, unknown>;
  budget_estimate_tokens?: number | null;
  next_run_at_unix_ms?: number | null;
  last_run_at_unix_ms?: number | null;
};

type RunRow = {
  run_id: string;
  status: string;
  trace_id: string;
  session_id?: string | null;
  error_detail?: string | null;
  started_at: string;
  finished_at?: string | null;
};

type TriggerKind = "interval" | "one_shot" | "cron";

function coerceTriggerKind(raw: unknown): TriggerKind {
  return raw === "cron" || raw === "one_shot" || raw === "interval" ? raw : "interval";
}

function buildTriggerPayload(
  kind: TriggerKind,
  intervalSec: number,
  cronExpr: string,
  oneShotMs: string,
): Record<string, unknown> {
  if (kind === "interval") {
    return { kind: "interval", interval_seconds: Math.max(60, intervalSec) };
  }
  if (kind === "one_shot") {
    const ms = Number.parseInt(oneShotMs, 10);
    return { kind: "one_shot", run_at_unix_ms: Number.isFinite(ms) ? ms : Date.now() + 60_000 };
  }
  return { kind: "cron", cron_expression: cronExpr.trim() || "0 * * * *" };
}

export function ScheduledJobsPage(): React.ReactElement {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const canRead = useHasCapability("jobs.read");
  const canTrigger = useHasCapability("jobs.trigger");
  const canManage = useHasCapability("jobs.manage");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  const [createName, setCreateName] = React.useState("");
  const [createTriggerKind, setCreateTriggerKind] = React.useState<TriggerKind>("interval");
  const [createIntervalSec, setCreateIntervalSec] = React.useState(120);
  const [createCronExpr, setCreateCronExpr] = React.useState("0 * * * *");
  const [createOneShotMs, setCreateOneShotMs] = React.useState(String(Date.now() + 120_000));
  const [createBinding, setCreateBinding] = React.useState<string>("named_persistent");
  const [createPinnedId, setCreatePinnedId] = React.useState("");
  const [createMessage, setCreateMessage] = React.useState("");
  const [createBudgetTok, setCreateBudgetTok] = React.useState("");

  const jobsQ = useQuery({
    queryKey: ["scheduled-jobs"],
    queryFn: async () => {
      const r = await apiFetch("/v1/jobs?page=1&page_size=50", { method: "GET" });
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json() as Promise<{ items: JobRow[] }>;
    },
    enabled: canRead,
  });

  const runsQ = useQuery({
    queryKey: ["scheduled-job-runs", selectedId],
    queryFn: async () => {
      const r = await apiFetch(
        `/v1/jobs/${encodeURIComponent(selectedId ?? "")}/runs?page=1&page_size=30`,
        { method: "GET" },
      );
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json() as Promise<{ items: RunRow[] }>;
    },
    enabled: Boolean(selectedId) && canRead,
  });

  const triggerMut = useMutation({
    mutationFn: async (jobId: string) => {
      const r = await apiFetch(`/v1/jobs/${encodeURIComponent(jobId)}/trigger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok && r.status !== 202) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json().catch(() => ({}));
    },
    onSuccess: async (_data, jobId) => {
      await qc.invalidateQueries({ queryKey: ["scheduled-jobs"] });
      await qc.invalidateQueries({ queryKey: ["scheduled-job-runs", jobId] });
    },
  });

  const createMut = useMutation({
    mutationFn: async () => {
      const trig = buildTriggerPayload(
        createTriggerKind,
        createIntervalSec,
        createCronExpr,
        createOneShotMs,
      );
      const budgetRaw = createBudgetTok.trim();
      const budget_estimate_tokens =
        budgetRaw === "" ? undefined : Math.max(0, Number.parseInt(budgetRaw, 10) || 0);
      const body: Record<string, unknown> = {
        name: createName.trim() || "job",
        enabled: true,
        task_kind: "chat_turn",
        trigger: trig,
        session_binding: createBinding,
        payload: {
          message_content: createMessage.trim() || " ",
        },
      };
      if (createBinding === "pinned_session") {
        body.pinned_session_id = createPinnedId.trim();
      }
      if (budget_estimate_tokens !== undefined) {
        body.budget_estimate_tokens = budget_estimate_tokens;
      }
      const r = await apiFetch("/v1/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok && r.status !== 201) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json() as Promise<{ job_id: string }>;
    },
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: ["scheduled-jobs"] });
      setSelectedId(data.job_id);
      setCreateName("");
      setCreateMessage("");
    },
  });

  const patchMut = useMutation({
    mutationFn: async (payload: { job_id: string; body: Record<string, unknown> }) => {
      const r = await apiFetch(`/v1/jobs/${encodeURIComponent(payload.job_id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload.body),
      });
      if (!r.ok) {
        throw new Error(`${r.status} ${await r.text()}`);
      }
      return r.json();
    },
    onSuccess: async (_data, variables) => {
      await qc.invalidateQueries({ queryKey: ["scheduled-jobs"] });
      await qc.invalidateQueries({ queryKey: ["scheduled-job-runs", variables.job_id] });
    },
  });

  const selectedJobResolved = React.useMemo(
    () => (jobsQ.data?.items ?? []).find((j) => j.job_id === selectedId) ?? null,
    [jobsQ.data?.items, selectedId],
  );

  const [editName, setEditName] = React.useState("");
  const [editEnabled, setEditEnabled] = React.useState(true);
  const [editTriggerKind, setEditTriggerKind] = React.useState<TriggerKind>("interval");
  const [editIntervalSec, setEditIntervalSec] = React.useState(120);
  const [editCronExpr, setEditCronExpr] = React.useState("0 * * * *");
  const [editOneShotMs, setEditOneShotMs] = React.useState(String(Date.now() + 120_000));
  const [editBinding, setEditBinding] = React.useState<string>("named_persistent");
  const [editPinnedId, setEditPinnedId] = React.useState("");
  const [editMessage, setEditMessage] = React.useState("");
  const [editBudgetTok, setEditBudgetTok] = React.useState("");

  React.useEffect(() => {
    if (!selectedJobResolved) {
      return;
    }
    setEditName(selectedJobResolved.name);
    setEditEnabled(selectedJobResolved.enabled);
    const trig = selectedJobResolved.trigger ?? {};
    setEditTriggerKind(coerceTriggerKind(trig.kind));
    setEditIntervalSec(
      typeof trig.interval_seconds === "number" ? trig.interval_seconds : 120,
    );
    setEditCronExpr(typeof trig.cron_expression === "string" ? trig.cron_expression : "0 * * * *");
    setEditOneShotMs(
      typeof trig.run_at_unix_ms === "number" ? String(trig.run_at_unix_ms) : String(Date.now() + 120_000),
    );
    setEditBinding(selectedJobResolved.session_binding || "named_persistent");
    setEditPinnedId((selectedJobResolved.pinned_session_id ?? "").trim());
    const mc = selectedJobResolved.payload?.message_content;
    setEditMessage(typeof mc === "string" ? mc : "");
    const bud = selectedJobResolved.budget_estimate_tokens;
    setEditBudgetTok(bud != null && Number.isFinite(Number(bud)) ? String(bud) : "");
  }, [selectedJobResolved]);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-slate-800">{t("scheduledJobs.title")}</h1>
      <p className="text-xs text-slate-600">{t("scheduledJobs.hint")}</p>
      {canManage ? (
        <CapabilityGuard need="jobs.manage" label={t("scheduledJobs.createSection")}>
          <div className="rounded border border-slate-200 bg-white p-3 text-sm">
            <div className="mb-2 text-xs font-semibold text-slate-700">{t("scheduledJobs.createSection")}</div>
            <div className="grid gap-2 md:grid-cols-2">
              <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                {t("scheduledJobs.formName")}
                <input
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                {t("scheduledJobs.formTriggerKind")}
                <select
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  value={createTriggerKind}
                  onChange={(e) => setCreateTriggerKind(e.target.value as TriggerKind)}
                >
                  <option value="interval">{t("scheduledJobs.triggerInterval")}</option>
                  <option value="one_shot">{t("scheduledJobs.triggerOneShot")}</option>
                  <option value="cron">{t("scheduledJobs.triggerCron")}</option>
                </select>
              </label>
              {createTriggerKind === "interval" ? (
                <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                  {t("scheduledJobs.formIntervalSec")}
                  <input
                    type="number"
                    min={60}
                    className="rounded border border-slate-300 px-2 py-1 text-sm"
                    value={createIntervalSec}
                    onChange={(e) => setCreateIntervalSec(Number(e.target.value))}
                  />
                </label>
              ) : null}
              {createTriggerKind === "cron" ? (
                <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                  {t("scheduledJobs.formCronExpr")}
                  <input
                    className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                    value={createCronExpr}
                    onChange={(e) => setCreateCronExpr(e.target.value)}
                  />
                </label>
              ) : null}
              {createTriggerKind === "one_shot" ? (
                <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                  {t("scheduledJobs.formOneShotMs")}
                  <input
                    className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                    value={createOneShotMs}
                    onChange={(e) => setCreateOneShotMs(e.target.value)}
                  />
                </label>
              ) : null}
              <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                {t("scheduledJobs.formSessionBinding")}
                <select
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  value={createBinding}
                  onChange={(e) => setCreateBinding(e.target.value)}
                >
                  <option value="named_persistent">named_persistent</option>
                  <option value="pinned_session">pinned_session</option>
                  <option value="fresh_each_run">fresh_each_run</option>
                </select>
              </label>
              {createBinding === "pinned_session" ? (
                <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                  {t("scheduledJobs.formPinnedSessionId")}
                  <input
                    className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                    value={createPinnedId}
                    onChange={(e) => setCreatePinnedId(e.target.value)}
                  />
                </label>
              ) : null}
              <label className="flex flex-col gap-1 text-[11px] text-slate-600 md:col-span-2">
                {t("scheduledJobs.formMessage")}
                <textarea
                  className="min-h-[72px] rounded border border-slate-300 px-2 py-1 text-sm"
                  value={createMessage}
                  onChange={(e) => setCreateMessage(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                {t("scheduledJobs.formBudgetTokens")}
                <input
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  placeholder={t("scheduledJobs.formBudgetTokensHint")}
                  value={createBudgetTok}
                  onChange={(e) => setCreateBudgetTok(e.target.value)}
                />
              </label>
            </div>
            <button
              type="button"
              className="mt-3 rounded bg-slate-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-900 disabled:opacity-50"
              disabled={createMut.isPending}
              onClick={() => void createMut.mutateAsync()}
            >
              {t("scheduledJobs.createSubmit")}
            </button>
            {createMut.isError ? (
              <div className="mt-2 text-xs text-red-700">
                {createMut.error instanceof Error ? createMut.error.message : String(createMut.error)}
              </div>
            ) : null}
          </div>
        </CapabilityGuard>
      ) : null}
      <CapabilityGuard need="jobs.read" label={t("scheduledJobs.title")}>
        {jobsQ.isLoading ? <div>{t("common.loading")}</div> : null}
        {jobsQ.isError ? (
          <div className="text-sm text-red-700">
            {jobsQ.error instanceof Error ? jobsQ.error.message : String(jobsQ.error)}
          </div>
        ) : null}
        {jobsQ.data ? (
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="rounded border border-slate-200 bg-white lg:col-span-1">
              <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">
                {t("scheduledJobs.jobList")}
              </div>
              <ul className="max-h-[480px] divide-y divide-slate-100 overflow-y-auto text-sm">
                {jobsQ.data.items.length === 0 ? (
                  <li className="px-3 py-4 text-slate-500">{t("scheduledJobs.empty")}</li>
                ) : (
                  jobsQ.data.items.map((j) => (
                    <li key={j.job_id}>
                      <div
                        className={`flex flex-col gap-1 px-3 py-2 hover:bg-slate-50 ${
                          selectedId === j.job_id ? "bg-blue-50/80" : ""
                        }`}
                      >
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => setSelectedId(j.job_id)}
                        >
                          <span className="font-medium text-slate-800">{j.name}</span>
                          <span className="block text-[11px] text-slate-500">
                            {j.job_id} · {j.enabled ? "on" : "off"} · {j.session_binding}
                          </span>
                        </button>
                        {canTrigger ? (
                          <button
                            type="button"
                            className="self-start rounded border border-slate-300 px-2 py-0.5 text-[11px] hover:bg-slate-100"
                            disabled={triggerMut.isPending}
                            onClick={() => void triggerMut.mutateAsync(j.job_id)}
                          >
                            {t("scheduledJobs.triggerNow")}
                          </button>
                        ) : null}
                      </div>
                    </li>
                  ))
                )}
              </ul>
            </div>
            <div className="rounded border border-slate-200 bg-white lg:col-span-1">
              <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold text-slate-700">
                {t("scheduledJobs.runHistory")}
              </div>
              {!selectedId ? (
                <div className="px-3 py-4 text-sm text-slate-500">{t("scheduledJobs.selectJob")}</div>
              ) : runsQ.isLoading ? (
                <div className="px-3 py-4">{t("common.loading")}</div>
              ) : runsQ.isError ? (
                <div className="px-3 py-4 text-sm text-red-700">
                  {runsQ.error instanceof Error ? runsQ.error.message : String(runsQ.error)}
                </div>
              ) : (
                <ul className="max-h-[480px] divide-y divide-slate-100 overflow-y-auto text-xs">
                  {(runsQ.data?.items ?? []).length === 0 ? (
                    <li className="px-3 py-4 text-slate-500">{t("scheduledJobs.noRuns")}</li>
                  ) : (
                    (runsQ.data?.items ?? []).map((r) => (
                      <li key={r.run_id} className="px-3 py-2">
                        <div className="font-medium text-slate-800">{r.status}</div>
                        <div className="text-slate-600">
                          <Link className="text-blue-700 underline" to={`/runs/${encodeURIComponent(r.run_id)}`}>
                            run {r.run_id}
                          </Link>
                          {r.trace_id ? <> · trace {r.trace_id}</> : null}
                          {r.session_id ? <> · session {r.session_id}</> : null}
                        </div>
                        {r.error_detail ? (
                          <pre className="mt-1 whitespace-pre-wrap text-[11px] text-red-800">{r.error_detail}</pre>
                        ) : null}
                        <div className="text-[10px] text-slate-400">
                          {r.started_at}
                          {r.finished_at ? ` → ${r.finished_at}` : ""}
                        </div>
                      </li>
                    ))
                  )}
                </ul>
              )}
            </div>
            {canManage && selectedJobResolved ? (
              <CapabilityGuard need="jobs.manage" label={t("scheduledJobs.editSection")}>
                <div className="rounded border border-slate-200 bg-white p-3 text-sm lg:col-span-1">
                  <div className="mb-2 text-xs font-semibold text-slate-700">{t("scheduledJobs.editSection")}</div>
                  <p className="mb-2 text-[11px] text-slate-500">{t("scheduledJobs.editFullHint")}</p>
                  <div className="grid gap-2 md:grid-cols-2">
                    <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                      {t("scheduledJobs.formName")}
                      <input
                        className="rounded border border-slate-300 px-2 py-1 text-sm"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                      {t("scheduledJobs.formTriggerKind")}
                      <select
                        className="rounded border border-slate-300 px-2 py-1 text-sm"
                        value={editTriggerKind}
                        onChange={(e) => setEditTriggerKind(e.target.value as TriggerKind)}
                      >
                        <option value="interval">{t("scheduledJobs.triggerInterval")}</option>
                        <option value="one_shot">{t("scheduledJobs.triggerOneShot")}</option>
                        <option value="cron">{t("scheduledJobs.triggerCron")}</option>
                      </select>
                    </label>
                    {editTriggerKind === "interval" ? (
                      <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                        {t("scheduledJobs.formIntervalSec")}
                        <input
                          type="number"
                          min={60}
                          className="rounded border border-slate-300 px-2 py-1 text-sm"
                          value={editIntervalSec}
                          onChange={(e) => setEditIntervalSec(Number(e.target.value))}
                        />
                      </label>
                    ) : null}
                    {editTriggerKind === "cron" ? (
                      <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                        {t("scheduledJobs.formCronExpr")}
                        <input
                          className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                          value={editCronExpr}
                          onChange={(e) => setEditCronExpr(e.target.value)}
                        />
                      </label>
                    ) : null}
                    {editTriggerKind === "one_shot" ? (
                      <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                        {t("scheduledJobs.formOneShotMs")}
                        <input
                          className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                          value={editOneShotMs}
                          onChange={(e) => setEditOneShotMs(e.target.value)}
                        />
                      </label>
                    ) : null}
                    <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                      {t("scheduledJobs.formSessionBinding")}
                      <select
                        className="rounded border border-slate-300 px-2 py-1 text-sm"
                        value={editBinding}
                        onChange={(e) => setEditBinding(e.target.value)}
                      >
                        <option value="named_persistent">named_persistent</option>
                        <option value="pinned_session">pinned_session</option>
                        <option value="fresh_each_run">fresh_each_run</option>
                      </select>
                    </label>
                    {editBinding === "pinned_session" ? (
                      <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                        {t("scheduledJobs.formPinnedSessionId")}
                        <input
                          className="rounded border border-slate-300 px-2 py-1 font-mono text-sm"
                          value={editPinnedId}
                          onChange={(e) => setEditPinnedId(e.target.value)}
                        />
                      </label>
                    ) : null}
                    <label className="flex flex-col gap-1 text-[11px] text-slate-600 md:col-span-2">
                      {t("scheduledJobs.formMessage")}
                      <textarea
                        className="min-h-[72px] rounded border border-slate-300 px-2 py-1 text-sm"
                        value={editMessage}
                        onChange={(e) => setEditMessage(e.target.value)}
                      />
                    </label>
                    <label className="flex flex-col gap-1 text-[11px] text-slate-600">
                      {t("scheduledJobs.formBudgetTokens")}
                      <input
                        className="rounded border border-slate-300 px-2 py-1 text-sm"
                        placeholder={t("scheduledJobs.formBudgetTokensHint")}
                        value={editBudgetTok}
                        onChange={(e) => setEditBudgetTok(e.target.value)}
                      />
                    </label>
                    <label className="flex items-center gap-2 text-[11px] text-slate-600 md:col-span-2">
                      <input
                        type="checkbox"
                        checked={editEnabled}
                        onChange={(e) => setEditEnabled(e.target.checked)}
                      />
                      {t("scheduledJobs.enabledLabel")}
                    </label>
                  </div>
                  <button
                    type="button"
                    className="mt-3 rounded border border-slate-400 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
                    disabled={patchMut.isPending}
                    onClick={() => {
                      const trig = buildTriggerPayload(
                        editTriggerKind,
                        editIntervalSec,
                        editCronExpr,
                        editOneShotMs,
                      );
                      const budgetRaw = editBudgetTok.trim();
                      const payloadBase =
                        selectedJobResolved.payload &&
                        typeof selectedJobResolved.payload === "object" &&
                        !Array.isArray(selectedJobResolved.payload)
                          ? { ...selectedJobResolved.payload }
                          : {};
                      const body: Record<string, unknown> = {
                        name: editName.trim() || selectedJobResolved.name,
                        enabled: editEnabled,
                        trigger: trig,
                        session_binding: editBinding,
                        payload: {
                          ...payloadBase,
                          message_content: editMessage.trim() || " ",
                        },
                      };
                      if (editBinding === "pinned_session") {
                        body.pinned_session_id = editPinnedId.trim();
                      } else {
                        body.pinned_session_id = null;
                      }
                      body.budget_estimate_tokens =
                        budgetRaw === "" ? null : Math.max(0, Number.parseInt(budgetRaw, 10) || 0);
                      void patchMut.mutateAsync({
                        job_id: selectedJobResolved.job_id,
                        body,
                      });
                    }}
                  >
                    {t("scheduledJobs.saveEdit")}
                  </button>
                  {patchMut.isError ? (
                    <div className="mt-2 text-xs text-red-700">
                      {patchMut.error instanceof Error ? patchMut.error.message : String(patchMut.error)}
                    </div>
                  ) : null}
                </div>
              </CapabilityGuard>
            ) : null}
          </div>
        ) : null}
      </CapabilityGuard>
    </div>
  );
}
