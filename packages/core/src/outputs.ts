import fs from "node:fs/promises";
import path from "node:path";
import { outputsDir, toHostRelative } from "./paths.js";
import type { JobStatus } from "./types.js";

export type RunOutputFile = {
  name: string;
  relativePath: string;
  kind: "markdown" | "html" | "plan" | "progress" | "page" | "artifact" | "other";
  bytes: number;
  chars?: number;
};

export type RunOutputsMeta = {
  runId: string;
  runDir: string;
  files: RunOutputFile[];
  hasMarkdown: boolean;
  hasHtml: boolean;
  pageMdCount: number;
  artifactCount: number;
  markdownChars?: number;
  markdownHeadings?: number;
  totalBytes: number;
};

export type JobListItem = {
  runId: string;
  status: JobStatus["status"];
  createdAt: string;
  updatedAt: string;
  inputPath: string;
  inputName: string;
  format: string;
  model?: string;
  pageCount?: number;
  error?: string;
  phase?: string;
  progressPct?: number;
  pagesDone?: number;
  pagesTotal?: number;
  markdown?: string;
  html?: string;
  runDir?: string;
  artifacts?: string;
  outputs?: RunOutputsMeta | null;
  lastEvent?: string;
};

function classifyFile(name: string, parent: string): RunOutputFile["kind"] {
  if (name === "document.md") return "markdown";
  if (name === "document.html") return "html";
  if (name === "plan.json") return "plan";
  if (name === "page-progress.json") return "progress";
  if (parent === "pages" && (name.endsWith(".md") || name.endsWith(".ir.json"))) {
    return "page";
  }
  if (parent === "artifacts") return "artifact";
  return "other";
}

function countHeadings(markdown: string): number {
  return (markdown.match(/^#{1,6}\s+\S+/gm) ?? []).length;
}

async function walkFiles(
  dir: string,
  runId: string,
  parent = "",
): Promise<RunOutputFile[]> {
  let entries: string[];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return [];
  }

  const out: RunOutputFile[] = [];
  for (const name of entries) {
    if (name.startsWith(".")) continue;
    const full = path.join(dir, name);
    const rel = parent ? `${parent}/${name}` : name;
    const stat = await fs.stat(full);
    if (stat.isDirectory()) {
      out.push(...(await walkFiles(full, runId, rel)));
      continue;
    }
    const kind = classifyFile(name, parent);
    const file: RunOutputFile = {
      name: rel,
      relativePath: toHostRelative(full),
      kind,
      bytes: stat.size,
    };
    if (kind === "markdown" || kind === "page" || kind === "html") {
      try {
        const text = await fs.readFile(full, "utf8");
        file.chars = text.length;
      } catch {
        // ignore
      }
    }
    out.push(file);
  }
  return out;
}

/** Inspect files under data/outputs/<runId> for UI metadata. */
export async function inspectRunOutputs(runId: string): Promise<RunOutputsMeta | null> {
  const runDir = path.join(outputsDir(), runId);
  try {
    await fs.access(runDir);
  } catch {
    return null;
  }

  const files = (await walkFiles(runDir, runId)).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  const md = files.find((f) => f.kind === "markdown");
  let markdownHeadings: number | undefined;
  if (md) {
    try {
      const text = await fs.readFile(path.join(runDir, "document.md"), "utf8");
      markdownHeadings = countHeadings(text);
    } catch {
      // ignore
    }
  }

  return {
    runId,
    runDir: toHostRelative(runDir),
    files,
    hasMarkdown: Boolean(md),
    hasHtml: files.some((f) => f.kind === "html"),
    pageMdCount: files.filter(
      (f) => f.kind === "page" && f.name.endsWith(".md"),
    ).length,
    artifactCount: files.filter((f) => f.kind === "artifact").length,
    markdownChars: md?.chars,
    markdownHeadings,
    totalBytes: files.reduce((n, f) => n + f.bytes, 0),
  };
}

function progressPct(job: JobStatus): number | undefined {
  const pp = job.pageProgress;
  if (!pp || pp.total <= 0) return undefined;
  const raw = Math.floor(((pp.completed + pp.skipped) / pp.total) * 100);
  // In-flight jobs never report 100% — reserve that for a completed status.
  if (job.status !== "completed") return Math.min(99, Math.max(0, raw));
  return Math.min(100, Math.max(0, raw));
}

export function toJobListItem(
  job: JobStatus,
  outputs?: RunOutputsMeta | null,
): JobListItem {
  const inputPath = job.payload.inputPath;
  const inputName = path.basename(inputPath);
  const events = job.events ?? [];
  const last = events[events.length - 1];

  return {
    runId: job.runId,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    inputPath,
    inputName,
    format: job.payload.format ?? "markdown",
    model: job.result?.model ?? job.payload.model,
    pageCount: job.result?.pageCount ?? job.plan?.pageCount ?? job.pageProgress?.total,
    error: job.error,
    phase: job.pageProgress?.phase ?? last?.phase,
    progressPct: progressPct(job),
    pagesDone:
      job.pageProgress != null
        ? job.pageProgress.completed + job.pageProgress.skipped
        : undefined,
    pagesTotal: job.pageProgress?.total,
    markdown: job.result?.hostPaths?.markdown,
    html: job.result?.hostPaths?.html,
    runDir: job.result?.hostPaths?.runDir ?? outputs?.runDir,
    artifacts: job.result?.hostPaths?.artifacts,
    outputs: outputs ?? null,
    lastEvent: last?.message,
  };
}

export async function listJobSummaries(limit = 50): Promise<JobListItem[]> {
  const { listJobs } = await import("./jobs.js");
  const jobs = await listJobs(Math.max(limit, 200));
  const sliced = jobs.slice(0, limit);
  const items: JobListItem[] = [];
  for (const job of sliced) {
    // Skip full output walks while running — keeps sidebar/status polls snappy on large PDFs.
    const outputs =
      job.status === "completed" || job.status === "failed" || job.status === "cancelled"
        ? await inspectRunOutputs(job.runId)
        : null;
    items.push(toJobListItem(job, outputs));
  }
  return items;
}
