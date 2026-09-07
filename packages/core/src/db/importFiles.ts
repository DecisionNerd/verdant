import fs from "node:fs/promises";
import path from "node:path";
import type { Client } from "@libsql/client";
import { dataRoot, jobsDir, outputsDir } from "../paths.js";
import type { JobStatus } from "../types.js";
import type { RunReview } from "../ir/structureTypes.js";
import { persistJobAtomic } from "./jobsRepo.js";

function legacyConfigPath(): string {
  return path.join(dataRoot(), "config.json");
}

const MARKER = ".db-migrated";

function markerPath(): string {
  return path.join(jobsDir(), MARKER);
}

async function alreadyMigrated(): Promise<boolean> {
  try {
    await fs.access(markerPath());
    return true;
  } catch {
    return false;
  }
}

async function markMigrated(): Promise<void> {
  await fs.mkdir(jobsDir(), { recursive: true });
  await fs.writeFile(
    markerPath(),
    `${new Date().toISOString()}\n`,
    "utf8",
  );
}

async function importJobs(client: Client): Promise<number> {
  let names: string[];
  try {
    names = (await fs.readdir(jobsDir())).filter(
      (f) => f.endsWith(".json") && !f.endsWith(".migrated"),
    );
  } catch {
    return 0;
  }

  let count = 0;
  for (const file of names) {
    try {
      const raw = await fs.readFile(path.join(jobsDir(), file), "utf8");
      const job = JSON.parse(raw) as JobStatus;
      if (!job?.runId) continue;
      await persistJobAtomic(client, job, job.events ?? []);
      await fs.rename(
        path.join(jobsDir(), file),
        path.join(jobsDir(), `${file}.migrated`),
      );
      count += 1;
    } catch (err) {
      console.warn(
        `[db] skip job file ${file}:`,
        err instanceof Error ? err.message : err,
      );
    }
  }
  return count;
}

async function importSettings(client: Client): Promise<boolean> {
  try {
    const raw = await fs.readFile(legacyConfigPath(), "utf8");
    const config = JSON.parse(raw) as unknown;
    const now = new Date().toISOString();
    await client.execute({
      sql: `INSERT INTO settings (id, config_json, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at`,
      args: [JSON.stringify(config), now],
    });
    await fs.rename(legacyConfigPath(), `${legacyConfigPath()}.migrated`);
    return true;
  } catch {
    return false;
  }
}

async function importReviews(client: Client): Promise<number> {
  let runDirs: string[];
  try {
    runDirs = await fs.readdir(outputsDir());
  } catch {
    return 0;
  }

  let count = 0;
  for (const runId of runDirs) {
    const reviewFile = path.join(outputsDir(), runId, "review.json");
    try {
      const raw = await fs.readFile(reviewFile, "utf8");
      const review = JSON.parse(raw) as RunReview;
      if (review?.v !== 1) continue;
      const now = review.updatedAt ?? new Date().toISOString();
      await client.execute({
        sql: `INSERT INTO reviews (run_id, review_json, updated_at) VALUES (?, ?, ?)
              ON CONFLICT(run_id) DO UPDATE SET review_json = excluded.review_json, updated_at = excluded.updated_at`,
        args: [runId, JSON.stringify(review), now],
      });
      await fs.rename(reviewFile, `${reviewFile}.migrated`);
      count += 1;
    } catch {
      // missing or invalid — skip
    }
  }
  return count;
}

/**
 * One-shot import of legacy JSON job/config/review files into Turso.
 * Idempotent via `data/jobs/.db-migrated` marker.
 */
export async function importFileStateIfNeeded(client: Client): Promise<void> {
  if (await alreadyMigrated()) return;

  const jobs = await importJobs(client);
  const settings = await importSettings(client);
  const reviews = await importReviews(client);
  await markMigrated();

  if (jobs || settings || reviews) {
    console.log(
      `[db] imported legacy state: jobs=${jobs} settings=${settings ? 1 : 0} reviews=${reviews}`,
    );
  }
}
