"use client";

import { useMemo } from "react";
import GithubSlugger from "github-slugger";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";

export type TocItem = {
  id: string;
  depth: number;
  text: string;
};

function extractToc(markdown: string): TocItem[] {
  const slugger = new GithubSlugger();
  const items: TocItem[] = [];
  let inFence = false;

  for (const line of markdown.split("\n")) {
    if (/^```/.test(line.trimStart())) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;

    const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;

    const depth = match[1]!.length;
    const text = match[2]!.replace(/\s+#+\s*$/, "").trim();
    if (!text) continue;
    items.push({ id: slugger.slug(text), depth, text });
  }

  return items;
}

function resolveAssetSrc(src: string | undefined, artifactBase?: string): string | undefined {
  if (!src) return src;
  if (!artifactBase) return src;
  if (/^https?:\/\//i.test(src) || src.startsWith("/api/")) return src;
  const cleaned = src.replace(/^\.?\//, "");
  if (cleaned.startsWith("artifacts/")) {
    return `${artifactBase}/${cleaned.slice("artifacts/".length)}`;
  }
  return src;
}

/** Rewrite markdown image targets before parse so relative artifacts/ never 404 in the UI. */
function rewriteArtifactMarkdown(markdown: string, artifactBase?: string): string {
  if (!artifactBase) return markdown;
  return markdown.replace(
    /!\[([^\]]*)\]\(\s*((?:\.?\/)?artifacts\/[^)\s]+)\s*\)/g,
    (_m, alt: string, src: string) => {
      const resolved = resolveAssetSrc(src, artifactBase) ?? src;
      return `![${alt}](${resolved})`;
    },
  );
}

export function MarkdownView({
  markdown,
  compact = false,
  artifactBase,
}: {
  markdown: string;
  compact?: boolean;
  /** e.g. /api/runs/<runId>/artifacts — rewrites artifacts/… image srcs */
  artifactBase?: string;
}) {
  const renderedMarkdown = useMemo(
    () => rewriteArtifactMarkdown(markdown, artifactBase),
    [markdown, artifactBase],
  );
  const toc = useMemo(
    () => (compact ? [] : extractToc(renderedMarkdown)),
    [renderedMarkdown, compact],
  );

  return (
    <article className={`md-doc${compact ? " md-doc-compact" : ""}`}>
      {toc.length > 1 ? (
        <details className="md-toc-details">
          <summary>Contents · {toc.length} sections</summary>
          <nav className="md-toc-inline" aria-label="Table of contents">
            <ol>
              {toc.map((item) => (
                <li key={item.id} data-depth={item.depth}>
                  <a href={`#${item.id}`}>{item.text}</a>
                </li>
              ))}
            </ol>
          </nav>
        </details>
      ) : null}
      <div className="md-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSlug]}
          urlTransform={(url) => url}
          components={{
            img: ({ src, alt }) => {
              const raw =
                typeof src === "string"
                  ? src
                  : src != null && typeof src === "object" && "src" in src
                    ? String((src as { src: unknown }).src)
                    : undefined;
              return (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={resolveAssetSrc(raw, artifactBase)}
                  alt={alt ?? ""}
                  loading="lazy"
                  className="md-figure"
                />
              );
            },
          }}
        >
          {renderedMarkdown}
        </ReactMarkdown>
      </div>
    </article>
  );
}
