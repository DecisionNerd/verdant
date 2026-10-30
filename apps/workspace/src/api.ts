export type Dataset = { id: string; namespace_id: string; owner_id: string; revision: number; slug: string; name: string; modality: string; draft_id: string; created_at: string; availability: "active" | "archived" };
export type Page<T> = { items: T[]; next_after: string | null; total?: number };
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function request<T>(path: string, csrf: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store",
    ...(body === undefined ? {} : { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body) }) });
  if (!response.ok) {
    let message = response.status === 401 ? "Your session expired. Sign in again." : response.status === 403 ? "You do not have permission for this workspace." : response.status === 404 ? "The requested record or artifact bytes are missing or inaccessible." : response.status === 409 ? "This record changed. Reload the current revision; your input is preserved." : "The request failed. Retry when the service is available.";
    if (response.status === 404 || response.status === 409) {
      try {
        const payload: unknown = await response.json();
        if (payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string" && payload.detail.length <= 500) message = payload.detail;
      } catch { /* Use the safe fallback if the server returned an unreadable error body. */ }
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}
