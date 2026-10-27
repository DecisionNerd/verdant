import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request, type Dataset } from "./api";
import { DatasetView } from "./dataset";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const dataset: Dataset = { id: "dataset", namespace_id: "namespace", owner_id: "owner", revision: 0, slug: "fixture", name: "Original", modality: "text", draft_id: "draft", created_at: "2026-10-27T00:00:00Z" };
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
