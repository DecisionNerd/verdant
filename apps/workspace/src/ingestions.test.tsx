import React, { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { Ingestions } from "./ingestions";
import { fileDigest, transferUploads } from "./upload";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
vi.mock("./upload", async original => ({ ...await original<typeof import("./upload")>(), fileDigest: vi.fn(), transferUploads: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); history.replaceState(null, "", "/"); });
const operation = { id: "op", kind: "ingest_upload", status: "running", epoch: 1 };
const item = { id: "item", state: "awaiting_upload", source_key: "file.txt", modality: "text", sha256: "a".repeat(64), size_bytes: 5, error: null, replacement_operation_id: null, retry_of_item_id: null, sample_id: null, accepted_revision_id: null };
function setup({ selected = false, writable = true, status = "running", state = "awaiting_upload", itemError = null as string | null } = {}) {
  history.replaceState(null, "", selected ? "/?namespace=ns&view=ingestions&ingestion=op" : "/?namespace=ns&view=ingestions");
  const batch = { operation: { ...operation, status }, items: [{ ...item, state, error: itemError, ...(state === "accepted" ? { sample_id: "sample", accepted_revision_id: "r1" } : {}) }], cleanup_operation_id: null };
  vi.mocked(fileDigest).mockResolvedValue(item.sha256);
  vi.mocked(transferUploads).mockResolvedValue(batch);
  vi.mocked(request).mockImplementation(async path => {
    if (path.endsWith("/limits")) return { configured: true, max_artifact_bytes: 268435456, max_items: 100, part_bytes: 8388608 };
    if (path.includes("?limit=")) return { items: [operation], next_after: null };
    return batch;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<StrictMode><QueryClientProvider client={client}><Ingestions namespace="ns" csrf="csrf" writable={writable} /></QueryClientProvider></StrictMode>);
  return batch;
}
it("retries an uncertain admission with exactly the same request identity", async () => {
  setup();
  const original = vi.mocked(request).getMockImplementation()!;
  let first = true;
  vi.mocked(request).mockImplementation(async (...args) => {
    if (args[0].endsWith("/uploads") && first) { first = false; throw new ApiError(502, "Response lost"); }
    return original(...args);
  });
  const input = await screen.findByLabelText("Files to upload");
  fireEvent.change(input, { target: { files: [new File(["hello"], "file.txt", { type: "text/plain" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Create upload operation" }));
  await screen.findByText("Response lost");
  expect((input as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Create upload operation" }));
  await screen.findByRole("article", { name: "Ingestion operation" });
  const commands = vi.mocked(request).mock.calls.filter(call => call[0].endsWith("/uploads"));
  expect(commands).toHaveLength(2); expect(commands[0][2]).toEqual(commands[1][2]);
});
it("unlocks a definitively rejected declaration for correction", async () => {
  setup(); const original = vi.mocked(request).getMockImplementation()!;
  vi.mocked(request).mockImplementation(async (...args) => { if (args[0].endsWith("/uploads")) throw new ApiError(409, "Source already exists"); return original(...args); });
  const input = await screen.findByLabelText("Files to upload");
  fireEvent.change(input, { target: { files: [new File(["hello"], "file.txt")] } });
  fireEvent.click(screen.getByRole("button", { name: "Create upload operation" }));
  await screen.findByText("Source already exists");
  expect((input as HTMLInputElement).disabled).toBe(false);
  expect((screen.getByLabelText("Source identity for file.txt") as HTMLInputElement).disabled).toBe(false);
});
it("offers linked re-upload for ready items whose verification failed", async () => {
  setup({ selected: true, status: "failed", state: "ready", itemError: "artifact_corrupt" });
  fireEvent.click(await screen.findByRole("button", { name: "Create linked re-upload for unfinished files" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/v1/namespaces/ns/ingestions/reuploads", "csrf", expect.objectContaining({ item_ids: ["item"] })));
  expect((screen.getByRole("button", { name: "Cancel upload operation" }) as HTMLButtonElement).disabled).toBe(false);
});
it("runs resumed transfer correctly after StrictMode effect replay", async () => {
  setup({ selected: true });
  fireEvent.click(await screen.findByRole("button", { name: "Upload or resume files" }));
  await screen.findByText(/Awaiting independent worker verification/);
  expect(transferUploads).toHaveBeenCalledTimes(1);
  expect(document.activeElement?.textContent).toBe("Ingestion op");
});
it("shows read-only history without upload or recovery controls", async () => {
  setup({ selected: true, writable: false });
  await screen.findByRole("article", { name: "Ingestion operation" });
  expect(screen.queryByLabelText("Files to upload")).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel upload operation" })).toBeNull();
});
it("hides operation details after current access is denied", async () => {
  setup({ selected: true });
  await screen.findByRole("article", { name: "Ingestion operation" });
  const original = vi.mocked(request).getMockImplementation()!;
  vi.mocked(request).mockImplementation(async (...args) => { if (args[0].endsWith("/op")) throw new ApiError(403, "Revoked"); return original(...args); });
  fireEvent.click(screen.getByRole("button", { name: "Refresh upload operation" }));
  await screen.findByText("Workspace access changed. Upload details are hidden.");
  expect(screen.queryByText("file.txt")).toBeNull();
});

it("pins replacement identity and retains selected metadata across uncertain admission", async () => {
  setup({ selected: true, status: "succeeded", state: "accepted" });
  const original = vi.mocked(request).getMockImplementation()!;
  let first = true;
  vi.mocked(request).mockImplementation(async (...args) => {
    if (args[0].endsWith("/revisions/r1")) return { revision: 7, metadata: { title: "retain" } };
    if (args[0].endsWith("/uploads") && first) { first = false; throw new ApiError(502, "Replacement response lost"); }
    return original(...args);
  });
  fireEvent.click(await screen.findByRole("button", { name: "Replace bytes for file.txt" }));
  fireEvent.change(screen.getByLabelText("Replacement file for file.txt"), { target: { files: [new File(["new bytes"], "new.txt")] } });
  fireEvent.submit(screen.getByRole("form", { name: "Replace content for file.txt" }));
  await screen.findByText(/Replacement response lost/);
  fireEvent.submit(screen.getByRole("form", { name: "Replace content for file.txt" }));
  await waitFor(() => expect(vi.mocked(request).mock.calls.filter(call => call[0].endsWith("/uploads"))).toHaveLength(2));
  const commands = vi.mocked(request).mock.calls.filter(call => call[0].endsWith("/uploads"));
  expect(commands[0][2]).toEqual(commands[1][2]);
  expect(commands[0][2]).toMatchObject({ items: [{ source_key: "file.txt", modality: "text", target_sample_id: "sample", expected_revision: 7, metadata: { title: "retain" } }] });
});
