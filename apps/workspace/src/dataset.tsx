import { Candidates } from "./candidates";
import { DraftEditor } from "./draft";
import { Versions } from "./versions";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Dataset } from "./api";

export function DatasetView({ namespace, id, csrf, writable }: { namespace: string; id: string; csrf: string; writable: boolean }) {
  const query = useQuery({ queryKey: ["dataset", namespace, id], queryFn: () => request<Dataset>(`/api/v1/namespaces/${namespace}/datasets/${id}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  if (query.isPending) return <p role="status">Loading dataset…</p>;
  if (query.isError && (!query.data || (query.error instanceof ApiError && [401,403].includes(query.error.status)))) return <p role="alert">{query.error.message} <button onClick={() => void query.refetch()}>Retry</button></p>;
  return <>{query.isError && <p role="alert">Refresh failed. Saved details may be stale; your input is preserved. <button onClick={() => void query.refetch()}>Retry</button></p>}<MetadataEditor key={`${namespace}/${id}`} dataset={query.data!} csrf={csrf} writable={writable} stale={query.isError} /></>;
}
function MetadataEditor({ dataset, csrf, writable, stale }: { dataset: Dataset; csrf: string; writable: boolean; stale: boolean }) {
  const cache = useQueryClient();
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [dataset.id]);
  const [name, setName] = useState(dataset.name);
  const [slug, setSlug] = useState(dataset.slug);
  const [modality, setModality] = useState(dataset.modality);
  const [revision, setRevision] = useState(dataset.revision);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const archived = dataset.availability === "archived";
  const path = `/api/v1/namespaces/${dataset.namespace_id}/datasets/${dataset.id}`;
  async function save(event: FormEvent) {
    event.preventDefault(); if (!writable || archived || busy || stale) return; setBusy(true); setError(""); setMessage("");
    try {
      const changed = await request<Dataset>(path, csrf, { name, slug, modality, expected_revision: revision }, "PUT");
      if (!active.current) return;
      setRevision(changed.revision); cache.setQueryData(["dataset", dataset.namespace_id, dataset.id], changed);
      await cache.invalidateQueries({ queryKey: ["datasets", dataset.namespace_id] }); setMessage("Metadata saved.");
    } catch (failure) { if (active.current) { setError((failure as Error).message); if (failure instanceof ApiError && [401,403].includes(failure.status)) setAccessDenied(true); } } finally { if (active.current) setBusy(false); }
  }
  async function reload() {
    setBusy(true); setError("");
    try {
      const current = await request<Dataset>(path, csrf); if (!active.current) return; setRevision(current.revision);
      cache.setQueryData(["dataset", dataset.namespace_id, dataset.id], current);
      setMessage(`Current revision ${current.revision} loaded. Your inputs are preserved. Review before saving.`);
    } catch (failure) { if (active.current) { setError((failure as Error).message); if (failure instanceof ApiError && [401,403].includes(failure.status)) setAccessDenied(true); } } finally { if (active.current) setBusy(false); }
  }
  async function changeAvailability() {
    if (!writable || busy || stale) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const changed = await request<Dataset>(`${path}/availability`, csrf, { action: archived ? "unarchive" : "archive", expected_revision: dataset.revision });
      if (!active.current) return;
      cache.setQueryData(["dataset", dataset.namespace_id, dataset.id], changed);
      void cache.invalidateQueries({ queryKey: ["datasets", dataset.namespace_id] });
      setMessage(`Dataset ${changed.availability}. Your metadata inputs and editing revision are preserved; load the current revision before saving them.`);
    } catch (failure) {
      if (!active.current) return;
      setError((failure as Error).message);
      if (failure instanceof ApiError && [401,403].includes(failure.status)) setAccessDenied(true);
    } finally { if (active.current) setBusy(false); }
  }
  if (accessDenied) return <p role="alert">Dataset access changed. Reload your workspace before continuing.</p>;
  return <article><h2 ref={heading} tabIndex={-1}>{dataset.name}</h2><p>Owner: <span className="identifier">{dataset.owner_id}</span><br />Metadata revision: {revision}</p>
    <p>Latest saved metadata: {dataset.name}; slug {dataset.slug}; modality {dataset.modality}.</p>
    <p>Dataset availability: {dataset.availability ?? "active"} · current saved revision {dataset.revision}.</p>
    {archived && <p role="status">This dataset is archived. New metadata, draft edits, captures, quality policies, validation and publication are paused. Exact versions and existing preparation checkpoints remain readable and resumable.</p>}
    {writable && <button disabled={busy || stale} onClick={() => void changeAvailability()}>{archived ? "Unarchive dataset" : "Archive dataset"}</button>}
    <form onSubmit={event => void save(event)}><fieldset disabled={!writable || archived || busy || stale}><legend>Catalog metadata</legend>
      <label>Name <input value={name} required maxLength={200} onChange={event => setName(event.target.value)} /></label>
      <label>Slug <input value={slug} required maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" onChange={event => setSlug(event.target.value)} /></label>
      <label>Modality <input value={modality} required maxLength={50} onChange={event => setModality(event.target.value)} /></label>
      {writable && <button>Save metadata</button>}</fieldset>
      <button type="button" disabled={busy} onClick={() => void reload()}>Load current revision</button>
    </form>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <Versions namespace={dataset.namespace_id} dataset={dataset.id} csrf={csrf} writable={writable} />
    <Candidates key={`${dataset.namespace_id}/${dataset.id}`} namespace={dataset.namespace_id} dataset={dataset.id} csrf={csrf} writable={writable} archived={archived} />
    <DraftEditor namespace={dataset.namespace_id} dataset={dataset.id} csrf={csrf} writable={writable && !archived} />
  </article>;
}
