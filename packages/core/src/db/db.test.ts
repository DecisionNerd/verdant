import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { after, before, describe, it } from "node:test";
import {
  ensureDb,
  resetDbForTests,
} from "./client.js";
import {
  enqueueDigest,
  listJobs,
  readJob,
  writeJob,
} from "../jobs.js";
import {
  hydrateConfigCache,
  readLlmSettings,
  resetConfigCacheForTests,
  writeVerdantConfig,
} from "../config.js";
import { readRunReview, writeRunReview } from "../review.js";
import { emptyRunReview } from "../ir/structureTypes.js";

describe("turso database-as-state", () => {
  let tmpRoot = "";

  before(async () => {
    tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "verdant-db-"));
    process.env.DATA_DIR = tmpRoot;
    process.env.TURSO_DATABASE_URL = `file:${path.join(tmpRoot, "turso", "verdant.db")}`;
    resetDbForTests();
    resetConfigCacheForTests();
    await ensureDb();
    await hydrateConfigCache();
  });

  after(async () => {
    resetDbForTests();
    resetConfigCacheForTests();
    delete process.env.TURSO_DATABASE_URL;
    if (tmpRoot) {
      await fs.rm(tmpRoot, { recursive: true, force: true });
    }
  });

  it("persists jobs and events", async () => {
    const job = await enqueueDigest({
      inputPath: path.join(tmpRoot, "inputs", "x.png"),
      format: "markdown",
    });
    assert.equal(job.status, "queued");
    const loaded = await readJob(job.runId);
    assert.ok(loaded);
    assert.equal(loaded!.status, "queued");
    assert.ok((loaded!.events ?? []).some((e) => e.phase === "queue"));

    loaded!.status = "running";
    loaded!.updatedAt = new Date().toISOString();
    await writeJob(loaded!);
    const again = await readJob(job.runId);
    assert.equal(again?.status, "running");

    const listed = await listJobs(10);
    assert.ok(listed.some((j) => j.runId === job.runId));
  });

  it("persists settings", async () => {
    await writeVerdantConfig({
      llm: {
        activeProvider: "gemini",
        providers: {
          gemini: { apiKey: "test-key-12345678", model: "gemini-2.5-flash" },
        },
      },
    });
    resetConfigCacheForTests();
    await hydrateConfigCache();
    const settings = readLlmSettings();
    assert.equal(settings.activeProvider, "gemini");
    assert.equal(settings.providers.gemini?.apiKey, "test-key-12345678");
  });

  it("persists reviews", async () => {
    const runId = "review-test-1";
    const review = emptyRunReview();
    review.document.tags = ["needs_work"];
    await writeRunReview(runId, review);
    const loaded = await readRunReview(runId);
    assert.deepEqual(loaded.document.tags, ["needs_work"]);
  });
});
