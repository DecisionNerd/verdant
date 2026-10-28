import React, { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { SourceRegistration } from "./sources";
vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function setup() {
  const select = vi.fn();
  render(<StrictMode><SourceRegistration namespace="ns" csrf="csrf" limits={{ configured: true, sources_configured: true, max_items: 100, max_artifact_bytes: 256, part_bytes: 8 }} select={select} /></StrictMode>);
  return select;
}
function fill(key = "object.txt", identity = "source") {
  fireEvent.change(screen.getByLabelText("Relative object key 1"), { target: { value: key } });
  fireEvent.change(screen.getByLabelText("Source identity 1"), { target: { value: identity } });
  fireEvent.change(screen.getByLabelText("Expected SHA-256 1"), { target: { value: "a".repeat(64) } });
  fireEvent.change(screen.getByLabelText("Expected bytes 1"), { target: { value: "4" } });
}
it("registers another batch after acknowledged success without remounting", async () => {
  vi.mocked(request).mockResolvedValue({ operation: { id: "op" } });
  const select = setup(); fill();
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("op"));
  expect((screen.getByLabelText("Relative object key 1") as HTMLInputElement).value).toBe("");
  fill("second.txt", "second");
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await waitFor(() => expect(select).toHaveBeenCalledTimes(2));
  const calls = vi.mocked(request).mock.calls;
  expect(calls[0][2]).toMatchObject({ items: [{ source_object: "object.txt", source_key: "source", sha256: "a".repeat(64), size_bytes: 4 }] });
  expect(calls[1][2]).toMatchObject({ items: [{ source_object: "second.txt", source_key: "second" }] });
  expect(calls[0][2]).not.toEqual(calls[1][2]);
});
it("retries an uncertain source admission with the same pinned command", async () => {
  vi.mocked(request).mockRejectedValueOnce(new ApiError(502, "Response lost")).mockResolvedValueOnce({ operation: { id: "committed" } });
  const select = setup(); fill();
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await screen.findByText("Response lost");
  expect((screen.getByLabelText("Relative object key 1") as HTMLInputElement).closest("fieldset")?.parentElement?.closest("fieldset")?.disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("committed"));
  const calls = vi.mocked(request).mock.calls;
  expect(calls[0][2]).toEqual(calls[1][2]);
});
it("unlocks definitive rejection and removes admission controls on denial", async () => {
  vi.mocked(request).mockRejectedValueOnce(new ApiError(422, "Invalid source key")).mockRejectedValueOnce(new ApiError(403, "Revoked"));
  setup(); fill();
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await screen.findByText("Invalid source key");
  fireEvent.change(screen.getByLabelText("Relative object key 1"), { target: { value: "corrected.txt" } });
  fireEvent.click(screen.getByRole("button", { name: "Start source verification" }));
  await screen.findByText("Source registration access changed. Reload the workspace.");
  expect(screen.queryByRole("button", { name: "Start source verification" })).toBeNull();
});
