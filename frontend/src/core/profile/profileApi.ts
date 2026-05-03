import { apiFetch } from "../http/client";
import type { MeResponse } from "./profileTypes";

export async function fetchMe(): Promise<MeResponse> {
  const res = await apiFetch("/v1/me", { method: "GET" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GET /v1/me ${res.status}: ${text}`);
  }
  return res.json() as Promise<MeResponse>;
}
