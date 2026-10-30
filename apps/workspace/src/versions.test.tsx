import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError, request } from "./api";
import { Versions } from "./versions";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); history.replaceState(null, "", "/"); });
const base = "/api/v1/namespaces/ns/datasets/ds";
const exact = `${base}/versions/version-1`;
function version() {
  return { id: "version-1", namespace_id: "ns", dataset_id: "ds", label: "release-1", generation_id: "generation-1", candidate_id: "candidate-1", report_id: "report-1", manifest_artifact_id: "manifest-1", manifest_digest: "manifest-digest", manifest_size: 1234, created_at: "2026-10-28T00:00:00Z", availability: "active", prior_availability: "active", availability_revision: 0 };
}
const member = { sample_id: "sample-1", sample_revision_id: "sample-revision-1", revision: 3, split: "train", metadata: { label: "cat" }, interpretation: {}, artifact: { id: "artifact-1", sha256: "sample-digest", size_bytes: 12 },
  lineage: [
    { kind: "sample_revision", id: "sample-revision-1", status: "present", label: "Exact frozen source revision" },
    { kind: "annotation_revision", id: null, status: "absent", label: "No annotation revision recorded" },
    { kind: "task_schema_revision", id: null, status: "absent", label: "No task schema revision recorded" },
    { kind: "review_evidence", id: "review-1", status: "partial", label: "Review evidence is incomplete" },
    { kind: "transformation", id: null, status: "absent", label: "No transformation recorded" },
    { kind: "quality_report", id: "report-1", status: "present", label: "Exact quality report" },
    { kind: "quality_policy", id: "policy-1", status: "present", label: "Exact policy revision" },
  ] };
const preview = { sample_id: member.sample_id, sample_revision_id: member.sample_revision_id, artifact_id: member.artifact.id, sha256: member.artifact.sha256, size_bytes: member.artifact.size_bytes, modality: "text", kind: "text", text: "<script>private text</script>", truncated: false, warning: null as string | null, download_path: "https://signed.example/private?token=do-not-render" };
function mockServer() {
  const server = { value: version(), next: null as string | null, memberNext: null as string | null, failure: null as Error | null,
    preview: { ...preview }, previewFailure: null as Error | null,
    mutation: async (_body: unknown) => server.value };
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    if (body !== undefined) return server.mutation(body);
    if (server.failure) throw server.failure;
    if (path.includes("/versions?")) return { items: server.value.availability === "archived" && !path.includes("include_archived=true") ? [] : [server.value], total: 1, next_after: server.next };
    if (path === exact) return server.value;
    if (path === `${base}/versions/version-2`) return { ...server.value, id: "version-2", label: "release-2" };
    if (path.includes("/members?")) return { version: path.includes("version-2") ? { ...server.value, id: "version-2" } : server.value,
      items: [member], total: 30, next_after: server.memberNext, lineage_status: "partial",
      quality_policy: { id: "policy-1", name: "ready", revision: 2, definition: {} },
      quality_report: { id: "report-1", policy_id: "policy-1", policy_revision: 2, source_revision: 5, evaluator_version: "verdant.core/1", state: "complete", required_rules_pass: true } };
    if (path.endsWith("/manifest")) return { artifact_id: "manifest-1", sha256: "manifest-digest", size_bytes: 1234, schema: "verdant.manifest.v1", download_path: "https://signed.example/manifest?secret=never-render" };
    if (path.endsWith("/preview")) { if (server.previewFailure) throw server.previewFailure; return server.preview; }
    throw new Error(`Unexpected ${path}`);
  });
  return server;
}
function mount(writable = true, selected = "version-1") {
  history.replaceState(null, "", `/?namespace=ns&dataset=ds${selected ? `&version=${selected}` : ""}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const component = (namespace = "ns", dataset = "ds") => <QueryClientProvider client={client}><Versions namespace={namespace} dataset={dataset} csrf="csrf" writable={writable} /></QueryClientProvider>;
  const view = render(component());
  return { refresh: () => act(async () => { await client.invalidateQueries(); }),
    switchContext: (namespace: string, dataset: string) => view.rerender(component(namespace, dataset)) };
}
const commands = () => vi.mocked(request).mock.calls.filter(call => call[2] !== undefined);
async function openPreview() {
  fireEvent.click(await screen.findByRole("button", { name: "Preview exact sample sample-1" }));
  return screen.findByRole("complementary", { name: "Exact version sample preview" });
}

describe("exact version consumption", () => {
  it("reads the exact deep-linked version, immutable identities, safe text and accessible partial lineage", async () => {
    mockServer(); mount();
    await screen.findByRole("heading", { name: "Exact published version release-1" });
    expect(screen.getByText("manifest-digest")).toBeTruthy();
    expect(screen.getByText(/These are recorded identities/)).toBeTruthy();
    const sample = await openPreview();
    await within(sample).findByText("<script>private text</script>");
    expect(sample.querySelector("script")).toBeNull();
    expect(screen.getByRole("img", { name: /Recorded lineage for sample sample-1/ })).toBeTruthy();
    const table = screen.getByRole("table", { name: "Accessible lineage evidence for sample sample-1" });
    expect(within(table).getByText("Review evidence is incomplete")).toBeTruthy();
    expect(within(table).getAllByText("No pin recorded")).toHaveLength(3);
    expect(screen.getByText(/Some interpretation lineage on this page is missing or partial/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download exact manifest (verifies bytes)" }).getAttribute("href")).toBe(`${exact}/manifest/download`);
    expect(screen.getByRole("link", { name: "Download exact sample (verifies bytes)" }).getAttribute("href")).toBe(`${exact}/samples/sample-1/download`);
    expect(document.body.innerHTML).not.toContain("signed.example");
    expect(document.body.innerHTML).not.toContain("do-not-render");
    expect(commands()).toHaveLength(0);
  });

  it("bounds the displayed Unicode preview to 4 KiB and never fabricates text for binary content", async () => {
    const server = mockServer(); server.preview.text = "é".repeat(3000); server.preview.truncated = true;
    const test = mount(); const sample = await openPreview();
    await within(sample).findByText("Text preview is limited to the first 4 KiB.");
    expect(new TextEncoder().encode(sample.querySelector("pre")!.textContent!).length).toBeLessThanOrEqual(4096);
    server.preview.kind = "binary"; server.preview.text = "this should not be displayed";
    await test.refresh();
    await within(sample).findByText("This is binary content. A text preview is unavailable; use the verified download.");
    expect(sample.querySelector("pre")).toBeNull();
    expect(within(sample).queryByText("this should not be displayed")).toBeNull();
    expect(within(sample).getByRole("link", { name: /Download exact sample/ })).toBeTruthy();
  });

  it.each([409, 503])("removes preview bytes after a storage error %s without claiming availability", async status => {
    const server = mockServer(); const test = mount(); const sample = await openPreview();
    await within(sample).findByText("<script>private text</script>");
    server.previewFailure = new ApiError(status, "Exact artifact is missing or could not be verified."); await test.refresh();
    await within(sample).findByText(/Preview is unavailable; no bytes are shown/);
    expect(within(sample).queryByText("<script>private text</script>")).toBeNull();
    expect(sample.querySelector("pre")).toBeNull();
    expect(within(sample).getByRole("button", { name: "Retry verified preview" })).toBeTruthy();
  });

  it("shows an actual missing-byte 404 response and clears previously rendered content", async () => {
    mockServer(); const test = mount(); const sample = await openPreview();
    await within(sample).findByText("<script>private text</script>");
    const actual = await vi.importActual<typeof import("./api")>("./api");
    const otherRequests = vi.mocked(request).getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({detail: "Object or upload is missing"}), {status: 404},
    )));
    vi.mocked(request).mockImplementation((path, csrf, body, method) => path.endsWith("/preview")
      ? actual.request(path, csrf, body, method) : otherRequests(path, csrf, body, method));
    await test.refresh();
    await within(sample).findByText(/Object or upload is missing/);
    expect(within(sample).queryByText("<script>private text</script>")).toBeNull();
    expect(sample.querySelector("pre")).toBeNull();
    expect(within(sample).getByRole("button", { name: "Retry verified preview" })).toBeTruthy();
  });

  it("rejects mismatched preview identities before showing any returned text", async () => {
    const server = mockServer(); server.preview.sample_revision_id = "newer-draft-revision"; mount();
    const sample = await openPreview();
    await within(sample).findByText("Preview identity does not match the frozen sample. No bytes are shown.");
    expect(sample.querySelector("pre")).toBeNull();
  });

  it("deprecates, archives and restores the prior availability with exact CAS revisions", async () => {
    const server = mockServer();
    server.mutation = async body => {
      const command = body as { action: string; expected_revision: number };
      expect(command.expected_revision).toBe(server.value.availability_revision);
      const previous = server.value.availability;
      server.value = { ...server.value, availability_revision: server.value.availability_revision + 1,
        prior_availability: command.action === "archive" ? previous : server.value.prior_availability,
        availability: command.action === "unarchive" ? server.value.prior_availability : command.action === "archive" ? "archived" : command.action === "deprecate" ? "deprecated" : "active" };
      return server.value;
    };
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Deprecate version" }));
    await screen.findByText(/This version is deprecated/);
    fireEvent.click(screen.getByRole("button", { name: "Archive version" }));
    await screen.findByText(/Unarchive restores deprecated availability/);
    expect(screen.getByText("manifest-digest")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download exact manifest/ })).toBeTruthy();
    expect(new URLSearchParams(location.search).get("version")).toBe("version-1");
    fireEvent.click(screen.getByRole("button", { name: "Unarchive version" }));
    await screen.findByText(/Version availability saved: deprecated/);
    expect(screen.getByRole("button", { name: "Activate version" })).toBeTruthy();
    expect(commands().map(call => call[2])).toEqual([
      { action: "deprecate", expected_revision: 0 }, { action: "archive", expected_revision: 1 }, { action: "unarchive", expected_revision: 2 },
    ]);
  });

  it("preserves selection on a CAS conflict and explicitly reloads availability before retrying", async () => {
    const server = mockServer(); let conflict = true;
    server.mutation = async body => {
      if (conflict) { server.value = { ...server.value, availability_revision: 4, availability: "deprecated" }; throw new ApiError(409, "Version availability changed."); }
      expect(body).toEqual({ action: "activate", expected_revision: 4 }); return { ...server.value, availability: "active", availability_revision: 5 };
    };
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Deprecate version" }));
    await screen.findByText(/Version availability changed/);
    expect(screen.getByRole("heading", { name: "Exact published version release-1" })).toBeTruthy();
    expect(new URLSearchParams(location.search).get("version")).toBe("version-1");
    fireEvent.click(screen.getByRole("button", { name: "Load current version availability" }));
    await screen.findByRole("button", { name: "Activate version" }); conflict = false;
    fireEvent.click(screen.getByRole("button", { name: "Activate version" }));
    await screen.findByText(/Version availability saved: active/);
  });

  it("preserves exact reads for an archived version and filters list pages on the server", async () => {
    const server = mockServer(); server.value.availability = "archived"; server.next = "version cursor"; server.memberNext = "sample cursor";
    mount(); await screen.findByRole("heading", { name: "Exact published version release-1" });
    await screen.findByText("No versions match this filter.");
    fireEvent.click(screen.getByLabelText("Show archived versions"));
    await screen.findByRole("link", { name: "release-1" });
    fireEvent.click(screen.getByRole("button", { name: "Next versions" }));
    fireEvent.click(screen.getByRole("button", { name: "Next version members" }));
    await waitFor(() => expect(vi.mocked(request).mock.calls.some(call => call[0] === `${base}/versions?limit=25&include_archived=true&after=version%20cursor`)).toBe(true));
    await waitFor(() => expect(vi.mocked(request).mock.calls.some(call => call[0] === `${exact}/members?limit=25&after=sample%20cursor`)).toBe(true));
  });

  it("lets readers inspect but hides all cached evidence after current access is revoked", async () => {
    const server = mockServer(); const test = mount(false); const sample = await openPreview();
    await within(sample).findByText("<script>private text</script>");
    expect(screen.queryByRole("button", { name: "Archive version" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Deprecate version" })).toBeNull();
    server.failure = new ApiError(403, "Revoked"); await test.refresh();
    await screen.findByText("Version access changed. Reload your workspace before continuing.");
    expect(screen.queryByText("manifest-digest")).toBeNull();
    expect(screen.queryByText("<script>private text</script>")).toBeNull();
    expect(screen.queryByRole("link", { name: /Download exact/ })).toBeNull();
    expect(commands()).toHaveLength(0);
  });

  it("preserves the selected version through a transient read outage", async () => {
    const server = mockServer(); const test = mount();
    await screen.findByRole("heading", { name: "Exact published version release-1" });
    server.failure = new ApiError(502, "Temporary outage"); await test.refresh();
    await screen.findByText(/displayed details may be stale/);
    expect(screen.getByRole("heading", { name: "Exact published version release-1" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Archive version" }) as HTMLButtonElement).disabled).toBe(true);
    expect(new URLSearchParams(location.search).get("version")).toBe("version-1");
  });

  it("opens version links and responds to popstate without keeping an old preview", async () => {
    mockServer(); mount(true, "");
    fireEvent.click(await screen.findByRole("link", { name: "release-1" }));
    expect(new URLSearchParams(location.search).get("version")).toBe("version-1");
    const sample = await openPreview(); await within(sample).findByText("<script>private text</script>");
    history.replaceState(null, "", "/?namespace=ns&dataset=ds&version=version-2");
    fireEvent(window, new PopStateEvent("popstate"));
    await screen.findByRole("heading", { name: "Exact published version release-2" });
    expect(screen.queryByRole("complementary", { name: "Exact version sample preview" })).toBeNull();
    expect(screen.queryByText("<script>private text</script>")).toBeNull();
  });

  it("discards delayed old-context preview results after a dataset switch", async () => {
    mockServer();
    const original = vi.mocked(request).getMockImplementation()!;
    let resolve: ((value: typeof preview) => void) | undefined;
    vi.mocked(request).mockImplementation((path, csrf, body, method) => path.endsWith("/preview") ? new Promise(done => { resolve = done; }) : original(path, csrf, body, method));
    const test = mount(); await openPreview();
    test.switchContext("other-ns", "other-ds");
    await act(async () => { resolve!(preview); });
    expect(screen.queryByRole("complementary", { name: "Exact version sample preview" })).toBeNull();
    expect(screen.queryByText("<script>private text</script>")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Exact published version release-1" })).toBeNull();
  });

  it("discards a delayed availability response after the selected version changes", async () => {
    const server = mockServer();
    let resolve: ((value: ReturnType<typeof version>) => void) | undefined;
    server.mutation = () => new Promise(done => { resolve = done; });
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Deprecate version" }));
    history.replaceState(null, "", "/?namespace=ns&dataset=ds&version=version-2");
    fireEvent(window, new PopStateEvent("popstate"));
    await screen.findByRole("heading", { name: "Exact published version release-2" });
    await act(async () => { resolve!({ ...server.value, availability: "deprecated", availability_revision: 1 }); });
    expect(screen.queryByText(/Version availability saved/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Exact published version release-1" })).toBeNull();
    expect(screen.getByRole("button", { name: "Deprecate version" })).toBeTruthy();
    expect(new URLSearchParams(location.search).get("version")).toBe("version-2");
  });
});
