"use client";

import {
  filterJobs,
  formatWhen,
  type JobCounts,
  type JobFilter,
  type JobListItem,
} from "../lib/jobs";

export function JobsPanel({
  jobs,
  counts,
  filter,
  selectedRunId,
  onFilterChange,
  onSelect,
  onRefresh,
}: {
  jobs: JobListItem[];
  counts: JobCounts;
  filter: JobFilter;
  selectedRunId?: string | null;
  onFilterChange: (f: JobFilter) => void;
  onSelect: (job: JobListItem) => void;
  onRefresh: () => void;
}) {
  const visible = filterJobs(jobs, filter);

  const chips: { id: JobFilter; label: string; count: number }[] = [
    { id: "ongoing", label: "Ongoing", count: counts.ongoing },
    { id: "failed", label: "Failed", count: counts.failed },
    { id: "completed", label: "Done", count: counts.completed },
    { id: "all", label: "All", count: counts.all },
  ];

  return (
    <div className="sidebar-jobs">
      <div className="sidebar-jobs-head">
        <h2>Jobs</h2>
        <button type="button" className="secondary sidebar-icon-btn" onClick={onRefresh}>
          Refresh
        </button>
      </div>

      <div className="sidebar-filters" role="tablist" aria-label="Job filters">
        {chips.map((c) => (
          <button
            key={c.id}
            type="button"
            role="tab"
            aria-selected={filter === c.id}
            className={filter === c.id ? "sidebar-filter active" : "sidebar-filter"}
            onClick={() => onFilterChange(c.id)}
          >
            {c.label}
            <span className="job-count">{c.count}</span>
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <p className="sidebar-empty">No {filter === "all" ? "" : `${filter} `}jobs yet.</p>
      ) : (
        <ul className="sidebar-job-list">
          {visible.map((job) => {
            const active = job.runId === selectedRunId;
            const running = job.status === "queued" || job.status === "running";
            return (
              <li key={job.runId}>
                <button
                  type="button"
                  className={`sidebar-job${active ? " active" : ""}${running ? " live" : ""}`}
                  onClick={() => onSelect(job)}
                >
                  <span className="sidebar-job-top">
                    <span className={`job-status ${job.status}`}>{job.status}</span>
                    <span className="job-when muted">{formatWhen(job.updatedAt)}</span>
                  </span>
                  <span className="sidebar-job-title">{job.inputName}</span>
                  <span className="sidebar-job-meta muted">
                    {job.pageCount != null ? `${job.pageCount}p` : null}
                    {job.progressPct != null && running ? ` · ${job.progressPct}%` : ""}
                    {job.outputs?.hasMarkdown
                      ? ` · ${(job.outputs.markdownChars ?? 0).toLocaleString()} chars`
                      : ""}
                  </span>
                  {job.error ? <span className="job-error">{job.error}</span> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
