export type PipelineEvent = {
  phase?: string;
  message?: string;
};

export type PipelinePageProgress = {
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  phase?: string;
};

export type PipelinePlan = {
  pageCount: number;
  digestPages: number;
  strategy: "single" | "multipage";
};

export type PipelineStageState = "completed" | "current" | "pending" | "failed";

export type PipelineStageView = {
  id: string;
  label: string;
  state: PipelineStageState;
  /** Short count shown under the dot, e.g. 2/2 or 27/622 */
  progressLabel: string;
  /** Full hover text */
  tooltip: string;
};

type StageDef = {
  id: string;
  label: string;
  phases: string[];
  /** Countable substeps within this stage (from event phases). */
  substeps?: string[];
};

const STAGES: StageDef[] = [
  {
    id: "prepare",
    label: "Prepare",
    phases: ["upload", "queue", "worker", "start", "diagnose", "normalize"],
    substeps: ["diagnose", "normalize"],
  },
  {
    id: "plan",
    label: "Plan",
    phases: ["plan", "provider", "toc"],
    substeps: ["plan", "provider", "toc"],
  },
  {
    id: "extract",
    label: "Extract",
    phases: ["llm"],
  },
  {
    id: "validate",
    label: "Validate",
    phases: ["validate"],
    substeps: ["validate"],
  },
  {
    id: "assemble",
    label: "Assemble",
    phases: ["structure", "stitch"],
    substeps: ["structure", "stitch"],
  },
  {
    id: "write",
    label: "Write",
    phases: ["write"],
    substeps: ["write"],
  },
  {
    id: "done",
    label: "Done",
    phases: ["done"],
    substeps: ["done"],
  },
];

function phaseToStageIndex(phase: string | undefined): number {
  if (!phase) return 0;
  if (phase === "cancel" || phase === "cancelled" || phase === "error") {
    return -1;
  }
  const idx = STAGES.findIndex((s) => s.phases.includes(phase));
  return idx >= 0 ? idx : 0;
}

function substepProgress(
  substeps: string[],
  events: PipelineEvent[],
  stageComplete: boolean,
): { done: number; total: number } {
  const total = substeps.length;
  if (stageComplete) return { done: total, total };
  const seen = new Set<string>();
  for (const ev of events) {
    if (ev.phase && substeps.includes(ev.phase)) seen.add(ev.phase);
  }
  return { done: seen.size, total };
}

function extractTotals(
  pageProgress: PipelinePageProgress | null | undefined,
  plan: PipelinePlan | null | undefined,
): number {
  if (pageProgress?.total && pageProgress.total > 0) return pageProgress.total;
  if (plan?.digestPages && plan.digestPages > 0) return plan.digestPages;
  if (plan?.pageCount && plan.pageCount > 0) return plan.pageCount;
  return 0;
}

function extractSummary(
  pageProgress: PipelinePageProgress | null | undefined,
  total: number,
): string {
  if (!pageProgress || total <= 0) return `0 / ${total || "?"}`;
  return `${pageProgress.completed} done · ${pageProgress.failed} failed · ${pageProgress.skipped} skipped / ${total}`;
}

export function canReviewExtractedPages(opts: {
  pageProgress?: PipelinePageProgress | null;
  pageMdCount?: number;
}): boolean {
  if (opts.pageMdCount != null && opts.pageMdCount > 0) return true;
  const pp = opts.pageProgress;
  if (!pp) return false;
  if (pp.completed > 0) return true;
  return false;
}

export function derivePipelineStages(input: {
  events?: PipelineEvent[];
  pageProgress?: PipelinePageProgress | null;
  plan?: PipelinePlan | null;
  status?: string;
}): PipelineStageView[] {
  const events = input.events ?? [];
  const phase =
    input.pageProgress?.phase ?? events[events.length - 1]?.phase ?? undefined;
  const status = input.status ?? "running";
  const isDone = status === "completed";
  const isFailed = status === "failed" || status === "cancelled";

  let currentIdx = isDone ? STAGES.length - 1 : phaseToStageIndex(phase);
  if (currentIdx < 0) {
    // cancel/error — infer from last non-terminal phase
    for (let i = events.length - 1; i >= 0; i--) {
      const p = events[i]?.phase;
      if (p && p !== "cancel" && p !== "cancelled" && p !== "error") {
        currentIdx = phaseToStageIndex(p);
        break;
      }
    }
    if (currentIdx < 0) currentIdx = 0;
  }

  const extractTotal = extractTotals(input.pageProgress, input.plan);

  return STAGES.map((stage, idx) => {
    let state: PipelineStageState;
    if (isDone) {
      state = "completed";
    } else if (isFailed) {
      if (idx < currentIdx) state = "completed";
      else if (idx === currentIdx) state = "failed";
      else state = "pending";
    } else if (idx < currentIdx) {
      state = "completed";
    } else if (idx === currentIdx) {
      state = "current";
    } else {
      state = "pending";
    }

    let progressLabel: string;
    let tooltip: string;

    if (stage.id === "extract") {
      const total = extractTotal;
      if (state === "completed" || isDone) {
        progressLabel = total > 0 ? `${total}/${total}` : "done";
        tooltip =
          total > 0
            ? `${stage.label} — ${total} pages extracted`
            : `${stage.label} — complete`;
      } else if (state === "current") {
        progressLabel =
          total > 0
            ? `${input.pageProgress?.completed ?? 0}/${total}`
            : "…";
        tooltip = `${stage.label} — ${extractSummary(input.pageProgress, total)}`;
      } else {
        progressLabel = total > 0 ? `0/${total}` : "0";
        tooltip =
          total > 0
            ? `${stage.label} — 0 / ${total} pages`
            : `${stage.label} — not started`;
      }
    } else if (stage.substeps) {
      const substeps =
        stage.id === "plan" && input.plan?.strategy === "single"
          ? stage.substeps.filter((p) => p !== "toc")
          : stage.substeps;
      const { done, total } = substepProgress(
        substeps,
        events,
        state === "completed",
      );
      progressLabel = `${done}/${total}`;
      if (state === "completed") {
        tooltip = `${stage.label} — ${total}/${total} steps complete`;
      } else if (state === "current") {
        const last = events.filter((e) => e.phase && substeps.includes(e.phase)).at(-1);
        tooltip = last?.message
          ? `${stage.label} — ${done}/${total} steps · ${last.message}`
          : `${stage.label} — ${done}/${total} steps`;
      } else if (state === "failed") {
        tooltip = `${stage.label} — stopped at ${done}/${total} steps`;
      } else {
        tooltip = `${stage.label} — 0/${total} steps`;
      }
    } else {
      progressLabel = state === "completed" ? "1/1" : state === "current" ? "…" : "0/1";
      tooltip =
        state === "completed"
          ? `${stage.label} — complete`
          : state === "current"
            ? `${stage.label} — in progress`
            : `${stage.label} — not started`;
    }

    return {
      id: stage.id,
      label: stage.label,
      state,
      progressLabel,
      tooltip,
    };
  });
}
