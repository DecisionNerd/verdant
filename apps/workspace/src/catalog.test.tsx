import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
