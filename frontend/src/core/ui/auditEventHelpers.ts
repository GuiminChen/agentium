export function auditEventTraceId(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return null;
  }
  const pl = payload as Record<string, unknown>;
  const tid = pl.trace_id;
  return typeof tid === "string" && tid ? tid : null;
}

export function auditEventToolUseId(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return null;
  }
  const pl = payload as Record<string, unknown>;
  const t = pl.tool_use_id;
  return typeof t === "string" && t ? t : null;
}
