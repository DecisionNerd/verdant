import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { ProcessWorkspace } from "./process";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
const flow = { id: "flow1", name: "Text processing", revision: 1, config_digest: "flowdigest", definition: { steps: [{ name: "Clean", kind: "preprocess", transform: "strip", profile_id: null, prompt: "", output_contract: "text" }] } };
const detail = { operation: { id: "run1", status: "paused", epoch: 1 }, flow, input: { dataset_id: "dataset", candidate_id: "candidate", version_id: null, generation_id: "generation", member_count: 1, completed_count: 0, trial_of: null }, needs_reconciliation: false, attempts: [{ epoch: 1, status: "paused", error_code: null }], outputs: [], next_after: null };
function setup(options: { writable?: boolean; detail?: typeof detail; startFails?: boolean } = {}) {
  if (options.detail) history.replaceState(null, "", "/?process=run1");
  const starts: any[] = [];
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (path.endsWith("/process-runs") && body) { starts.push(body); if (options.startFails && starts.length === 1) throw new ApiError(503, "Response lost"); return { id: "run1", status: "queued", epoch: 1 }; }
    if (path.includes("/process-runs/run1?")) return options.detail ?? detail;
    if (path.includes("/process-flows?")) return { items: [flow], next_after: null };
    if (path.includes("/candidates?")) return { items: [{ id: "candidate", state: "sealed", source_revision: 1 }], next_after: null };
    if (path.includes("/process-runs?")) return { items: [{ id: "run1", status: "paused", epoch: 1 }], next_after: null };
    if (path.endsWith("/actions")) return {};
    return { items: [], next_after: null };
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ProcessWorkspace namespace="ns" dataset="dataset" csrf="csrf" writable={options.writable ?? true} /></QueryClientProvider>);
  return { starts };
}

it("preserves a failed admission request identity for recovery", async () => {
  const { starts } = setup({ startFails: true });
  await waitFor(() => expect(screen.getByLabelText("Saved flow version").querySelectorAll("option").length).toBe(2));
  fireEvent.change(screen.getByLabelText("Saved flow version"), { target: { value: "flow1" } });
  fireEvent.change(screen.getByLabelText("Pinned input"), { target: { value: "candidate" } });
  fireEvent.click(screen.getByRole("button", { name: "Start pinned run", exact: true }));
  await screen.findByText("Response lost");
  fireEvent.click(screen.getByRole("button", { name: "Recover start request" }));
  await screen.findByText("Run admitted on pinned input.");
  expect(starts).toHaveLength(2); expect(starts[0]).toEqual(starts[1]);
  expect(new URLSearchParams(location.search).get("process")).toBe("run1");
});

it("uses the owning action with the current epoch and exposes workflow reconciliation", async () => {
  setup({ detail }); const run = await screen.findByRole("article", { name: "Selected process run" });
  fireEvent.click(within(run).getByRole("button", { name: "Resume run" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/namespaces/ns/process-runs/run1/actions", "csrf", { action: "resume", epoch: 1 }));
  await waitFor(() => expect((within(run).getByRole("button", { name: "Reconcile workflow" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(within(run).getByRole("button", { name: "Reconcile workflow" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/namespaces/ns/process-runs/run1/actions", "csrf", { action: "reconcile", epoch: 1 }));
});

it("shows unknown provider outcomes and disables recovery until reconciliation", async () => {
  setup({ detail: { ...detail, operation: { ...detail.operation, status: "reconciliation_needed" }, needs_reconciliation: true } });
  const run = await screen.findByRole("article", { name: "Selected process run" });
  expect(within(run).getByRole("alert").textContent).toContain("Calls will not be replayed");
  expect((within(run).getByRole("button", { name: "Recover same trial" }) as HTMLButtonElement).disabled).toBe(true);
});

it("keeps model and flow editing unavailable to readers", async () => {
  setup({ writable: false, detail });
  await screen.findByRole("article", { name: "Selected process run" });
  expect(screen.queryByRole("button", { name: "Save profile version" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save flow version" })).toBeNull();
  expect((screen.getByRole("button", { name: "Start pinned run" }) as HTMLButtonElement).disabled).toBe(true);
});
