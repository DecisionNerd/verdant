import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request, type Page } from "./api";

type Rule = {
  id: string;
  kind: "minimum_samples" | "required_metadata";
  split: string | null;
  required: boolean;
  minimum?: number;
  key?: string;
  max_missing_fraction?: number;
};
type Definition = { schema_version: 1; rules: Rule[] };
type Policy = { id: string; name: string; revision: number; definition: Definition; created_at: string };
type Offender = { sample_id: string; sample_revision_id: string };
type RuleResult = {
  rule_id: string; required: boolean; split: string | null;
  status: "pending" | "passed" | "failed" | "unknown";
  examined: number; failures: number; offending_samples: Offender[];
  offending_samples_truncated: boolean;
};
type Report = {
  id: string; candidate_id: string; generation_id: string; source_revision: number;
  policy_id: string; policy_revision: number; evaluator_version: string;
  state: "running" | "complete"; expected_count: number; processed_count: number;
  required_rules_pass: boolean | null; results: RuleResult[]; created_at: string;
};
type PolicyCommand = { request_key: string; name: string; expected_revision: number; definition: Definition };
type ReportCommand = { request_key: string; policy_id: string };
type SampleRevision = { id: string; sample_id: string; revision: number; sha256: string; size_bytes: number; metadata: Record<string, unknown> };
const denied = (error: Error | null) => error instanceof ApiError && [401, 403].includes(error.status);
const initialRule = (): Rule => ({ id: "rule_1", kind: "minimum_samples", minimum: 1, split: null, required: true });
function describeRule(rule: Rule) {
  return `${rule.id}: ${rule.kind === "minimum_samples" ? `at least ${rule.minimum} samples` : `metadata “${rule.key}”, maximum missing fraction ${rule.max_missing_fraction}`} · ${rule.split ?? "all splits"} · ${rule.required ? "required" : "optional"}`;
}
function metadataPreview(value: unknown) {
  const text = JSON.stringify(value, null, 2) ?? "Unknown";
  const bytes = new TextEncoder().encode(text);
  return { text: bytes.length > 4096 ? new TextDecoder().decode(bytes.slice(0, 4096), { stream: true }) : text, truncated: bytes.length > 4096 };
}

type QualityProps = {
  namespace: string; dataset: string; candidate?: string; csrf: string; writable: boolean;
};
export function Quality(props: QualityProps) {
  return <QualityPanel key={`${props.namespace}/${props.dataset}`} {...props} />;
}
function QualityPanel({ namespace, dataset, candidate = "", csrf, writable }: QualityProps) {
  const cache = useQueryClient();
  const policyNameHelp = useId();
  const base = `/api/v1/namespaces/${namespace}/datasets/${dataset}`;
  const context = `${base}/${candidate}`;
  const currentContext = useRef(context); currentContext.current = context;
  const [policyPages, setPolicyPages] = useState<(string | null)[]>([null]);
  const [reportPages, setReportPages] = useState<(string | null)[]>([null]);
  const [selectedPolicy, setSelectedPolicy] = useState("");
  const [selectedReport, setSelectedReport] = useState("");
  const [name, setName] = useState("");
  const [expectedRevision, setExpectedRevision] = useState(0);
  const [rules, setRules] = useState<Rule[]>([initialRule()]);
  const nextRule = useRef(2);
  const [busy, setBusy] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [offender, setOffender] = useState<(Offender & { rule_id: string }) | null>(null);
  const policyCommand = useRef<PolicyCommand | null>(null);
  const reportCommand = useRef<ReportCommand | null>(null);
  const policyCursor = policyPages.at(-1), reportCursor = reportPages.at(-1);
  const policiesKey = ["quality-policies", namespace, dataset];
  const reportsKey = ["quality-reports", namespace, dataset, candidate];
  const policies = useQuery({ queryKey: [...policiesKey, policyCursor],
    queryFn: () => request<Page<Policy>>(`${base}/quality/policies?limit=25${policyCursor ? `&after=${encodeURIComponent(policyCursor)}` : ""}`, csrf), refetchOnReconnect: true });
  const policy = useQuery({ queryKey: ["quality-policy", namespace, dataset, selectedPolicy], enabled: !!selectedPolicy,
    queryFn: () => request<Policy>(`${base}/quality/policies/${selectedPolicy}`, csrf), refetchOnReconnect: true });
  const reports = useQuery({ queryKey: [...reportsKey, reportCursor], enabled: !!candidate,
    queryFn: () => request<Page<Report>>(`${base}/candidates/${candidate}/quality/reports?limit=25${reportCursor ? `&after=${encodeURIComponent(reportCursor)}` : ""}`, csrf), refetchOnReconnect: true });
  const report = useQuery({ queryKey: ["quality-report", namespace, dataset, selectedReport], enabled: !!candidate && !!selectedReport,
    queryFn: () => request<Report>(`${base}/quality/reports/${selectedReport}`, csrf), refetchInterval: 3000, refetchOnReconnect: true });
  const detail = report.data?.candidate_id === candidate ? report.data : undefined;
  const reportPolicy = useQuery({ queryKey: ["quality-policy", namespace, dataset, detail?.policy_id], enabled: !!detail,
    queryFn: () => request<Policy>(`${base}/quality/policies/${detail!.policy_id}`, csrf), refetchOnReconnect: true });
  const revisionPath = offender ? `/api/v1/namespaces/${namespace}/samples/${offender.sample_id}/revisions/${offender.sample_revision_id}` : "";
  const revision = useQuery({ queryKey: ["quality-offender", namespace, offender?.sample_id, offender?.sample_revision_id], enabled: !!offender,
    queryFn: () => request<SampleRevision>(revisionPath, csrf), refetchOnReconnect: true });

  useEffect(() => {
    setSelectedReport(""); setReportPages([null]); setOffender(null);
    setError(""); setMessage(""); reportCommand.current = null;
  }, [candidate, namespace, dataset]);

  function editRule(index: number, patch: Partial<Rule>) {
    policyCommand.current = null;
    setRules(items => items.map((item, position) => position === index ? { ...item, ...patch } : item));
  }
  function editSelected() {
    if (!policy.data || policy.isError) return;
    setName(policy.data.name); setExpectedRevision(policy.data.revision);
    setRules(policy.data.definition.rules.map(rule => ({ ...rule })));
    nextRule.current = policy.data.definition.rules.length + 1;
    policyCommand.current = null; setError(""); setMessage("");
  }
  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    if (!writable || busy || policies.isError) return;
    setBusy(true); setError(""); setMessage("");
    try {
      policyCommand.current ??= {
        request_key: crypto.randomUUID(), name: name.trim(), expected_revision: expectedRevision,
        definition: { schema_version: 1, rules: rules.map(rule => rule.kind === "minimum_samples"
          ? { id: rule.id, kind: rule.kind, split: rule.split, required: rule.required, minimum: rule.minimum }
          : { id: rule.id, kind: rule.kind, split: rule.split, required: rule.required, key: rule.key, max_missing_fraction: rule.max_missing_fraction }) },
      };
      const result = await request<Policy>(`${base}/quality/policies`, csrf, policyCommand.current);
      policyCommand.current = null; setSelectedPolicy(result.id); setExpectedRevision(result.revision);
      cache.setQueryData(["quality-policy", namespace, dataset, result.id], result);
      setMessage(`Saved ${result.name}, immutable policy revision ${result.revision}. Earlier reports keep their original policy.`);
      await cache.invalidateQueries({ queryKey: policiesKey });
    } catch (failure) {
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      if (failure instanceof ApiError && failure.status === 409) policyCommand.current = null;
    } finally { setBusy(false); }
  }
  async function validate(resume: boolean) {
    if (!writable || busy || !candidate || (resume ? !report.data || report.isError : !policy.data || policy.isError)) return;
    const startedContext = context;
    setBusy(true); setError(""); setMessage("");
    try {
      if (!resume) reportCommand.current ??= { request_key: crypto.randomUUID(), policy_id: selectedPolicy };
      const result = await request<Report>(resume ? `${base}/quality/reports/${report.data!.id}/evaluate` : `${base}/candidates/${candidate}/quality/reports`, csrf,
        resume ? { expected_count: report.data!.processed_count, limit: 100 } : reportCommand.current);
      if (startedContext !== currentContext.current) return;
      reportCommand.current = null; setSelectedReport(result.id); setOffender(null);
      cache.setQueryData(["quality-report", namespace, dataset, result.id], result);
      setMessage(result.state === "complete" ? "Validation completed. Review the required gates below." : "Validation checkpoint saved. Resume to evaluate the next bounded batch.");
      await cache.invalidateQueries({ queryKey: reportsKey });
    } catch (failure) {
      if (startedContext !== currentContext.current) return;
      setError((failure as Error).message);
      if (denied(failure as Error)) setAccessDenied(true);
      if (failure instanceof ApiError && failure.status === 409) reportCommand.current = null;
      if (resume) void report.refetch();
    } finally { setBusy(false); }
  }

  if (accessDenied || [policies.error, policy.error, reports.error, report.error, reportPolicy.error, revision.error].some(denied)) {
    return <p role="alert">Quality access changed. Reload your workspace before continuing.</p>;
  }
  const offendingRule = reportPolicy.data?.definition.rules.find(rule => rule.id === offender?.rule_id);
  const offendingValue = offendingRule?.kind === "required_metadata" && revision.data && Object.hasOwn(revision.data.metadata, offendingRule.key!) ? revision.data.metadata[offendingRule.key!] : undefined;
  const preview = offendingValue === undefined ? null : metadataPreview(offendingValue);
  return <section aria-label="Quality policies and validation">
    <h2>Quality policies and validation</h2>
    <p>Validate a sealed candidate against an exact policy revision. Reports keep the captured evidence when the working draft changes.</p>
    {policies.isPending ? <p role="status">Loading quality policies…</p> : policies.isError ? <p role="alert">{policies.error.message} Your policy input is preserved. <button onClick={() => void policies.refetch()}>Retry policies</button></p> : null}
    <label>Validation policy <select value={selectedPolicy} disabled={busy || policies.isError} onChange={event => { setSelectedPolicy(event.target.value); reportCommand.current = null; }}>
      <option value="">Select a policy revision</option>
      {selectedPolicy && policy.data && !policies.data?.items.some(item => item.id === selectedPolicy) && <option value={selectedPolicy}>{policy.data.name} · revision {policy.data.revision}</option>}
      {policies.data?.items.map(item => <option key={item.id} value={item.id}>{item.name} · revision {item.revision}</option>)}
    </select></label>
    <nav aria-label="Quality policy pages"><button disabled={busy || policyPages.length === 1} onClick={() => setPolicyPages(policyPages.slice(0, -1))}>Previous policies</button><button disabled={busy || !policies.data?.next_after} onClick={() => setPolicyPages([...policyPages, policies.data!.next_after])}>Next policies</button></nav>
    {selectedPolicy && policy.isError && <p role="alert">{policy.error.message} <button onClick={() => void policy.refetch()}>Retry selected policy</button></p>}
    {policy.data && !policy.isError && <details><summary>Selected policy: {policy.data.name} · revision {policy.data.revision}</summary><ul>{policy.data.definition.rules.map(rule => <li key={rule.id}>{describeRule(rule)}</li>)}</ul></details>}
    {writable && <form onSubmit={event => void savePolicy(event)}>
      <h3>Author a policy revision</h3>
      <p>{expectedRevision ? `Save a new revision after revision ${expectedRevision}. Existing revisions remain unchanged.` : "Create the first revision of a new named policy."}</p>
      <button type="button" disabled={busy || !policy.data || policy.isError} onClick={editSelected}>Revise selected policy</button>
      <label>Policy name <input required pattern="[a-z][a-z0-9_-]*" maxLength={100} aria-describedby={policyNameHelp} value={name} disabled={busy} onChange={event => { setName(event.target.value); setExpectedRevision(0); policyCommand.current = null; }} /></label>
      <p id={policyNameHelp}>Use a lowercase identifier beginning with a letter, followed by lowercase letters, numbers, underscores or hyphens; up to 100 characters.</p>
      {rules.map((rule, index) => <fieldset key={index} disabled={busy}><legend>Rule {index + 1}</legend>
        <label>Rule {index + 1} identifier <input required pattern="[a-z][a-z0-9_-]*" maxLength={100} value={rule.id} onChange={event => editRule(index, { id: event.target.value })} /></label>
        <label>Rule {index + 1} kind <select value={rule.kind} onChange={event => editRule(index, event.target.value === "minimum_samples" ? { kind: "minimum_samples", minimum: 1 } : { kind: "required_metadata", key: "", max_missing_fraction: 0 })}><option value="minimum_samples">Minimum samples</option><option value="required_metadata">Required metadata</option></select></label>
        {rule.kind === "minimum_samples" ? <label>Rule {index + 1} minimum samples <input type="number" min="1" step="1" max="9007199254740991" required value={rule.minimum ?? 1} onChange={event => editRule(index, { minimum: Number(event.target.value) })} /></label> : <>
          <label>Rule {index + 1} metadata key <input required maxLength={200} value={rule.key ?? ""} onChange={event => editRule(index, { key: event.target.value })} /></label>
          <label>Rule {index + 1} maximum missing fraction <input type="number" min="0" max="1" step="any" required value={rule.max_missing_fraction ?? 0} onChange={event => editRule(index, { max_missing_fraction: Number(event.target.value) })} /></label>
        </>}
        <label>Rule {index + 1} split (blank includes all) <input maxLength={50} pattern="[a-z][a-z0-9_-]*" value={rule.split ?? ""} onChange={event => editRule(index, { split: event.target.value || null })} /></label>
        <label><input type="checkbox" checked={rule.required} onChange={event => editRule(index, { required: event.target.checked })} />Rule {index + 1} blocks publication when unsuccessful</label>
        <button type="button" disabled={rules.length === 1} onClick={() => { setRules(rules.filter((_, position) => position !== index)); policyCommand.current = null; }}>Remove rule {index + 1}</button>
      </fieldset>)}
      <button type="button" disabled={busy || rules.length >= 32} onClick={() => { let id: string; do { id = `rule_${nextRule.current++}`; } while (rules.some(rule => rule.id === id)); setRules([...rules, { ...initialRule(), id }]); policyCommand.current = null; }}>Add rule</button>
      <button disabled={busy || policies.isError}>{policyCommand.current && !busy ? "Retry saving policy revision" : "Save policy revision"}</button>
    </form>}
    {!candidate ? <p>Select a sealed candidate to start validation.</p> : <>
      <h3>Selected candidate validation</h3><p>Candidate: <span className="identifier">{candidate}</span></p>
      {writable && <button disabled={busy || !policy.data || policy.isError || policies.isError || reports.isError} onClick={() => void validate(false)}>{reportCommand.current && !busy ? "Retry starting validation" : "Start new validation"}</button>}
      {reports.isPending ? <p role="status">Loading validation reports…</p> : reports.isError ? <p role="alert">{reports.error.message} Selected evidence is preserved. <button onClick={() => void reports.refetch()}>Retry reports</button></p> : !reports.data?.items.length ? <p>No validation reports for this candidate.</p> : <ul>{reports.data.items.map(item => <li key={item.id}><button disabled={busy} onClick={() => { setSelectedReport(item.id); setOffender(null); }}>Inspect report {item.id} · policy revision {item.policy_revision} · {item.state}</button></li>)}</ul>}
      <nav aria-label="Validation report pages"><button disabled={busy || reportPages.length === 1} onClick={() => setReportPages(reportPages.slice(0, -1))}>Previous reports</button><button disabled={busy || !reports.data?.next_after} onClick={() => setReportPages([...reportPages, reports.data!.next_after])}>Next reports</button></nav>
      {selectedReport && report.isPending && <p role="status">Loading selected validation report…</p>}
      {selectedReport && report.isError && <p role="alert">{report.error.message} Displayed evidence may be stale. <button onClick={() => void report.refetch()}>Retry selected report</button></p>}
      {detail && <article aria-label="Selected validation report"><h4>Validation report {detail.id}</h4>
        <p>Captured draft revision {detail.source_revision} · generation <span className="identifier">{detail.generation_id}</span></p>
        <p>Policy <span className="identifier">{detail.policy_id}</span> · revision {detail.policy_revision} · evaluator {detail.evaluator_version}</p>
        {reportPolicy.isPending ? <p role="status">Loading pinned report policy…</p> : reportPolicy.isError ? <p role="alert">{reportPolicy.error.message} Exact rule thresholds are unavailable. <button onClick={() => void reportPolicy.refetch()}>Retry pinned report policy</button></p> : reportPolicy.data && <div aria-label="Pinned report policy"><p>Pinned report policy: {reportPolicy.data.name} · revision {reportPolicy.data.revision}</p><ul>{reportPolicy.data.definition.rules.map(rule => <li key={rule.id}>{describeRule(rule)}</li>)}</ul></div>}
        <p>Progress: {detail.processed_count} / {detail.expected_count} members · {detail.state}</p>
        <p role="status">{detail.state !== "complete" ? "Validation is running. Publication remains blocked." : detail.required_rules_pass === true ? "Required gates passed for this candidate and policy revision." : detail.required_rules_pass === false ? "Required gates failed. Publication remains blocked." : "Required gates are unknown. Publication remains blocked."}</p>
        {writable && detail.state === "running" && <button disabled={busy || report.isError || reports.isError} onClick={() => void validate(true)}>Resume validation</button>}
        <div className="table-scroll"><table><caption>Validation rules and offending frozen sample revisions</caption><thead><tr><th scope="col">Rule</th><th scope="col">Scope</th><th scope="col">Gate</th><th scope="col">Result</th><th scope="col">Examined</th><th scope="col">Failures</th><th scope="col">Sample evidence</th></tr></thead><tbody>{detail.results.map(result => <tr key={result.rule_id}><th scope="row">{result.rule_id}</th><td>{result.split ?? "All splits"}</td><td>{result.required ? "Required" : "Optional"}</td><td>{result.status}</td><td>{result.examined}</td><td>{result.failures}</td><td>{result.offending_samples.length ? <ul>{result.offending_samples.map(item => <li key={`${item.sample_id}/${item.sample_revision_id}`}><a href={`/api/v1/namespaces/${namespace}/samples/${item.sample_id}/revisions/${item.sample_revision_id}/summary`} onClick={event => { event.preventDefault(); setOffender({ ...item, rule_id: result.rule_id }); }}>Inspect sample {item.sample_id} · revision {item.sample_revision_id}</a></li>)}</ul> : "No offending samples listed."}{result.offending_samples_truncated && <p>Only a bounded subset of offending samples is listed.</p>}</td></tr>)}</tbody></table></div>
      </article>}
    </>}
    {offender && <aside aria-label="Offending exact sample revision"><h3>Offending exact sample revision</h3>
      {offendingRule && !reportPolicy.isError ? <p>Failing rule: {describeRule(offendingRule)}</p> : <p>The pinned rule threshold is unavailable.</p>}
      {revision.isPending ? <p role="status">Loading sample revision…</p> : revision.isError ? <p role="alert">{revision.error.message} <button onClick={() => void revision.refetch()}>Retry sample evidence</button></p> : revision.data && <>
        <dl><dt>Sample</dt><dd>{revision.data.sample_id}</dd><dt>Revision</dt><dd>{revision.data.id}</dd><dt>SHA-256</dt><dd className="identifier">{revision.data.sha256}</dd><dt>Bytes</dt><dd>{revision.data.size_bytes}</dd></dl>
        {offendingRule?.kind === "required_metadata" && !reportPolicy.isError && <div aria-label="Offending metadata field"><p>Metadata field “{offendingRule.key}”: {offendingValue === undefined ? "missing" : offendingValue === null ? "null (counts as missing)" : "present"}.</p>{preview && <><pre>{preview.text}</pre>{preview.truncated && <p>Value preview truncated at 4 KiB.</p>}</>}</div>}
      </>}
    </aside>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
