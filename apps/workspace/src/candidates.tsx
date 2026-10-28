import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Candidate = { id: string; source_revision: number; latest_draft_revision: number; generation_id: string; state: "preparing" | "sealed"; prepared_count: number; cursor: string | null };
type Member = { sample_id: string; sample_revision_id: string; split: string; interpretation: { schema: string; annotation_revision: unknown; task_schema_revision: unknown; review_evidence: unknown; transformation: unknown } };
type GenerationPage = { candidate: Candidate; items: Member[]; next_after: string | null };
const denied = (error: Error | null) => error instanceof ApiError && [401,403].includes(error.status);

export function Candidates({ namespace, dataset, csrf, writable }: { namespace: string; dataset: string; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState(() => new URLSearchParams(location.search).get("candidate") ?? "");
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [memberPages, setMemberPages] = useState<(string | null)[]>([null]);
  const [mode, setMode] = useState("selected_candidate");
  const [busy, setBusy] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const captureCommand = useRef<{ request_key: string; expected_revision: number } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}`;
  const key = ["candidates", namespace, dataset];
  const cursor = pages.at(-1), memberCursor = memberPages.at(-1);
  const source = useQuery({ queryKey: ["draft", namespace, dataset, "candidate-source"], queryFn: () => request<{revision: number}>(`${base}/draft/members?limit=1`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  const candidates = useQuery({ queryKey: [...key, cursor], queryFn: () => request<Page<Candidate>>(`${base}/candidates?limit=25${cursor ? `&after=${cursor}` : ""}`, csrf) });
  const generation = useQuery({ queryKey: ["candidate", namespace, dataset, selected, memberCursor], enabled: !!selected,
    queryFn: () => request<GenerationPage>(`${base}/candidates/${selected}/members?limit=25${memberCursor ? `&after=${memberCursor}` : ""}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  useEffect(() => { const update = () => { setSelected(new URLSearchParams(location.search).get("candidate") ?? ""); setMemberPages([null]); }; window.addEventListener("popstate", update); return () => window.removeEventListener("popstate", update); }, []);
  useEffect(() => { if (generation.data) heading.current?.focus(); }, [selected, !!generation.data]);
  function select(id: string) {
    const params = new URLSearchParams(location.search); params.set("candidate", id);
    history.pushState(null, "", `/?${params}`); setSelected(id); setMemberPages([null]); setError(""); setMessage("");
  }
  async function run(action: "capture" | "prepare" | "check-selection") {
    if (busy || !source.data || source.isError || (action !== "capture" && (!generation.data || generation.isError))) return;
    setBusy(true); setError(""); setMessage("");
    try {
      let result: Candidate;
      if (action === "capture") {
        captureCommand.current ??= { request_key: crypto.randomUUID(), expected_revision: source.data.revision };
        result = await request<Candidate>(`${base}/candidates`, csrf, captureCommand.current);
        captureCommand.current = null; select(result.id);
        setMessage(`Captured draft revision ${result.source_revision}. Preparation can resume in bounded batches.`);
      } else {
        result = await request<Candidate>(`${base}/candidates/${selected}/${action}`, csrf, action === "prepare" ? { expected_count: generation.data!.candidate.prepared_count, limit: 100 } : { mode });
        setMessage(action === "prepare" ? `${result.prepared_count} members prepared. ${result.state === "sealed" ? "Generation sealed." : "Resume to prepare the next batch."}` : "Selection confirmed for this revision. Quality validation and publication are separate steps.");
      }
      await Promise.all([
        cache.invalidateQueries({ queryKey: key }),
        cache.invalidateQueries({ queryKey: ["candidate", namespace, dataset] }),
      ]);
    } catch (failure) {
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      if (action === "capture" && failure instanceof ApiError && failure.status === 409) captureCommand.current = null;
      void source.refetch();
    } finally { setBusy(false); }
  }
  if (accessDenied || denied(source.error) || denied(candidates.error) || denied(generation.error)) return <p role="alert">Candidate access changed. Reload your workspace before continuing.</p>;
  const detail = generation.data?.candidate;
  const stale = source.isError || (!!selected && generation.isError);
  return <section aria-label="Frozen candidates"><h2>Frozen candidates</h2>
    <p>Capture a draft revision while the working draft remains editable.</p>
    <p>Latest draft revision: {source.data?.revision ?? "Loading…"}</p>
    {stale && <p role="alert">Refresh failed. Displayed revisions may be stale; candidate selection is preserved.</p>}
    {writable && <button disabled={busy || !source.data || source.isError} onClick={() => void run("capture")}>{captureCommand.current && !busy ? `Retry capture of draft revision ${captureCommand.current.expected_revision}` : "Capture latest draft"}</button>}
    {candidates.isError && <p role="alert">{candidates.error.message} <button onClick={() => void candidates.refetch()}>Retry candidate list</button></p>}
    {candidates.isPending ? <p role="status">Loading candidates…</p> : <ul>{candidates.data?.items.map(item => <li key={item.id}><button disabled={busy} onClick={() => select(item.id)}>Select candidate {item.id} · draft {item.source_revision} · {item.state}</button></li>)}</ul>}
    <nav aria-label="Candidate pages"><button disabled={pages.length === 1 || busy} onClick={() => setPages(pages.slice(0,-1))}>Previous candidates</button><button disabled={!candidates.data?.next_after || busy} onClick={() => setPages([...pages,candidates.data!.next_after])}>Next candidates</button></nav>
    {selected && (generation.isPending ? <p role="status">Loading selected candidate…</p> : generation.data ? <article aria-label="Selected candidate"><h3 ref={heading} tabIndex={-1}>Selected candidate {selected}</h3>
      <p>Captured draft revision: <strong>{detail!.source_revision}</strong> · latest draft revision: {source.data?.revision ?? detail!.latest_draft_revision}</p>
      <p>Preparation: {detail!.state} · {detail!.prepared_count} members</p><p>Generation: <span className="identifier">{detail!.generation_id}</span></p>
      <p>Quality validation has not been run.</p>
      {writable && <><button disabled={busy || stale || detail!.state === "sealed"} onClick={() => void run("prepare")}>Resume preparation</button>
        <label>Selection mode <select value={mode} onChange={event => setMode(event.target.value)}><option value="selected_candidate">Selected candidate</option><option value="current_draft">Current draft</option></select></label>
        <button disabled={busy || stale || detail!.state !== "sealed"} onClick={() => void run("check-selection")}>Check selection</button></>}
      {!generation.data.items.length ? <p>No prepared members on this page.</p> : <div className="table-scroll"><table><caption>Frozen sample revisions and splits</caption><thead><tr><th scope="col">Sample</th><th scope="col">Exact revision</th><th scope="col">Split</th></tr></thead><tbody>{generation.data.items.map(item => <tr key={item.sample_id}><td className="identifier">{item.sample_id}</td><td className="identifier">{item.sample_revision_id}</td><td>{item.split}</td></tr>)}</tbody></table></div>}
      <p>These samples have no attached annotation, review, or transformation evidence.</p>
      <nav aria-label="Frozen member pages"><button disabled={memberPages.length === 1 || busy} onClick={() => setMemberPages(memberPages.slice(0,-1))}>Previous frozen members</button><button disabled={!generation.data.next_after || busy} onClick={() => setMemberPages([...memberPages,generation.data!.next_after])}>Next frozen members</button></nav>
    </article> : <p role="alert">{generation.error?.message} <button onClick={() => void generation.refetch()}>Retry selected candidate</button></p>)}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
