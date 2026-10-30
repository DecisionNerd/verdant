import { afterEach, expect, it, vi } from "vitest";
import { request } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("shows a safe namespace identity conflict instead of a revision instruction", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: false, status: 409,
    json: async () => ({detail: "Identity already exists in this namespace"})}));
  await expect(request("/api/test", "csrf", {})).rejects.toThrow("Identity already exists in this namespace");
});
it("retains a safe fallback for unreadable conflict responses", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: false, status: 409,
    json: async () => { throw new Error("invalid JSON"); }}));
  await expect(request("/api/test", "csrf", {})).rejects.toThrow("This record changed");
});
it.each([null, {detail: "x".repeat(501)}])("keeps a truthful bounded missing-resource fallback", async payload => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: false, status: 404, json: async () => payload}));
  await expect(request("/api/test", "csrf")).rejects.toThrow("The requested record or artifact bytes are missing or inaccessible.");
});
