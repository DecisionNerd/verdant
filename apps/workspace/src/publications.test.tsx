import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request } from "./api";
import { Publication } from "./publications";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const base = "/api/v1/namespaces/ns/datasets/ds";
const createPath = `${base}/candidates/candidate-1/publications`;
const publicationPath = `${base}/publications/publication-1`;
const report = { id: "report-1", candidate_id: "candidate-1", generation_id: "generation-1", source_revision: 5, policy_id: "policy-1", policy_revision: 3, state: "complete", required_rules_pass: true };
function preparation() {
  return { id: "publication-1", operation_id: "operation-1", candidate_id: "candidate-1", generation_id: "generation-1", report_id: "report-1", policy_id: "policy-1", source_revision: 5, latest_draft_revision: 6,
    label: "v1.0", mode: "selected_candidate", state: "building", expected_count: 26, prepared_count: 0,
    expires_at: "2026-11-01T12:00:00Z", expired: false, last_error: null as string | null,
    manifest_digest: null as string | null, manifest_size: null as number | null, version_id: null as string | null };
}
type View = ReturnType<typeof preparation>;
type Harness = { value: View; failure: Error | null; listed: boolean; reports: typeof report[]; nextReport: string | null;
  mutate: (path: string, body: unknown) => Promise<View> };
function mockServer(initial: Partial<View> = {}) {
  const server: Harness = { value: { ...preparation(), ...initial }, failure: null, listed: true, reports: [report], nextReport: null,
    mutate: async () => server.value };
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) return server.mutate(path, body);
    if (server.failure) throw server.failure;
    if (path.includes("/quality/reports?")) return { items: path.includes("candidate-2") ? [] : server.reports, next_after: server.nextReport };
    if (path.includes("/publications?")) return { items: server.listed && !path.includes("candidate-2") ? [server.value] : [], next_after: null };
    if (path === publicationPath) return server.value;
    if (path === `${base}/quality/reports/report-1`) return report;
    throw new Error(`Unexpected ${path}`);
  });
  return server;
}
function mount(writable = true, archived = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const component = (candidate: string) => <QueryClientProvider client={client}><Publication namespace="ns" dataset="ds" candidate={candidate} csrf="csrf" writable={writable} archived={archived} /></QueryClientProvider>;
  const view = render(component("candidate-1"));
  return { refresh: () => act(async () => { await client.invalidateQueries(); }),
    switchCandidate: (candidate: string) => view.rerender(component(candidate)) };
}
async function fillForm() {
  await screen.findByRole("option", { name: "report-1 · policy revision 3 · passed" });
  fireEvent.change(screen.getByLabelText("Passing quality report"), { target: { value: "report-1" } });
  fireEvent.change(screen.getByLabelText("Version label"), { target: { value: "v1.0" } });
}
async function inspect() {
  fireEvent.click(await screen.findByRole("button", { name: /Inspect publication v1.0/ }));
  await screen.findByText("Required gates passed for this exact candidate and policy revision.");
}
const commands = () => vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);

describe("immutable dataset publication", () => {
  it("prepares bounded resumable source checks and publishes only the completed manifest", async () => {
    const server = mockServer(); server.listed = false;
    server.mutate = async (path, body) => {
      if (path === createPath) { server.listed = true; return server.value; }
      if (path.endsWith("/prepare")) {
        const expected = (body as { expected_count: number }).expected_count;
        server.value = { ...server.value, prepared_count: expected === 0 ? 25 : 26,
          ...(expected === 25 ? { state: "prepared", manifest_digest: "immutable-manifest-digest", manifest_size: 2048 } : {}) };
        return server.value;
      }
      if (path.endsWith("/finalize")) { server.value = { ...server.value, state: "published", version_id: "version-1" }; return server.value; }
      throw new Error(`Unexpected mutation ${path}`);
    };
    mount(); await fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Start publication preparation" }));
    await screen.findByText("Source checks: 0 / 26 members · building");
    expect(screen.queryByRole("button", { name: "Publish immutable version" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume source checks" }));
    await screen.findByText("Source checks: 25 / 26 members · building");
    expect(screen.queryByText(/Published immutable version/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume source checks" }));
    await screen.findByText("immutable-manifest-digest");
    expect(screen.getByText("2048")).toBeTruthy();
    expect(screen.getByText(/Captured draft revision 5 · latest draft revision 6/)).toBeTruthy();
    expect(screen.getByText(/Pinned report/).textContent).toContain("policy-1 revision 3");
    fireEvent.click(screen.getByRole("button", { name: "Publish immutable version" }));
    await screen.findByText(/Published immutable version/);
    expect(screen.getByText("version-1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Publish immutable version" })).toBeNull();
    expect(commands().map(call => call[2])).toEqual([
      { request_key: expect.any(String), label: "v1.0", report_id: "report-1", mode: "selected_candidate" },
      { expected_count: 0, limit: 25 }, { expected_count: 25, limit: 25 }, {},
    ]);
  });

  it("preserves the exact admission and form after an unknown network result", async () => {
    const server = mockServer(); server.listed = false; let attempts = 0;
    server.mutate = async () => { if (++attempts === 1) throw new TypeError("Connection interrupted"); return server.value; };
    mount(); await fillForm();
    fireEvent.change(screen.getByLabelText("Publication selection mode"), { target: { value: "current_draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Start publication preparation" }));
    await screen.findByText("Connection interrupted");
    expect((screen.getByLabelText("Version label") as HTMLInputElement).value).toBe("v1.0");
    expect((screen.getByLabelText("Version label") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Publication selection mode") as HTMLSelectElement).value).toBe("current_draft");
    fireEvent.click(screen.getByRole("button", { name: "Retry starting publication" }));
    await screen.findByRole("heading", { name: "Publication v1.0" });
    expect(commands()).toHaveLength(2);
    expect(commands()[0][2]).toEqual(commands()[1][2]);
    expect(commands()[1][2]).toMatchObject({ mode: "current_draft" });
  });

  it("retries finalization on the same preparation and displays the stored commit result", async () => {
    const server = mockServer({ state: "prepared", prepared_count: 26, manifest_digest: "digest", manifest_size: 42 }); let attempts = 0;
    server.mutate = async path => {
      expect(path).toBe(`${publicationPath}/finalize`);
      if (++attempts === 1) throw new ApiError(502, "Commit response lost");
      server.value = { ...server.value, state: "published", version_id: "same-version" }; return server.value;
    };
    mount(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Publish immutable version" }));
    await screen.findByText("Commit response lost");
    await waitFor(() => expect((screen.getByRole("button", { name: "Publish immutable version" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Publish immutable version" }));
    await screen.findByText("same-version");
    expect(commands().map(call => [call[0], call[2]])).toEqual([[`${publicationPath}/finalize`, {}], [`${publicationPath}/finalize`, {}]]);
    expect(screen.queryByText(/Source checks complete/)).toBeNull();
  });

  it("recovers an already committed version after the finalization response is lost", async () => {
    const server = mockServer({ state: "prepared", prepared_count: 26, manifest_digest: "digest", manifest_size: 42 });
    server.mutate = async () => {
      server.value = { ...server.value, state: "published", version_id: "recovered-version" };
      throw new TypeError("Commit connection interrupted");
    };
    mount(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Publish immutable version" }));
    await screen.findByText("recovered-version");
    expect(screen.queryByRole("button", { name: "Publish immutable version" })).toBeNull();
    expect(commands()).toHaveLength(1);
    expect(commands()[0][0]).toBe(`${publicationPath}/finalize`);
  });

  it("retains preparation and fixed selection on a current-draft conflict", async () => {
    const server = mockServer({ state: "prepared", prepared_count: 26, mode: "current_draft", manifest_digest: "digest", manifest_size: 42 });
    server.mutate = async () => { throw new ApiError(409, "The current draft changed; selected candidate is retained."); };
    mount(); await inspect();
    expect(screen.getByText(/Artifacts are verified, but the current draft has changed/)).toBeTruthy();
    expect(screen.queryByText(/Ready to publish after reviewing/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Publish immutable version" }));
    await screen.findByText("The current draft changed; selected candidate is retained.");
    expect(screen.getByRole("heading", { name: "Publication v1.0" })).toBeTruthy();
    expect(screen.getByText("digest")).toBeTruthy();
    expect(screen.getByText("Fixed selection: Current draft must still match.")).toBeTruthy();
    expect(screen.queryByText(/Published immutable version/)).toBeNull();
    expect(commands()).toHaveLength(1);
  });

  it("renews expired protection with zero checked sources and releases abandoned protection in batches", async () => {
    const server = mockServer({ state: "prepared", prepared_count: 26, expired: true, manifest_digest: "old-digest", manifest_size: 42 });
    let releases = 0;
    server.mutate = async path => {
      server.value = path.endsWith("/renew") ? { ...server.value, expired: false, state: "building", prepared_count: 0, manifest_digest: null, manifest_size: null }
        : { ...server.value, state: ++releases === 1 ? "releasing" : "released" };
      return server.value;
    };
    mount(); await inspect();
    expect(screen.queryByRole("button", { name: "Publish immutable version" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Renew protection and recheck all sources" }));
    await screen.findByText("Source checks: 0 / 26 members · building");
    expect(screen.queryByText("old-digest")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Release abandoned preparation" }));
    fireEvent.click(await screen.findByRole("button", { name: "Continue releasing protection" }));
    await screen.findByText("Preparation released. It cannot be published; start a new preparation when ready.");
    expect(commands().map(call => [call[0], call[2]])).toEqual([
      [`${publicationPath}/renew`, {}], [`${publicationPath}/release`, { limit: 100 }], [`${publicationPath}/release`, { limit: 100 }],
    ]);
  });

  it("keeps failed-source checkpoints resumable without claiming readiness", async () => {
    const server = mockServer({ prepared_count: 25 });
    server.mutate = async () => { server.value = { ...server.value, last_error: "Source checksum mismatch." }; return server.value; };
    mount(); await inspect();
    fireEvent.click(screen.getByRole("button", { name: "Resume source checks" }));
    await screen.findByText(/Source checksum mismatch/);
    expect(screen.getByText("Source checks: 25 / 26 members · building")).toBeTruthy();
    expect(screen.queryByText(/Ready to publish/)).toBeNull();
    expect(screen.getByRole("button", { name: "Resume source checks" })).toBeTruthy();
  });

  it("permits only completed passing evidence for this candidate and pages available reports", async () => {
    const server = mockServer();
    server.reports = [report, { ...report, id: "running", state: "running", required_rules_pass: false }, { ...report, id: "failed", required_rules_pass: false }, { ...report, id: "other-candidate", candidate_id: "candidate-2" }];
    server.nextReport = "report cursor";
    mount(); await fillForm();
    for (const name of [/running ·/, /failed ·/, /other-candidate ·/]) expect((screen.getByRole("option", { name }) as HTMLOptionElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Next publication reports" }));
    await waitFor(() => expect(vi.mocked(request).mock.calls.some(call => call[0].endsWith("&after=report%20cursor"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Passing quality report"), { target: { value: "other-candidate" } });
    fireEvent.click(screen.getByRole("button", { name: "Start publication preparation" }));
    expect(commands()).toHaveLength(0);
  });

  it("blocks publication when pinned evidence belongs to a different frozen generation", async () => {
    mockServer({ state: "prepared", prepared_count: 26, generation_id: "another-generation" }); mount();
    fireEvent.click(await screen.findByRole("button", { name: /Inspect publication/ }));
    await screen.findByText("Passing evidence does not match this exact candidate, generation and revision. Publication is blocked.");
    expect((screen.getByRole("button", { name: "Publish immutable version" }) as HTMLButtonElement).disabled).toBe(true);
    expect(commands()).toHaveLength(0);
  });

  it("does not select a delayed preparation after switching candidates", async () => {
    const server = mockServer(); server.listed = false;
    let resolve: ((value: View) => void) | undefined;
    server.mutate = () => new Promise<View>(done => { resolve = done; });
    const test = mount(); await fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Start publication preparation" }));
    test.switchCandidate("candidate-2");
    await act(async () => { resolve!(server.value); });
    await screen.findByText("candidate-2");
    expect(screen.queryByRole("heading", { name: "Publication v1.0" })).toBeNull();
    expect(vi.mocked(request).mock.calls.some(call => call[0] === publicationPath)).toBe(false);
  });

  it("allows read-only inspection and hides cached publication evidence after authorization is revoked", async () => {
    const server = mockServer({ state: "published", version_id: "private-version" }); const test = mount(false);
    await inspect(); await screen.findByText("private-version");
    expect(screen.queryByLabelText("Version label")).toBeNull();
    expect(screen.queryByRole("button", { name: "Publish immutable version" })).toBeNull();
    server.failure = new ApiError(403, "Revoked"); await test.refresh();
    await screen.findByText("Publication access changed. Reload your workspace before continuing.");
    expect(screen.queryByText("private-version")).toBeNull();
    expect(screen.queryByText(/Pinned report/)).toBeNull();
    expect(commands()).toHaveLength(0);
  });

  it("keeps form input during read failures and enforces the native label contract", async () => {
    const server = mockServer(); const test = mount(); await fillForm();
    const label = screen.getByLabelText("Version label") as HTMLInputElement;
    expect(label.maxLength).toBe(100);
    for (const value of ["bad label", ".hidden", "-bad", "bad/label"]) {
      fireEvent.change(label, { target: { value } }); expect(label.checkValidity()).toBe(false);
      fireEvent.click(screen.getByRole("button", { name: "Start publication preparation" }));
    }
    expect(commands()).toHaveLength(0);
    fireEvent.change(label, { target: { value: "V1.2-rc_3" } }); expect(label.checkValidity()).toBe(true);
    server.failure = new ApiError(502, "Temporary outage"); await test.refresh();
    await screen.findByRole("button", { name: "Retry publication reports" });
    expect((screen.getByLabelText("Version label") as HTMLInputElement).value).toBe("V1.2-rc_3");
    expect((screen.getByRole("button", { name: "Start publication preparation" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

it("pauses new publication and finalization on an archived dataset while retaining source checks and release", async () => {
  const server = mockServer();
  server.mutate = async path => {
    server.value = { ...server.value, state: path.endsWith("/release") ? "released" : "prepared", prepared_count: 26 };
    return server.value;
  };
  mount(true, true); await inspect();
  expect(screen.queryByLabelText("Version label")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Resume source checks" }));
  await screen.findByText("Source checks: 26 / 26 members · prepared");
  expect(screen.getByText("Artifacts are verified, but the dataset is archived. Unarchive the dataset before publishing.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Publish immutable version" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Release abandoned preparation" }));
  await screen.findByText(/Preparation released/);
  expect(commands().map(call => call[0])).toEqual([`${publicationPath}/prepare`, `${publicationPath}/release`]);
});

it("allows expired publication protection to be renewed on an archived dataset", async () => {
  const server = mockServer({ expired: true });
  server.mutate = async () => { server.value = { ...server.value, expired: false, prepared_count: 0 }; return server.value; };
  mount(true, true); await inspect();
  fireEvent.click(screen.getByRole("button", { name: "Renew protection and recheck all sources" }));
  await screen.findByRole("button", { name: "Resume source checks" });
  expect(commands()).toHaveLength(1);
  expect(commands()[0][0]).toBe(`${publicationPath}/renew`);
});
