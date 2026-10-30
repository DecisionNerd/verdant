import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, request } from "./api";
import { SamplePreview } from "./preview";

vi.mock("./api", async original => ({ ...await original<typeof import("./api")>(), request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
const result = {
  revision_id: "r1", sha256: "digest", size_bytes: 30, kind: "text", text: "<script>unsafe()</script>",
  image_png_base64: null, truncated: true, reason: null,
  download_url: "https://objects.test/original?signature=temporary", expires_in: 120,
};
function setup() {
  return render(<SamplePreview namespace="ns" sample="sample" revision="r1" csrf="csrf" />);
}
it("loads exact revision on demand and displays markup as inert text", async () => {
  vi.mocked(request).mockResolvedValue(result);
  const view = setup();
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Load verified preview" }));
  await screen.findByText("<script>unsafe()</script>");
  expect(view.container.querySelector("script")).toBeNull();
  expect(request).toHaveBeenCalledWith("/api/v1/namespaces/ns/samples/sample/revisions/r1/preview", "csrf");
  expect(screen.getByText(/limited to 64 KiB/)).toBeTruthy();
  const link = screen.getByRole("link", { name: /Download verified original/ });
  expect(link.getAttribute("rel")).toBe("noreferrer");
});
it("clears prior content and signed link when refreshed access is denied", async () => {
  vi.mocked(request).mockResolvedValueOnce(result).mockRejectedValueOnce(new ApiError(403, "Access revoked"));
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Load verified preview" }));
  await screen.findByRole("link", { name: /Download verified original/ });
  fireEvent.click(screen.getByRole("button", { name: "Load verified preview" }));
  await screen.findByRole("alert");
  expect(screen.queryByText(result.text)).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});
it("removes expired download capability and renders only server-generated PNG", async () => {
  vi.useFakeTimers();
  vi.mocked(request).mockResolvedValue({ ...result, kind: "image", text: null, image_png_base64: "png" });
  setup();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Load verified preview" })); });
  expect(screen.getByRole("img").getAttribute("src")).toBe("data:image/png;base64,png");
  await act(async () => { vi.advanceTimersByTime(120_000); });
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByText(/Download link expired/)).toBeTruthy();
});
