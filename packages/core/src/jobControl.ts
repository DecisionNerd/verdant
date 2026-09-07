import fs from "node:fs/promises";
import path from "node:path";
import { outputsDir } from "./paths.js";

/** Thrown when a digest run is cancelled cooperatively between page work. */
export class DigestCancelledError extends Error {
  constructor(message = "Digest cancelled") {
    super(message);
    this.name = "DigestCancelledError";
  }
}

export function throwIfCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DigestCancelledError();
}

const activeRuns = new Map<string, AbortController>();

export function registerJobAbort(runId: string): AbortSignal {
  const existing = activeRuns.get(runId);
  if (existing) return existing.signal;
  const controller = new AbortController();
  activeRuns.set(runId, controller);
  return controller.signal;
}

export function unregisterJobAbort(runId: string): void {
  activeRuns.delete(runId);
}

export function abortJobRun(runId: string): boolean {
  const controller = activeRuns.get(runId);
  if (!controller) return false;
  controller.abort();
  return true;
}

export function isJobRunActive(runId: string): boolean {
  return activeRuns.has(runId);
}

/** Remove saved page digests so the next run re-processes every page. */
export async function clearPageDigestsForRerun(runId: string): Promise<void> {
  const runDir = path.join(outputsDir(), runId);
  const pagesDir = path.join(runDir, "pages");
  try {
    const names = await fs.readdir(pagesDir);
    await Promise.all(
      names
        .filter((n) => /^page-\d+\.(md|ir\.json)$/i.test(n))
        .map((n) => fs.unlink(path.join(pagesDir, n)).catch(() => undefined)),
    );
  } catch {
    // no pages dir yet
  }
  for (const name of ["document.md", "document.html", "page-progress.json", "structure.json"]) {
    await fs.unlink(path.join(runDir, name)).catch(() => undefined);
  }
}
