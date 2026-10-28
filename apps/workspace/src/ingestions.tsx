import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";
import { SamplePreview } from "./preview";
import { fileDigest, transferUploads, type Declaration, type UploadBatch, type UploadItem, type UploadLimits } from "./upload";

const denied = (failure: unknown) => failure instanceof ApiError && [401, 403].includes(failure.status);
const modalities = ["text", "image", "document", "audio", "video", "mixed"];
type Choice = { file: File; source: string; modality: string };
const inferModality = (file: File) => file.type.startsWith("image/") ? "image" : file.type.startsWith("text/") ? "text" : file.type.startsWith("audio/") ? "audio" : file.type.startsWith("video/") ? "video" : "document";

export function Ingestions({ namespace, csrf, writable }: { namespace: string; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get("ingestion") ?? "");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const admission = useRef<{ request_key: string; items: Declaration[] } | null>(null);
  const generation = useRef(0);
  const files = useRef(new Map<string, File>());
  const cursor = cursors.at(-1);
  const base = `/api/v1/namespaces/${namespace}/ingestions`;
  const limits = useQuery({ queryKey: ["ingestion-limits", namespace], queryFn: () => request<UploadLimits>(`${base}/limits`, csrf) });
  const history = useQuery({ queryKey: ["ingestions", namespace, cursor], queryFn: () => request<Page<{ id: string; status: string }>>(`${base}?limit=25${cursor ? `&after=${cursor}` : ""}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  useEffect(() => {
    const update = () => setSelected(new URLSearchParams(location.search).get("ingestion") ?? "");
    window.addEventListener("popstate", update);
    return () => { generation.current++; window.removeEventListener("popstate", update); };
  }, []);
  function select(id: string, suppliedFiles?: Map<string, File>) {
    if (suppliedFiles) files.current = suppliedFiles;
    const query = new URLSearchParams({ namespace, view: "ingestions", ingestion: id });
    historyPush(query); setSelected(id);
  }
  async function admit(event: FormEvent) {
    event.preventDefault();
    if (busy || !limits.data?.configured || !choices.length) return;
    const current = ++generation.current;
    setBusy(true); setError("");
    try {
      if (!admission.current) {
        if (choices.length > limits.data.max_items || choices.some(choice => choice.file.size > limits.data!.max_artifact_bytes)) throw new Error("Selected files exceed the displayed upload bounds.");
        if (new Set(choices.map(choice => choice.source)).size !== choices.length) throw new Error("Each source identity must be unique in this batch.");
        const items: Declaration[] = [];
        for (const choice of choices) {
          const sha256 = await fileDigest(choice.file);
          if (generation.current !== current) return;
          items.push({ source_key: choice.source, modality: choice.modality, sha256, size_bytes: choice.file.size, metadata: {} });
        }
        admission.current = { request_key: crypto.randomUUID(), items };
      }
      const batch = await request<UploadBatch>(`${base}/uploads`, csrf, admission.current);
      if (generation.current !== current) return;
      files.current = new Map(choices.map(choice => [choice.source, choice.file]));
      select(batch.operation.id); setChoices([]); admission.current = null;
      await cache.invalidateQueries({ queryKey: ["ingestions", namespace] });
    } catch (failure) {
      if (generation.current === current) {
        setError((failure as Error).message);
        if (failure instanceof ApiError && [409, 422].includes(failure.status)) admission.current = null;
        if (denied(failure)) setAccessDenied(true);
      }
    } finally { if (generation.current === current) setBusy(false); }
  }
  if (accessDenied || denied(history.error) || denied(limits.error)) return <p role="alert">Workspace access changed. Reload your workspace.</p>;
  return <section aria-label="Data ingestion"><h2>Ingest data</h2>
    <p>Upload original files, then verify bytes before registering sample revisions. Accepted samples can be added to dataset drafts.</p>
    {limits.isPending ? <p role="status">Loading upload limits…</p> : limits.isError ? <p role="alert">{limits.error.message}</p> : <>
      <p>At most {limits.data.max_items} files per batch, {limits.data.max_artifact_bytes / 1024 / 1024} MiB each. Parts: {limits.data.part_bytes / 1024 / 1024} MiB. Files upload one at a time.</p>
      {!limits.data.configured && <p role="alert">Object storage is not configured. Existing history remains available.</p>}
      {writable && limits.data.configured && <form onSubmit={event => void admit(event)}><h3>New upload</h3>
        <label>Files to upload <input type="file" multiple disabled={busy || admission.current !== null} onChange={event => { setError(""); setChoices(Array.from(event.target.files ?? []).map(file => ({ file, source: file.name, modality: inferModality(file) }))); }} /></label>
        {choices.map((choice, index) => <fieldset key={index} disabled={busy || admission.current !== null}><legend>{choice.file.name} · {choice.file.size} bytes</legend>
          <label>Source identity for {choice.file.name}<input required maxLength={500} value={choice.source} onChange={event => setChoices(values => values.map((value, position) => position === index ? { ...value, source: event.target.value } : value))} /></label>
          <label>Modality for {choice.file.name}<select value={choice.modality} onChange={event => setChoices(values => values.map((value, position) => position === index ? { ...value, modality: event.target.value } : value))}>{modalities.map(modality => <option key={modality}>{modality}</option>)}</select></label>
        </fieldset>)}
        <button disabled={busy || !choices.length}>{busy ? "Preparing upload…" : "Create upload operation"}</button>
        {admission.current && <p>Retry preserves this declaration and request identity. After reload, discover committed operations in history.</p>}
      </form>}
    </>}
    {error && <p role="alert">{error}</p>}
    <h3>Upload history</h3><button onClick={() => void history.refetch()}>Refresh upload history</button>
    {history.isError && <p role="alert">{history.error.message} Last known status may be stale.</p>}
    {history.isPending ? <p role="status">Loading uploads…</p> : history.data && <>
      {!history.data.items.length ? <p>No upload operations on this page.</p> : <ul>{history.data.items.map(operation => <li key={operation.id}><button onClick={() => select(operation.id)}>{operation.id}</button> · {operation.status.replaceAll("_", " ")}</li>)}</ul>}
      <nav aria-label="Upload history pages"><button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>Previous uploads</button><button disabled={!history.data.next_after} onClick={() => setCursors([...cursors, history.data!.next_after])}>Next uploads</button></nav>
    </>}
    {selected && limits.data && <UploadDetail key={selected} namespace={namespace} id={selected} csrf={csrf} writable={writable} limits={limits.data} initialFiles={files.current} select={select} />}
  </section>;
}
function historyPush(query: URLSearchParams) { window.history.pushState(null, "", `/?${query}`); }

function UploadDetail({ namespace, id, csrf, writable, limits, initialFiles, select }: {
  namespace: string; id: string; csrf: string; writable: boolean; limits: UploadLimits;
  initialFiles: Map<string, File>; select: (id: string, suppliedFiles?: Map<string, File>) => void;
}) {
  const cache = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const files = useRef(initialFiles);
  const transfer = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const recovery = useRef<{ request_key: string; item_ids: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeTransfer, setActiveTransfer] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [replacement, setReplacement] = useState<string | null>(null);
  const path = `/api/v1/namespaces/${namespace}/ingestions/${id}`;
  const query = useQuery({ queryKey: ["ingestion", namespace, id], queryFn: () => request<UploadBatch>(path, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  useEffect(() => { alive.current = true; return () => { alive.current = false; transfer.current?.abort(); }; }, []);
  useEffect(() => { if (query.data) heading.current?.focus(); }, [id, !!query.data]);
  async function refresh() {
    await cache.invalidateQueries({ queryKey: ["ingestion", namespace, id] });
    await cache.invalidateQueries({ queryKey: ["ingestions", namespace] });
    await cache.invalidateQueries({ queryKey: ["samples", namespace] });
  }
  async function run(action: "transfer" | "cancel" | "retry" | "reupload") {
    if (!query.data || query.isError || busy) return;
    setBusy(true); setActiveTransfer(action === "transfer"); setError(""); setMessage("");
    const controller = new AbortController(); transfer.current = controller;
    try {
      const epoch = query.data.operation.epoch;
      if (action === "transfer") {
        await transferUploads(namespace, csrf, id, files.current, limits, controller.signal, value => { if (alive.current) setMessage(value); });
        if (alive.current) setMessage("Uploaded parts are complete. Awaiting independent worker verification; this is not yet accepted sample content.");
      } else if (action === "reupload") {
        recovery.current ??= { request_key: crypto.randomUUID(), item_ids: query.data.items.filter(item => !["accepted", "superseded"].includes(item.state) && !item.replacement_operation_id).map(item => item.id) };
        const next = await request<UploadBatch>(`/api/v1/namespaces/${namespace}/ingestions/reuploads`, csrf, recovery.current);
        if (alive.current) select(next.operation.id);
      } else {
        await request(action === "cancel" ? `${path}/cancel` : `/api/v1/namespaces/${namespace}/operations/${id}/retry`, csrf, { epoch });
        if (alive.current) setMessage(action === "cancel" ? "Cancellation requested. Cleanup may remain pending while provider effects are uncertain." : "Retry requested. Accepted siblings remain unchanged.");
      }
      if (alive.current) await refresh();
    } catch (failure) {
      if (alive.current) {
        setError(controller.signal.aborted ? "Transfer stopped here. Uploaded parts remain discoverable; resume or request cancellation." : (failure as Error).message);
        if (denied(failure)) setAccessDenied(true);
        void refresh();
      }
    } finally { if (alive.current) { setBusy(false); setActiveTransfer(false); transfer.current = null; } }
  }
  if (accessDenied || denied(query.error)) return <p role="alert">Workspace access changed. Upload details are hidden.</p>;
  if (!query.data) return <p role={query.isError ? "alert" : "status"}>{query.isError ? query.error.message : "Loading upload operation…"}</p>;
  const batch = query.data, status = batch.operation.status;
  const canTransfer = status === "running" && batch.items.some(item => ["awaiting_upload", "uploading"].includes(item.state));
  const recoverable = ["failed", "cancelled"].includes(status) && batch.items.some(item => !["accepted", "superseded"].includes(item.state) && !item.replacement_operation_id);
  return <article aria-label="Upload operation"><h3 ref={heading} tabIndex={-1}>Upload {id}</h3>
    <p>Status: <strong>{status.replaceAll("_", " ")}</strong> · attempt {batch.operation.epoch}</p>
    {query.isError && <p role="alert">Connection interrupted. Last known status shown; write controls paused.</p>}
    {status === "running" && batch.items.some(item => item.state === "failed") && <p>Some uploads failed before verification. Cancel this operation, wait for cleanup, then create a linked re-upload.</p>}
    <button onClick={() => void query.refetch()}>Refresh upload operation</button>
    {writable && <div>
      {canTransfer && <><label>Reselect original files to resume<input type="file" multiple disabled={busy} onChange={event => { files.current = new Map(Array.from(event.target.files ?? []).map((file, index) => [String(index), file])); }} /></label><button disabled={busy || query.isError || !limits.configured} onClick={() => void run("transfer")}>Upload or resume files</button></>}
      {busy && activeTransfer && <button onClick={() => transfer.current?.abort()}>Stop local transfer</button>}
      <button disabled={busy || query.isError || !["running", "queued", "failed"].includes(status)} onClick={() => void run("cancel")}>Cancel upload operation</button>
      {status === "failed" && batch.items.every(item => ["ready", "accepted"].includes(item.state)) && <button disabled={busy || query.isError} onClick={() => void run("retry")}>Retry verification</button>}
      {recoverable && <button disabled={busy || query.isError || !limits.configured} onClick={() => void run("reupload")}>Create linked re-upload for unfinished files</button>}
    </div>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    {batch.cleanup_operation_id && <p><a href={`/?${new URLSearchParams({ namespace, view: "operations", operation: batch.cleanup_operation_id })}`}>Inspect staging cleanup</a></p>}
    <ul>{batch.items.map(item => <li key={item.id}><strong>{item.source_key}</strong> · {item.modality} · {item.size_bytes} bytes · {item.state}{item.error && <p role="alert">{item.error.replaceAll("_", " ")}</p>}
      {item.retry_of_item_id && <p>Re-upload of item <span className="identifier">{item.retry_of_item_id}</span></p>}
      {item.replacement_operation_id && <button onClick={() => select(item.replacement_operation_id!)}>Open replacement upload</button>}
      {item.accepted_revision_id && item.sample_id && <><p>Accepted sample <span className="identifier">{item.sample_id}</span> · exact revision <span className="identifier">{item.accepted_revision_id}</span></p><button onClick={() => setPreview(item.id)}>Inspect accepted content for {item.source_key}</button>{writable && <button onClick={() => setReplacement(item.id)}>Replace bytes for {item.source_key}</button>}{replacement === item.id && <ReplaceUpload key={item.accepted_revision_id} namespace={namespace} csrf={csrf} item={item} limits={limits} select={select} />}{preview === item.id && <SamplePreview key={item.accepted_revision_id} namespace={namespace} sample={item.sample_id} revision={item.accepted_revision_id} csrf={csrf} />}</>}
    </li>)}</ul>
  </article>;
}


function ReplaceUpload({ namespace, csrf, item, limits, select }: {
  namespace: string; csrf: string; item: UploadItem; limits: UploadLimits;
  select: (id: string, files?: Map<string, File>) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<{ request_key: string; items: Declaration[] } | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  async function replace(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    const current = ++generation.current;
    setBusy(true); setError("");
    try {
      if (file.size > limits.max_artifact_bytes) throw new Error("Replacement exceeds the upload limit.");
      if (!pending.current) {
        const original = await request<{ revision: number; metadata: Record<string, unknown> }>(
          `/api/v1/namespaces/${namespace}/samples/${item.sample_id}/revisions/${item.accepted_revision_id}`, csrf,
        );
        const sha256 = await fileDigest(file);
        if (current !== generation.current) return;
        pending.current = { request_key: crypto.randomUUID(), items: [{ source_key: item.source_key,
          modality: item.modality, sha256, size_bytes: file.size, metadata: original.metadata,
          target_sample_id: item.sample_id!, expected_revision: original.revision }] };
      }
      const next = await request<UploadBatch>(`/api/v1/namespaces/${namespace}/ingestions/uploads`, csrf, pending.current);
      if (current === generation.current) select(next.operation.id, new Map([["replacement", file]]));
    } catch (failure) {
      if (current === generation.current) {
        setError((failure as Error).message);
        if (failure instanceof ApiError && [409, 422].includes(failure.status)) pending.current = null;
      }
    } finally { if (current === generation.current) setBusy(false); }
  }
  return <form onSubmit={event => void replace(event)} aria-label={`Replace content for ${item.source_key}`}>
    <p>Replace this sample's bytes, preserving its source identity, modality and selected revision metadata. A newer sample revision causes a conflict; inspect its latest accepted upload before replacing.</p>
    <label>Replacement file for {item.source_key}<input type="file" required disabled={busy || pending.current !== null} onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
    <button disabled={busy || !file || !limits.configured}>{busy ? "Preparing replacement…" : "Create replacement upload"}</button>
    {error && <p role="alert">{error} Retry preserves the request identity when the result is uncertain.</p>}
  </form>;
}
