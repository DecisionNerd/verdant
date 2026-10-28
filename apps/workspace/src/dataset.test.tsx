import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request, type Dataset } from "./api";
import { DatasetView } from "./dataset";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
const dataset: Dataset = { id: "dataset", namespace_id: "namespace", owner_id: "owner", revision: 0, slug: "fixture", name: "Original", modality: "text", draft_id: "draft", created_at: "2026-10-27T00:00:00Z", availability: "active" };
function setup() {
  let failure: ApiError | null = null;
  vi.mocked(request).mockImplementation(async path => {
    if (path.endsWith("/datasets/dataset")) { if (failure) throw failure; return dataset; }
    if (path.includes("/draft/members")) return { draft_id: "draft", revision: 0, members: [], next_after: null };
    return { items: [], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><DatasetView namespace="namespace" id="dataset" csrf="test" writable /></QueryClientProvider>);
  return { fail: (error: ApiError) => { failure = error; }, refresh: () => act(async () => { await client.invalidateQueries({queryKey: ["dataset", "namespace", "dataset"]}); }) };
}
describe("metadata refresh safety", () => {
  it("preserves unsaved input after transient background refresh failure", async () => {
    const test = setup();
    const input = await screen.findByLabelText("Name");
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Original" }));
    fireEvent.change(input, { target: { value: "Unsaved work" } });
    test.fail(new ApiError(502, "Unavailable")); await test.refresh();
    await screen.findByText(/Refresh failed/);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Unsaved work");
  });
  it("removes protected metadata when current access is denied", async () => {
    const test = setup(); await screen.findByLabelText("Name");
    test.fail(new ApiError(403, "Access revoked")); await test.refresh();
    await screen.findByText("Access revoked");
    await waitFor(() => expect(screen.queryByLabelText("Name")).toBeNull());
    expect(screen.queryByText("Original")).toBeNull();
  });
});

it("archives without losing metadata input or its editing revision and unarchives with current CAS", async () => {
  let current = { ...dataset };
  const path = "/api/v1/namespaces/namespace/datasets/dataset";
  vi.mocked(request).mockImplementation(async (url, _csrf, body) => {
    if (url === `${path}/availability`) {
      const command = body as { expected_revision: number; action: string };
      expect(command.expected_revision).toBe(current.revision);
      current = { ...current, revision: current.revision + 1, availability: command.action === "archive" ? "archived" : "active" };
      return current;
    }
    if (url === path) {
      if (body) {
        const command = body as { expected_revision: number; name: string };
        if (command.expected_revision !== current.revision) throw new ApiError(409, "Metadata revision changed; load the current revision.");
        current = { ...current, name: command.name, revision: current.revision + 1 };
      }
      return current;
    }
    if (url.includes("/draft/members")) return { draft_id: "draft", revision: 0, members: [], next_after: null };
    return { items: [], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><DatasetView namespace="namespace" id="dataset" csrf="test" writable /></QueryClientProvider>);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Unsaved metadata" } });
  fireEvent.click(screen.getByRole("button", { name: "Archive dataset" }));
  await screen.findByRole("button", { name: "Unarchive dataset" });
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Unsaved metadata");
  expect(screen.getByLabelText("Name").matches(":disabled")).toBe(true);
  expect((screen.getByRole("button", { name: "Capture latest draft" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("heading", { name: "Author a policy revision" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save membership" })).toBeNull();
  expect(screen.getByRole("region", { name: "Published dataset versions" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Unarchive dataset" }));
  await screen.findByRole("button", { name: "Archive dataset" });
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Unsaved metadata");
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  await screen.findByText("Metadata revision changed; load the current revision.");
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Unsaved metadata");
  fireEvent.click(screen.getByRole("button", { name: "Load current revision" }));
  await screen.findByText(/Current revision 2 loaded/);
  fireEvent.click(screen.getByRole("button", { name: "Save metadata" }));
  await screen.findByText("Metadata saved.");
  const mutations = vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
  expect(mutations.map(call => (call[2] as { expected_revision: number }).expected_revision)).toEqual([0, 1, 0, 2]);
});

it("keeps unsaved metadata on an availability conflict and hides all content on a denied mutation", async () => {
  let denied = false;
  const path = "/api/v1/namespaces/namespace/datasets/dataset";
  vi.mocked(request).mockImplementation(async (url, _csrf, body) => {
    if (body) throw new ApiError(denied ? 403 : 409, denied ? "Access revoked" : "Dataset availability changed.");
    if (url === path) return dataset;
    if (url.includes("/draft/members")) return { draft_id: "draft", revision: 0, members: [], next_after: null };
    return { items: [], next_after: null };
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><DatasetView namespace="namespace" id="dataset" csrf="test" writable /></QueryClientProvider>);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Keep this" } });
  fireEvent.click(screen.getByRole("button", { name: "Archive dataset" }));
  await screen.findByText("Dataset availability changed.");
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Keep this");
  denied = true; fireEvent.click(screen.getByRole("button", { name: "Archive dataset" }));
  await screen.findByText("Dataset access changed. Reload your workspace before continuing.");
  expect(screen.queryByLabelText("Name")).toBeNull();
  expect(screen.queryByRole("region", { name: "Published dataset versions" })).toBeNull();
});
