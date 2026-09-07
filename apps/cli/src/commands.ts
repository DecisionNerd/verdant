import fs from "node:fs/promises";
import path from "node:path";
import {
  cancelDigestJob,
  digestAndWait,
  enqueueDigest,
  fixturesToLangfuseItems,
  listJobs,
  loadFixtures,
  outputsDir,
  readJob,
  rerunDigestJob,
  resolveDataPath,
  runDigestJob,
  runLocalEval,
  toHostRelative,
  type DigestFormat,
  type RerunDigestOptions,
} from "@verdant/core";

export async function cmdDigest(opts: {
  input: string;
  format?: DigestFormat;
  wait?: boolean;
  model?: string;
}): Promise<object> {
  const payload = {
    inputPath: opts.input,
    format: opts.format ?? ("markdown" as DigestFormat),
    model: opts.model,
  };

  if (opts.wait !== false) {
    const job = await digestAndWait(payload);
    return {
      runId: job.runId,
      status: job.status,
      error: job.error,
      ...job.result?.hostPaths,
      pageCount: job.result?.pageCount,
      model: job.result?.model,
    };
  }

  const job = await enqueueDigest(payload);
  return { runId: job.runId, status: job.status };
}

export async function cmdStatus(runId: string): Promise<object> {
  const job = await readJob(runId);
  if (!job) return { error: `Unknown run: ${runId}` };
  return {
    runId: job.runId,
    status: job.status,
    error: job.error,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    payload: job.payload,
    events: job.events ?? [],
    plan: job.plan,
    pageProgress: job.pageProgress,
    result: job.result
      ? {
          ...job.result.hostPaths,
          pageCount: job.result.pageCount,
          model: job.result.model,
        }
      : undefined,
  };
}

export async function cmdCancelDigest(runId: string): Promise<object> {
  const job = await cancelDigestJob(runId);
  return {
    runId: job.runId,
    status: job.status,
    error: job.error,
    events: job.events ?? [],
  };
}

export async function cmdRerunDigest(
  runId: string,
  opts: RerunDigestOptions & { wait?: boolean } = {},
): Promise<object> {
  const { wait, ...patch } = opts;
  const job = await rerunDigestJob(runId, patch);
  if (wait === false) {
    return {
      runId: job.runId,
      status: job.status,
      payload: job.payload,
      events: job.events ?? [],
    };
  }

  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const latest = await readJob(runId);
    if (!latest) return { error: `Unknown run: ${runId}` };
    if (latest.status !== "queued" && latest.status !== "running") {
      return {
        runId: latest.runId,
        status: latest.status,
        error: latest.error,
        payload: latest.payload,
        events: latest.events ?? [],
        ...latest.result?.hostPaths,
        pageCount: latest.result?.pageCount,
        model: latest.result?.model,
      };
    }
  }
}

export async function cmdListOutputs(runId?: string): Promise<object> {
  if (runId) {
    const job = await readJob(runId);
    if (!job?.result) return { error: `No completed output for ${runId}` };
    return job.result.hostPaths;
  }

  const root = outputsDir();
  let entries: string[] = [];
  try {
    entries = await fs.readdir(root);
  } catch {
    return { outputs: [] };
  }

  const outputs = [];
  for (const id of entries) {
    const dir = path.join(root, id);
    const stat = await fs.stat(dir).catch(() => null);
    if (!stat?.isDirectory()) continue;
    const files = await fs.readdir(dir);
    outputs.push({
      runId: id,
      runDir: toHostRelative(dir),
      files: files.filter((f) => !f.startsWith(".")),
    });
  }
  return { outputs };
}

export async function cmdReadOutput(opts: {
  runId: string;
  file?: "markdown" | "html";
}): Promise<object> {
  const job = await readJob(opts.runId);
  const hostPath =
    opts.file === "html"
      ? job?.result?.hostPaths.html
      : job?.result?.hostPaths.markdown ?? job?.result?.hostPaths.html;

  if (!hostPath) {
    // Fall back to filesystem
    const runDir = path.join(outputsDir(), opts.runId);
    const md = path.join(runDir, "document.md");
    const html = path.join(runDir, "document.html");
    const preferHtml = opts.file === "html";
    const candidate = preferHtml ? html : md;
    const fallback = preferHtml ? md : html;
    try {
      const content = await fs.readFile(candidate, "utf8");
      return { path: toHostRelative(candidate), content };
    } catch {
      try {
        const content = await fs.readFile(fallback, "utf8");
        return { path: toHostRelative(fallback), content };
      } catch {
        return { error: `No output found for run ${opts.runId}` };
      }
    }
  }

  const content = await fs.readFile(resolveDataPath(hostPath), "utf8");
  return { path: hostPath, content };
}

export async function cmdSyncDataset(opts?: {
  datasetDir?: string;
  name?: string;
}): Promise<object> {
  const name = opts?.name ?? "verdant-truth";
  const items = await fixturesToLangfuseItems(opts?.datasetDir);
  const baseUrl = (
    process.env.LANGFUSE_BASE_URL_INTERNAL ||
    process.env.LANGFUSE_BASE_URL ||
    "http://localhost:18703"
  ).replace(/\/$/, "");
  const publicKey = process.env.LANGFUSE_PUBLIC_KEY;
  const secretKey = process.env.LANGFUSE_SECRET_KEY;

  if (!publicKey || !secretKey) {
    // Offline sync: write a manifest for inspection
    const fixtures = await loadFixtures(opts?.datasetDir);
    const manifestPath = path.join(
      resolveDataPath(opts?.datasetDir ?? "data/datasets"),
      "_langfuse-sync.json",
    );
    await fs.writeFile(
      manifestPath,
      JSON.stringify({ dataset: name, items, fixtureCount: fixtures.length }, null, 2),
      "utf8",
    );
    return {
      mode: "offline-manifest",
      dataset: name,
      itemCount: items.length,
      manifest: toHostRelative(manifestPath),
      note: "LANGFUSE keys missing — wrote local manifest only",
    };
  }

  const auth = Buffer.from(`${publicKey}:${secretKey}`).toString("base64");

  // Ensure dataset exists
  await fetch(`${baseUrl}/api/public/datasets`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${auth}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name, description: "Verdant truth fixtures" }),
  }).catch(() => null);

  let upserted = 0;
  for (const item of items) {
    const res = await fetch(`${baseUrl}/api/public/dataset-items`, {
      method: "POST",
      headers: {
        Authorization: `Basic ${auth}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        datasetName: name,
        input: item.input,
        expectedOutput: item.expectedOutput,
        metadata: item.metadata,
      }),
    });
    if (res.ok) upserted += 1;
  }

  return { mode: "langfuse", dataset: name, upserted, itemCount: items.length };
}

export async function cmdEval(opts?: {
  dataset?: string;
  datasetDir?: string;
  name?: string;
  model?: string;
}): Promise<object> {
  // Prefer local eval (works offline); also try to annotate Langfuse when configured
  const local = await runLocalEval({
    datasetDir: opts?.datasetDir,
    model: opts?.model,
  });

  return {
    experiment: opts?.name ?? `verdant-eval-${new Date().toISOString()}`,
    ...local,
    dataset: opts?.dataset ?? local.dataset,
  };
}

export async function cmdProcessQueueOnce(): Promise<object> {
  const jobs = await listJobs(100);
  const next = jobs.find((j) => j.status === "queued");
  if (!next) return { processed: false };
  const result = await runDigestJob(next.runId);
  return { processed: true, runId: result.runId, status: result.status };
}
