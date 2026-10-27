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
