import { useConnectionStore } from "../connection/connectionStore";

export function apiPath(path: string): string {
  const envBase = import.meta.env.VITE_API_BASE_URL?.trim() ?? "";
  const stored = useConnectionStore.getState().apiBaseUrl.trim().replace(/\/$/, "");
  const base = stored || envBase.replace(/\/$/, "");
  if (base) {
    return `${base}${path}`;
  }
  return path;
}

/** Merge connection identity into request headers (no secrets in query string). */
export function buildAuthHeaders(): Record<string, string> {
  const s = useConnectionStore.getState();
  const h: Record<string, string> = {};

  if (s.identityMode === "bearer") {
    const t = s.bearerToken.trim();
    if (t) {
      h.Authorization = `Bearer ${t}`;
    }
    return h;
  }

  if (s.tenantId.trim()) {
    h["X-Tenant-Id"] = s.tenantId.trim();
  }
  if (s.userId.trim()) {
    h["X-User-Id"] = s.userId.trim();
  }
  const role = s.role.trim() || "user";
  h["X-Role"] = role;
  return h;
}

export async function apiFetch(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const url = apiPath(path);
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body != null) {
    headers.set("Content-Type", "application/json");
  }
  for (const [k, v] of Object.entries(buildAuthHeaders())) {
    headers.set(k, v);
  }
  return fetch(url, { ...init, headers });
}
