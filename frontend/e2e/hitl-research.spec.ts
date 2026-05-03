import { test, expect } from "@playwright/test";

const controlPlaneURL = process.env.AGENTIUM_CONTROL_PLANE_URL ?? "http://127.0.0.1:8765";

const headers = {
  "Content-Type": "application/json",
  "X-Tenant-Id": "tenant-a",
  "X-User-Id": "user-1",
  "X-Role": "admin",
};

test.describe("HITL + research against e2e control plane", () => {
  test("approval hub shows pending tool and approve updates response", async ({ page, request }) => {
    const runId = `e2e-ui-${Date.now()}`;
    const turn = await request.post(`${controlPlaneURL}/v1/turn`, {
      headers,
      data: JSON.stringify({
        tool_name: "db_export",
        args: { dataset: "daily" },
        run_id: runId,
        request_id: `${runId}-req`,
        trace_id: `${runId}-trace`,
      }),
    });
    expect(turn.ok()).toBeTruthy();
    const turnBody = (await turn.json()) as { status: string; approval_id: string };
    expect(turnBody.status).toBe("pending_approval");
    const approvalId = turnBody.approval_id;
    expect(approvalId?.length).toBeGreaterThan(0);

    await page.goto("/approval");

    await expect(page.getByText("db_export", { exact: false })).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button").filter({ hasText: approvalId }).click();
    await page.getByRole("button", { name: "Approve" }).click();

    await expect(page.locator("pre")).toContainText(/"applied"\s*:\s*true|"status"\s*:\s*"approved"/, {
      timeout: 15_000,
    });
  });

  test("research run returns workflow snapshot (API)", async ({ request }) => {
    const runId = `e2e-rs-${Date.now()}`;
    const res = await request.post(`${controlPlaneURL}/v1/research/run`, {
      headers,
      data: JSON.stringify({
        query: "e2e stub query",
        run_id: runId,
        request_id: `${runId}-r`,
        trace_id: `${runId}-t`,
        deployment_mode: "prod",
      }),
    });
    expect(res.ok(), await res.text()).toBeTruthy();
    const body = (await res.json()) as { run_id: string; workflow?: unknown };
    expect(body.run_id).toBe(runId);

    const poll = await request.get(`${controlPlaneURL}/v1/research/${runId}`, { headers });
    expect(poll.ok(), await poll.text()).toBeTruthy();
    const snap = (await poll.json()) as { run_id?: string };
    expect(snap.run_id).toBe(runId);
  });
});
