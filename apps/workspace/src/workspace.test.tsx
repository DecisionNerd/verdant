import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Workspace } from "./workspace";
vi.mock("./catalog", () => ({ Catalog: ({principal}: {principal: string}) => <section aria-label={`Account ${principal}`}>Catalog {principal}</section> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function session(principal: string) {
  return { ok: true, json: async () => ({ authenticated: true, principal_id: principal,
    csrf_token: `csrf-${principal}`, expires_at: Date.now() / 1000 + 600, display_name: principal }) };
}
it("hides old account while revalidating and replaces its catalog after shared cookie changes", async () => {
  const fetcher = vi.fn().mockResolvedValue(session("A")); vi.stubGlobal("fetch", fetcher);
  render(<Workspace />);
  await screen.findByRole("region", {name: "Account A"});
  let resolve!: (response: unknown) => void;
  fetcher.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  fireEvent(window, new Event("focus"));
  expect(screen.queryByRole("region", {name: "Account A"})).toBeNull();
  await act(async () => { resolve(session("B")); });
  await screen.findByRole("region", {name: "Account B"});
  expect(screen.queryByText("Catalog A")).toBeNull();
});
