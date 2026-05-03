/** Build a readable message from control-plane JSON error bodies. */

export async function readApiError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const j = JSON.parse(text) as {
      error?: string;
      message?: string;
      detail?: unknown;
    };
    const head = [j.error, j.message].filter(Boolean).join(": ");
    let extra = "";
    if (j.detail !== undefined && j.detail !== null && j.detail !== "") {
      extra =
        typeof j.detail === "string"
          ? `\n${j.detail}`
          : `\n${JSON.stringify(j.detail, null, 0)}`;
    }
    return (head || text) + extra;
  } catch {
    return text;
  }
}
