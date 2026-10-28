import { ApiError, request } from "./api";

export type UploadItem = {
  id: string; source_key: string; modality: string; sha256: string; size_bytes: number;
  state: string; error: string | null; sample_id: string | null; accepted_revision_id: string | null;
  replacement_operation_id: string | null; retry_of_item_id: string | null;
};
export type UploadBatch = {
  operation: { id: string; epoch: number; status: string }; items: UploadItem[];
  cleanup_operation_id: string | null;
};
export type UploadLimits = { configured: boolean; max_artifact_bytes: number; max_items: number; part_bytes: number };
export type Declaration = {
  source_key: string; modality: string; sha256: string; size_bytes: number; metadata: Record<string, unknown>;
  target_sample_id?: string; expected_revision?: number;
};
export async function fileDigest(file: Blob): Promise<string> {
  if (!crypto.subtle) throw new Error("A secure browser origin is required to calculate file digests.");
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function transferUploads(namespace: string, csrf: string, id: string,
  files: Map<string, File>, limits: UploadLimits, signal: AbortSignal, progress: (message: string) => void,
): Promise<UploadBatch> {
  const base = `/api/v1/namespaces/${namespace}/ingestions/${id}`;
  const batch = await request<UploadBatch>(base, csrf);
  signal.throwIfAborted();
  if (batch.operation.status !== "running") throw new Error("This operation cannot receive uploads. Inspect its current status.");
  const epoch = batch.operation.epoch;
  // Hash one bounded file at a time. Browser-selected names never authorize server locations.
  const byDigest = new Map<string, File>();
  for (const file of files.values()) {
    signal.throwIfAborted();
    if (file.size > limits.max_artifact_bytes) throw new Error("A selected file exceeds the upload limit.");
    const digest = await fileDigest(file);
    signal.throwIfAborted();
    byDigest.set(`${digest}:${file.size}`, file);
  }
  const pending = batch.items.filter(item => ["awaiting_upload", "uploading"].includes(item.state));
  // Check every selected input before issuing any provider write in this invocation.
  for (const item of pending) {
    if (!byDigest.has(`${item.sha256}:${item.size_bytes}`)) throw new Error(`Reselect the original bytes for ${item.source_key}. Digest and size must match.`);
  }
  for (const item of pending) {
    signal.throwIfAborted();
    const file = byDigest.get(`${item.sha256}:${item.size_bytes}`)!;
    const path = `${base}/items/${item.id}`;
    await request(`${path}/initiate`, csrf, { epoch });
    signal.throwIfAborted();
    let uploaded: { parts: { part_number: number; size_bytes: number }[] };
    try {
      uploaded = await request(`${path}/parts?epoch=${epoch}`, csrf);
    } catch (failure) {
      signal.throwIfAborted();
      if (!(failure instanceof ApiError) || failure.status !== 404) throw failure;
      // A completed native upload disappears before the DB-ready commit can be observed.
      // Reconcile completion against staging facts rather than recreate or blindly write.
      await request(`${path}/complete`, csrf, { epoch });
      continue;
    }
    const count = Math.max(1, Math.ceil(file.size / limits.part_bytes));
    for (let part = 1; part <= count; part++) {
      signal.throwIfAborted();
      const start = (part - 1) * limits.part_bytes, end = Math.min(file.size, start + limits.part_bytes);
      if (uploaded.parts.some(value => value.part_number === part && value.size_bytes === end - start)) continue;
      progress(`${item.source_key}: uploading part ${part} of ${count}`);
      const signed = await request<{ url: string; size_bytes: number }>(`${path}/sign-part`, csrf, { epoch, part_number: part });
      signal.throwIfAborted();
      if (signed.size_bytes !== end - start) throw new Error("Signed part differs from the declared file size.");
      const response = await fetch(signed.url, { method: "PUT", body: file.slice(start, end), credentials: "omit", redirect: "error", cache: "no-store", signal });
      if (!response.ok) throw new Error("Object storage rejected a part. Resume to refresh signing and discover uploaded parts.");
    }
    signal.throwIfAborted();
    await request(`${path}/complete`, csrf, { epoch });
  }
  signal.throwIfAborted();
  return request<UploadBatch>(base, csrf);
}
