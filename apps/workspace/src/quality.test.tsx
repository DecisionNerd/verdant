import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request } from "./api";
import { Quality } from "./quality";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const base = "/api/v1/namespaces/ns/datasets/ds";
const policy = { id: "policy-1", name: "ready", revision: 1, created_at: "2026-10-28T00:00:00Z", definition: { schema_version: 1, rules: [{ id: "count", kind: "minimum_samples", minimum: 1, required: true, split: null }] } };
function completeReport(pass: boolean | null = true) {
  return { id: "report-1", candidate_id: "candidate-1", generation_id: "generation-1", source_revision: 5, policy_id: policy.id, policy_revision: 1, evaluator_version: "verdant.core/1", state: "complete", expected_count: 101, processed_count: 101, required_rules_pass: pass, created_at: "2026-10-28T00:00:00Z", results: [{ rule_id: "count", required: true, split: "train", status: pass === true ? "passed" : pass === false ? "failed" : "unknown", examined: 101, failures: pass === true ? 0 : 1, offending_samples: pass === true ? [] : [{ sample_id: "sample-1", sample_revision_id: "revision-1" }], offending_samples_truncated: false }] };
}
function mount(writable = true, candidate = "candidate-1", archived = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><Quality namespace="ns" dataset="ds" candidate={candidate} csrf="csrf" writable={writable} archived={archived} /></QueryClientProvider>);
  return { refresh: () => act(async () => { await client.invalidateQueries(); }),
    switchCandidate: (selected: string) => view.rerender(<QueryClientProvider client={client}><Quality namespace="ns" dataset="ds" candidate={selected} csrf="csrf" writable={writable} archived={archived} /></QueryClientProvider>),
  };
}
function mockReads(report = completeReport(), readFailure: () => ApiError | null = () => null) {
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    const error = readFailure();
    if (error && body === undefined) throw error;
    if (path === `${base}/quality/policies/${policy.id}`) return policy;
    if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
    if (path.includes("/candidates/candidate-1/quality/reports?")) return { items: [report], next_after: null };
    if (path === `${base}/quality/reports/${report.id}`) return report;
    if (path.endsWith("/samples/sample-1/revisions/revision-1")) return { id: "revision-1", sample_id: "sample-1", revision: 3, sha256: "frozen-sha256", size_bytes: 42, metadata: {}, object_key: "do-not-render-private-object-key" };
    throw new Error(`Unexpected request ${path}`);
  });
}
async function choosePolicy() {
  await screen.findByRole("option", { name: "ready · revision 1" });
  fireEvent.change(screen.getByLabelText("Validation policy"), { target: { value: policy.id } });
  await screen.findByText("Selected policy: ready · revision 1");
}
describe("candidate quality evidence", () => {
  it.each([
    [true, "Required gates passed for this candidate and policy revision."],
    [false, "Required gates failed. Publication remains blocked."],
    [null, "Required gates are unknown. Publication remains blocked."],
  ] as const)("distinguishes the completed required gate verdict %s", async (pass, expected) => {
    mockReads(completeReport(pass)); mount();
    fireEvent.click(await screen.findByRole("button", { name: /Inspect report report-1/ }));
    await screen.findByText(expected);
    expect(screen.getByText(/Captured draft revision 5/)).toBeTruthy();
    expect(screen.getByText("generation-1")).toBeTruthy();
    expect(screen.getByRole("table").textContent).toContain("train");
    expect(screen.queryByRole("button", { name: "Resume validation" })).toBeNull();
  });

  it("inspects an offending immutable sample revision without rendering its storage key", async () => {
    mockReads(completeReport(false)); mount(false);
    fireEvent.click(await screen.findByRole("button", { name: /Inspect report report-1/ }));
    const link = await screen.findByRole("link", { name: "Inspect sample sample-1 · revision revision-1" });
    expect(link.getAttribute("href")).toBe("/api/v1/namespaces/ns/samples/sample-1/revisions/revision-1/summary");
    fireEvent.click(link);
    await screen.findByText("frozen-sha256");
    expect(vi.mocked(request)).toHaveBeenCalledWith("/api/v1/namespaces/ns/samples/sample-1/revisions/revision-1", "csrf");
    expect(screen.queryByText("do-not-render-private-object-key")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start new validation" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Author a policy revision" })).toBeNull();
  });

  it("starts a new report then resumes bounded checkpoints without reporting a running gate as passed", async () => {
    let report: ReturnType<typeof completeReport> = { ...completeReport(), state: "running", processed_count: 0, required_rules_pass: null, results: [] };
    vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
      if (path === `${base}/quality/policies/${policy.id}`) return policy;
      if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
      if (path.includes("/candidates/candidate-1/quality/reports?")) return { items: [], next_after: null };
      if (path === `${base}/candidates/candidate-1/quality/reports`) return report;
      if (path.endsWith("/evaluate")) {
        const expected = (body as { expected_count: number }).expected_count;
        report = expected === 0 ? { ...report, processed_count: 100 } : completeReport();
        return report;
      }
      if (path === `${base}/quality/reports/report-1`) return report;
      throw new Error(`Unexpected ${path}`);
    });
    mount(); await choosePolicy();
    fireEvent.click(screen.getByRole("button", { name: "Start new validation" }));
    await screen.findByText("Validation is running. Publication remains blocked.");
    expect(screen.queryByText(/Required gates passed/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume validation" }));
    await screen.findByText("Progress: 100 / 101 members · running");
    fireEvent.click(screen.getByRole("button", { name: "Resume validation" }));
    await screen.findByText("Required gates passed for this candidate and policy revision.");
    const checkpoints = vi.mocked(request).mock.calls.filter(call => call[0].endsWith("/evaluate"));
    expect(checkpoints.map(call => call[2])).toEqual([{ expected_count: 0, limit: 100 }, { expected_count: 100, limit: 100 }]);
  });

  it("hides cached reports after current authorization is revoked and preserves author input during transient failures", async () => {
    let failure: ApiError | null = null;
    mockReads(completeReport(), () => failure); const test = mount();
    fireEvent.change(screen.getByLabelText("Policy name"), { target: { value: "my_thresholds" } });
    fireEvent.click(await screen.findByRole("button", { name: /Inspect report report-1/ }));
    await screen.findByText("Required gates passed for this candidate and policy revision.");
    failure = new ApiError(502, "Temporary outage"); await test.refresh();
    await screen.findByRole("button", { name: "Retry policies" });
    expect((screen.getByLabelText("Policy name") as HTMLInputElement).value).toBe("my_thresholds");
    expect((screen.getByRole("button", { name: "Save policy revision" }) as HTMLButtonElement).disabled).toBe(true);
    failure = new ApiError(403, "Revoked"); await test.refresh();
    await screen.findByText("Quality access changed. Reload your workspace before continuing.");
    expect(screen.queryByText(/Required gates passed/)).toBeNull();
    expect(screen.queryByLabelText("Policy name")).toBeNull();
  });

  it("does not select a delayed report from a previously selected candidate", async () => {
    let resolveAdmission: ((value: ReturnType<typeof completeReport>) => void) | undefined;
    vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
      if (body !== undefined) return new Promise<ReturnType<typeof completeReport>>(resolve => { resolveAdmission = resolve; });
      if (path === `${base}/quality/policies/${policy.id}`) return policy;
      if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
      if (path.includes("/quality/reports?")) return { items: [], next_after: null };
      throw new Error(`Unexpected ${path}`);
    });
    const test = mount(); await choosePolicy();
    fireEvent.click(screen.getByRole("button", { name: "Start new validation" }));
    test.switchCandidate("candidate-2");
    await act(async () => { resolveAdmission!(completeReport()); });
    await screen.findByText("candidate-2");
    expect(screen.queryByRole("heading", { name: "Validation report report-1" })).toBeNull();
    expect(screen.queryByText(/Required gates passed/)).toBeNull();
    expect(vi.mocked(request).mock.calls.some(call => call[0] === `${base}/quality/reports/report-1`)).toBe(false);
  });

  it.each([
    [{}, "missing"],
    [{ label: null }, "null (counts as missing)"],
    [{ label: "existing value" }, "present"],
    [{ label: "é".repeat(3000) }, "present"],
  ] as const)("uses the report's immutable policy to explain metadata evidence %s", async (metadata, presence) => {
    const pinnedPolicy = { ...policy, id: "pinned-policy", name: "original", revision: 3, definition: { schema_version: 1, rules: [{ id: "metadata", kind: "required_metadata", key: "label", max_missing_fraction: 0.1, required: true, split: "train" }] } };
    const report = { ...completeReport(false), policy_id: pinnedPolicy.id, policy_revision: 3, results: [{ ...completeReport(false).results[0], rule_id: "metadata" }] };
    vi.mocked(request).mockImplementation(async path => {
      if (path === `${base}/quality/policies/policy-1`) return policy;
      if (path === `${base}/quality/policies/pinned-policy`) return pinnedPolicy;
      if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
      if (path.includes("/quality/reports?")) return { items: [report], next_after: null };
      if (path === `${base}/quality/reports/report-1`) return report;
      if (path.endsWith("/samples/sample-1/revisions/revision-1")) return { id: "revision-1", sample_id: "sample-1", revision: 3, sha256: "frozen-sha256", size_bytes: 42, metadata, object_key: "https://credential:secret@private.example/object" };
      throw new Error(`Unexpected ${path}`);
    });
    mount(); await choosePolicy();
    fireEvent.click(await screen.findByRole("button", { name: /Inspect report report-1/ }));
    await screen.findByText("Pinned report policy: original · revision 3");
    fireEvent.click(screen.getByRole("link", { name: /Inspect sample sample-1/ }));
    const evidence = await screen.findByRole("complementary", { name: "Offending exact sample revision" });
    await within(evidence).findByText(`Metadata field “label”: ${presence}.`);
    expect(within(evidence).getByText(/Failing rule: metadata: metadata “label”, maximum missing fraction 0.1/)).toBeTruthy();
    if ("label" in metadata && metadata.label === "existing value") expect(within(evidence).getByText('"existing value"')).toBeTruthy();
    if ("label" in metadata && typeof metadata.label === "string" && metadata.label.length > 2000) {
      expect(within(evidence).getByText("Value preview truncated at 4 KiB.")).toBeTruthy();
      expect(new TextEncoder().encode(evidence.querySelector("pre")!.textContent!).length).toBeLessThanOrEqual(4096);
    }
    expect(evidence.textContent).not.toContain("at least 1 samples");
    expect(document.body.textContent).not.toContain("credential:secret");
    expect(document.body.textContent).not.toContain("private.example");
  });
});

it("authors a new immutable revision and retains the exact admission command after response loss", async () => {
  let attempts = 0;
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) {
      attempts += 1;
      if (attempts === 1) throw new ApiError(502, "Policy response lost");
      return { ...policy, id: "policy-2", revision: 2, definition: (body as { definition: unknown }).definition };
    }
    if (path === `${base}/quality/policies/policy-1`) return policy;
    if (path === `${base}/quality/policies/policy-2`) return { ...policy, id: "policy-2", revision: 2 };
    if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
    if (path.includes("/quality/reports?")) return { items: [], next_after: null };
    throw new Error(`Unexpected ${path}`);
  });
  mount(); await choosePolicy();
  fireEvent.click(screen.getByRole("button", { name: "Revise selected policy" }));
  fireEvent.change(screen.getByLabelText("Rule 1 minimum samples"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
  fireEvent.change(screen.getByLabelText("Rule 2 kind"), { target: { value: "required_metadata" } });
  fireEvent.change(screen.getByLabelText("Rule 2 metadata key"), { target: { value: "label" } });
  fireEvent.change(screen.getByLabelText("Rule 2 maximum missing fraction"), { target: { value: "0.1" } });
  fireEvent.change(screen.getByLabelText("Rule 2 split (blank includes all)"), { target: { value: "train" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy revision" }));
  await screen.findByText("Policy response lost");
  expect((screen.getByLabelText("Policy name") as HTMLInputElement).value).toBe("ready");
  expect((screen.getByLabelText("Rule 2 metadata key") as HTMLInputElement).value).toBe("label");
  fireEvent.click(screen.getByRole("button", { name: "Retry saving policy revision" }));
  await screen.findByText(/Saved ready, immutable policy revision 2/);
  const commands = vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
  expect(commands).toHaveLength(2);
  expect(commands[0][2]).toEqual(commands[1][2]);
  expect(commands[1][2]).toMatchObject({ name: "ready", expected_revision: 1, definition: { schema_version: 1, rules: [
    { id: "count", kind: "minimum_samples", minimum: 3, split: null, required: true },
    { id: "rule_2", kind: "required_metadata", key: "label", max_missing_fraction: 0.1, split: "train", required: true },
  ] } });
});

it("keeps the validation admission key across a lost response and lets readers inspect policies without a candidate", async () => {
  let attempts = 0;
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) { attempts++; if (attempts === 1) throw new ApiError(502, "Report response lost"); return completeReport(); }
    if (path === `${base}/quality/policies/${policy.id}`) return policy;
    if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
    if (path.includes("/quality/reports?")) return { items: [], next_after: null };
    if (path === `${base}/quality/reports/report-1`) return completeReport();
    throw new Error(`Unexpected ${path}`);
  });
  mount(); await choosePolicy();
  fireEvent.click(screen.getByRole("button", { name: "Start new validation" }));
  await screen.findByText("Report response lost");
  fireEvent.click(screen.getByRole("button", { name: "Retry starting validation" }));
  await screen.findByText("Required gates passed for this candidate and policy revision.");
  const commands = vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
  expect(commands[0][2]).toEqual(commands[1][2]);
  cleanup(); mount(false, "");
  await screen.findByText("Select a sealed candidate to start validation.");
  expect(within(screen.getByRole("region", { name: "Quality policies and validation" })).queryByRole("button", { name: "Start new validation" })).toBeNull();
});

it("uses the backend policy-name contract in native form validation", async () => {
  mockReads(); mount();
  await screen.findByRole("option", { name: "ready · revision 1" });
  const input = screen.getByLabelText("Policy name") as HTMLInputElement;
  expect(input.maxLength).toBe(100);
  expect(screen.getByText(/Use a lowercase identifier beginning with a letter/)).toBeTruthy();
  for (const value of ["Ready", "not ready", "9ready"]) {
    fireEvent.change(input, { target: { value } });
    expect(input.validity.patternMismatch).toBe(true);
    expect(input.checkValidity()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save policy revision" }));
  }
  expect(vi.mocked(request).mock.calls.filter(call => call[2] !== undefined)).toHaveLength(0);
  fireEvent.change(input, { target: { value: "ready_2-for-publication" } });
  expect(input.checkValidity()).toBe(true);
});

it("advances the current policy revision after saving rules and starts a new policy when its name changes", async () => {
  let saved = policy;
  const commands: { name: string; expected_revision: number }[] = [];
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) {
      const command = body as { name: string; expected_revision: number; definition: typeof policy.definition };
      commands.push(command);
      saved = { ...policy, id: `saved-${commands.length}`, name: command.name, revision: command.expected_revision + 1, definition: command.definition };
      return saved;
    }
    if (path.includes("/quality/policies?")) return { items: [policy], next_after: null };
    if (path.includes("/quality/policies/saved-")) return saved;
    throw new Error(`Unexpected ${path}`);
  });
  mount(true, "");
  await screen.findByRole("option", { name: "ready · revision 1" });
  fireEvent.change(screen.getByLabelText("Policy name"), { target: { value: "fresh" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy revision" }));
  await screen.findByText(/Saved fresh, immutable policy revision 1/);
  await waitFor(() => expect((screen.getByRole("button", { name: "Save policy revision" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Rule 1 minimum samples"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy revision" }));
  await screen.findByText(/Saved fresh, immutable policy revision 2/);
  await waitFor(() => expect((screen.getByRole("button", { name: "Save policy revision" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Policy name"), { target: { value: "other_policy" } });
  fireEvent.click(screen.getByRole("button", { name: "Save policy revision" }));
  await screen.findByText(/Saved other_policy, immutable policy revision 1/);
  expect(commands.map(({ name, expected_revision }) => ({ name, expected_revision }))).toEqual([
    { name: "fresh", expected_revision: 0 }, { name: "fresh", expected_revision: 1 }, { name: "other_policy", expected_revision: 0 },
  ]);
});

it("pauses new quality admission on archived datasets while preserving validation resume", async () => {
  mockReads({ ...completeReport(), state: "running", processed_count: 100, required_rules_pass: null });
  const original = vi.mocked(request).getMockImplementation()!;
  vi.mocked(request).mockImplementation((path, csrf, body, method) => path.endsWith("/evaluate") ? Promise.resolve(completeReport()) : original(path, csrf, body, method));
  mount(true, "candidate-1", true);
  fireEvent.click(await screen.findByRole("button", { name: /Inspect report report-1/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Resume validation" }));
  await screen.findByText("Required gates passed for this candidate and policy revision.");
  expect(screen.queryByRole("heading", { name: "Author a policy revision" })).toBeNull();
  expect((screen.getByRole("button", { name: "Start new validation" }) as HTMLButtonElement).disabled).toBe(true);
  const writes = vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe(`${base}/quality/reports/report-1/evaluate`);
});
