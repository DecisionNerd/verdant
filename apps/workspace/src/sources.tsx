import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, request } from "./api";
import type { Declaration, UploadBatch, UploadLimits } from "./upload";

type SourceInput = { id: string; source_object: string; source_key: string; modality: string; sha256: string; size_bytes: string };
const newInput = (): SourceInput => ({ id: crypto.randomUUID(), source_object: "", source_key: "", modality: "text", sha256: "", size_bytes: "" });
export function SourceRegistration({ namespace, csrf, limits, select }: {
  namespace: string; csrf: string; limits: UploadLimits; select: (id: string) => void;
}) {
  const [items, setItems] = useState<SourceInput[]>(() => [newInput()]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [denied, setDenied] = useState(false);
  const pending = useRef<{ request_key: string; items: (Declaration & { source_object: string })[] } | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  function update(id: string, field: keyof SourceInput, value: string) {
    setItems(previous => previous.map(item => item.id === id ? { ...item, [field]: value } : item));
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    const current = ++generation.current;
    setBusy(true); setError("");
    try {
      if (!pending.current) {
        if (items.length > limits.max_items || new Set(items.map(item => item.source_key)).size !== items.length) throw new Error("Choose unique source identities within the displayed batch limit.");
        const declarations = items.map(item => {
          const size = Number(item.size_bytes);
          if (!item.source_object || !item.source_key || !/^[0-9a-f]{64}$/.test(item.sha256) || item.size_bytes === "" || !Number.isSafeInteger(size) || size < 0 || size > limits.max_artifact_bytes) throw new Error("Each source requires a relative key, identity, SHA-256 and supported byte size.");
          return { source_object: item.source_object, source_key: item.source_key, modality: item.modality,
            sha256: item.sha256, size_bytes: size, metadata: {} };
        });
        pending.current = { request_key: crypto.randomUUID(), items: declarations };
      }
      const result = await request<UploadBatch>(`/api/v1/namespaces/${namespace}/ingestions/sources`, csrf, pending.current);
      if (generation.current === current) { pending.current = null; setItems([newInput()]); select(result.operation.id); }
    } catch (failure) {
      if (generation.current === current) {
        setError((failure as Error).message);
        if (failure instanceof ApiError && [409, 422].includes(failure.status)) pending.current = null;
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) setDenied(true);
      }
    } finally { if (generation.current === current) setBusy(false); }
  }
  if (denied) return <p role="alert">Source registration access changed. Reload the workspace.</p>;
  return <form onSubmit={event => void submit(event)} aria-label="Register configured sources"><h3>Register existing source objects</h3>
    <p>Objects must already exist in your workspace's configured S3 source area. Enter relative object keys and expected bytes; workers verify and copy them into immutable sample artifacts. Original sources remain unchanged. URLs are not accepted.</p>
    <fieldset disabled={busy || pending.current !== null}>{items.map((item, index) => <fieldset key={item.id}><legend>Source object {index + 1}</legend>
      <label>Relative object key {index + 1}<input required maxLength={500} value={item.source_object} onChange={event => update(item.id, "source_object", event.target.value)} /></label>
      <label>Source identity {index + 1}<input required maxLength={500} value={item.source_key} onChange={event => update(item.id, "source_key", event.target.value)} /></label>
      <label>Source modality {index + 1}<select value={item.modality} onChange={event => update(item.id, "modality", event.target.value)}>{["text", "image", "document", "mixed", "audio", "video"].map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Expected SHA-256 {index + 1}<input required pattern="[0-9a-f]{64}" minLength={64} maxLength={64} value={item.sha256} onChange={event => update(item.id, "sha256", event.target.value)} /></label>
      <label>Expected bytes {index + 1}<input type="number" min="0" max={limits.max_artifact_bytes} step="1" required value={item.size_bytes} onChange={event => update(item.id, "size_bytes", event.target.value)} /></label>
      <button type="button" disabled={items.length === 1} onClick={() => setItems(previous => previous.filter(value => value.id !== item.id))}>Remove source {index + 1}</button>
    </fieldset>)}<button type="button" disabled={items.length >= limits.max_items} onClick={() => setItems(previous => [...previous, newInput()])}>Add source object</button></fieldset>
    <button disabled={busy}>{busy ? "Registering sources…" : "Start source verification"}</button>
    {pending.current && <p>Retry keeps the same source declaration and request identity. Reloaded operations remain discoverable in history.</p>}
    {error && <p role="alert">{error}</p>}
  </form>;
}
