import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Report = {
  id: string; candidate_id: string; generation_id: string; source_revision: number;
  policy_id: string; policy_revision: number; state: "running" | "complete";
  required_rules_pass: boolean | null;
};
type PublicationView = {
  id: string; operation_id: string; candidate_id: string; generation_id: string;
  report_id: string; policy_id: string; source_revision: number; latest_draft_revision: number;
  label: string; mode: "selected_candidate" | "current_draft";
  state: "building" | "prepared" | "published" | "releasing" | "released";
  expected_count: number; prepared_count: number; expires_at: string; expired: boolean;
  last_error: string | null; manifest_digest: string | null; manifest_size: number | null;
  version_id: string | null;
};
type Admission = { request_key: string; label: string; report_id: string; mode: PublicationView["mode"] };
type Props = { namespace: string; dataset: string; candidate: string; csrf: string; writable: boolean };
const denied = (error: Error | null) => error instanceof ApiError && [401, 403].includes(error.status);
const passed = (report: Report) => report.state === "complete" && report.required_rules_pass === true;

export function Publication(props: Props) {
  return <PublicationPanel key={`${props.namespace}/${props.dataset}/${props.candidate}`} {...props} />;
}

function PublicationPanel({ namespace, dataset, candidate, csrf, writable }: Props) {
  const cache = useQueryClient();
  const labelHelp = useId();
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}`;
  const [reportPages, setReportPages] = useState<(string | null)[]>([null]);
  const [publicationPages, setPublicationPages] = useState<(string | null)[]>([null]);
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [selected, setSelected] = useState("");
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<PublicationView["mode"]>("selected_candidate");
  const admission = useRef<Admission | null>(null);
  const [busy, setBusy] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState("");
  const reportCursor = reportPages.at(-1), publicationCursor = publicationPages.at(-1);
  const reports = useQuery({ queryKey: ["quality-reports", namespace, dataset, candidate, reportCursor], enabled: !!candidate,
    queryFn: () => request<Page<Report>>(`${base}/candidates/${candidate}/quality/reports?limit=25${reportCursor ? `&after=${encodeURIComponent(reportCursor)}` : ""}`, csrf), refetchOnReconnect: true });
  const publicationsKey = ["publications", namespace, dataset, candidate];
  const publications = useQuery({ queryKey: [...publicationsKey, publicationCursor], enabled: !!candidate,
    queryFn: () => request<Page<PublicationView>>(`${base}/candidates/${candidate}/publications?limit=25${publicationCursor ? `&after=${encodeURIComponent(publicationCursor)}` : ""}`, csrf), refetchOnReconnect: true });
  const detailKey = ["publication", namespace, dataset, selected];
  const publication = useQuery({ queryKey: detailKey, enabled: !!selected,
    queryFn: () => request<PublicationView>(`${base}/publications/${selected}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  const detail = publication.data?.candidate_id === candidate ? publication.data : undefined;
  const gate = useQuery({ queryKey: ["quality-report", namespace, dataset, detail?.report_id], enabled: !!detail,
    queryFn: () => request<Report>(`${base}/quality/reports/${detail!.report_id}`, csrf), refetchOnReconnect: true });
  const gateMatches = !!detail && !!gate.data && passed(gate.data) && gate.data.candidate_id === candidate
    && gate.data.generation_id === detail.generation_id && gate.data.source_revision === detail.source_revision
    && gate.data.policy_id === detail.policy_id && gate.data.id === detail.report_id && !gate.isError;
  const stale = publications.isError || publication.isError;

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!writable || busy || reports.isError || publications.isError || !selectedReport || !passed(selectedReport)
      || (selectedReport.candidate_id && selectedReport.candidate_id !== candidate)) return;
    setBusy(true); setError("");
    try {
      admission.current ??= { request_key: crypto.randomUUID(), label: label.trim(), report_id: selectedReport.id, mode };
      const result = await request<PublicationView>(`${base}/candidates/${candidate}/publications`, csrf, admission.current);
      if (!active.current) return;
      admission.current = null;
      cache.setQueryData(["publication", namespace, dataset, result.id], result);
      setSelected(result.id);
      void cache.invalidateQueries({ queryKey: publicationsKey });
    } catch (failure) {
      if (!active.current) return;
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500) admission.current = null;
    } finally { if (active.current) setBusy(false); }
  }

  async function run(action: "prepare" | "finalize" | "renew" | "release") {
    if (!writable || busy || stale || !detail || (action === "finalize" && (!gateMatches || detail.expired))) return;
    setBusy(true); setError("");
    try {
      const body = action === "prepare" ? { expected_count: detail.prepared_count, limit: 25 }
        : action === "release" ? { limit: 100 } : {};
      const result = await request<PublicationView>(`${base}/publications/${detail.id}/${action}`, csrf, body);
      if (!active.current) return;
      cache.setQueryData(detailKey, result);
      void cache.invalidateQueries({ queryKey: publicationsKey });
    } catch (failure) {
      if (!active.current) return;
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      // A lost response can already have saved a checkpoint or committed a version.
      // Recover the stored operation before offering the next action.
      void publication.refetch();
    } finally { if (active.current) setBusy(false); }
  }

  if (accessDenied || [reports.error, publications.error, publication.error, gate.error].some(denied)) {
    return <p role="alert">Publication access changed. Reload your workspace before continuing.</p>;
  }
  if (!candidate) return <section aria-label="Dataset publication"><h2>Dataset publication</h2><p>Select a sealed candidate to prepare publication.</p></section>;
  const formLocked = busy || !!admission.current;
  return <section aria-label="Dataset publication">
    <h2>Dataset publication</h2>
    <p>One manifest supports up to 256 MiB. Larger candidates need a smaller captured selection before publication.</p>
    <p>Publish the exact selected candidate with its passing quality evidence. Preparation protects its source objects and checks them in bounded batches.</p>
    <p>Publication candidate: <span className="identifier">{candidate}</span></p>
    {reports.isPending ? <p role="status">Loading publication quality evidence…</p> : reports.isError ? <p role="alert">{reports.error.message} Your publication input is preserved. <button onClick={() => void reports.refetch()}>Retry publication reports</button></p> : null}
    {writable && <form onSubmit={event => void start(event)}>
      <label>Passing quality report <select required value={selectedReport?.id ?? ""} disabled={formLocked || reports.isError} onChange={event => setSelectedReport(reports.data?.items.find(item => item.id === event.target.value) ?? null)}>
        <option value="">Select exact passing evidence</option>
        {selectedReport && !reports.data?.items.some(item => item.id === selectedReport.id) && <option value={selectedReport.id}>{selectedReport.id} · policy revision {selectedReport.policy_revision}</option>}
        {reports.data?.items.map(item => <option key={item.id} value={item.id} disabled={!passed(item) || (!!item.candidate_id && item.candidate_id !== candidate)}>{item.id} · policy revision {item.policy_revision} · {passed(item) ? "passed" : item.state === "running" ? "running" : "blocked"}</option>)}
      </select></label>
      {selectedReport && <p>Selected gate: report <span className="identifier">{selectedReport.id}</span> · policy <span className="identifier">{selectedReport.policy_id}</span> revision {selectedReport.policy_revision} · captured draft revision {selectedReport.source_revision} · generation <span className="identifier">{selectedReport.generation_id}</span></p>}
      <label>Version label <input required pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*" maxLength={100} aria-describedby={labelHelp} value={label} disabled={formLocked} onChange={event => setLabel(event.target.value)} /></label>
      <p id={labelHelp}>Choose a unique label beginning with a letter or number, followed by letters, numbers, dots, underscores or hyphens; up to 100 characters.</p>
      <label>Publication selection mode <select value={mode} disabled={formLocked} onChange={event => setMode(event.target.value as PublicationView["mode"])}><option value="selected_candidate">Selected candidate</option><option value="current_draft">Current draft must still match</option></select></label>
      <p>The candidate, gate, label and selection mode stay fixed for this preparation. Later draft edits never select a different candidate.</p>
      <button disabled={busy || reports.isError || publications.isError || !selectedReport || !passed(selectedReport)}>{admission.current && !busy ? "Retry starting publication" : "Start publication preparation"}</button>
      {admission.current && !busy && <p role="status">The last result is unknown. Retry to recover the same preparation; your input is preserved.</p>}
    </form>}
    <nav aria-label="Publication quality report pages"><button disabled={formLocked || reportPages.length === 1} onClick={() => setReportPages(reportPages.slice(0, -1))}>Previous publication reports</button><button disabled={formLocked || !reports.data?.next_after} onClick={() => setReportPages([...reportPages, reports.data!.next_after])}>Next publication reports</button></nav>
    {publications.isPending ? <p role="status">Loading publications…</p> : publications.isError ? <p role="alert">{publications.error.message} <button onClick={() => void publications.refetch()}>Retry publications</button></p> : !publications.data?.items.length ? <p>No publication preparations for this candidate.</p> : <ul>{publications.data.items.map(item => <li key={item.id}><button disabled={busy} onClick={() => { setSelected(item.id); setError(""); }}>Inspect publication {item.label} · {item.state}</button></li>)}</ul>}
    <nav aria-label="Publication pages"><button disabled={busy || publicationPages.length === 1} onClick={() => setPublicationPages(publicationPages.slice(0, -1))}>Previous publications</button><button disabled={busy || !publications.data?.next_after} onClick={() => setPublicationPages([...publicationPages, publications.data!.next_after])}>Next publications</button></nav>
    {selected && publication.isPending && <p role="status">Loading selected publication…</p>}
    {selected && publication.isError && <p role="alert">{publication.error.message} Displayed publication may be stale. <button onClick={() => void publication.refetch()}>Retry selected publication</button></p>}
    {detail && <article aria-label="Selected publication">
      <h3>Publication {detail.label}</h3>
      <p>Captured draft revision {detail.source_revision} · latest draft revision {detail.latest_draft_revision} · generation <span className="identifier">{detail.generation_id}</span></p>
      <p>Fixed selection: {detail.mode === "current_draft" ? "Current draft must still match" : "Selected candidate"}.</p>
      <p>Pinned report <span className="identifier">{detail.report_id}</span> · policy <span className="identifier">{detail.policy_id}</span>{gate.data && !gate.isError && <> revision {gate.data.policy_revision}</>}</p>
      {gate.isPending ? <p role="status">Loading pinned publication gate…</p> : gate.isError ? <p role="alert">{gate.error.message} <button onClick={() => void gate.refetch()}>Retry publication gate</button></p> : !gateMatches ? <p role="alert">Passing evidence does not match this exact candidate, generation and revision. Publication is blocked.</p> : <p>Required gates passed for this exact candidate and policy revision.</p>}
      <p>Source checks: {detail.prepared_count} / {detail.expected_count} members · {detail.state}</p>
      {detail.manifest_digest && <dl><dt>Manifest SHA-256</dt><dd className="identifier">{detail.manifest_digest}</dd><dt>Manifest bytes</dt><dd>{detail.manifest_size}</dd></dl>}
      {detail.last_error && <p role="alert">{detail.last_error} The saved preparation is retained. Resolve the source problem and resume, or release its protection.</p>}
      {detail.state === "published" ? <p role="status">Published immutable version <span className="identifier">{detail.version_id}</span> as {detail.label}.</p>
        : detail.state === "released" ? <p role="status">Preparation released. It cannot be published; start a new preparation when ready.</p>
        : detail.state === "releasing" ? <p role="status">Releasing abandoned source protection. Continue release to finish the remaining batch.</p>
        : detail.expired ? <p role="status">Preparation expired. Renew protection and recheck every source object before publishing.</p>
        : detail.state === "prepared" && detail.mode === "current_draft" && detail.source_revision !== detail.latest_draft_revision ? <p role="status">Artifacts are verified, but the current draft has changed. Publication will report a revision conflict and retain this preparation.</p>
        : detail.state === "prepared" ? <p role="status">Source checks complete. Ready to publish after reviewing the pinned evidence.</p>
        : <p role="status">Preparation checkpoint saved. Resume source checks to continue.</p>}
      {["building", "prepared"].includes(detail.state) && <p>Source protection expires: <time dateTime={detail.expires_at}>{detail.expires_at}</time>.</p>}
      {writable && <>
        {detail.state === "building" && !detail.expired && <button disabled={busy || stale} onClick={() => void run("prepare")}>Resume source checks</button>}
        {["building", "prepared"].includes(detail.state) && detail.expired && <button disabled={busy || stale} onClick={() => void run("renew")}>Renew protection and recheck all sources</button>}
        {detail.state === "prepared" && !detail.expired && <button disabled={busy || stale || !gateMatches || publication.isFetching} onClick={() => void run("finalize")}>Publish immutable version</button>}
        {["building", "prepared", "releasing"].includes(detail.state) && <button disabled={busy || stale} onClick={() => void run("release")}>{detail.state === "releasing" ? "Continue releasing protection" : "Release abandoned preparation"}</button>}
      </>}
    </article>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
