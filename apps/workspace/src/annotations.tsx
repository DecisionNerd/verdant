import { useEffect, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Schema = { task: "single_categorical_text.v1"; labels: string[]; instructions: string; instruction_revision: string };
type Cohort = { id: string; name: string; origin: string; image: string; contributors: { principal_id: string; native_user_id: number; reviewer: boolean }[] };
type Integration = { available: boolean; reason: string; shared_trust: string; cohorts: Cohort[] };
type Assignment = { id: string; sample_id: string; sample_revision_id: string; source_revision: number; schema_digest: string; task_schema: Schema; contributors: string[]; editor_url: string; native_task_id: number; cohort_id: string };
type Original = { id: string; digest: string; principal_id: string; native_annotation_id: number; sequence: number; payload: { label: string | null; state: string; assistance: string; issuer: string; subject: string } };
type Detail = { assignment: Assignment; originals: Original[]; latest: Original[]; missing_contributors: string[]; canonical_id: string | null };
type ContributorResult = { original_id?: string; digest?: string; state: string; assistance: string; label: string | null };
type Unit = { assignment_id: string; sample_id: string; sample_revision_id: string; contributors: Record<string, ContributorResult> };
type Pair = { left: string; right: string; overlap: number; agreed: number; declared_independent_overlap: number; observed_agreement: number | null; agreement_95_wilson: number[] | null; cohen_kappa: number | null; kappa_undefined_reason: string | null; confusion: Record<string, Record<string, number>>; prevalence_left: Record<string, number>; prevalence_right: Record<string, number> };
type Report = { id: string; digest: string; payload: { units: Unit[]; pairs: Pair[]; counts: Record<string, number>; disagreement_queue: string[]; limits: string } };
type Canonical = { id: string; digest: string; payload: { annotation: { payload: { label: string | null } }; unresolved: boolean } };
type DraftPage = { revision: number; members: { sample_id: string; sample_revision_id: string }[]; next_after: string | null };
const denied = (error: unknown) => error instanceof ApiError && [401, 403].includes(error.status);
const key = () => crypto.randomUUID();
const percent = (value: number | null) => value === null ? "undefined" : `${(100 * value).toFixed(1)}%`;
export function Annotations({ namespace, dataset, csrf, writable }: { namespace: string; dataset: string; csrf: string; writable: boolean }) {
  const cache = useQueryClient();
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}`;
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [cohort, setCohort] = useState("");
  const [contributors, setContributors] = useState<string[]>([]);
  const [sample, setSample] = useState("");
  const [nativeProject, setNativeProject] = useState("");
  const [nativeTask, setNativeTask] = useState("");
  const [labels, setLabels] = useState("positive\nnegative");
  const [instructions, setInstructions] = useState("Choose the sentiment from the text.");
  const [instructionVersion, setInstructionVersion] = useState("v1");
  const [report, setReport] = useState<Report | null>(null);
  const [reportInput, setReportInput] = useState("");
  const [label, setLabel] = useState("");
  const [rationale, setRationale] = useState("");
  const [canonical, setCanonical] = useState<Canonical | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [accessDenied, setAccessDenied] = useState(false);
  const integration = useQuery({ queryKey: ["annotation-integration", namespace], queryFn: () => request<Integration>(`/api/v1/namespaces/${namespace}/annotation-integration`, csrf) });
  const assignments = useQuery({ queryKey: ["annotations", namespace, dataset, cursor], queryFn: () => request<Page<Assignment>>(`${base}/annotations?limit=25${cursor ? `&after=${cursor}` : ""}`, csrf) });
  const draft = useQuery({ queryKey: ["annotation-draft", namespace, dataset], queryFn: async () => {
    let after: string | null = null; let revision: number | undefined; const members: DraftPage["members"] = [];
    do { const page: DraftPage = await request<DraftPage>(`${base}/draft/members?limit=100${after ? `&after=${after}&revision=${revision}` : ""}`, csrf); revision = page.revision; members.push(...page.members); after = page.next_after;
      if (members.length > 500) throw new Error("Annotation workspace supports at most 500 dataset members.");
    } while (after); return { revision: revision!, members };
  } });
  const detail = useQuery({ queryKey: ["annotation", namespace, dataset, selected], enabled: !!selected, queryFn: () => request<Detail>(`${base}/annotations/${selected}`, csrf) });
  useEffect(() => { setCanonical(null); setLabel(""); }, [selected]);
  const activeCohort = integration.data?.cohorts.find(item => item.id === cohort);
  async function perform(action: () => Promise<void>) {
    if (busy) return; setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (failure) { setError((failure as Error).message); if (denied(failure)) setAccessDenied(true); } finally { setBusy(false); }
  }
  async function create(event: FormEvent) {
    event.preventDefault(); if (!draft.data || !writable || !integration.data?.available) return;
    await perform(async () => {
      const member = draft.data.members.find(item => item.sample_id === sample); if (!member) throw new Error("Choose an exact current sample.");
      const created = await request<Assignment>(`${base}/annotations`, csrf, { request_key: key(), expected_revision: draft.data.revision, cohort_id: cohort, sample_id: member.sample_id, sample_revision_id: member.sample_revision_id, native_project_id: Number(nativeProject), native_task_id: Number(nativeTask), contributors,
        task_schema: { task: "single_categorical_text.v1", labels: labels.split("\n").map(value => value.trim()).filter(Boolean), instructions, instruction_revision: instructionVersion } });
      setSelected(created.id); setChosen(previous => [...new Set([...previous, created.id])]); await cache.invalidateQueries({ queryKey: ["annotations", namespace, dataset] }); setMessage("Assignment created with exact task, instruction, content and contributor pins.");
    });
  }
  async function loadReport(id: string) { await perform(async () => { const saved = await request<Report>(`${base}/agreement/${id}`, csrf); setReport(saved); setReportInput(saved.id); }); }
  if (accessDenied || [integration.error, assignments.error, draft.error, detail.error].some(denied)) return <p role="alert">Annotation access changed. Reload your workspace.</p>;
  return <section aria-labelledby="annotations-title"><h2 id="annotations-title">Annotations and agreement</h2>
    <p>Supported: single categorical text tasks, up to 500 dataset members and 100 native tasks per project. Other task types are unavailable.</p>
    {integration.isPending ? <p role="status">Checking labeling integration…</p> : integration.isError ? <p role="alert">{integration.error.message}</p> : <><p role={integration.data.available ? undefined : "status"}>{integration.data.reason}</p><p>{integration.data.shared_trust}</p></>}
    {error && <p role="alert">{error} Inputs and original evidence are preserved.</p>}{message && <p role="status">{message}</p>}
    <button disabled={busy} onClick={() => void perform(async () => { await Promise.all([integration.refetch(), assignments.refetch(), draft.refetch(), ...(selected ? [detail.refetch()] : [])]); setMessage("Current annotation state loaded; form inputs preserved."); })}>Refresh annotation state</button>
    {writable && <form onSubmit={event => void create(event)}><fieldset disabled={busy || !integration.data?.available || draft.isError}><legend>Create labeling assignment</legend>
      <label>Trust cohort <select aria-label="Trust cohort" required value={cohort} onChange={event => { setCohort(event.target.value); setContributors([]); }}><option value="">Choose admitted cohort</option>{integration.data?.cohorts.map(item => <option key={item.id} value={item.id}>{item.name} · {item.origin}</option>)}</select></label>
      {activeCohort && <><p>Pinned image: <span className="identifier">{activeCohort.image}</span></p><fieldset><legend>Independent contributors</legend>{activeCohort.contributors.map(person => <label key={person.principal_id}><input type="checkbox" checked={contributors.includes(person.principal_id)} onChange={event => setContributors(event.target.checked ? [...contributors, person.principal_id] : contributors.filter(id => id !== person.principal_id))} />Native {person.native_user_id} · {person.principal_id}{person.reviewer ? " · reviewer" : ""}</label>)}</fieldset></>}
      <label>Annotation sample <select aria-label="Annotation sample" required value={sample} onChange={event => setSample(event.target.value)}><option value="">Choose exact dataset member</option>{draft.data?.members.map(member => <option key={member.sample_id} value={member.sample_id}>{member.sample_id} · {member.sample_revision_id}</option>)}</select></label>
      <label>Native project ID <input type="number" min="1" max="9007199254740991" step="1" required value={nativeProject} onChange={event => setNativeProject(event.target.value)} /></label>
      <label>Native task ID <input type="number" min="1" max="9007199254740991" step="1" required value={nativeTask} onChange={event => setNativeTask(event.target.value)} /></label>
      <p>A cohort operator prepares the native task with the exact content and revision ID. Assignment verifies the native project schema, instructions and source digest before opening the stock editor.</p>
      <label>Categories (one per line)<textarea aria-label="Categories (one per line)" required maxLength={3000} value={labels} onChange={event => setLabels(event.target.value)} /></label>
      <label>Instruction revision <input required maxLength={100} value={instructionVersion} onChange={event => setInstructionVersion(event.target.value)} /></label>
      <label>Labeling instructions <textarea aria-label="Labeling instructions" required maxLength={4000} value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
      <button disabled={!contributors.length || !sample || !cohort}>Create assignment</button>
    </fieldset></form>}
    {draft.isError && <p role="alert">{draft.error.message}</p>}
    {assignments.isPending ? <p role="status">Loading assignments…</p> : assignments.isError ? <p role="alert">{assignments.error.message}</p> : <><fieldset><legend>Overlap assignments for report</legend>{assignments.data.items.map(item => <div key={item.id}><label><input type="checkbox" checked={chosen.includes(item.id)} onChange={event => setChosen(event.target.checked ? [...chosen, item.id] : chosen.filter(id => id !== item.id))} />{item.sample_id} · task {item.native_task_id}</label><button onClick={() => setSelected(item.id)}>Inspect assignment {item.native_task_id}</button></div>)}</fieldset><button disabled={!cursor} onClick={() => setCursor(null)}>First assignments</button><button disabled={!assignments.data.next_after} onClick={() => setCursor(assignments.data.next_after)}>Next assignments</button></>}
    {selected && <article aria-label="Selected annotation assignment">{detail.isPending ? <p role="status">Loading annotation…</p> : detail.isError ? <p role="alert">{detail.error.message}</p> : detail.data && <>
      <h3>Task {detail.data.assignment.native_task_id}</h3><p>Assignment {detail.data.assignment.id}<br />Sample revision {detail.data.assignment.sample_revision_id}<br />Dataset source revision {detail.data.assignment.source_revision}<br />Schema {detail.data.assignment.schema_digest}</p>
      {integration.data?.available && integration.data.cohorts.some(item => item.id === detail.data.assignment.cohort_id && new URL(detail.data.assignment.editor_url).origin === item.origin) ? <a href={detail.data.assignment.editor_url} target="_blank" rel="noopener noreferrer">Open stock labeling editor</a> : <p role="status">The allowed native origin is unavailable.</p>}
      <p>Sign in as yourself. Return to this workspace tab and import authoritative native results. No result from the return URL is trusted.</p>
      {writable && <button disabled={busy || !integration.data?.available} onClick={() => void perform(async () => { const result = await request<Detail>(`${base}/annotations/${selected}/import`, csrf, { expected_original_count: detail.data.originals.length }); cache.setQueryData(["annotation", namespace, dataset, selected], result); setMessage(`Verified import: ${result.originals.length} immutable original revisions.`); })}>Return and import native annotations</button>}
      <p>Original revisions: {detail.data.originals.length} · missing contributors: {detail.data.missing_contributors.length}. Current canonical: {canonical?.id ?? detail.data.canonical_id ?? "unresolved"}.</p>
      {detail.data.latest.map(original => <p key={original.id}>{original.principal_id}: {original.payload.label ?? original.payload.state} · {original.payload.assistance} · original {original.id} · digest {original.digest}</p>)}
      <details><summary>Immutable original and cancellation history</summary><pre>{JSON.stringify(detail.data.originals, null, 2)}</pre></details>
    </>}</article>}
    {writable && <button disabled={busy || !chosen.length || chosen.length > 100} onClick={() => void perform(async () => { const saved = await request<Report>(`${base}/agreement`, csrf, { request_key: key(), assignment_ids: chosen }); setReport(saved); setReportInput(saved.id); setMessage("Immutable pre-adjudication report saved."); })}>Create agreement report</button>}
    <form onSubmit={event => { event.preventDefault(); void loadReport(reportInput); }}><label>Saved agreement report ID <input value={reportInput} required onChange={event => setReportInput(event.target.value)} /></label><button disabled={busy}>Open saved report</button></form>
    {report && <article aria-label="Agreement report"><h3>Agreement report</h3><p>{report.id} · digest {report.digest}</p><p>{report.payload.limits}</p><p>{Object.entries(report.payload.counts).map(([name, count]) => `${name}: ${count}`).join(" · ")}</p>
      {report.payload.pairs.map(pair => <article key={`${pair.left}/${pair.right}`} aria-label="Pairwise agreement"><h4>{pair.left} / {pair.right}</h4><p>Overlap {pair.overlap} · agreed {pair.agreed} · declared independent overlap {pair.declared_independent_overlap}</p><p>Observed agreement {percent(pair.observed_agreement)} · 95% Wilson {pair.agreement_95_wilson?.map(value => percent(value)).join("–") ?? "undefined: no overlap"} · Cohen κ {pair.cohen_kappa?.toFixed(3) ?? `undefined: ${pair.kappa_undefined_reason}`}</p><details><summary>Confusion and prevalence counts</summary><pre>{JSON.stringify({ confusion: pair.confusion, prevalence_left: pair.prevalence_left, prevalence_right: pair.prevalence_right }, null, 2)}</pre></details></article>)}
      <h4>Side-by-side originals and disagreement queue</h4><div className="table-scroll"><table><caption>Exact original identities before adjudication</caption><thead><tr><th scope="col">Sample</th><th scope="col">Contributors</th><th scope="col">Review</th></tr></thead><tbody>{report.payload.units.map(unit => <tr key={unit.assignment_id}><td>{unit.sample_id}</td><td><div className="annotation-comparison">{Object.entries(unit.contributors).map(([principal, value]) => <article key={principal}><strong>{principal}</strong><p>{value.label ?? value.state} · {value.assistance}</p><p>{value.original_id ?? "No original"}<br />{value.digest}</p></article>)}</div></td><td>{report.payload.disagreement_queue.includes(unit.assignment_id) ? "Disagreement or incomplete" : "Agreement"}<button onClick={() => setSelected(unit.assignment_id)}>Review this sample</button></td></tr>)}</tbody></table></div>
      {writable && selected && detail.data && report.payload.units.some(unit => unit.assignment_id === selected) && <form onSubmit={event => { event.preventDefault(); void perform(async () => { const saved = await request<Canonical>(`${base}/adjudications`, csrf, { request_key: key(), report_id: report.id, assignment_id: selected, expected_canonical_id: canonical?.id ?? detail.data.canonical_id, label: label || null, rationale }); setCanonical(saved); await detail.refetch(); await cache.invalidateQueries({ queryKey: ["truth-workspace", namespace, dataset] }); setMessage(`New immutable canonical review ${saved.id}${saved.payload.unresolved ? "; unresolved and excluded from truth" : " saved"}.`); }); }}><fieldset disabled={busy}><legend>Adjudicate selected sample</legend><label>Canonical category <select aria-label="Canonical category" value={label} onChange={event => setLabel(event.target.value)}><option value="">Unresolved / abstain</option>{detail.data.assignment.task_schema.labels.map(value => <option key={value}>{value}</option>)}</select></label><label>Adjudication rationale <textarea aria-label="Adjudication rationale" required maxLength={2000} value={rationale} onChange={event => setRationale(event.target.value)} /></label><button>Save canonical review</button></fieldset></form>}
    </article>}
  </section>;
}
