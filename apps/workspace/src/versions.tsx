import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Version = {
  id: string; namespace_id: string; dataset_id: string; label: string;
  generation_id: string; candidate_id: string; report_id: string;
  manifest_digest: string; manifest_size: number; manifest_artifact_id: string; created_at: string;
  availability: "active" | "deprecated" | "archived";
  prior_availability: "active" | "deprecated"; availability_revision: number;
};
type Lineage = { kind: string; id: string | null; status: "present" | "absent" | "partial"; label: string };
type Member = { sample_id: string; sample_revision_id: string; revision: number; split: string;
  metadata: Record<string, unknown>; interpretation: Record<string, unknown>;
  artifact: { id: string; sha256: string; size_bytes: number }; lineage: Lineage[] };
type Members = Page<Member> & { version: Version; lineage_status: "complete" | "partial";
  quality_policy: { id: string; name: string; revision: number; definition: unknown };
  quality_report: { id: string; policy_id: string; policy_revision: number; source_revision: number;
    evaluator_version: string; state: string; required_rules_pass: boolean | null } };
type Manifest = { artifact_id: string; sha256: string; size_bytes: number; schema: string; download_path: string };
type Preview = { sample_id: string; sample_revision_id: string; artifact_id: string; sha256: string;
  size_bytes: number; modality: string; kind: "text" | "binary"; text: string | null;
  truncated: boolean; warning: string | null; download_path: string };
type Props = { namespace: string; dataset: string; csrf: string; writable: boolean };
const denied = (error: Error | null) => error instanceof ApiError && [401, 403].includes(error.status);
function routedVersion(namespace: string, dataset: string) {
  const params = new URLSearchParams(location.search);
  return params.get("namespace") === namespace && params.get("dataset") === dataset ? params.get("version") ?? "" : "";
}
function boundedText(text: string) {
  const bytes = new TextEncoder().encode(text);
  return { text: new TextDecoder().decode(bytes.slice(0, 4096), { stream: true }), truncated: bytes.length > 4096 };
}

export function Versions(props: Props) {
  return <VersionsPanel key={`${props.namespace}/${props.dataset}`} {...props} />;
}
function VersionsPanel({ namespace, dataset, csrf, writable }: Props) {
  const [selected, setSelected] = useState(() => routedVersion(namespace, dataset));
  const [includeArchived, setIncludeArchived] = useState(false);
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [accessDenied, setAccessDenied] = useState(false);
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}`;
  const cursor = pages.at(-1);
  const versions = useQuery({ queryKey: ["versions", namespace, dataset, includeArchived, cursor],
    queryFn: () => request<Page<Version>>(`${base}/versions?limit=25${includeArchived ? "&include_archived=true" : ""}${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`, csrf), refetchOnReconnect: true });
  useEffect(() => {
    const update = () => setSelected(routedVersion(namespace, dataset));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, [namespace, dataset]);
  function select(id: string) {
    const params = new URLSearchParams(location.search);
    params.set("namespace", namespace); params.set("dataset", dataset); params.set("version", id);
    history.pushState(null, "", `/?${params}`); setSelected(id);
  }
  if (accessDenied || denied(versions.error)) return <p role="alert">Version access changed. Reload your workspace before continuing.</p>;
  return <section aria-label="Published dataset versions">
    <h2>Published dataset versions</h2>
    <p>Inspect an exact immutable version. Deprecation and archive change availability, never its captured members or evidence.</p>
    <label><input type="checkbox" checked={includeArchived} onChange={event => { setIncludeArchived(event.target.checked); setPages([null]); }} />Show archived versions</label>
    <button onClick={() => void versions.refetch()}>Refresh version list</button>
    {versions.isPending ? <p role="status">Loading published versions…</p> : versions.isError ? <p role="alert">{versions.error.message} Your selected version is preserved.</p>
      : !versions.data.items.length ? <p>No versions match this filter.</p> : <ul>{versions.data.items.map(item => <li key={item.id}><a href={`/?${new URLSearchParams({ namespace, dataset, version: item.id })}`} aria-current={selected === item.id ? "true" : undefined} onClick={event => { event.preventDefault(); select(item.id); }}>{item.label}</a> · {item.availability}{item.availability === "deprecated" && " · Prefer an active version for new work."}</li>)}</ul>}
    <nav aria-label="Version pages"><button disabled={pages.length === 1 || versions.isFetching} onClick={() => setPages(pages.slice(0, -1))}>Previous versions</button><button disabled={!versions.data?.next_after || versions.isFetching || versions.isError} onClick={() => setPages([...pages, versions.data!.next_after])}>Next versions</button></nav>
    {selected && <VersionInspection key={selected} namespace={namespace} dataset={dataset} versionId={selected} csrf={csrf} writable={writable} onDenied={() => setAccessDenied(true)} />}
  </section>;
}

function VersionInspection({ namespace, dataset, versionId, csrf, writable, onDenied }: Props & { versionId: string; onDenied: () => void }) {
  const cache = useQueryClient();
  const active = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { active.current = true; heading.current?.focus(); return () => { active.current = false; }; }, []);
  const [pages, setPages] = useState<(string | null)[]>([null]);
  const [sample, setSample] = useState<Member | null>(null);
  const [lineage, setLineage] = useState<Member | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}/versions/${versionId}`;
  const versionKey = ["version", namespace, dataset, versionId];
  const version = useQuery({ queryKey: versionKey, queryFn: () => request<Version>(base, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  const cursor = pages.at(-1);
  const members = useQuery({ queryKey: ["version-members", namespace, dataset, versionId, cursor],
    queryFn: () => request<Members>(`${base}/members?limit=25${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`, csrf), refetchOnReconnect: true });
  const manifest = useQuery({ queryKey: ["version-manifest", namespace, dataset, versionId],
    queryFn: () => request<Manifest>(`${base}/manifest`, csrf), refetchOnReconnect: true });
  const preview = useQuery({ queryKey: ["version-preview", namespace, dataset, versionId, sample?.sample_id, sample?.sample_revision_id], enabled: !!sample,
    queryFn: () => request<Preview>(`${base}/samples/${sample!.sample_id}/preview`, csrf), refetchOnReconnect: true });
  const accessDenied = [version.error, members.error, manifest.error, preview.error].some(denied);
  useEffect(() => { if (accessDenied) onDenied(); }, [accessDenied, onDenied]);
  const detail = version.data?.id === versionId && version.data.namespace_id === namespace && version.data.dataset_id === dataset ? version.data : undefined;
  const recordedVersion = members.data?.version;
  const exactMembers = recordedVersion?.id === versionId && recordedVersion.namespace_id === namespace && recordedVersion.dataset_id === dataset
    && members.data?.quality_report.id === recordedVersion.report_id
    && members.data.quality_report.policy_id === members.data.quality_policy.id
    && members.data.quality_report.policy_revision === members.data.quality_policy.revision
    && (!detail || (recordedVersion.generation_id === detail.generation_id && recordedVersion.manifest_digest === detail.manifest_digest)) ? members.data : undefined;
  const exactManifest = detail && manifest.data?.artifact_id === detail.manifest_artifact_id && manifest.data.sha256 === detail.manifest_digest && manifest.data.size_bytes === detail.manifest_size ? manifest.data : undefined;
  const exactPreview = sample && preview.data?.sample_id === sample.sample_id && preview.data.sample_revision_id === sample.sample_revision_id
    && preview.data.artifact_id === sample.artifact.id && preview.data.sha256 === sample.artifact.sha256 && preview.data.size_bytes === sample.artifact.size_bytes ? preview.data : undefined;
  const text = exactPreview?.kind === "text" && exactPreview.text !== null ? boundedText(exactPreview.text) : null;

  async function changeAvailability(action: "deprecate" | "activate" | "archive" | "unarchive") {
    if (!writable || busy || !detail || version.isError || version.isFetching) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await request<Version>(`${base}/availability`, csrf, { action, expected_revision: detail.availability_revision });
      if (!active.current) return;
      cache.setQueryData(versionKey, result);
      setMessage(`Version availability saved: ${result.availability}. Captured contents are unchanged.`);
      void cache.invalidateQueries({ queryKey: ["versions", namespace, dataset] });
    } catch (failure) {
      if (!active.current) return;
      setError((failure as Error).message);
      if (denied(failure as Error)) onDenied();
    } finally { if (active.current) setBusy(false); }
  }
  if (accessDenied) return <p role="alert">Version access changed. Reload your workspace before continuing.</p>;
  return <article aria-label="Exact published version">
    <h3 ref={heading} tabIndex={-1}>Exact published version {detail?.label ?? versionId}</h3>
    <button disabled={busy || version.isFetching} onClick={() => void version.refetch()}>Load current version availability</button>
    {version.isPending ? <p role="status">Loading exact version…</p> : version.isError ? <p role="alert">{version.error.message} Your version selection is preserved; displayed details may be stale.</p> : !detail ? <p role="alert">The returned version identity does not match this dataset.</p> : null}
    {detail && <>
      <p>Version <span className="identifier">{detail.id}</span> · published <time dateTime={detail.created_at}>{detail.created_at}</time></p>
      <p>Version availability: <strong>{detail.availability}</strong> · availability revision {detail.availability_revision}.</p>
      {detail.availability === "deprecated" && <p role="status">This version is deprecated. Its exact contents remain readable; prefer an active version for new work.</p>}
      {detail.availability === "archived" && <p role="status">This version is archived and hidden from the default list. Exact reads remain available. Unarchive restores {detail.prior_availability} availability.</p>}
      <dl><dt>Candidate</dt><dd className="identifier">{detail.candidate_id}</dd><dt>Generation</dt><dd className="identifier">{detail.generation_id}</dd><dt>Validation report</dt><dd className="identifier">{detail.report_id}</dd><dt>Manifest artifact</dt><dd className="identifier">{detail.manifest_artifact_id}</dd><dt>Recorded manifest SHA-256</dt><dd className="identifier">{detail.manifest_digest}</dd><dt>Recorded manifest bytes</dt><dd>{detail.manifest_size}</dd></dl>
      <p>These are recorded identities. Preview and download verify stored bytes before returning content.</p>
      {writable && <div aria-label="Version availability controls">
        {detail.availability === "archived" ? <button disabled={busy || version.isError || version.isFetching} onClick={() => void changeAvailability("unarchive")}>Unarchive version</button> : <>
          <button disabled={busy || version.isError || version.isFetching} onClick={() => void changeAvailability(detail.availability === "deprecated" ? "activate" : "deprecate")}>{detail.availability === "deprecated" ? "Activate version" : "Deprecate version"}</button>
          <button disabled={busy || version.isError || version.isFetching} onClick={() => void changeAvailability("archive")}>Archive version</button>
        </>}
      </div>}
    </>}
    {error && <p role="alert">{error} The selected version is preserved. Load current version availability before retrying.</p>}{message && <p role="status">{message}</p>}
    {manifest.isPending ? <p role="status">Loading manifest identity…</p> : manifest.isError ? <p role="alert">{manifest.error.message} <button onClick={() => void manifest.refetch()}>Retry manifest identity</button></p> : exactManifest ? <p>Manifest schema: {exactManifest.schema}. <a href={`${base}/manifest/download`}>Download exact manifest (verifies bytes)</a></p> : detail ? <p role="alert">Manifest identity does not match the published version. Download is blocked.</p> : null}
    {members.isPending ? <p role="status">Loading version members and lineage…</p> : members.isError ? <p role="alert">{members.error.message} <button onClick={() => void members.refetch()}>Retry version members</button></p> : !exactMembers ? <p role="alert">Version membership identity does not match the selected version.</p> : <>
      <h4>Pinned quality evidence</h4><p>Policy {exactMembers.quality_policy.name} · revision {exactMembers.quality_policy.revision} · <span className="identifier">{exactMembers.quality_policy.id}</span></p>
      <p>Report <span className="identifier">{exactMembers.quality_report.id}</span> · policy revision {exactMembers.quality_report.policy_revision} · captured draft revision {exactMembers.quality_report.source_revision} · evaluator {exactMembers.quality_report.evaluator_version}.</p>
      <p>{exactMembers.quality_report.state === "complete" && exactMembers.quality_report.required_rules_pass === true ? "Required publication gates passed for this captured report." : "The captured report does not establish passing required gates."}</p>
      {exactMembers.lineage_status === "partial" && <p role="status">Some interpretation lineage on this page is missing or partial. Missing pins do not imply review approval or complete provenance.</p>}
      {!exactMembers.items.length ? <p>No samples on this version page.</p> : <div className="table-scroll"><table><caption>Exact version members · {exactMembers.total ?? "Recorded"} total · page {pages.length}</caption><thead><tr><th scope="col">Sample</th><th scope="col">Frozen revision</th><th scope="col">Split</th><th scope="col">Artifact identity</th><th scope="col">Inspect</th></tr></thead><tbody>{exactMembers.items.map(item => <tr key={item.sample_id}><th scope="row" className="identifier">{item.sample_id}</th><td className="identifier">{item.sample_revision_id} · revision {item.revision}</td><td>{item.split}</td><td><span className="identifier">{item.artifact.id}<br />{item.artifact.sha256}</span><br />{item.artifact.size_bytes} bytes (recorded)</td><td><button onClick={() => { setSample(item); setLineage(item); }}>Preview exact sample {item.sample_id}</button><button onClick={() => setLineage(item)}>Inspect lineage for {item.sample_id}</button></td></tr>)}</tbody></table></div>}
      <nav aria-label="Version member pages"><button disabled={pages.length === 1 || members.isFetching} onClick={() => setPages(pages.slice(0, -1))}>Previous version members</button><button disabled={!exactMembers.next_after || members.isFetching} onClick={() => setPages([...pages, exactMembers.next_after])}>Next version members</button></nav>
    </>}
    {lineage && <LineageEvidence member={lineage} versionLabel={detail?.label ?? versionId} />}
    {sample && <aside aria-label="Exact version sample preview"><h4>Exact version sample preview</h4>
      <p>Sample <span className="identifier">{sample.sample_id}</span> · revision <span className="identifier">{sample.sample_revision_id}</span>.</p>
      {preview.isPending ? <p role="status">Verifying sample preview…</p> : preview.isError ? <p role="alert">{preview.error.message} Preview is unavailable; no bytes are shown. <button onClick={() => void preview.refetch()}>Retry verified preview</button></p> : !exactPreview ? <p role="alert">Preview identity does not match the frozen sample. No bytes are shown.</p> : <>
        <p>Verified sample SHA-256: <span className="identifier">{exactPreview.sha256}</span> · {exactPreview.size_bytes} bytes · {exactPreview.modality}.</p>
        {exactPreview.kind === "binary" ? <p>This is binary content. A text preview is unavailable; use the verified download.</p> : text ? <><pre>{text.text}</pre>{(text.truncated || exactPreview.truncated) && <p>Text preview is limited to the first 4 KiB.</p>}</> : <p role="alert">Text preview content is unavailable.</p>}
        {exactPreview.warning && <p role="status">{exactPreview.warning}</p>}
      </>}
      <p><a href={`${base}/samples/${sample.sample_id}/download`}>Download exact sample (verifies bytes)</a></p>
      <button onClick={() => setSample(null)}>Close sample preview</button>
    </aside>}
  </article>;
}

function LineageEvidence({ member, versionLabel }: { member: Member; versionLabel: string }) {
  const title = useId(), description = useId();
  return <section aria-label="Pinned member lineage"><h4>Pinned member lineage</h4>
    <p>Version {versionLabel}, sample <span className="identifier">{member.sample_id}</span>. Lines connect the version to its recorded evidence for this member; they do not assert a transformation order.</p>
    <svg role="img" aria-labelledby={`${title} ${description}`} viewBox={`0 0 900 ${Math.max(100, member.lineage.length * 60)}`} style={{ width: "100%", maxWidth: "900px" }}>
      <title id={title}>Recorded lineage for sample {member.sample_id}</title><desc id={description}>Each branch is labeled with its evidence kind and whether it is present, absent or partial. The following table provides full identities and descriptions.</desc>
      {member.lineage.map((item, index) => { const y = index * 60 + 30; return <g key={item.kind}><line x1="130" y1={Math.max(50, member.lineage.length * 30)} x2="370" y2={y} stroke="currentColor" strokeDasharray={item.status === "present" ? undefined : "6 4"} /><rect x="370" y={y - 20} width="510" height="40" rx="5" fill="none" stroke="currentColor" /><text x="385" y={y + 5} fill="currentColor">{item.kind.replaceAll("_", " ")} · {item.status}</text></g>; })}
      <rect x="5" y={Math.max(50, member.lineage.length * 30) - 22} width="125" height="44" rx="5" fill="var(--surface, white)" stroke="currentColor" /><text x="15" y={Math.max(50, member.lineage.length * 30) + 5} fill="currentColor">Exact version</text>
    </svg>
    <div className="table-scroll"><table><caption>Accessible lineage evidence for sample {member.sample_id}</caption><thead><tr><th scope="col">Evidence</th><th scope="col">Status</th><th scope="col">Pinned identity</th><th scope="col">Meaning</th></tr></thead><tbody>{member.lineage.map(item => <tr key={item.kind}><th scope="row">{item.kind.replaceAll("_", " ")}</th><td>{item.status}</td><td className="identifier">{item.id ?? "No pin recorded"}</td><td>{item.label}</td></tr>)}</tbody></table></div>
    {!member.lineage.length && <p>No lineage entries were returned. Interpretation completeness is unknown.</p>}
  </section>;
}
