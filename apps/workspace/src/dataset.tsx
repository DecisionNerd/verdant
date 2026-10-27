import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, type Dataset } from "./catalog";

export function DatasetView({ namespace, id, csrf, writable }: { namespace: string; id: string; csrf: string; writable: boolean }) {
  const query = useQuery({ queryKey: ["dataset", namespace, id], queryFn: () => request<Dataset>(`/api/v1/namespaces/${namespace}/datasets/${id}`, csrf) });
  if (query.isPending) return <p role="status">Loading dataset…</p>;
  if (query.isError) return <p role="alert">{query.error.message} <button onClick={() => void query.refetch()}>Retry</button></p>;
  return <MetadataEditor key={id} dataset={query.data} csrf={csrf} writable={writable} />;
}
function MetadataEditor({ dataset, csrf, writable }: { dataset: Dataset; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const [name, setName] = useState(dataset.name);
  const [slug, setSlug] = useState(dataset.slug);
  const [modality, setModality] = useState(dataset.modality);
  const [revision, setRevision] = useState(dataset.revision);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = `/api/v1/namespaces/${dataset.namespace_id}/datasets/${dataset.id}`;
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const changed = await request<Dataset>(path, csrf, { name, slug, modality, expected_revision: revision }, "PUT");
      setRevision(changed.revision); cache.setQueryData(["dataset", dataset.namespace_id, dataset.id], changed);
      await cache.invalidateQueries({ queryKey: ["datasets", dataset.namespace_id] }); setMessage("Metadata saved.");
    } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }
  async function reload() {
    setBusy(true); setError("");
    try {
      const current = await request<Dataset>(path, csrf); setRevision(current.revision);
      cache.setQueryData(["dataset", dataset.namespace_id, dataset.id], current);
      setMessage(`Current revision ${current.revision} loaded. Your inputs are preserved. Review before saving.`);
    } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }
  return <article><h2>{dataset.name}</h2><p>Owner: <span className="identifier">{dataset.owner_id}</span><br />Metadata revision: {revision}</p>
    <form onSubmit={event => void save(event)}><fieldset disabled={!writable || busy}><legend>Catalog metadata</legend>
      <label>Name <input value={name} required maxLength={200} onChange={event => setName(event.target.value)} /></label>
      <label>Slug <input value={slug} required maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" onChange={event => setSlug(event.target.value)} /></label>
      <label>Modality <input value={modality} required maxLength={50} onChange={event => setModality(event.target.value)} /></label>
      {writable && <button>Save metadata</button>}</fieldset>
      <button type="button" disabled={busy} onClick={() => void reload()}>Load current revision</button>
    </form>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <h2>Draft</h2><p className="identifier">{dataset.draft_id}</p>
  </article>;
}
