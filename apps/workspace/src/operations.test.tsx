import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request } from "./api";
import { Operations, VerifyArtifact } from "./operations";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
function setup(writable = true) {
  history.replaceState(null, "", "/?namespace=ns&view=operations&operation=op");
  let failure: ApiError | null = null;
  const operation = { id: "op", kind: "verify_sample_artifact", status: "failed", epoch: 3, revision: 7, created_at: "2026-10-27T00:00:00Z" };
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) return operation;
    if (failure) throw failure;
    if (path.includes("/operations/op")) return { operation, needs_reconciliation: true, provider_run_id: "provider", accepted_digest: null, effects: [{ id: "effect", kind: "artifact.read", status: "unknown", revision: 1, result_digest: null }], next_effect_after: null };
    return { items: [operation], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><Operations namespace="ns" csrf="csrf" writable={writable} /></QueryClientProvider>);
  return { fail: (error: ApiError | null) => { failure = error; }, refresh: () => act(async () => { await client.invalidateQueries(); }) };
}
describe("durable operation recovery", () => {
  it("requests reconciliation using the displayed epoch and no provider facts", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Reconcile operation" }));
    await screen.findByText(/Reconciliation requested/);
    expect(vi.mocked(request)).toHaveBeenCalledWith("/api/v1/namespaces/ns/operations/op/reconcile", "csrf", { epoch: 3 });
    expect(screen.getByText(/reconciliation needed/)).toBeTruthy();
  });
  it("retains last known status through disconnection, disables actions, then reconnects", async () => {
    const test = setup(); await screen.findByRole("button", { name: "Retry operation" });
    test.fail(new ApiError(502, "Unavailable")); await test.refresh();
    await screen.findByText(/Last known status shown/);
    expect((screen.getByRole("button", { name: "Retry operation" }) as HTMLButtonElement).disabled).toBe(true);
    test.fail(null); await test.refresh();
    await waitFor(() => expect((screen.getByRole("button", { name: "Retry operation" }) as HTMLButtonElement).disabled).toBe(false));
  });
  it("removes operation details after access revocation", async () => {
    const test = setup(); await screen.findByText(/Workflow run:/);
    test.fail(new ApiError(403, "Access revoked")); await test.refresh();
    await screen.findByText("Access revoked");
    expect(screen.queryByText(/Workflow run:/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry operation" })).toBeNull();
  });
  it("shows read-only operation details without recovery controls", async () => {
    setup(false); await screen.findByText(/Workflow run:/);
    expect(screen.queryByRole("button", { name: "Retry operation" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reconcile operation" })).toBeNull();
  });
});


it("reuses verification request identity after a lost admission response", async () => {
  vi.mocked(request).mockRejectedValueOnce(new ApiError(502, "Response lost"))
    .mockResolvedValueOnce({ id: "admitted" });
  render(<VerifyArtifact namespace="ns" sample="sample" revision="revision" csrf="csrf" />);
  fireEvent.click(screen.getByRole("button", { name: "Verify artifact" }));
  await screen.findByText(/Response lost/);
  fireEvent.click(screen.getByRole("button", { name: "Verify artifact" }));
  await screen.findByRole("link", { name: "View verification operation" });
  const calls = vi.mocked(request).mock.calls;
  expect(calls[0][2]).toEqual(calls[1][2]);
  expect(calls[0][2]).toMatchObject({ sample_id: "sample", revision_id: "revision" });
});
