import { randomUUID } from "node:crypto";
import { digestDocument } from "./digest.js";
import {
  abortJobRun,
  clearPageDigestsForRerun,
  DigestCancelledError,
  isJobRunActive,
  registerJobAbort,
  unregisterJobAbort,
} from "./jobControl.js";
import { ensureDb } from "./db/client.js";
import {
  listJobRows,
  listRunningJobIds,
  patchJobAtomic,
  persistJobAtomic,
  readJobRow,
} from "./db/jobsRepo.js";
import { emitJobLog } from "./observability.js";
import type {
  DigestPayload,
  DigestPlan,
  JobEvent,
  JobStatus,
  PageProgress,
} from "./types.js";

/** Serialize read-modify-write per runId within this process (cross-process: SQL write tx). */
const jobChains = new Map<string, Promise<unknown>>();

function withJobLock<T>(runId: string, fn: () => Promise<T>): Promise<T> {
  const prev = jobChains.get(runId) ?? Promise.resolve();
  const next = prev.then(fn, fn);
  jobChains.set(
    runId,
    next.then(
      () => undefined,
      () => undefined,
    ),
  );
  return next;
}

async function persistJob(job: JobStatus, events?: JobEvent[]): Promise<void> {
  const client = await ensureDb();
  await persistJobAtomic(client, job, events);
}

export async function writeJob(job: JobStatus): Promise<void> {
  await withJobLock(job.runId, async () => {
    await persistJob(job, job.events ?? []);
  });
}

export async function readJob(runId: string): Promise<JobStatus | null> {
  const client = await ensureDb();
  return readJobRow(client, runId);
}

async function patchJob(
  runId: string,
  patch: {
    message?: string;
    phase?: string;
    plan?: DigestPlan;
    pageProgress?: PageProgress;
  },
): Promise<void> {
  await withJobLock(runId, async () => {
    const client = await ensureDb();
    const job = await readJobRow(client, runId);
    if (!job) return;
    if (
      job.status === "completed" ||
      job.status === "failed" ||
      job.status === "cancelled"
    ) {
      return;
    }

    const now = new Date().toISOString();
    let event: JobEvent | undefined;
    if (patch.message) {
      event = {
        ts: now,
        message: patch.message,
        ...(patch.phase ? { phase: patch.phase } : {}),
      };
      job.events = [...(job.events ?? []), event];
      emitJobLog(runId, event.message, event.phase);
    }
    if (patch.plan) job.plan = patch.plan;
    if (patch.pageProgress) job.pageProgress = patch.pageProgress;
    job.updatedAt = now;
    await patchJobAtomic(client, job, event);
  });
}

export async function enqueueDigest(payload: DigestPayload): Promise<JobStatus> {
  const runId = payload.runId ?? randomUUID().slice(0, 12);
  const now = new Date().toISOString();
  const job: JobStatus = {
    runId,
    status: "queued",
    createdAt: now,
    updatedAt: now,
    payload: { ...payload, runId },
    events: [
      {
        ts: now,
        message: "Job queued",
        phase: "queue",
      },
    ],
  };
  await writeJob(job);
  emitJobLog(runId, "Job queued", "queue");
  return job;
}

function jobCancelWasRequested(job: JobStatus): boolean {
  if (job.cancelRequestedAt) return true;
  return (job.events ?? []).some((e) => e.phase === "cancel");
}

function finalizeCancelledJob(job: JobStatus, message = "Cancelled by user"): JobStatus {
  if (job.status === "cancelled") return job;
  const now = new Date().toISOString();
  job.status = "cancelled";
  job.error = message;
  job.updatedAt = now;
  job.events = [
    ...(job.events ?? []),
    { ts: now, message, phase: "cancelled" },
  ];
  return job;
}

/** Finalize jobs left `running` after worker restart or orphaned cancel requests. */
export async function reconcileStaleJobs(): Promise<number> {
  const client = await ensureDb();
  const runningIds = await listRunningJobIds(client);
  let reconciled = 0;

  for (const runId of runningIds) {
    await withJobLock(runId, async () => {
      const job = await readJobRow(client, runId);
      if (!job || job.status !== "running" || isJobRunActive(runId)) return;

      if (jobCancelWasRequested(job)) {
        const next = finalizeCancelledJob(job);
        await persistJob(next, next.events ?? []);
        reconciled += 1;
        return;
      }

      const now = new Date().toISOString();
      job.status = "failed";
      job.error = "Worker restarted while job was running";
      job.updatedAt = now;
      job.events = [
        ...(job.events ?? []),
        {
          ts: now,
          message: job.error,
          phase: "error",
        },
      ];
      await persistJob(job, job.events);
      reconciled += 1;
    });
  }

  return reconciled;
}

export async function runDigestJob(runId: string): Promise<JobStatus> {
  const signal = registerJobAbort(runId);

  await withJobLock(runId, async () => {
    const job = await readJob(runId);
    if (!job) throw new Error(`Unknown job: ${runId}`);
    if (job.status === "cancelled") return;
    if (job.cancelRequestedAt || jobCancelWasRequested(job)) {
      const cancelled = finalizeCancelledJob(job);
      await persistJob(cancelled, cancelled.events ?? []);
      return;
    }
    if (signal.aborted) {
      const cancelled = finalizeCancelledJob(job);
      await persistJob(cancelled, cancelled.events ?? []);
      return;
    }
    job.status = "running";
    job.error = undefined;
    job.result = undefined;
    job.updatedAt = new Date().toISOString();
    job.events = [
      ...(job.events ?? []),
      { ts: job.updatedAt, message: "Worker picked up job", phase: "worker" },
    ];
    await persistJob(job, job.events);
    emitJobLog(runId, "Worker picked up job", "worker");
  });

  const job = await readJob(runId);
  if (!job) throw new Error(`Unknown job: ${runId}`);
  if (job.status === "cancelled") {
    unregisterJobAbort(runId);
    return job;
  }

  try {
    const result = await digestDocument(job.payload, {
      signal,
      onProgress: async (message, phase, extra) => {
        await patchJob(runId, {
          message,
          phase,
          plan: extra?.plan,
          pageProgress: extra?.pageProgress,
        });
      },
    });

    return await withJobLock(runId, async () => {
      const latest = (await readJob(runId)) ?? job;
      if (latest.status === "cancelled" || signal.aborted) {
        const cancelled = finalizeCancelledJob(latest);
        await persistJob(cancelled, cancelled.events ?? []);
        return cancelled;
      }
      latest.status = result.status === "completed" ? "completed" : "failed";
      latest.result = result;
      latest.error = result.error;
      latest.plan = result.plan ?? latest.plan;
      latest.updatedAt = new Date().toISOString();
      latest.events = [
        ...(latest.events ?? []),
        {
          ts: latest.updatedAt,
          message:
            latest.status === "completed"
              ? "Digest completed"
              : `Digest failed: ${result.error ?? "unknown error"}`,
          phase: latest.status === "completed" ? "done" : "error",
        },
      ];
      await persistJob(latest, latest.events);
      emitJobLog(
        runId,
        latest.events[latest.events.length - 1]!.message,
        latest.events[latest.events.length - 1]!.phase,
      );
      return latest;
    });
  } catch (err) {
    if (err instanceof DigestCancelledError || signal.aborted) {
      return await withJobLock(runId, async () => {
        const latest = (await readJob(runId)) ?? job;
        const cancelled = finalizeCancelledJob(latest);
        await persistJob(cancelled, cancelled.events ?? []);
        return cancelled;
      });
    }
    return await withJobLock(runId, async () => {
      const latest = (await readJob(runId)) ?? job;
      latest.status = "failed";
      latest.error = err instanceof Error ? err.message : String(err);
      latest.updatedAt = new Date().toISOString();
      latest.events = [
        ...(latest.events ?? []),
        {
          ts: latest.updatedAt,
          message: `Digest failed: ${latest.error}`,
          phase: "error",
        },
      ];
      await persistJob(latest, latest.events);
      return latest;
    });
  } finally {
    unregisterJobAbort(runId);
  }
}

/** Queue and immediately process (used by CLI --wait). */
export async function digestAndWait(payload: DigestPayload): Promise<JobStatus> {
  const job = await enqueueDigest(payload);
  return runDigestJob(job.runId);
}

/** Enqueue and process in the background; returns as soon as the job is queued. */
export async function digestAsync(payload: DigestPayload): Promise<JobStatus> {
  const job = await enqueueDigest(payload);
  void runDigestJob(job.runId).catch(async (err) => {
    await withJobLock(job.runId, async () => {
      const latest = await readJob(job.runId);
      if (
        !latest ||
        latest.status === "completed" ||
        latest.status === "failed" ||
        latest.status === "cancelled"
      ) {
        return;
      }
      latest.status = "failed";
      latest.error = err instanceof Error ? err.message : String(err);
      latest.updatedAt = new Date().toISOString();
      await persistJob(latest);
    });
  });
  return job;
}

export type RerunDigestPatch = Partial<
  Pick<DigestPayload, "model" | "format" | "stitchPrompt" | "stitchPreset" | "pdfText">
>;

export type RerunDigestOptions = RerunDigestPatch & {
  /** When true, delete saved page digests and restart from scratch. */
  fresh?: boolean;
};

/**
 * Re-run a job under the same runId. By default resumes saved page digests;
 * pass `fresh: true` to re-digest every page. Optional payload patches (e.g.
 * model) apply to the new attempt.
 */
export async function rerunDigestJob(
  runId: string,
  opts: RerunDigestOptions = {},
): Promise<JobStatus> {
  const { fresh, ...patch } = opts;

  const prepared = await withJobLock(runId, async () => {
    const job = await readJob(runId);
    if (!job) throw new Error(`Unknown job: ${runId}`);
    if (job.status === "queued" || job.status === "running") {
      throw new Error("Job is already in progress");
    }

    job.payload = {
      ...job.payload,
      ...(patch.model !== undefined ? { model: patch.model } : {}),
      ...(patch.format !== undefined ? { format: patch.format } : {}),
      ...(patch.stitchPrompt !== undefined ? { stitchPrompt: patch.stitchPrompt } : {}),
      ...(patch.stitchPreset !== undefined ? { stitchPreset: patch.stitchPreset } : {}),
      ...(patch.pdfText !== undefined ? { pdfText: patch.pdfText } : {}),
    };

    if (fresh) {
      await clearPageDigestsForRerun(runId);
    }

    const now = new Date().toISOString();
    job.status = "queued";
    job.error = undefined;
    job.result = undefined;
    job.cancelRequestedAt = undefined;
    if (fresh) job.pageProgress = undefined;
    job.updatedAt = now;
    const patchBits = [
      patch.model ? `model=${patch.model}` : "",
      patch.format ? `format=${patch.format}` : "",
      fresh ? "fresh" : "resume",
    ]
      .filter(Boolean)
      .join(", ");
    job.events = [
      ...(job.events ?? []),
      {
        ts: now,
        message: patchBits
          ? `Rerun requested (${patchBits})`
          : "Rerun requested — resuming saved page digests where possible",
        phase: "queue",
      },
    ];
    await persistJob(job, job.events);
    return job;
  });

  void runDigestJob(prepared.runId).catch(async (err) => {
    await withJobLock(prepared.runId, async () => {
      const latest = await readJob(prepared.runId);
      if (
        !latest ||
        latest.status === "completed" ||
        latest.status === "failed" ||
        latest.status === "cancelled"
      ) {
        return;
      }
      latest.status = "failed";
      latest.error = err instanceof Error ? err.message : String(err);
      latest.updatedAt = new Date().toISOString();
      await persistJob(latest);
    });
  });

  return prepared;
}

/** @deprecated Use rerunDigestJob */
export async function retryDigestJob(runId: string): Promise<JobStatus> {
  return rerunDigestJob(runId);
}

/** Stop a queued or running digest. Completed pages on disk are kept. */
export async function cancelDigestJob(runId: string): Promise<JobStatus> {
  return withJobLock(runId, async () => {
    const job = await readJob(runId);
    if (!job) throw new Error(`Unknown job: ${runId}`);
    if (job.status === "completed" || job.status === "cancelled") {
      throw new Error("Job is already finished");
    }
    if (job.status === "failed") {
      throw new Error("Job is not running");
    }

    const now = new Date().toISOString();
    const alreadyRequested = jobCancelWasRequested(job);
    job.cancelRequestedAt = job.cancelRequestedAt ?? now;

    if (job.status === "queued") {
      const cancelled = finalizeCancelledJob(job);
      await persistJob(cancelled, cancelled.events ?? []);
      return cancelled;
    }

    const aborted = abortJobRun(runId);
    if (!aborted || alreadyRequested) {
      const cancelled = finalizeCancelledJob(job);
      await persistJob(cancelled, cancelled.events ?? []);
      return cancelled;
    }

    job.events = [
      ...(job.events ?? []),
      {
        ts: now,
        message: "Cancel requested — stopping after current page work",
        phase: "cancel",
      },
    ];
    job.updatedAt = now;
    await persistJob(job, job.events);
    return job;
  });
}

export async function listJobs(limit = 50): Promise<JobStatus[]> {
  await reconcileStaleJobs();
  const client = await ensureDb();
  return listJobRows(client, limit);
}
