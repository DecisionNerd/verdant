"use client";

import { useMemo } from "react";
import {
  derivePipelineStages,
  type PipelineEvent,
  type PipelinePageProgress,
  type PipelinePlan,
} from "../lib/pipelineSteps";

export function DigestPipelineSteps({
  events,
  pageProgress,
  plan,
  status,
  compact,
}: {
  events?: PipelineEvent[];
  pageProgress?: PipelinePageProgress | null;
  plan?: PipelinePlan | null;
  status?: string;
  compact?: boolean;
}) {
  const stages = useMemo(
    () =>
      derivePipelineStages({
        events,
        pageProgress,
        plan,
        status,
      }),
    [events, pageProgress, plan, status],
  );

  const live = status === "queued" || status === "running";

  return (
    <div
      className={`pipeline-steps${compact ? " compact" : ""}`}
      role="list"
      aria-label="Digest pipeline stages"
    >
      {stages.map((stage, i) => (
        <div key={stage.id} className="pipeline-step-wrap" role="listitem">
          {i > 0 ? <span className="pipeline-connector" aria-hidden /> : null}
          <div
            className={`pipeline-step ${stage.state}`}
            title={stage.tooltip}
          >
            <span className="pipeline-dot" aria-hidden>
              {stage.state === "current" && live ? (
                <span className="pipeline-spinner" />
              ) : null}
            </span>
            <span className="pipeline-label">{stage.label}</span>
            <span className="pipeline-count mono">{stage.progressLabel}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
