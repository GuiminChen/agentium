/** Interpolate `{run_id}` / `{trace_id}` (and other keys) into observability base URLs. */

export function expandObservabilityUrl(
  template: string,
  vars: Record<string, string | undefined>,
): string {
  let out = template;
  for (const [key, val] of Object.entries(vars)) {
    if (val === undefined) {
      continue;
    }
    out = out.split(`{${key}}`).join(encodeURIComponent(val));
  }
  return out;
}
