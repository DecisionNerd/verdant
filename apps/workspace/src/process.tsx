import { useEffect, useRef, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request } from "./api";

type Profile = { id: string; name: string; revision: number; provider: string; transport: "native" | "openrouter"; endpoint: string; model: string; identity_kind: string; config_digest: string; credential_status: "worker_unverified"; settings: { temperature: number; top_p: number; max_output_tokens: number }; capabilities: string[] };
type Step = { name: string; kind: "preprocess" | "model"; transform: "identity" | "strip" | "lowercase"; profile_id: string | null; prompt: string; output_contract: "text" | "json"; profile_digest?: string };
type Flow = { id: string; name: string; revision: number; config_digest: string; definition: { steps: Step[] } };
type Operation = { id: string; status: string; epoch: number };
type Detail = { operation: Operation; flow: Flow; input: { dataset_id: string; candidate_id: string; version_id: string | null; generation_id: string; member_count: number; completed_count: number; trial_of: string | null }; needs_reconciliation: boolean; attempts: { epoch: number; status: string; error_code: string | null; provider_run_id?: string | null; invocation_key?: string | null }[]; outputs: { sample_id: string; sample_revision_id: string; step_position: number; accepted_epoch: number; receipt_id: string; input_digest: string; output_digest: string; profile_id: string | null; profile_digest: string | null; result: { text: string; input_tokens: number | null; output_tokens: number | null; resolved_model: string | null; cost: null } }[]; next_after: string | null };
type Page<T> = { items: T[]; next_after: string | null };
function useProcessPage<T>(key: string[], path: string, csrf: string, interval: number) {
  const query = useInfiniteQuery({ queryKey: key,
    queryFn: ({ pageParam }) => request<Page<T>>(`${path}?limit=100${pageParam ? `&after=${pageParam}` : ""}`, csrf),
    initialPageParam: "", getNextPageParam: page => page.next_after ?? undefined, refetchInterval: interval });
  return { ...query, data: query.data ? { items: query.data.pages.flatMap(page => page.items), next_after: query.data.pages.at(-1)?.next_after } : undefined };
}
const newStep = (): Step => ({ name: "Clean text", kind: "preprocess", transform: "strip", profile_id: null, prompt: "", output_contract: "text" });

export function ProcessWorkspace({ namespace, dataset, csrf, writable }: { namespace: string; dataset: string; csrf: string; writable: boolean }) {
  const prefix = `/api/v1/namespaces/${namespace}`;
  const path = `${prefix}/datasets/${dataset}`;
  const cache = useQueryClient();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [denied, setDenied] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const profiles = useProcessPage<Profile>(["model-profiles", namespace], `${prefix}/model-profiles`, csrf, 5000);
  const flows = useProcessPage<Flow>(["process-flows", namespace, dataset], `${path}/process-flows`, csrf, 3000);
  const runs = useProcessPage<Operation>(["process-runs", namespace, dataset], `${path}/process-runs`, csrf, 2000);
  const candidates = useProcessPage<{ id: string; state: string; source_revision: number }>(["process-candidates", namespace, dataset], `${path}/candidates`, csrf, 5000);
  const versions = useProcessPage<{ id: string; label: string }>(["process-versions", namespace, dataset], `${path}/versions`, csrf, 5000);
  const [profileName, setProfileName] = useState("Default model");
  const [provider, setProvider] = useState("gemini");
  const [transport, setTransport] = useState("native");
  const [model, setModel] = useState("");
  const [identityKind, setIdentityKind] = useState("exact");
  const [credentialRef, setCredentialRef] = useState("");
  const [temperature, setTemperature] = useState(0);
  const [topP, setTopP] = useState(1);
  const [tokens, setTokens] = useState(256);
  const [json, setJson] = useState(false);
  const [profileRevision, setProfileRevision] = useState(0);
  const [flowName, setFlowName] = useState("Text processing");
  const [flowRevision, setFlowRevision] = useState(0);
  const [steps, setSteps] = useState<Step[]>([newStep()]);
  const [flowId, setFlowId] = useState("");
  const [inputKind, setInputKind] = useState("candidate");
  const [inputId, setInputId] = useState("");
  const [members, setMembers] = useState(100);
  const [calls, setCalls] = useState(100);
  const [runId, setRunId] = useState(new URLSearchParams(window.location.search).get("process") ?? "");
  const [after, setAfter] = useState("");
  const pendingStart = useRef<Record<string, unknown> | null>(null);
  const [uncertainStart, setUncertainStart] = useState(false);
  const detail = useQuery({ queryKey: ["process-detail", namespace, runId, after], queryFn: () => request<Detail>(`${prefix}/process-runs/${runId}?limit=10${after ? `&after_sample=${after}` : ""}`, csrf), enabled: !!runId, refetchInterval: 2000 });
  const readError = [profiles, flows, runs, candidates, versions, detail].find(q => q.isError)?.error;
  const accessLost = denied || (readError instanceof ApiError && [401, 403].includes(readError.status));
  const stale = !!readError;
  async function act(work: () => Promise<void>) {
    if (busy || accessLost) return;
    setBusy(true); setError(""); setMessage("");
    try { await work(); }
    catch (failure) { if (alive.current) { setError((failure as Error).message); if (failure instanceof ApiError && [401, 403].includes(failure.status)) setDenied(true); } }
    finally { if (alive.current) setBusy(false); }
  }
  function selectRun(id: string) {
    setRunId(id); setAfter("");
    const url = new URL(window.location.href); url.searchParams.set("process", id); window.history.replaceState({}, "", url);
  }
  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      const result = await request<Profile>(`${prefix}/model-profiles`, csrf, { name: profileName, expected_revision: profileRevision, provider, transport, model, identity_kind: identityKind, credential_ref: credentialRef, settings: { temperature, top_p: topP, max_output_tokens: tokens }, capabilities: json ? ["text", "json"] : ["text"] });
      if (!alive.current) return;
      setProfileRevision(result.revision); setCredentialRef("");
      await cache.invalidateQueries({ queryKey: ["model-profiles", namespace] });
      setMessage(`Model profile ${result.name} revision ${result.revision} saved. Worker credential status is unverified.`);
    });
  }
  async function saveFlow(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      const result = await request<Flow>(`${path}/process-flows`, csrf, { name: flowName, expected_revision: flowRevision, steps: steps.map(({ profile_digest: _digest, ...step }) => step) });
      if (!alive.current) return;
      setFlowRevision(result.revision); setFlowId(result.id);
      await cache.invalidateQueries({ queryKey: ["process-flows", namespace, dataset] });
      setMessage(`Flow revision ${result.revision} saved with immutable profile assignments.`);
    });
  }
  function patchStep(index: number, patch: Partial<Step>) { setSteps(previous => previous.map((step, i) => i === index ? { ...step, ...patch } : step)); }
  async function start(trial?: Detail) {
    await act(async () => {
      if (!pendingStart.current) pendingStart.current = trial ? { request_key: crypto.randomUUID(), flow_id: trial.flow.id, candidate_id: trial.input.version_id ? null : trial.input.candidate_id, version_id: trial.input.version_id, max_members: members, max_model_calls: calls, trial_of: trial.operation.id } : { request_key: crypto.randomUUID(), flow_id: flowId, candidate_id: inputKind === "candidate" ? inputId : null, version_id: inputKind === "version" ? inputId : null, max_members: members, max_model_calls: calls };
      setUncertainStart(true);
      const result = await request<Operation>(`${path}/process-runs`, csrf, pendingStart.current);
      if (!alive.current) return;
      pendingStart.current = null; setUncertainStart(false); selectRun(result.id);
      await cache.invalidateQueries({ queryKey: ["process-runs", namespace, dataset] });
      setMessage(trial ? "Fresh trial admitted with new logical effects." : "Run admitted on pinned input.");
    });
  }
  async function action(action: string) {
    if (!detail.data) return;
    await act(async () => { await request(`${prefix}/process-runs/${runId}/actions`, csrf, { action, epoch: detail.data!.operation.epoch }); await detail.refetch(); await runs.refetch(); });
  }
  if (accessLost) return <section aria-label="Process flows"><p role="alert">Process access changed. Reload your workspace before continuing.</p></section>;
  const d = detail.data;
  return <section aria-label="Process flows"><h3>Process flows</h3>
    <p>Immutable sequential steps over a sealed candidate or exact dataset version. Only verified text content enters model calls.</p>
    {readError && <p role="alert">Saved process details may be stale: {readError.message} <button onClick={() => { void profiles.refetch(); void flows.refetch(); void runs.refetch(); void candidates.refetch(); void versions.refetch(); if (runId) void detail.refetch(); }}>Refresh process details</button></p>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <details><summary>Model profiles</summary>
      <ul>{profiles.data?.items.map(p => <li key={p.id}>{p.name} · revision {p.revision} · {p.provider} via {p.transport} · {p.model} · {p.identity_kind === "mutable_alias" ? "Mutable alias: provider may change this model" : "Exact identifier declared"} · worker credential unverified<br /><span className="identifier">{p.config_digest}</span> · {p.endpoint}
        {writable && <button onClick={() => { setProfileName(p.name); setProfileRevision(p.revision); setProvider(p.provider); setTransport(p.transport ?? "native"); setModel(p.model); setIdentityKind(p.identity_kind); setTemperature(p.settings.temperature); setTopP(p.settings.top_p); setTokens(p.settings.max_output_tokens); setJson(p.capabilities.includes("json")); setCredentialRef(""); }}>Version {p.name} r{p.revision}</button>}</li>)}</ul>
      {profiles.hasNextPage && <button disabled={profiles.isFetchingNextPage} onClick={() => void profiles.fetchNextPage()}>More model profiles</button>}
      {writable && <form onSubmit={event => void saveProfile(event)}><fieldset disabled={busy || stale}><legend>Save immutable model profile</legend>
        <label>Profile name <input required value={profileName} onChange={e => { setProfileName(e.target.value); setProfileRevision(0); }} /></label><p>Expected profile revision: {profileRevision}</p>
        <label>Provider <select value={provider} onChange={e => setProvider(e.target.value)}><option value="gemini">Gemini</option><option value="glm">GLM</option></select></label>
        <label>Transport <select value={transport} onChange={e => { setTransport(e.target.value); if (e.target.value === "openrouter") setIdentityKind("mutable_alias"); }}><option value="native">Direct provider</option><option value="openrouter">OpenRouter</option></select></label>
        {transport === "openrouter" && <p>Use a google/ model for Gemini or z-ai/ model for GLM. OpenRouter routing may change; the route and identifier are pinned, not model weights.</p>}
        <label>Model identifier <input required value={model} onChange={e => setModel(e.target.value)} /></label>
        <label>Reproducibility <select value={identityKind} onChange={e => setIdentityKind(e.target.value)}><option value="exact" disabled={transport === "openrouter"}>Exact identifier</option><option value="mutable_alias">Mutable alias (may change at provider)</option></select></label>
        <label>Operator credential reference <input required autoComplete="off" placeholder="VERDANT_MODEL_SECRET_TEAM" pattern="VERDANT_MODEL_SECRET_[A-Z0-9_]+" value={credentialRef} onChange={e => setCredentialRef(e.target.value)} /></label><p>Enter an operator environment reference. Credential values are never entered here.</p>
        <label>Temperature <input type="number" min="0" max="1" step="0.1" value={temperature} onChange={e => setTemperature(Number(e.target.value))} /></label><label>Top p <input type="number" min="0.01" max="1" step="0.01" value={topP} onChange={e => setTopP(Number(e.target.value))} /></label><label>Maximum output tokens <input type="number" min="1" max="4096" value={tokens} onChange={e => setTokens(Number(e.target.value))} /></label>
        <label><input type="checkbox" checked={json} onChange={e => setJson(e.target.checked)} /> Supports JSON output</label><button>Save profile version</button>
      </fieldset></form>}
    </details>
    <label>Saved flow version <select value={flowId} onChange={e => setFlowId(e.target.value)}><option value="">Select a flow</option>{flows.data?.items.map(f => <option key={f.id} value={f.id}>{f.name} r{f.revision} · {f.id}</option>)}</select></label>
    {flows.hasNextPage && <button disabled={flows.isFetchingNextPage} onClick={() => void flows.fetchNextPage()}>More flow versions</button>}
    {writable && <button disabled={!flowId || busy} onClick={() => { const flow = flows.data?.items.find(f => f.id === flowId); if (flow) { setFlowName(flow.name); setFlowRevision(flow.revision); setSteps(flow.definition.steps); } }}>Load selected flow for versioning</button>}
    {writable && <form onSubmit={event => void saveFlow(event)}><fieldset disabled={busy || stale || uncertainStart}><legend>Author flow version</legend>
      <label>Flow name <input required maxLength={100} value={flowName} onChange={e => { setFlowName(e.target.value); setFlowRevision(0); }} /></label><p>Expected flow revision: {flowRevision}</p>
      {steps.map((step, index) => <fieldset key={index}><legend>Step {index + 1}</legend><label>Step name <input required value={step.name} onChange={e => patchStep(index, { name: e.target.value })} /></label>
        <label>Step type <select value={step.kind} onChange={e => patchStep(index, e.target.value === "model" ? { kind: "model", transform: "identity", prompt: "Describe the text.", profile_id: null } : { kind: "preprocess", transform: "strip", profile_id: null, prompt: "", output_contract: "text" })}><option value="preprocess">Preprocess text</option><option value="model">Model call</option></select></label>
        {step.kind === "preprocess" ? <label>Transformation <select value={step.transform} onChange={e => patchStep(index, { transform: e.target.value as Step["transform"] })}><option value="identity">Identity</option><option value="strip">Trim whitespace</option><option value="lowercase">Lowercase</option></select></label> : <>
          <label>Step model profile <select required value={step.profile_id ?? ""} onChange={e => patchStep(index, { profile_id: e.target.value })}><option value="">Select exact profile version</option>{profiles.data?.items.map(p => <option key={p.id} value={p.id}>{p.name} r{p.revision} · {p.provider}/{p.model}</option>)}</select></label>
          <label>Prompt <textarea required maxLength={4096} value={step.prompt} onChange={e => patchStep(index, { prompt: e.target.value })} /></label><label>Output contract <select value={step.output_contract} onChange={e => patchStep(index, { output_contract: e.target.value as Step["output_contract"] })}><option value="text">Text</option><option value="json">JSON</option></select></label>
        </>}
        {steps.length > 1 && <button type="button" onClick={() => setSteps(previous => previous.filter((_, i) => i !== index))}>Remove step {index + 1}</button>}
      </fieldset>)}
      <button type="button" disabled={steps.length >= 8} onClick={() => setSteps(previous => [...previous, { ...newStep(), name: `Step ${previous.length + 1}` }])}>Add sequential step</button><button>Save flow version</button>
    </fieldset></form>}
    {candidates.hasNextPage && <button disabled={candidates.isFetchingNextPage} onClick={() => void candidates.fetchNextPage()}>More process candidates</button>}{versions.hasNextPage && <button disabled={versions.isFetchingNextPage} onClick={() => void versions.fetchNextPage()}>More process versions</button>}
    <fieldset disabled={!writable || busy || stale || uncertainStart}><legend>Run pinned flow</legend>
      <label>Input kind <select value={inputKind} onChange={e => { setInputKind(e.target.value); setInputId(""); }}><option value="candidate">Sealed candidate</option><option value="version">Exact dataset version</option></select></label>
      <label>Pinned input <select value={inputId} onChange={e => setInputId(e.target.value)}><option value="">Select exact input</option>{inputKind === "candidate" ? candidates.data?.items.filter(c => c.state === "sealed").map(c => <option key={c.id} value={c.id}>Draft revision {c.source_revision} · {c.id}</option>) : versions.data?.items.map(v => <option key={v.id} value={v.id}>{v.label} · {v.id}</option>)}</select></label>
      <label>Member budget <input type="number" min="1" max="1000" value={members} onChange={e => setMembers(Number(e.target.value))} /></label><label>Model call budget <input type="number" min="0" max="1000" value={calls} onChange={e => setCalls(Number(e.target.value))} /></label><button disabled={!flowId || !inputId} onClick={() => void start()}>Start pinned run</button>
    </fieldset>
    {uncertainStart && <p role="alert">Admission was not confirmed. Recovery resends the same request identity; inspect saved runs after reconnecting. <button disabled={busy || accessLost} onClick={() => void start()}>Recover start request</button></p>}
    <label>Saved run <select value={runId} onChange={e => selectRun(e.target.value)}><option value="">Select a run</option>{runs.data?.items.map(r => <option key={r.id} value={r.id}>{r.id} · {r.status}</option>)}</select></label>
    {runs.hasNextPage && <button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>More saved runs</button>}
    {d && !detail.isError && <article aria-label="Selected process run"><h4>Run {d.operation.id}</h4><p>Status: {d.operation.status} · attempt {d.operation.epoch} · {d.input.completed_count}/{d.input.member_count} members</p><p>Flow {d.flow.name} r{d.flow.revision} · digest <span className="identifier">{d.flow.config_digest}</span></p><p>Input candidate {d.input.candidate_id} · generation {d.input.generation_id}{d.input.version_id && ` · version ${d.input.version_id}`}{d.input.trial_of && ` · fresh trial of ${d.input.trial_of}`}</p>
      {d.needs_reconciliation && <p role="alert">Provider outcome unknown. Calls will not be replayed until authoritative provider evidence is reconciled.</p>}
      {writable && <div><button disabled={busy || stale || !["queued", "running"].includes(d.operation.status)} onClick={() => void action("pause")}>Pause at safe boundary</button><button disabled={busy || stale || d.operation.status !== "paused"} onClick={() => void action("resume")}>Resume run</button><button disabled={busy || stale || ["succeeded", "cancelled"].includes(d.operation.status)} onClick={() => void action("cancel")}>Cancel run</button><button disabled={busy || stale || d.needs_reconciliation || !["failed", "reconciliation_needed"].includes(d.operation.status)} onClick={() => void action("retry")}>Recover same trial</button><button disabled={busy || stale || ["succeeded", "cancelled"].includes(d.operation.status)} onClick={() => void action("reconcile")}>Reconcile workflow</button><button disabled={busy || stale || uncertainStart} onClick={() => void start(d)}>Start fresh trial</button></div>}
      <h5>Attempts</h5><ul>{d.attempts.map(a => <li key={a.epoch}>Attempt {a.epoch}: {a.status}{a.error_code && ` · ${a.error_code}`}{a.provider_run_id && ` · workflow ${a.provider_run_id}`}</li>)}</ul><h5>Step outputs and lineage</h5>
      {d.outputs.map(o => <details key={`${o.sample_id}:${o.step_position}`}><summary>{d.flow.definition.steps[o.step_position].name} · sample {o.sample_id} · accepted attempt {o.accepted_epoch}</summary><p>Revision {o.sample_revision_id} · receipt {o.receipt_id}</p><p>Input digest <span className="identifier">{o.input_digest}</span><br />Output digest <span className="identifier">{o.output_digest}</span></p>{o.profile_id && <p>Pinned profile {o.profile_id} · digest {o.profile_digest} · resolved model {o.result.resolved_model ?? "unknown"}</p>}<p>Usage: input {o.result.input_tokens ?? "unknown"}; output {o.result.output_tokens ?? "unknown"}; cost unknown</p><pre>{o.result.text}</pre></details>)}
      {d.next_after && <button onClick={() => setAfter(d.next_after!)}>Next output members</button>}{after && <button onClick={() => setAfter("")}>First output members</button>}
    </article>}
  </section>;
}
