import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Operation = { id: string; kind: string; status: string; epoch: number; revision: number; created_at: string };
type Effect = { id: string; kind: string; status: string; revision: number; result_digest: string | null };
type Detail = { operation: Operation; needs_reconciliation: boolean; provider_run_id: string | null; accepted_digest: string | null; effects: Effect[]; next_effect_after: string | null };
const label = (status: string) => status.replaceAll("_", " ");
const denied = (error: Error | null) => error instanceof ApiError && [401, 403].includes(error.status);

export function Operations({ namespace, csrf, writable }: { namespace: string; csrf: string; writable: boolean }) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get("operation") ?? "");
  const cursor = cursors[cursors.length - 1];
  const path = `/api/v1/namespaces/${namespace}/operations`;
  const query = useQuery({ queryKey: ["operations", namespace, cursor], queryFn: () => request<Page<Operation>>(`${path}?limit=25${cursor ? `&after=${cursor}` : ""}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  useEffect(() => { const update = () => setSelected(new URLSearchParams(location.search).get("operation") ?? ""); window.addEventListener("popstate", update); return () => window.removeEventListener("popstate", update); }, []);
  function select(id: string) {
    const params = new URLSearchParams(location.search); params.set("operation", id);
    history.pushState(null, "", `/?${params}`); setSelected(id);
  }
  if (denied(query.error)) return <p role="alert">{query.error!.message}</p>;
  return <section aria-label="Operations"><h2>Operations</h2>
    <p role="status">{query.isError ? "Connection interrupted. Displayed status may be stale." : query.isFetching ? "Checking durable status…" : "Connected. Status refreshes automatically."}</p>
    <button onClick={() => void query.refetch()}>Refresh operations</button>
    {query.isPending ? <p>Loading operations…</p> : query.data && <>
      {!query.data.items.length ? <p>No operations on this page.</p> : <div className="table-scroll"><table><caption>Operations · page {cursors.length}</caption><thead><tr><th scope="col">Operation</th><th scope="col">Status</th><th scope="col">Attempt</th></tr></thead><tbody>{query.data.items.map(op => <tr key={op.id}><td><button onClick={() => select(op.id)}>{label(op.kind)} · {op.id}</button></td><td>{label(op.status)}</td><td>{op.epoch}</td></tr>)}</tbody></table></div>}
      <nav aria-label="Operation pages"><button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>Previous operations</button><button disabled={!query.data.next_after} onClick={() => setCursors([...cursors, query.data!.next_after])}>Next operations</button></nav>
    </>}
    {selected && <OperationDetail key={`${namespace}:${selected}`} namespace={namespace} id={selected} csrf={csrf} writable={writable} />}
  </section>;
}

function OperationDetail({ namespace, id, csrf, writable }: { namespace: string; id: string; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const heading = useRef<HTMLHeadingElement>(null);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const [message, setMessage] = useState("");
  const cursor = cursors[cursors.length - 1];
  const path = `/api/v1/namespaces/${namespace}/operations/${id}`;
  const query = useQuery({ queryKey: ["operation", namespace, id, cursor], queryFn: () => request<Detail>(`${path}?limit=25${cursor ? `&after=${cursor}` : ""}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  useEffect(() => { if (query.data) heading.current?.focus(); }, [id, !!query.data]);
  async function action(name: "retry" | "cancel" | "reconcile") {
    if (!query.data || query.isError || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await request<Operation>(`${path}/${name}`, csrf, { epoch: query.data.operation.epoch });
      setMessage(name === "reconcile" ? "Reconciliation requested. Current status is shown above." : name === "cancel" ? "Cancellation requested. Current status is shown above." : "Retry requested. Current status is shown above.");
      await cache.invalidateQueries({ queryKey: ["operation", namespace, id] });
      await cache.invalidateQueries({ queryKey: ["operations", namespace] });
    } catch (failure) {
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      void query.refetch();
    } finally { setBusy(false); }
  }
  if (accessDenied || denied(query.error)) return <p role="alert">Workspace access changed. Reload your workspace before continuing.</p>;
  if (!query.data) return <p role={query.isError ? "alert" : "status"}>{query.isError ? query.error.message : "Loading operation…"}</p>;
  const detail = query.data, op = detail.operation;
  return <article aria-label="Operation detail"><h3 ref={heading} tabIndex={-1}>Operation {op.id}</h3>
    {query.isError && <p role="alert">Connection interrupted. Last known status shown; recovery controls paused.</p>}
    <p>Status: <strong>{label(op.status)}</strong>{detail.needs_reconciliation && " · reconciliation needed"}<br />Attempt {op.epoch} · revision {op.revision}</p>
    {detail.provider_run_id && <p>Workflow run: <span className="identifier">{detail.provider_run_id}</span></p>}
    {detail.accepted_digest && <p>Accepted SHA-256: <span className="identifier">{detail.accepted_digest}</span></p>}
    {writable && <div className="catalog-tools"><button disabled={busy || query.isError || op.status !== "failed"} onClick={() => void action("retry")}>Retry operation</button><button disabled={busy || query.isError || !["queued", "running"].includes(op.status)} onClick={() => void action("cancel")}>Cancel operation</button><button disabled={busy || query.isError || ["succeeded", "cancelled"].includes(op.status)} onClick={() => void action("reconcile")}>Reconcile operation</button></div>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <h4>Prior effects</h4>{!detail.effects.length ? <p>No effects recorded.</p> : <ul>{detail.effects.map(effect => <li key={effect.id}>{effect.kind}: {effect.status} · revision {effect.revision}{effect.result_digest && <span className="identifier"> · {effect.result_digest}</span>}</li>)}</ul>}
    <nav aria-label="Effect pages"><button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>Previous effects</button><button disabled={!detail.next_effect_after} onClick={() => setCursors([...cursors, detail.next_effect_after])}>Next effects</button></nav>
  </article>;
}

export function VerifyArtifact({ namespace, sample, revision, csrf }: { namespace: string; sample: string; revision: string; csrf: string }) {
  const [requestKey] = useState(() => crypto.randomUUID());
  const [operation, setOperation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function verify() {
    if (busy || operation) return;
    setBusy(true); setError("");
    try {
      const admitted = await request<Operation>(`/api/v1/namespaces/${namespace}/operations/verify-artifact`, csrf, { request_key: requestKey, sample_id: sample, revision_id: revision });
      setOperation(admitted.id);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  return <div>{operation ? <p role="status">Verification queued. <a href={`/?${new URLSearchParams({ namespace, view: "operations", operation })}`}>View verification operation</a></p> : <button disabled={busy} onClick={() => void verify()}>Verify artifact</button>}{error && <p role="alert">{error} Retry uses the same request identity.</p>}</div>;
}
