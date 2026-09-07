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
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
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

export type JobFilter = "all" | "ongoing" | "failed" | "completed";

export type JobCounts = {
  all: number;
  ongoing: number;
  failed: number;
  completed: number;
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function filterJobs(jobs: JobListItem[], filter: JobFilter): JobListItem[] {
  if (filter === "ongoing") {
    return jobs.filter((j) => j.status === "queued" || j.status === "running");
  }
  if (filter === "failed") {
    return jobs.filter((j) => j.status === "failed" || j.status === "cancelled");
  }
  if (filter === "completed") return jobs.filter((j) => j.status === "completed");
  return jobs;
}

/** Turn raw worker errors into operator-facing recovery copy. */
export function explainJobError(error?: string | null): {
  title: string;
  detail: string;
  hint?: string;
} {
  const detail = (error ?? "").trim() || "Something went wrong during digestion.";
  if (/cancelled/i.test(detail)) {
    return {
      title: "Digest cancelled",
      detail,
      hint: "Completed pages are saved on disk. Rerun to resume, or rerun with fresh=true to start over.",
    };
  }
  if (/429|rate.?limit|too many requests/i.test(detail)) {
    return {
      title: "Rate limited by the LLM provider",
      detail,
      hint: "The provider returned HTTP 429. Verdant backs off exponentially and retries; if the job still fails, wait a minute then Rerun — completed pages resume from disk.",
    };
  }
  if (/api.?key|credentials|unauthorized|401|403/i.test(detail)) {
    return {
      title: "LLM credentials rejected",
      detail,
      hint: "Open Settings, test the active provider, then Retry.",
    };
  }
  if (/timed? ?out|ECONNRESET|fetch failed|network/i.test(detail)) {
    return {
      title: "Network or timeout while calling the LLM",
      detail,
      hint: "Check connectivity and Retry. Saved page digests will be reused.",
    };
  }
  if (/\d+ page\(s\) failed/i.test(detail)) {
    return {
      title: "Some pages failed to digest",
      detail,
      hint: "Retry resumes successful pages and re-attempts only the failures.",
    };
  }
  return { title: "Digest failed", detail };
}
