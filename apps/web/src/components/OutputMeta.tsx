"use client";

import { formatBytes, type RunOutputsMeta } from "../lib/jobs";

export function OutputMeta({
  outputs,
  model,
  inputName,
  format,
  pageCount,
  status,
}: {
  outputs: RunOutputsMeta | null | undefined;
  model?: string;
  inputName?: string;
  format?: string;
  pageCount?: number;
  status?: string;
}) {
  if (!outputs && !inputName && !model) return null;

  const files = outputs?.files ?? [];
  const primary = files.filter((f) =>
    ["markdown", "html", "plan", "progress"].includes(f.kind),
  );
  const pages = files.filter((f) => f.kind === "page");
  const artifacts = files.filter((f) => f.kind === "artifact");

  return (
    <div className="output-meta">
      <p className="output-meta-title">Output</p>
      <dl className="output-meta-grid">
        {status ? (
          <>
            <dt>Status</dt>
            <dd>{status}</dd>
          </>
        ) : null}
        {inputName ? (
          <>
            <dt>Input</dt>
            <dd className="mono" title={inputName}>
              {inputName}
            </dd>
          </>
        ) : null}
        {format ? (
          <>
            <dt>Format</dt>
            <dd>{format}</dd>
          </>
        ) : null}
        {model ? (
          <>
            <dt>Model</dt>
            <dd className="mono">{model}</dd>
          </>
        ) : null}
        {pageCount != null ? (
          <>
            <dt>Pages</dt>
            <dd>{pageCount}</dd>
          </>
        ) : null}
        {outputs?.markdownChars != null ? (
          <>
            <dt>Markdown</dt>
            <dd>
              {outputs.markdownChars.toLocaleString()} chars
              {outputs.markdownHeadings != null
                ? ` · ${outputs.markdownHeadings} headings`
                : ""}
            </dd>
          </>
        ) : null}
        {outputs ? (
          <>
            <dt>Files</dt>
            <dd>
              {outputs.hasMarkdown ? "document.md" : "—"}
              {outputs.hasHtml ? " · document.html" : ""}
              {outputs.pageMdCount > 0 ? ` · ${outputs.pageMdCount} page md` : ""}
              {outputs.artifactCount > 0 ? ` · ${outputs.artifactCount} artifacts` : ""}
              {` · ${formatBytes(outputs.totalBytes)}`}
            </dd>
          </>
        ) : null}
        {outputs?.runDir ? (
          <>
            <dt>Path</dt>
            <dd className="mono">{outputs.runDir}</dd>
          </>
        ) : null}
      </dl>

      {primary.length > 0 ? (
        <ul className="output-file-list">
          {primary.map((f) => (
            <li key={f.name}>
              <span className={`file-kind ${f.kind}`}>{f.kind}</span>
              <span className="mono">{f.name}</span>
              <span className="muted">
                {f.chars != null ? `${f.chars.toLocaleString()} chars` : formatBytes(f.bytes)}
              </span>
            </li>
          ))}
          {pages.length > 0 ? (
            <li>
              <span className="file-kind page">page</span>
              <span className="mono">pages/*.md</span>
              <span className="muted">{pages.length} files</span>
            </li>
          ) : null}
          {artifacts.length > 0 ? (
            <li>
              <span className="file-kind artifact">artifact</span>
              <span className="mono">artifacts/*</span>
              <span className="muted">{artifacts.length} files</span>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
