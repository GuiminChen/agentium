/** PRD §3.5-style contract checks for list/detail payloads (client-side guard). */

export type ContractIssue = { path: string; message: string };

export function validateRecordKeys(
  item: unknown,
  index: number,
  requiredKeys: string[],
): ContractIssue[] {
  const issues: ContractIssue[] = [];
  if (!item || typeof item !== "object") {
    issues.push({ path: `[${index}]`, message: "not an object" });
    return issues;
  }
  const rec = item as Record<string, unknown>;
  for (const k of requiredKeys) {
    const v = rec[k];
    if (v === undefined || v === null || v === "") {
      issues.push({ path: `[${index}].${k}`, message: "missing or empty" });
    }
  }
  return issues;
}

export function auditEventCorrelationIssues(
  events: unknown[],
  opts: { requirePayloadKeys?: string[] } = {},
): ContractIssue[] {
  const payloadKeys = opts.requirePayloadKeys ?? ["trace_id", "request_id"];
  const issues: ContractIssue[] = [];
  if (!Array.isArray(events)) {
    return [{ path: "events", message: "not an array" }];
  }
  events.forEach((ev, i) => {
    issues.push(...validateRecordKeys(ev, i, ["event_type", "timestamp", "run_id"]));
    if (!ev || typeof ev !== "object") {
      return;
    }
    const payload = (ev as Record<string, unknown>).payload;
    if (!payload || typeof payload !== "object") {
      issues.push({ path: `[${i}].payload`, message: "missing object payload" });
      return;
    }
    const p = payload as Record<string, unknown>;
    for (const k of payloadKeys) {
      const v = p[k];
      if (v === undefined || v === null || v === "") {
        issues.push({ path: `[${i}].payload.${k}`, message: "missing for correlation" });
      }
    }
  });
  return issues;
}
