import type { Client, Row } from "@libsql/client";
import type {
  DigestPlan,
  JobEvent,
  JobStatus,
  PageProgress,
} from "../types.js";

function parseJson<T>(raw: unknown, fallback: T): T {
  if (raw == null || raw === "") return fallback;
  try {
    return JSON.parse(String(raw)) as T;
  } catch {
    return fallback;
  }
}

export function rowToJob(row: Row, events: JobEvent[] = []): JobStatus {
  return {
    runId: String(row.run_id),
    status: row.status as JobStatus["status"],
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
    payload: parseJson(row.payload_json, {} as JobStatus["payload"]),
    result: row.result_json
      ? parseJson(row.result_json, undefined)
      : undefined,
    error: row.error != null ? String(row.error) : undefined,
    cancelRequestedAt:
      row.cancel_requested_at != null
        ? String(row.cancel_requested_at)
        : undefined,
    plan: row.plan_json ? parseJson<DigestPlan | undefined>(row.plan_json, undefined) : undefined,
    pageProgress: row.page_progress_json
      ? parseJson<PageProgress | undefined>(row.page_progress_json, undefined)
      : undefined,
    events,
  };
}

export async function loadJobEvents(
  client: Client,
  runId: string,
): Promise<JobEvent[]> {
  const rs = await client.execute({
    sql: "SELECT ts, message, phase FROM job_events WHERE run_id = ? ORDER BY id ASC",
    args: [runId],
  });
  return rs.rows.map((r) => ({
    ts: String(r.ts),
    message: String(r.message),
    ...(r.phase != null && r.phase !== "" ? { phase: String(r.phase) } : {}),
  }));
}

export async function upsertJobRow(client: Client, job: JobStatus): Promise<void> {
  await client.execute({
    sql: `INSERT INTO jobs (
      run_id, status, created_at, updated_at, payload_json, result_json,
      error, cancel_requested_at, plan_json, page_progress_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id) DO UPDATE SET
      status = excluded.status,
      updated_at = excluded.updated_at,
      payload_json = excluded.payload_json,
      result_json = excluded.result_json,
      error = excluded.error,
      cancel_requested_at = excluded.cancel_requested_at,
      plan_json = excluded.plan_json,
      page_progress_json = excluded.page_progress_json`,
    args: [
      job.runId,
      job.status,
      job.createdAt,
      job.updatedAt,
      JSON.stringify(job.payload),
      job.result ? JSON.stringify(job.result) : null,
      job.error ?? null,
      job.cancelRequestedAt ?? null,
      job.plan ? JSON.stringify(job.plan) : null,
      job.pageProgress ? JSON.stringify(job.pageProgress) : null,
    ],
  });
}

export async function replaceJobEvents(
  client: Client,
  runId: string,
  events: JobEvent[],
): Promise<void> {
  await client.execute({
    sql: "DELETE FROM job_events WHERE run_id = ?",
    args: [runId],
  });
  for (const ev of events) {
    await client.execute({
      sql: "INSERT INTO job_events (run_id, ts, message, phase) VALUES (?, ?, ?, ?)",
      args: [runId, ev.ts, ev.message, ev.phase ?? null],
    });
  }
}

export async function appendJobEvent(
  client: Client,
  runId: string,
  event: JobEvent,
): Promise<void> {
  await client.execute({
    sql: "INSERT INTO job_events (run_id, ts, message, phase) VALUES (?, ?, ?, ?)",
    args: [runId, event.ts, event.message, event.phase ?? null],
  });
}

export async function readJobRow(
  client: Client,
  runId: string,
): Promise<JobStatus | null> {
  const rs = await client.execute({
    sql: "SELECT * FROM jobs WHERE run_id = ?",
    args: [runId],
  });
  const row = rs.rows[0];
  if (!row) return null;
  const events = await loadJobEvents(client, runId);
  return rowToJob(row, events);
}

export async function listJobRows(
  client: Client,
  limit: number,
): Promise<JobStatus[]> {
  const rs = await client.execute({
    sql: "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
    args: [limit],
  });
  const jobs: JobStatus[] = [];
  for (const row of rs.rows) {
    const runId = String(row.run_id);
    const events = await loadJobEvents(client, runId);
    jobs.push(rowToJob(row, events));
  }
  return jobs;
}

export async function listRunningJobIds(client: Client): Promise<string[]> {
  const rs = await client.execute(
    "SELECT run_id FROM jobs WHERE status = 'running'",
  );
  return rs.rows.map((r) => String(r.run_id));
}
