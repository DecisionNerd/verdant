import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request } from "./api";
import { Candidates } from "./candidates";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
function setup(writable = true) {
  history.replaceState(null, "", "/?namespace=ns&dataset=ds&candidate=frozen");
  let latest = 2, failure: ApiError | null = null;
  const candidate = { id: "frozen", source_revision: 2, latest_draft_revision: 2, generation_id: "generation", state: "sealed", prepared_count: 1, cursor: "sample" };
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (failure && body === undefined) throw failure;
    if (path.endsWith("/check-selection")) throw new ApiError(409, "Latest draft changed. Preparation is retained.");
    if (path.includes("/draft/members")) return { revision: latest };
    if (path.includes("/frozen/members")) return { candidate: { ...candidate, latest_draft_revision: latest }, items: [{ sample_id: "sample", sample_revision_id: "exact-revision", split: "train", interpretation: {} }], next_after: null };
    return { items: [candidate], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Candidates namespace="ns" dataset="ds" csrf="csrf" writable={writable} /></QueryClientProvider>);
  return { advance: () => { latest = 3; }, fail: (error: ApiError) => { failure = error; }, refresh: () => act(async () => { await client.invalidateQueries(); }) };
}
describe("frozen candidate selection", () => {
  it("keeps the selected generation when the working draft advances and current mode conflicts", async () => {
    const test = setup(); await screen.findByText("exact-revision");
    test.advance(); await test.refresh();
    expect(screen.getByRole("heading", { name: "Selected candidate frozen" })).toBeTruthy();
    await screen.findByText("Latest draft revision: 3");
    fireEvent.change(screen.getByLabelText("Selection mode"), { target: { value: "current_draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Check selection" }));
    await screen.findByText(/Preparation is retained/);
    expect(screen.getByText("exact-revision")).toBeTruthy();
    expect(screen.getByText("train")).toBeTruthy();
    expect(vi.mocked(request)).toHaveBeenCalledWith("/api/v1/namespaces/ns/datasets/ds/candidates/frozen/check-selection", "csrf", { mode: "current_draft" });
  });
  it("preserves frozen content during an outage and removes it after revocation", async () => {
    const test = setup(); await screen.findByText("exact-revision");
    test.fail(new ApiError(502, "Unavailable")); await test.refresh();
    await screen.findByText(/candidate selection is preserved/);
    expect(screen.getByText("exact-revision")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Check selection" }) as HTMLButtonElement).disabled).toBe(true);
    test.fail(new ApiError(403, "Revoked")); await test.refresh();
    await screen.findByText(/Candidate access changed/);
    expect(screen.queryByText("exact-revision")).toBeNull();
  });
  it("lets read-only users inspect frozen membership without preparation controls", async () => {
    setup(false); await screen.findByText("exact-revision");
    expect(screen.queryByRole("button", { name: "Capture latest draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resume preparation" })).toBeNull();
  });
});

it("retries lost capture admission with the same request and source revision", async () => {
  let latest = 5, attempts = 0;
  const candidate = { id: "captured", source_revision: 5, latest_draft_revision: 6, generation_id: "generation", state: "preparing", prepared_count: 0, cursor: null };
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (path.includes("/draft/members")) return { revision: latest };
    if (body !== undefined) {
      attempts += 1;
      if (attempts === 1) { latest = 6; throw new ApiError(502, "Capture response lost"); }
      return candidate;
    }
    if (path.includes("/captured/members")) return { candidate, items: [], next_after: null };
    return { items: [], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Candidates namespace="ns" dataset="ds" csrf="csrf" writable /></QueryClientProvider>);
  await screen.findByText("Latest draft revision: 5");
  fireEvent.click(screen.getByRole("button", { name: "Capture latest draft" }));
  await screen.findByText("Capture response lost");
  await screen.findByText("Latest draft revision: 6");
  fireEvent.click(screen.getByRole("button", { name: "Retry capture of draft revision 5" }));
  await screen.findByRole("heading", { name: "Selected candidate captured" });
  const calls = vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
  expect(calls).toHaveLength(2);
  expect(calls[0][2]).toEqual(calls[1][2]);
  expect(calls[1][2]).toMatchObject({ expected_revision: 5 });
});
