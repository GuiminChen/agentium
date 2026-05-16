import { apiFetch } from "../../core/http/client";
import { readApiError } from "../../core/http/readApiError";

export interface WikiPageSummaryRow {
  logical_path: string;
  updated_at: string;
  content_sha256: string;
}

export interface WikiPagesResponse {
  tenant_id: string;
  items: WikiPageSummaryRow[];
  limit: number;
  offset: number;
}

export interface WikiPageDetail {
  tenant_id: string;
  logical_path: string;
  body_md: string;
  content_sha256: string;
  updated_at: string;
}

export interface WikiGraphResponse {
  tenant_id: string;
  scope: string;
  session_id?: string;
  max_pages?: number;
  nodes: Array<{ id: string; label: string; path: string }>;
  edges: Array<{ source: string; target: string }>;
}

export interface WikiSearchHit {
  logical_path?: string;
  tenant_id?: string;
  body_md?: string;
  updated_at?: string;
}

/** Response from GET /v1/wiki/ping (auth required; wiki.read optional). */
export interface WikiWirePingResponse {
  tenant_id?: string;
  service_wired: boolean;
  plugins_config_path?: string;
  plugins_llm_wiki_enabled_in_effective_settings?: boolean;
  yaml_disk_llm_wiki_enabled_if_readable?: boolean | null;
  environment_AGENTIUM_LLM_WIKI_ENABLED?: string;
  wiki_db_backend?: string;
  python_executable?: string;
  crate_import_ok?: boolean;
  crate_file?: string;
  crate_import_error?: string | null;
  postgresql_conninfo_env_expected?: boolean;
  postgresql_conninfo_env_name?: string;
  postgresql_conninfo_env_non_empty?: boolean | null;
  hints?: string[];
}

async function guardOk(r: Response): Promise<void> {
  if (!r.ok) {
    const detail = await readApiError(r);
    const err = new Error(detail) as Error & { httpStatus?: number };
    err.httpStatus = r.status;
    throw err;
  }
}

export async function fetchWikiWirePing(): Promise<WikiWirePingResponse> {
  const r = await apiFetch("/v1/wiki/ping", { method: "GET" });
  await guardOk(r);
  return (await r.json()) as WikiWirePingResponse;
}

export async function fetchWikiPages(opts: {
  prefix?: string;
  limit?: number;
  offset?: number;
}): Promise<WikiPagesResponse> {
  const sp = new URLSearchParams();
  if (opts.prefix) {
    sp.set("prefix", opts.prefix);
  }
  sp.set("limit", String(opts.limit ?? 300));
  sp.set("offset", String(opts.offset ?? 0));
  const qs = sp.toString();
  const path = qs ? `/v1/wiki/pages?${qs}` : "/v1/wiki/pages";
  const r = await apiFetch(path, { method: "GET" });
  await guardOk(r);
  return (await r.json()) as WikiPagesResponse;
}

export async function fetchWikiPage(logicalPath: string): Promise<WikiPageDetail> {
  const sp = new URLSearchParams({ path: logicalPath });
  const r = await apiFetch(`/v1/wiki/page?${sp}`, { method: "GET" });
  await guardOk(r);
  return (await r.json()) as WikiPageDetail;
}

export async function fetchWikiGraph(opts: {
  scope: "tenant" | "session";
  sessionId?: string;
  maxPages?: number;
}): Promise<WikiGraphResponse> {
  const sp = new URLSearchParams({ scope: opts.scope });
  if (opts.scope === "session") {
    if (!opts.sessionId?.trim()) {
      throw new Error("session id required");
    }
    sp.set("session_id", opts.sessionId.trim());
  }
  sp.set("max_pages", String(opts.maxPages ?? 120));
  const r = await apiFetch(`/v1/wiki/graph?${sp}`, { method: "GET" });
  await guardOk(r);
  return (await r.json()) as WikiGraphResponse;
}

export async function searchWikiLiteral(opts: {
  q: string;
  scope?: "tenant" | "session";
  sessionId?: string;
  limit?: number;
}): Promise<{ literals: WikiSearchHit[]; search_meta?: unknown } & Record<string, unknown>> {
  const sp = new URLSearchParams({ q: opts.q.trim() });
  sp.set("scope", opts.scope ?? "tenant");
  if (opts.scope === "session") {
    if (!opts.sessionId?.trim()) {
      throw new Error("session id required for session scope");
    }
    sp.set("session_id", opts.sessionId.trim());
  }
  sp.set("limit", String(opts.limit ?? 15));
  const r = await apiFetch(`/v1/wiki/search?${sp}`, { method: "GET" });
  await guardOk(r);
  return (await r.json()) as { literals: WikiSearchHit[]; search_meta?: unknown } & Record<string, unknown>;
}
