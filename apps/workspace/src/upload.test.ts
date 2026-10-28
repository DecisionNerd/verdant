import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { fileDigest, transferUploads, type UploadBatch } from "./upload";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); });
const limits = { configured: true, max_artifact_bytes: 256, max_items: 100, part_bytes: 4 };
async function fixture(content = "abcdefghij") {
  vi.stubGlobal("crypto", webcrypto);
  const data = new TextEncoder().encode(content);
  const file = new File([data], "file.txt");
  Object.defineProperty(file, "arrayBuffer", { value: async () => data.buffer });
  const digest = await fileDigest(file);
  const batch: UploadBatch = { operation: { id: "op", kind: "ingest_upload", epoch: 2, status: "running" }, cleanup_operation_id: null,
    items: [{ id: "item", source_key: "source", modality: "text", sha256: digest, size_bytes: file.size,
      state: "uploading", error: null, sample_id: null, accepted_revision_id: null, replacement_operation_id: null, retry_of_item_id: null }] };
  const calls: { path: string; body: unknown }[] = [];
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    calls.push({ path, body });
    if (path.endsWith("/parts?epoch=2")) return { parts: [{ part_number: 1, size_bytes: 4 }] };
    if (path.endsWith("/sign-part")) {
      const part = (body as { part_number: number }).part_number;
      return { url: `https://objects.test/part${part}`, size_bytes: Math.min(4, file.size - (part - 1) * 4) };
    }
    return batch;
  });
  const put = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal("fetch", put);
  return { batch, file, calls, put, files: new Map([["file", file]]) };
}
it("resumes only missing native parts with cookie-free direct PUTs", async () => {
  const test = await fixture();
  await transferUploads("ns", "csrf", "op", test.files, limits, new AbortController().signal, vi.fn());
  expect(test.put.mock.calls.map(call => call[0])).toEqual(["https://objects.test/part2", "https://objects.test/part3"]);
  expect(test.put.mock.calls.map(call => call[1].body.size)).toEqual([4, 2]);
  expect(test.put.mock.calls[0][1]).toMatchObject({ credentials: "omit", redirect: "error", method: "PUT" });
  expect(test.calls.find(call => call.path.endsWith("/complete"))?.body).toEqual({ epoch: 2 });
});
it("rejects a wrong file before initiation or signing", async () => {
  const test = await fixture(); test.batch.items[0].sha256 = "f".repeat(64);
  await expect(transferUploads("ns", "csrf", "op", test.files, limits, new AbortController().signal, vi.fn())).rejects.toThrow(/Reselect the original bytes/);
  expect(test.calls).toHaveLength(1); expect(test.put).not.toHaveBeenCalled();
});
it("reconciles a completed upload after multipart discovery returns missing", async () => {
  const test = await fixture();
  vi.mocked(request).mockImplementation(async (path, _csrf, body) => {
    test.calls.push({ path, body });
    if (path.includes("/parts?")) throw new ApiError(404, "Native upload missing");
    return test.batch;
  });
  await transferUploads("ns", "csrf", "op", test.files, limits, new AbortController().signal, vi.fn());
  expect(test.calls.some(call => call.path.endsWith("/complete"))).toBe(true);
  expect(test.put).not.toHaveBeenCalled();
});
it("stops after an in-flight part without issuing completion", async () => {
  const test = await fixture(); const controller = new AbortController();
  test.put.mockImplementation(async () => { controller.abort(); return { ok: true }; });
  await expect(transferUploads("ns", "csrf", "op", test.files, limits, controller.signal, vi.fn())).rejects.toThrow();
  expect(test.put).toHaveBeenCalledTimes(1);
  expect(test.calls.some(call => call.path.endsWith("/complete"))).toBe(false);
});
it("uploads one zero-byte part for an empty declared artifact", async () => {
  const test = await fixture("");
  await transferUploads("ns", "csrf", "op", test.files, limits, new AbortController().signal, vi.fn());
  expect(test.put).toHaveBeenCalledTimes(1); expect(test.put.mock.calls[0][1].body.size).toBe(0);
});

it("never sends source registrations through upload controls", async () => {
  const test = await fixture(); test.batch.operation.kind = "ingest_source";
  await expect(transferUploads("ns", "csrf", "op", test.files, limits, new AbortController().signal, vi.fn())).rejects.toThrow(/cannot receive uploads/);
  expect(test.calls).toHaveLength(1); expect(test.put).not.toHaveBeenCalled();
});
