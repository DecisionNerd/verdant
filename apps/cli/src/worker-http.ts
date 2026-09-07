import express from "express";
import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import {
  cancelDigestJob,
  digestAndWait,
  digestAsync,
  getLlmSettingsPublic,
  inputsDir,
  inspectRunOutputs,
  isProviderId,
  listJobSummaries,
  listProviderModels,
  patchPageReview,
  readJob,
  readRunReview,
  reassembleRun,
  reconcileStaleJobs,
  renderRunHtml,
  rerunDigestJob,
  resolveProviderConfig,
  testLlmConnection,
  toJobListItem,
  upsertProviderSettings,
  type DigestFormat,
  type ProviderId,
  type RestitchPreset,
  type RerunDigestOptions,
  ensureDb,
  hydrateConfigCache,
  initObservability,
  shutdownObservability,
} from "@verdant/core";
import { cmdProcessQueueOnce } from "./commands.js";

export async function startWorkerHttp(port = Number(process.env.WORKER_HTTP_PORT ?? 8791)) {
  await ensureDb();
  await hydrateConfigCache();
  initObservability();
  const app = express();
  // Base64 inflates ~4/3; 150mb allows ~110MB source files from the Web UI.
  const jsonLimit = process.env.WORKER_JSON_LIMIT ?? "150mb";
  app.use(express.json({ limit: jsonLimit }));

  app.get("/health", (_req, res) => res.json({ ok: true, service: "verdant-worker" }));

  app.post("/digest", async (req, res) => {
    try {
      const {
        inputPath,
        format,
        model,
        runId,
        fileBase64,
        fileName,
        wait,
        stitchPrompt,
        stitchPreset,
      } = req.body as {
        inputPath?: string;
        format?: DigestFormat;
        model?: string;
        runId?: string;
        fileBase64?: string;
        fileName?: string;
        /** When false, enqueue and return immediately; poll GET /status/:runId. */
        wait?: boolean;
        stitchPrompt?: string;
        stitchPreset?: RestitchPreset;
      };

      let resolvedInput = inputPath;
      if (fileBase64 && fileName) {
        await fs.mkdir(inputsDir(), { recursive: true });
        const safe = fileName.replace(/[^\w.\-]+/g, "_");
        const name = `${Date.now()}-${safe}`;
        const full = path.join(inputsDir(), name);
        await fs.writeFile(full, Buffer.from(fileBase64, "base64"));
        resolvedInput = `data/inputs/${name}`;
      }

      if (!resolvedInput) {
        res.status(400).json({ error: "inputPath or fileBase64 required" });
        return;
      }

      const payload = {
        inputPath: resolvedInput,
        format: format ?? ("markdown" as DigestFormat),
        model,
        runId: runId ?? randomUUID().slice(0, 12),
        stitchPrompt,
        stitchPreset,
      };

      if (wait === false) {
        const job = await digestAsync(payload);
        res.json({
          runId: job.runId,
          status: job.status,
          events: job.events ?? [],
        });
        return;
      }

      const job = await digestAndWait(payload);

      if (job.status === "failed") {
        res.status(500).json({
          error: job.error ?? "Digest failed",
          runId: job.runId,
          events: job.events ?? [],
        });
        return;
      }

      res.json({
        runId: job.runId,
        status: job.status,
        ...job.result?.hostPaths,
        pageCount: job.result?.pageCount,
        model: job.result?.model,
        events: job.events ?? [],
      });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.get("/status/:runId", async (req, res) => {
    await reconcileStaleJobs();
    const job = await readJob(req.params.runId);
    if (!job) {
      res.status(404).json({ error: "not found" });
      return;
    }
    // Avoid scanning artifacts on every poll while pages are still writing.
    const outputs =
      job.status === "completed" || job.status === "failed" || job.status === "cancelled"
        ? await inspectRunOutputs(job.runId)
        : null;
    const summary = toJobListItem(job, outputs);
    res.json({
      runId: job.runId,
      status: job.status,
      createdAt: job.createdAt,
      updatedAt: job.updatedAt,
      error: job.error,
      events: job.events ?? [],
      plan: job.plan,
      pageProgress: job.pageProgress,
      pageCount: summary.pageCount,
      model: summary.model,
      markdown: summary.markdown,
      html: summary.html,
      runDir: summary.runDir,
      artifacts: summary.artifacts,
      inputPath: summary.inputPath,
      inputName: summary.inputName,
      format: summary.format,
      progressPct: summary.progressPct,
      lastEvent: summary.lastEvent,
      outputs,
      job: summary,
    });
  });

  app.post("/jobs/:runId/retry", async (req, res) => {
    try {
      const body = (req.body ?? {}) as RerunDigestOptions;
      const job = await rerunDigestJob(req.params.runId, body);
      res.json({
        runId: job.runId,
        status: job.status,
        events: job.events ?? [],
        inputPath: job.payload.inputPath,
        inputName: path.basename(job.payload.inputPath),
        format: job.payload.format ?? "markdown",
        model: job.payload.model,
      });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.post("/jobs/:runId/rerun", async (req, res) => {
    try {
      const body = (req.body ?? {}) as RerunDigestOptions;
      const job = await rerunDigestJob(req.params.runId, body);
      res.json({
        runId: job.runId,
        status: job.status,
        events: job.events ?? [],
        inputPath: job.payload.inputPath,
        inputName: path.basename(job.payload.inputPath),
        format: job.payload.format ?? "markdown",
        model: job.payload.model,
        payload: job.payload,
      });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.post("/jobs/:runId/cancel", async (req, res) => {
    try {
      const job = await cancelDigestJob(req.params.runId);
      res.json({
        runId: job.runId,
        status: job.status,
        error: job.error,
        events: job.events ?? [],
      });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.get("/jobs", async (req, res) => {
    const limit = Math.min(200, Math.max(1, Number(req.query.limit ?? 50) || 50));
    const jobs = await listJobSummaries(limit);
    res.json({
      jobs,
      counts: {
        all: jobs.length,
        ongoing: jobs.filter((j) => j.status === "queued" || j.status === "running").length,
        failed: jobs.filter((j) => j.status === "failed" || j.status === "cancelled").length,
        completed: jobs.filter((j) => j.status === "completed").length,
        cancelled: jobs.filter((j) => j.status === "cancelled").length,
      },
    });
  });

  app.get("/runs/:runId", async (req, res) => {
    const outputs = await inspectRunOutputs(req.params.runId);
    if (!outputs) {
      res.status(404).json({ error: "run outputs not found" });
      return;
    }
    const job = await readJob(req.params.runId);
    res.json({
      outputs,
      job: job ? toJobListItem(job, outputs) : null,
    });
  });

  app.post("/runs/:runId/render-html", async (req, res) => {
    try {
      const result = await renderRunHtml(req.params.runId);
      const outputs = await inspectRunOutputs(req.params.runId);
      res.json({ ...result, outputs });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.post("/runs/:runId/reassemble", async (req, res) => {
    try {
      const body = (req.body ?? {}) as {
        preset?: RestitchPreset;
        prompt?: string;
        codeOnly?: boolean;
        writeHtml?: boolean;
      };
      const result = await reassembleRun(req.params.runId, body);
      res.json(result);
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.get("/runs/:runId/review", async (req, res) => {
    try {
      const review = await readRunReview(req.params.runId);
      res.json(review);
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.put("/runs/:runId/review", async (req, res) => {
    try {
      const body = (req.body ?? {}) as {
        page?: number | "document";
        figure?: string;
        tags?: string[];
        addComment?: string;
        removeCommentId?: string;
        updateComment?: { id: string; text: string };
        removeTag?: string;
        clearPage?: boolean;
        clearFigure?: boolean;
      };
      const page = body.page ?? "document";
      const review = await patchPageReview(req.params.runId, page, {
        figure: body.figure,
        tags: body.tags,
        addComment: body.addComment,
        removeCommentId: body.removeCommentId,
        updateComment: body.updateComment,
        removeTag: body.removeTag,
        clearPage: body.clearPage,
        clearFigure: body.clearFigure,
      });
      res.json(review);
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.get("/settings", (_req, res) => {
    res.json({ llm: getLlmSettingsPublic() });
  });

  app.put("/settings", async (req, res) => {
    try {
      const body = req.body as {
        llm?: {
          provider?: string;
          apiKey?: string;
          baseUrl?: string;
          model?: string;
          region?: string;
          secretAccessKey?: string;
          sessionToken?: string;
          clearApiKey?: boolean;
          clearSecretAccessKey?: boolean;
          clearSessionToken?: boolean;
          makeActive?: boolean;
        };
      };
      const llm = body.llm ?? {};
      const provider: ProviderId =
        llm.provider && isProviderId(llm.provider)
          ? llm.provider
          : getLlmSettingsPublic().activeProvider;

      await upsertProviderSettings(
        provider,
        {
          apiKey: llm.apiKey,
          baseUrl: llm.baseUrl,
          model: llm.model,
          region: llm.region,
          secretAccessKey: llm.secretAccessKey,
          sessionToken: llm.sessionToken,
          clearApiKey: llm.clearApiKey === true,
          clearSecretAccessKey: llm.clearSecretAccessKey === true,
          clearSessionToken: llm.clearSessionToken === true,
        },
        { makeActive: llm.makeActive !== false },
      );
      res.json({ ok: true, llm: getLlmSettingsPublic() });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.post("/settings/test", async (req, res) => {
    try {
      const body = req.body as {
        llm?: {
          provider?: string;
          apiKey?: string;
          baseUrl?: string;
          model?: string;
          region?: string;
          secretAccessKey?: string;
          sessionToken?: string;
          save?: boolean;
        };
      };
      const llm = body.llm ?? {};
      const provider: ProviderId =
        llm.provider && isProviderId(llm.provider)
          ? llm.provider
          : getLlmSettingsPublic().activeProvider;
      const apiKey =
        typeof llm.apiKey === "string" && llm.apiKey.trim() && !llm.apiKey.includes("…")
          ? llm.apiKey.trim()
          : undefined;
      const baseUrl =
        typeof llm.baseUrl === "string" && llm.baseUrl.trim() ? llm.baseUrl.trim() : undefined;
      const model =
        typeof llm.model === "string" && llm.model.trim() ? llm.model.trim() : undefined;
      const region =
        typeof llm.region === "string" && llm.region.trim() ? llm.region.trim() : undefined;
      const secretAccessKey =
        typeof llm.secretAccessKey === "string" &&
        llm.secretAccessKey.trim() &&
        !llm.secretAccessKey.includes("…")
          ? llm.secretAccessKey.trim()
          : undefined;
      const sessionToken =
        typeof llm.sessionToken === "string" &&
        llm.sessionToken.trim() &&
        !llm.sessionToken.includes("…")
          ? llm.sessionToken.trim()
          : undefined;

      const pub = getLlmSettingsPublic().providers.find((p) => p.id === provider);
      if (provider === "bedrock") {
        if (!pub?.configured && !(apiKey && secretAccessKey) && !region) {
          res.status(400).json({
            error:
              "Bedrock needs a region plus Access Key ID + Secret Access Key (or AWS env credentials).",
          });
          return;
        }
      } else if (!apiKey && !pub?.configured) {
        res.status(400).json({ error: "Paste an API key to test." });
        return;
      }

      const result = await testLlmConnection({
        provider,
        apiKey,
        baseUrl,
        model,
        region,
        secretAccessKey,
        sessionToken,
      });

      if (llm.save !== false) {
        await upsertProviderSettings(
          provider,
          { apiKey, baseUrl, model, region, secretAccessKey, sessionToken },
          { makeActive: true },
        );
      }

      let models: { id: string; name: string }[] = [];
      try {
        const cfg = resolveProviderConfig({
          provider,
          apiKey,
          baseUrl,
          model,
          region,
          secretAccessKey,
          sessionToken,
        });
        models = await listProviderModels({
          provider,
          apiKey: cfg.apiKey,
          baseUrl: cfg.baseUrl,
          region: cfg.region,
          secretAccessKey: cfg.secretAccessKey,
          sessionToken: cfg.sessionToken,
        });
      } catch {
        // test passed; model list is best-effort
      }

      res.json({
        ok: true,
        tested: result,
        saved: llm.save !== false,
        models,
        llm: getLlmSettingsPublic(),
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      res.status(400).json({ ok: false, error: message });
    }
  });

  app.get("/settings/models", async (req, res) => {
    try {
      const providerRaw = String(req.query.provider ?? "");
      const provider: ProviderId = isProviderId(providerRaw)
        ? providerRaw
        : getLlmSettingsPublic().activeProvider;
      const apiKeyOverride =
        typeof req.query.apiKey === "string" && req.query.apiKey && !req.query.apiKey.includes("…")
          ? req.query.apiKey
          : undefined;
      const baseUrlOverride =
        typeof req.query.baseUrl === "string" && req.query.baseUrl
          ? req.query.baseUrl
          : undefined;
      const regionOverride =
        typeof req.query.region === "string" && req.query.region
          ? req.query.region
          : undefined;
      const secretOverride =
        typeof req.query.secretAccessKey === "string" &&
        req.query.secretAccessKey &&
        !req.query.secretAccessKey.includes("…")
          ? req.query.secretAccessKey
          : undefined;
      const sessionOverride =
        typeof req.query.sessionToken === "string" &&
        req.query.sessionToken &&
        !req.query.sessionToken.includes("…")
          ? req.query.sessionToken
          : undefined;

      const pub = getLlmSettingsPublic().providers.find((p) => p.id === provider);
      const config = resolveProviderConfig({
        provider,
        apiKey: apiKeyOverride,
        baseUrl: baseUrlOverride,
        model: pub?.model,
        region: regionOverride,
        secretAccessKey: secretOverride,
        sessionToken: sessionOverride,
      });
      const models = await listProviderModels({
        provider,
        apiKey: config.apiKey,
        baseUrl: config.baseUrl,
        region: config.region,
        secretAccessKey: config.secretAccessKey,
        sessionToken: config.sessionToken,
      });
      res.json({ provider, models });
    } catch (err) {
      res.status(400).json({ error: err instanceof Error ? err.message : String(err) });
    }
  });

  app.use((err: unknown, _req: express.Request, res: express.Response, next: express.NextFunction) => {
    if (!err) {
      next();
      return;
    }
    const e = err as { type?: string; status?: number; statusCode?: number; message?: string; limit?: number };
    const tooLarge =
      e.type === "entity.too.large" ||
      e.status === 413 ||
      e.statusCode === 413 ||
      (typeof e.message === "string" && /entity too large/i.test(e.message));
    if (tooLarge) {
      res.status(413).json({
        error:
          "Upload is too large for the worker. Try a smaller file, or raise WORKER_JSON_LIMIT (default 150mb; base64 needs ~1.4× the file size).",
      });
      return;
    }
    console.error(err);
    res.status(500).json({ error: e.message ?? "Internal worker error" });
  });

  const reconciled = await reconcileStaleJobs();
  if (reconciled > 0) {
    console.log(`Reconciled ${reconciled} stale job(s) on startup`);
  }

  app.listen(port, "0.0.0.0", () => {
    console.log(`Verdant worker HTTP on http://0.0.0.0:${port}`);
  });

  const shutdown = () => {
    void shutdownObservability().finally(() => process.exit(0));
  };
  process.once("SIGTERM", shutdown);
  process.once("SIGINT", shutdown);

  // Also drain queue
  const interval = Number(process.env.WORKER_POLL_MS ?? 2000);
  setInterval(() => {
    void cmdProcessQueueOnce();
  }, interval);
}
