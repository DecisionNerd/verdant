export type Dataset = { id: string; namespace_id: string; owner_id: string; revision: number; slug: string; name: string; modality: string; draft_id: string; created_at: string };
export type Page<T> = { items: T[]; next_after: string | null; total?: number };
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function request<T>(path: string, csrf: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store",
    ...(body === undefined ? {} : { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(body) }) });
  if (!response.ok) throw new ApiError(response.status,
    response.status === 401 ? "Your session expired. Sign in again." : response.status === 403 ? "You do not have permission for this workspace." : response.status === 409 ? "This record changed. Reload the current revision; your input is preserved." : "The request failed. Retry when the service is available.");
  return response.json() as Promise<T>;
}
