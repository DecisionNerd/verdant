import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Annotations } from "./annotations";
import { ApiError, request } from "./api";
vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const assignment = { id: "assignment", sample_id: "sample", sample_revision_id: "revision", source_revision: 7, schema_digest: "digest", contributors: ["human"], cohort_id: "cohort", native_task_id: 42, task_schema: { labels: ["positive", "negative"] }, editor_url: "https://labels.example.test/projects/1/data?task=42" };
function setup(available: boolean, editorURL = assignment.editor_url) {
  vi.mocked(request).mockImplementation(async path => {
    if (path.endsWith("/annotation-integration")) return { available, reason: available ? "Admitted integration" : "External labeling unavailable", shared_trust: "Community projects do not isolate humans; blindness is unverified.", cohorts: [{id: "cohort", name: "Team", origin: "https://labels.example.test", image: "pinned", contributors: []}] };
    if (path.includes("/draft/members")) return { revision: 7, members: [], next_after: null };
    if (path.endsWith("/annotations/assignment")) return { assignment: {...assignment, editor_url: editorURL}, originals: [], latest: [], missing_contributors: ["human"], canonical_id: null };
    return {items: [assignment], next_after: null};
  });
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><Annotations namespace="namespace" dataset="dataset" csrf="csrf" writable /></QueryClientProvider>);
}
it("discloses an unavailable integration and prevents assignment mutation", async () => {
  setup(false);
  await screen.findByText("External labeling unavailable");
  expect((screen.getByRole("button", {name: "Create assignment"}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("link", {name: "Open stock labeling editor"})).toBeNull();
});
it("opens only the admitted origin with stock editor isolation attributes", async () => {
  setup(true); fireEvent.click(await screen.findByRole("button", {name: "Inspect assignment 42"}));
  const link = await screen.findByRole("link", {name: "Open stock labeling editor"});
  expect(link.getAttribute("href")).toBe(assignment.editor_url);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByText(/Community projects do not isolate/)).toBeTruthy();
});
it("rejects an editor URL outside the admitted cohort origin", async () => {
  setup(true, "https://evil.example.test/projects/1/data?task=42");
  fireEvent.click(await screen.findByRole("button", {name: "Inspect assignment 42"}));
  await screen.findByText("The allowed native origin is unavailable.");
  expect(screen.queryByRole("link", {name: "Open stock labeling editor"})).toBeNull();
});
it("removes original and handoff data after a current authorization denial", async () => {
  setup(true); fireEvent.click(await screen.findByRole("button", {name: "Inspect assignment 42"}));
  await screen.findByRole("link", {name: "Open stock labeling editor"});
  vi.mocked(request).mockRejectedValue(new ApiError(403, "Revoked"));
  fireEvent.click(screen.getByRole("button", {name: "Refresh annotation state"}));
  await screen.findByText("Annotation access changed. Reload your workspace.");
  expect(screen.queryByRole("link", {name: "Open stock labeling editor"})).toBeNull();
  expect(screen.queryByRole("article", {name: "Selected annotation assignment"})).toBeNull();
});
