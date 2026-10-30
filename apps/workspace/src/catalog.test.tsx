import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { Catalog } from "./catalog";
vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
const namespace = {id: "requested", name: "Requested workspace", access: "write"};
function setup(outsideFirstPage = false) {
  history.replaceState(null, "", "/?namespace=requested&dataset=dataset");
  vi.mocked(request).mockImplementation(async path => {
    if (path.startsWith("/api/v1/namespaces?")) {
      if (path.includes("after=")) throw new ApiError(502, "Page unavailable");
      return { items: outsideFirstPage ? [{id: "other", name: "Other workspace", access: "read"}] : [namespace], next_after: "more" };
    }
    if (path === "/api/v1/namespaces/requested") return namespace;
    if (path.endsWith("/datasets/dataset")) return {id: "dataset", namespace_id: "requested", owner_id: "actor", name: "Saved name", slug: "saved", modality: "image", revision: 0, draft_id: "draft", created_at: "2026-10-27T00:00:00Z"};
    if (path.includes("/draft/members")) return {draft_id: "draft", revision: 0, members: [], next_after: null};
    return {items: [], next_after: null};
  });
  render(<Catalog csrf="test" principal="actor" />);
}
it("retains an open editor and unsaved input when the next namespace page fails", async () => {
  setup(); const input = await screen.findByLabelText("Name");
  fireEvent.change(input, {target: {value: "Keep this input"}});
  fireEvent.click(screen.getByRole("button", {name: "Load more workspaces"}));
  await screen.findByText(/More workspaces could not be loaded/);
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Keep this input");
});
it("opens a valid dataset deep link outside the first namespace page", async () => {
  setup(true); await screen.findByLabelText("Name");
  expect((screen.getByLabelText("Workspace") as HTMLSelectElement).value).toBe("requested");
  expect(screen.queryByText(/unavailable or access was removed/)).toBeNull();
});

it("filters archived datasets on the server and resets pagination when toggled", async () => {
  history.replaceState(null, "", "/?namespace=requested");
  vi.mocked(request).mockImplementation(async path => {
    if (path.startsWith("/api/v1/namespaces?")) return { items: [namespace], next_after: null };
    if (path === "/api/v1/namespaces/requested") return namespace;
    if (path.includes("/datasets?")) return { items: path.includes("include_archived=true") ? [{ id: "archived", namespace_id: "requested", name: "Archived fixture", modality: "text", owner_id: "actor", revision: 2, availability: "archived" }] : [], total: path.includes("include_archived=true") ? 1 : 0, next_after: "next-page" };
    throw new Error(`Unexpected ${path}`);
  });
  render(<Catalog csrf="test" principal="actor" />);
  await screen.findByText("No datasets match these filters.");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(vi.mocked(request).mock.calls.some(call => call[0].includes("after=next-page"))).toBe(true));
  fireEvent.click(screen.getByLabelText("Show archived datasets"));
  await screen.findByRole("link", { name: "Archived fixture" });
  expect(screen.getByText("archived")).toBeTruthy();
  const filtered = vi.mocked(request).mock.calls.filter(call => call[0].includes("include_archived=true"));
  expect(filtered.at(-1)![0]).not.toContain("after=");
  expect(screen.getByRole("table").textContent).toContain("1 matching datasets · page 1");
});
