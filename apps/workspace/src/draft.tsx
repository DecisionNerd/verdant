import { VerifyArtifact } from "./operations";
import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "./api";

type Member = { sample_id: string; sample_revision_id: string; split: string };
type DraftPage = { draft_id: string; revision: number; members: Member[]; next_after: string | null };
type Sample = { sample_id: string; revision_id: string; revision: number; modality: string; sha256: string; size_bytes: number };
export function DraftEditor({ namespace, dataset, csrf, writable }: { namespace: string; dataset: string; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [snapshot, setSnapshot] = useState<number | null>(null);
  const [sampleCursors, setSampleCursors] = useState<(string | null)[]>([null]);
  const [editRevision, setEditRevision] = useState<number | null>(null);
  const [sampleId, setSampleId] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [split, setSplit] = useState("train");
  const [inspected, setInspected] = useState<Member | null>(null);
  const [revisionInput, setRevisionInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}/draft/members`;
  const params = new URLSearchParams({ limit: "25" });
  const cursor = cursors.at(-1); if (cursor) params.set("after", cursor);
  if (snapshot !== null) params.set("revision", String(snapshot));
  const key = ["draft", namespace, dataset];
  const draft = useQuery({ queryKey: [...key, cursor, snapshot], queryFn: () => request<DraftPage>(`${base}?${params}`, csrf) });
  const sampleCursor = sampleCursors.at(-1);
  const samples = useQuery({ queryKey: ["samples", namespace, sampleCursor], enabled: writable,
    queryFn: () => request<{items: Sample[]; next_after: string | null}>(`/api/v1/namespaces/${namespace}/samples?limit=25${sampleCursor ? `&after=${sampleCursor}` : ""}`, csrf) });
  const inspection = useQuery({ queryKey: ["sample-revision", namespace, inspected?.sample_id, inspected?.sample_revision_id], enabled: inspected !== null,
    queryFn: () => request<Sample>(`/api/v1/namespaces/${namespace}/samples/${inspected!.sample_id}/revisions/${inspected!.sample_revision_id}/summary`, csrf) });
  async function refresh(adopt = false) {
    setCursors([null]); setSnapshot(null);
    await cache.invalidateQueries({ queryKey: key });
    if (adopt) {
      const current = await request<DraftPage>(`${base}?limit=25`, csrf);
      setEditRevision(current.revision);
      setMessage(`Current draft revision ${current.revision} loaded. Selected input is preserved; review before saving.`);
    }
  }
  async function write(event: FormEvent) {
    event.preventDefault(); if (!draft.data) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const changed = await request<{revision: number}>(`${base}/${sampleId}`, csrf, { expected_revision: editRevision ?? draft.data.revision, sample_revision_id: revisionId, split }, "PUT");
      setEditRevision(changed.revision);
      await refresh(); setMessage("Draft membership saved.");
    } catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }
  async function remove(member: Member) {
    if (!draft.data) return; setBusy(true); setError("");
    try { await request(`${base}/${member.sample_id}?expected_revision=${draft.data.revision}`, csrf, {}, "DELETE"); await refresh(); setMessage("Membership removed."); }
    catch (failure) { setError((failure as Error).message); } finally { setBusy(false); }
  }
  return <section className="draft" aria-labelledby="draft-title"><h2 id="draft-title">Draft membership</h2>
    <button disabled={busy} onClick={() => { void refresh(true).catch(failure => setError((failure as Error).message)); }}>Load current draft</button>
    <form onSubmit={event => { event.preventDefault(); setCursors([null]); setSnapshot(Number(revisionInput)); }}><label>Inspect draft revision <input type="number" min="0" max="9007199254740991" step="1" required value={revisionInput} onChange={event => setRevisionInput(event.target.value)} /></label><button>Open revision</button></form>
    {inspected && <aside aria-label="Exact sample revision"><h3>Exact sample revision</h3>{inspection.isPending ? <p role="status">Loading revision…</p> : inspection.isError ? <p role="alert">{inspection.error.message}</p> : <dl><dt>Sample</dt><dd className="identifier">{inspection.data.sample_id}</dd><dt>Revision</dt><dd>{inspection.data.revision} · {inspection.data.revision_id}</dd><dt>SHA-256</dt><dd className="identifier">{inspection.data.sha256}</dd><dt>Bytes</dt><dd>{inspection.data.size_bytes}</dd><dt>Modality</dt><dd>{inspection.data.modality}</dd></dl>}{writable && inspection.data && !inspection.isError && <VerifyArtifact key={inspection.data.revision_id} namespace={namespace} sample={inspection.data.sample_id} revision={inspection.data.revision_id} csrf={csrf} />}<button onClick={() => setInspected(null)}>Close inspection</button></aside>}
    {error && <p role="alert">{error} Your selected sample and split are preserved.</p>}{message && <p role="status">{message}</p>}
    {draft.isPending ? <p role="status">Loading draft…</p> : draft.isError ? <p role="alert">{draft.error.message}</p> : <>
      <p>Draft revision {draft.data.revision}{draft.isFetching ? " · Refreshing…" : ""}</p>
      {!draft.data.members.length ? <p>No samples on this draft page.</p> : <div className="table-scroll"><table><caption>Exact sample revisions in this draft</caption><thead><tr><th scope="col">Sample</th><th scope="col">Revision</th><th scope="col">Split</th>{writable && <th scope="col">Actions</th>}</tr></thead><tbody>{draft.data.members.map(member => <tr key={member.sample_id}><td className="identifier">{member.sample_id}</td><td className="identifier"><button onClick={() => setInspected(member)}>Inspect {member.sample_revision_id}</button></td><td>{member.split}</td>{writable && <td><button onClick={() => { setEditRevision(draft.data.revision); setSampleId(member.sample_id); setRevisionId(member.sample_revision_id); setSplit(member.split); }}>Edit split</button><button disabled={busy} onClick={() => void remove(member)}>Remove</button></td>}</tr>)}</tbody></table></div>}
      <nav aria-label="Draft pages"><button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0,-1))}>Previous</button><button disabled={!draft.data.next_after} onClick={() => { setSnapshot(draft.data.revision); setCursors([...cursors, draft.data.next_after]); }}>Next</button></nav>
      {writable && <form onSubmit={event => void write(event)}><h3>Add sample or change split</h3>
        {samples.isPending ? <p role="status">Loading samples…</p> : samples.isError ? <p role="alert">{samples.error.message}</p> : <>
          <label>Available sample <select value="" onChange={event => { const sample = samples.data.items.find(item => item.sample_id === event.target.value); if (sample) { setEditRevision(draft.data.revision); setSampleId(sample.sample_id); setRevisionId(sample.revision_id); } }}><option value="">Choose a sample revision</option>{samples.data.items.map(sample => <option key={sample.sample_id} value={sample.sample_id}>{sample.modality} · {sample.sample_id} · revision {sample.revision}</option>)}</select></label>
          {!samples.data.items.length && <p>No registered samples on this page.</p>}
          <div><button type="button" disabled={sampleCursors.length === 1} onClick={() => setSampleCursors(sampleCursors.slice(0,-1))}>Previous samples</button><button type="button" disabled={!samples.data.next_after} onClick={() => setSampleCursors([...sampleCursors, samples.data.next_after])}>Next samples</button></div>
        </>}
        <p className="identifier">Selected sample: {sampleId || "None"}<br />Exact revision: {revisionId || "None"}</p>
        <label>Split <input required maxLength={50} pattern="[a-z][a-z0-9_-]*" value={split} onChange={event => setSplit(event.target.value)} /></label>
        <button disabled={busy || !sampleId || !revisionId || draft.isFetching}>Save membership</button>
      </form>}
    </>}
  </section>;
}
