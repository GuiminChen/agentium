import { test, expect } from "@playwright/test";

test("shell loads title", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/Agentium/i);
});
