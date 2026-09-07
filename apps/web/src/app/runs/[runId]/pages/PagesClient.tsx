"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { MarkdownView } from "../../../../components/MarkdownView";

const PAGE_TAGS = [
  "too_short",
  "too_much_garbage",
  "missing_content",
  "bad_structure",
  "chrome_leak",
  "figure_issue",
  "looks_good",
] as const;

const FIGURE_TAGS = ["recrop", "not_useful", "wrong_crop", "looks_good"] as const;

const ISSUE_TAGS = new Set<string>(PAGE_TAGS.filter((t) => t !== "looks_good"));
const FIGURE_ISSUE_TAGS = new Set<string>(
  FIGURE_TAGS.filter((t) => t !== "looks_good"),
);

type PageSummary = {
  pageNumber: number;
  chars?: number;
  hasMarkdown: boolean;
  hasImage: boolean;
  figureCount?: number;
  figureMissing?: number;
  status?: string;
  error?: string;
  validation?: string[];
  markdownUrl: string;
  imageUrl: string;
};

type PagesIndex = {
  runId: string;
  pageCount: number;
  documentChars?: number;
  pageCharsSum?: number;
  pages: PageSummary[];
  error?: string;
};

type PageFigure = {
  id: string;
  t: string;
  caption?: string;
  artifact?: string;
  hasArtifact: boolean;
  imageUrl: string | null;
};

type PageDetail = {
  pageNumber: number;
  markdown: string | null;
  chars: number;
  discard?: Array<{ k: string; t: string; z: string }> | null;
  figures?: PageFigure[];
  figureCount?: number;
  hasImage: boolean;
  imageUrl: string | null;
  error?: string;
};

type ReviewComment = { id: string; ts: string; text: string };
type FigureReview = { tags: string[]; comments: ReviewComment[] };
type PageReview = {
  tags: string[];
  comments: ReviewComment[];
  figures?: Record<string, FigureReview>;
};
type RunReview = {
  v: 1;
  updatedAt: string;
  pages: Record<string, PageReview>;
  document: PageReview;
};

function figureReviewHasIssue(fr: FigureReview | undefined): boolean {
  if (!fr) return false;
  return (
    fr.tags.some((t) => FIGURE_ISSUE_TAGS.has(t)) || (fr.comments?.length ?? 0) > 0
  );
}

function pageHasFigureIssues(pageReview: PageReview | undefined): boolean {
  if (!pageReview?.figures) return false;
  return Object.values(pageReview.figures).some((fr) => figureReviewHasIssue(fr));
}

function pageDotClass(
  page: PageSummary,
  pageReview: PageReview | undefined,
  selected: boolean,
): string {
  const tags = pageReview?.tags ?? [];
  const hasReviewIssue = tags.some((t) => ISSUE_TAGS.has(t));
  const hasSoftIssue = (page.validation?.length ?? 0) > 0;
  const hasFigIssue =
    pageHasFigureIssues(pageReview) || (page.figureMissing ?? 0) > 0;
  const status = page.status ?? (page.hasMarkdown ? "done" : "pending");

  let tone = status;
  if (status === "failed" || page.error) tone = "failed";
  else if (status === "skipped") tone = "skipped";
  else if (
    hasSoftIssue ||
    hasReviewIssue ||
    hasFigIssue ||
    (pageReview?.comments.length ?? 0) > 0
  ) {
    tone = "issue";
  } else if (status === "done") tone = "done";

  const figs = (page.figureCount ?? 0) > 0 ? " has-figures" : "";
  return `page-dot ${tone}${figs}${selected ? " selected" : ""}`;
}

function pageDotTitle(page: PageSummary, pageReview: PageReview | undefined): string {
  const parts = [`Page ${page.pageNumber}`];
  if (page.status) parts.push(page.status);
  if (page.chars != null) parts.push(`${page.chars} chars`);
  if (page.figureCount) parts.push(`${page.figureCount} figure(s)`);
  if (page.figureMissing) parts.push(`${page.figureMissing} missing crop(s)`);
  if (page.error) parts.push(page.error);
  if (page.validation?.length) parts.push(`soft: ${page.validation.join(", ")}`);
  const tags = pageReview?.tags ?? [];
  if (tags.length) parts.push(`tags: ${tags.join(", ")}`);
  if (pageReview?.comments.length) parts.push(`${pageReview.comments.length} comment(s)`);
  if (pageHasFigureIssues(pageReview)) parts.push("figure notes");
  return parts.join(" · ");
}

function emptyPageReview(): PageReview {
  return { tags: [], comments: [], figures: {} };
}

function emptyFigureReview(): FigureReview {
  return { tags: [], comments: [] };
}

function getFigureReview(
  pageReview: PageReview,
  fig: PageFigure,
): FigureReview {
  const figs = pageReview.figures ?? {};
  if (figs[fig.id]) return figs[fig.id]!;
  const art = fig.artifact?.replace(/^artifacts\//, "");
  if (art && figs[art]) return figs[art]!;
  if (fig.artifact && figs[fig.artifact]) return figs[fig.artifact]!;
  return emptyFigureReview();
}

export default function RunPagesPage() {
  const params = useParams<{ runId: string }>();
  const search = useSearchParams();
  const runId = params.runId;
  const initialPage = Number(search.get("p") ?? "1");

  const [index, setIndex] = useState<PagesIndex | null>(null);
  const [selected, setSelected] = useState(
    Number.isFinite(initialPage) && initialPage > 0 ? initialPage : 1,
  );
  const [detail, setDetail] = useState<PageDetail | null>(null);
  const [review, setReview] = useState<RunReview | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draftTags, setDraftTags] = useState<string[]>([]);
  const [draftComment, setDraftComment] = useState("");
  const [savingReview, setSavingReview] = useState(false);
  /** Persist across page navigation within this run. */
  const [addReviewOpen, setAddReviewOpen] = useState(false);
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);
  const [editingCommentText, setEditingCommentText] = useState("");
  const [editingTags, setEditingTags] = useState(false);
  const [editTagDraft, setEditTagDraft] = useState<string[]>([]);

  const [reassembleOpen, setReassembleOpen] = useState(false);
  const [restitchBusy, setRestitchBusy] = useState(false);
  const [restitchPrompt, setRestitchPrompt] = useState("");
  const [restitchMsg, setRestitchMsg] = useState<string | null>(null);

  /** Figure key currently expanded for adding a note. */
  const [figureDraftKey, setFigureDraftKey] = useState<string | null>(null);
  const [figureDraftTags, setFigureDraftTags] = useState<string[]>([]);
  const [figureDraftComment, setFigureDraftComment] = useState("");
  const [savingFigure, setSavingFigure] = useState(false);

  const pageReview = review?.pages[String(selected)] ?? emptyPageReview();
  const canSubmitPageReview =
    draftTags.length > 0 || draftComment.trim().length > 0;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setBusy(true);
      setError(null);
      try {
        const [pagesRes, reviewRes] = await Promise.all([
          fetch(`/api/runs/${encodeURIComponent(runId)}/pages`, { cache: "no-store" }),
          fetch(`/api/runs/${encodeURIComponent(runId)}/review`, { cache: "no-store" }),
        ]);
        const data = (await pagesRes.json()) as PagesIndex;
        if (!pagesRes.ok) throw new Error(data.error ?? "Failed to load pages");
        const reviewData = (await reviewRes.json()) as RunReview;
        if (cancelled) return;
        setIndex(data);
        if (reviewRes.ok) setReview(reviewData);
        const first = data.pages[0]?.pageNumber ?? 1;
        setSelected((prev) =>
          data.pages.some((p) => p.pageNumber === prev) ? prev : first,
        );
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const loadPage = useCallback(
    async (pageNumber: number) => {
      setSelected(pageNumber);
      setDraftTags([]);
      setDraftComment("");
      setEditingCommentId(null);
      setEditingCommentText("");
      setEditingTags(false);
      setEditTagDraft([]);
      setFigureDraftKey(null);
      setFigureDraftTags([]);
      setFigureDraftComment("");
      try {
        const res = await fetch(
          `/api/runs/${encodeURIComponent(runId)}/pages/${pageNumber}`,
          { cache: "no-store" },
        );
        const data = (await res.json()) as PageDetail;
        if (!res.ok) throw new Error(data.error ?? "Failed to load page");
        setDetail(data);
        const url = new URL(window.location.href);
        url.searchParams.set("p", String(pageNumber));
        window.history.replaceState(null, "", url.toString());
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [runId],
  );

  useEffect(() => {
    if (!index?.pages.length) return;
    void loadPage(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, selected]);

  useEffect(() => {
    if (!reassembleOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setReassembleOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reassembleOpen]);

  const currentMeta = useMemo(
    () => index?.pages.find((p) => p.pageNumber === selected),
    [index, selected],
  );

  const condensed =
    index?.documentChars != null &&
    index.pageCharsSum != null &&
    index.pageCharsSum > 0 &&
    index.documentChars < index.pageCharsSum * 0.75;

  function go(delta: number) {
    if (!index?.pages.length) return;
    const idx = index.pages.findIndex((p) => p.pageNumber === selected);
    const next = index.pages[idx + delta];
    if (next) setSelected(next.pageNumber);
  }

  function toggleDraftTag(tag: string) {
    setDraftTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }

  async function patchReview(body: Record<string, unknown>) {
    const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: selected, ...body }),
    });
    const data = (await res.json()) as RunReview & { error?: string };
    if (!res.ok) throw new Error(data.error ?? "Failed to update review");
    setReview(data);
    return data;
  }

  function toggleFigureDraftTag(tag: string) {
    setFigureDraftTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }

  function openFigureDraft(figId: string) {
    setFigureDraftKey(figId);
    setFigureDraftTags([]);
    setFigureDraftComment("");
  }

  async function saveFigureReview(fig: PageFigure) {
    const can =
      figureDraftTags.length > 0 || figureDraftComment.trim().length > 0;
    if (!can) return;
    setSavingFigure(true);
    setError(null);
    try {
      const existing = getFigureReview(pageReview, fig);
      const mergedTags =
        figureDraftTags.length > 0
          ? [...new Set([...existing.tags, ...figureDraftTags])]
          : undefined;
      await patchReview({
        figure: fig.id,
        tags: mergedTags,
        addComment: figureDraftComment.trim() || undefined,
      });
      setFigureDraftKey(null);
      setFigureDraftTags([]);
      setFigureDraftComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingFigure(false);
    }
  }

  async function removeFigureTag(fig: PageFigure, tag: string) {
    setError(null);
    try {
      await patchReview({ figure: fig.id, removeTag: tag });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeFigureComment(fig: PageFigure, id: string) {
    setError(null);
    try {
      await patchReview({ figure: fig.id, removeCommentId: id });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function clearFigureReview(fig: PageFigure) {
    setError(null);
    try {
      await patchReview({ figure: fig.id, clearFigure: true });
      if (figureDraftKey === fig.id) {
        setFigureDraftKey(null);
        setFigureDraftTags([]);
        setFigureDraftComment("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function addPageReview() {
    if (!canSubmitPageReview) return;
    setSavingReview(true);
    setError(null);
    try {
      const mergedTags =
        draftTags.length > 0
          ? [...new Set([...pageReview.tags, ...draftTags])]
          : undefined;
      await patchReview({
        tags: mergedTags,
        addComment: draftComment.trim() || undefined,
      });
      setDraftTags([]);
      setDraftComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingReview(false);
    }
  }

  async function removeTag(tag: string) {
    setError(null);
    try {
      await patchReview({ removeTag: tag });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeComment(id: string) {
    setError(null);
    try {
      await patchReview({ removeCommentId: id });
      if (editingCommentId === id) {
        setEditingCommentId(null);
        setEditingCommentText("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function startEditComment(c: ReviewComment) {
    setEditingCommentId(c.id);
    setEditingCommentText(c.text);
  }

  async function saveEditComment() {
    if (!editingCommentId) return;
    const text = editingCommentText.trim();
    if (!text) return;
    setSavingReview(true);
    setError(null);
    try {
      await patchReview({ updateComment: { id: editingCommentId, text } });
      setEditingCommentId(null);
      setEditingCommentText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingReview(false);
    }
  }

  function startEditTags() {
    setEditingTags(true);
    setEditTagDraft([...pageReview.tags]);
  }

  function toggleEditTag(tag: string) {
    setEditTagDraft((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }

  async function saveEditTags() {
    setSavingReview(true);
    setError(null);
    try {
      await patchReview({ tags: editTagDraft });
      setEditingTags(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingReview(false);
    }
  }

  async function clearPageReview() {
    if (!window.confirm(`Clear all tags, comments, and figure reviews on page ${selected}?`)) return;
    setError(null);
    try {
      await patchReview({ clearPage: true });
      setEditingTags(false);
      setEditingCommentId(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function restitch(preset: string) {
    setRestitchBusy(true);
    setRestitchMsg(null);
    setError(null);
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/reassemble`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset,
          prompt: restitchPrompt.trim() || undefined,
          writeHtml: true,
        }),
      });
      const data = (await res.json()) as {
        markdownChars?: number;
        figurePagesFixed?: number[];
        error?: string;
      };
      if (!res.ok) throw new Error(data.error ?? "Reassemble failed");
      const figs = data.figurePagesFixed?.length
        ? ` · figures fixed on p.${data.figurePagesFixed.join(", ")}`
        : "";
      setRestitchMsg(
        `Reassembled (${preset}) · ${(data.markdownChars ?? 0).toLocaleString()} chars${figs}`,
      );
      const pagesRes = await fetch(`/api/runs/${encodeURIComponent(runId)}/pages`, {
        cache: "no-store",
      });
      const pagesData = (await pagesRes.json()) as PagesIndex;
      if (pagesRes.ok) setIndex(pagesData);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRestitchBusy(false);
    }
  }

  const artifactBase = `/api/runs/${encodeURIComponent(runId)}/artifacts`;

  return (
    <main className="pages-review">
      <header className="pages-review-bar">
        <div className="pages-review-bar-main">
          <a className="button-link secondary" href={`/?run=${encodeURIComponent(runId)}`}>
            ← Back to digest
          </a>
          <div>
            <h1 className="pages-review-title">Page review</h1>
            <p className="pages-review-sub mono muted">{runId}</p>
          </div>
        </div>
        <div className="pages-review-bar-tools">
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setRestitchMsg(null);
              setReassembleOpen(true);
            }}
          >
            Reassemble
          </button>
          <button
            type="button"
            className="secondary"
            disabled={!index || selected <= (index.pages[0]?.pageNumber ?? 1)}
            onClick={() => go(-1)}
          >
            Previous
          </button>
          <span className="pages-review-pos mono">
            {selected}
            {index ? ` / ${index.pageCount}` : ""}
          </span>
          <button
            type="button"
            className="secondary"
            disabled={
              !index ||
              selected >= (index.pages[index.pages.length - 1]?.pageNumber ?? 1)
            }
            onClick={() => go(1)}
          >
            Next
          </button>
        </div>
      </header>

      {error ? (
        <div className="alert alert-error" role="alert">
          <div className="alert-body">
            <p className="alert-title">Something went wrong</p>
            <div className="alert-text">
              <p>{error}</p>
            </div>
          </div>
        </div>
      ) : null}

      {condensed ? (
        <p className="pages-review-note">
          Stitched <span className="mono">document.md</span> is{" "}
          {(index!.documentChars ?? 0).toLocaleString()} chars, while page digests
          total {(index!.pageCharsSum ?? 0).toLocaleString()} chars. Open{" "}
          <strong>Reassemble</strong> and try “Too short” if the final document feels
          truncated.
        </p>
      ) : null}

      {index?.pages.length ? (
        <div className="pages-review-dots-wrap">
          <div
            className="page-dots pages-review-dots"
            role="listbox"
            aria-label="Page status"
          >
            {index.pages.map((p) => {
              const pr = review?.pages[String(p.pageNumber)];
              return (
                <button
                  key={p.pageNumber}
                  type="button"
                  role="option"
                  aria-label={pageDotTitle(p, pr)}
                  aria-selected={p.pageNumber === selected}
                  className={pageDotClass(p, pr, p.pageNumber === selected)}
                  title={pageDotTitle(p, pr)}
                  onClick={() => setSelected(p.pageNumber)}
                />
              );
            })}
          </div>
          <p className="pages-review-dots-legend muted">
            <span>
              <i className="page-dot done" aria-hidden /> ok
            </span>
            <span>
              <i className="page-dot issue" aria-hidden /> soft issue / notes
            </span>
            <span>
              <i className="page-dot failed" aria-hidden /> failed
            </span>
            <span>
              <i className="page-dot skipped" aria-hidden /> skipped
            </span>
            <span>
              <i className="page-dot done has-figures" aria-hidden /> has figures
            </span>
          </p>
        </div>
      ) : null}

      <div className="pages-review-body">
        <aside className="pages-review-nav" aria-label="Pages">
          {busy && !index ? <p className="hint">Loading pages…</p> : null}
          <ol className="pages-review-list">
            {(index?.pages ?? []).map((p) => {
              const tags = review?.pages[String(p.pageNumber)]?.tags ?? [];
              const comments = review?.pages[String(p.pageNumber)]?.comments ?? [];
              const soft = p.validation?.length ?? 0;
              const figs = p.figureCount ?? 0;
              const figIssues = pageHasFigureIssues(review?.pages[String(p.pageNumber)]);
              return (
                <li key={p.pageNumber}>
                  <button
                    type="button"
                    className={`pages-review-item${p.pageNumber === selected ? " active" : ""}${
                      p.status === "failed" ||
                      soft ||
                      tags.some((t) => ISSUE_TAGS.has(t)) ||
                      figIssues ||
                      (p.figureMissing ?? 0) > 0
                        ? " has-issue"
                        : ""
                    }`}
                    onClick={() => setSelected(p.pageNumber)}
                  >
                    <span className="pages-review-item-num mono">{p.pageNumber}</span>
                    <span className="pages-review-item-meta muted">
                      {p.chars != null ? `${p.chars.toLocaleString()}c` : "—"}
                      {figs > 0 ? ` · ${figs}img` : ""}
                      {p.figureMissing ? ` · ${p.figureMissing} miss` : ""}
                      {p.status === "failed"
                        ? " · failed"
                        : soft
                          ? ` · ${soft} issue${soft === 1 ? "" : "s"}`
                          : ""}
                      {tags.length || comments.length
                        ? ` · ${tags.length + comments.length} note${tags.length + comments.length === 1 ? "" : "s"}`
                        : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </aside>

        <section className="pages-review-stage" aria-label={`Page ${selected}`}>
          <div className="pages-review-pane">
            <header className="pages-review-pane-head">
              <h2>Source page</h2>
              <p className="muted">
                {currentMeta?.hasImage ? "Raster artifact" : "No image on disk"}
              </p>
            </header>
            <div className="pages-review-image-wrap">
              {detail?.hasImage && detail.imageUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={detail.imageUrl}
                  alt={`Page ${selected} raster`}
                  className="pages-review-image"
                />
              ) : (
                <p className="hint">No page image available.</p>
              )}
            </div>
          </div>

          <div className="pages-review-pane">
            <header className="pages-review-pane-head">
              <h2>Page digest (keep)</h2>
              <p className="muted">
                {detail?.chars != null
                  ? `${detail.chars.toLocaleString()} characters`
                  : currentMeta?.error
                    ? currentMeta.error
                    : "—"}
              </p>
            </header>
            <div className="pages-review-md">
              {detail?.markdown ? (
                <MarkdownView
                  markdown={detail.markdown}
                  compact
                  artifactBase={artifactBase}
                />
              ) : (
                <p className="hint">No markdown digest for this page.</p>
              )}
            </div>
            {detail?.discard && detail.discard.length > 0 ? (
              <div className="pages-review-discard">
                <h3>Discarded chrome</h3>
                <ul>
                  {detail.discard.map((d, i) => (
                    <li key={`${d.k}-${i}`}>
                      <span className="mono">{d.k}</span>
                      <span className="muted"> · {d.z}</span>
                      {d.t ? <span> — {d.t}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {(detail?.figures?.length ?? 0) > 0 ? (
              <div className="pages-review-figures">
                <header className="pages-review-figures-head">
                  <h3>Extracted figures</h3>
                  <p className="muted">
                    {detail!.figures!.length} crop
                    {detail!.figures!.length === 1 ? "" : "s"} · tag{" "}
                    <span className="mono">recrop</span> or{" "}
                    <span className="mono">not useful</span> before Reassemble
                  </p>
                </header>
                <ul className="pages-review-figure-list">
                  {detail!.figures!.map((fig) => {
                    const fr = getFigureReview(pageReview, fig);
                    const drafting = figureDraftKey === fig.id;
                    const canSaveFig =
                      figureDraftTags.length > 0 ||
                      figureDraftComment.trim().length > 0;
                    return (
                      <li
                        key={fig.id}
                        className={`pages-review-figure${
                          figureReviewHasIssue(fr) ? " has-issue" : ""
                        }${!fig.hasArtifact ? " missing" : ""}`}
                      >
                        <div className="pages-review-figure-preview">
                          {fig.imageUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={fig.imageUrl}
                              alt={fig.t || fig.caption || fig.id}
                            />
                          ) : (
                            <span className="hint">Missing crop</span>
                          )}
                        </div>
                        <div className="pages-review-figure-body">
                          <p className="pages-review-figure-title">
                            {fig.t || fig.caption || (
                              <span className="mono muted">{fig.id}</span>
                            )}
                          </p>
                          {fig.caption && fig.t ? (
                            <p className="muted pages-review-figure-cap">{fig.caption}</p>
                          ) : null}
                          {!fig.hasArtifact ? (
                            <p className="pages-review-figure-warn">No artifact on disk</p>
                          ) : null}

                          {(fr.tags.length > 0 || fr.comments.length > 0) && (
                            <div className="pages-review-figure-saved">
                              {fr.tags.length > 0 ? (
                                <div className="pages-review-tags is-saved">
                                  {fr.tags.map((tag) => (
                                    <span key={tag} className="pages-review-tag-chip">
                                      {tag.replaceAll("_", " ")}
                                      <button
                                        type="button"
                                        className="pages-review-chip-x"
                                        aria-label={`Remove ${tag}`}
                                        onClick={() => void removeFigureTag(fig, tag)}
                                      >
                                        ×
                                      </button>
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                              {fr.comments.length > 0 ? (
                                <ul className="pages-review-comments">
                                  {fr.comments.map((c) => (
                                    <li key={c.id} className="pages-review-comment-item">
                                      <div className="pages-review-comment-body">{c.text}</div>
                                      <div className="pages-review-comment-actions">
                                        <button
                                          type="button"
                                          className="pages-review-text-btn danger"
                                          onClick={() => void removeFigureComment(fig, c.id)}
                                        >
                                          Remove
                                        </button>
                                      </div>
                                    </li>
                                  ))}
                                </ul>
                              ) : null}
                              <button
                                type="button"
                                className="pages-review-text-btn"
                                onClick={() => void clearFigureReview(fig)}
                              >
                                Clear figure review
                              </button>
                            </div>
                          )}

                          {drafting ? (
                            <div className="pages-review-figure-draft">
                              <div className="pages-review-tags">
                                {FIGURE_TAGS.map((tag) => {
                                  const on = figureDraftTags.includes(tag);
                                  return (
                                    <button
                                      key={tag}
                                      type="button"
                                      className={on ? undefined : "secondary"}
                                      aria-pressed={on}
                                      onClick={() => toggleFigureDraftTag(tag)}
                                    >
                                      {tag.replaceAll("_", " ")}
                                    </button>
                                  );
                                })}
                              </div>
                              <div className="pages-review-comment-form">
                                <textarea
                                  rows={2}
                                  value={figureDraftComment}
                                  onChange={(e) => setFigureDraftComment(e.target.value)}
                                  placeholder="e.g. crop tighter around the face, or drop this decorative image…"
                                />
                                <div className="pages-review-edit-actions">
                                  <button
                                    type="button"
                                    className="secondary"
                                    onClick={() => {
                                      setFigureDraftKey(null);
                                      setFigureDraftTags([]);
                                      setFigureDraftComment("");
                                    }}
                                  >
                                    Cancel
                                  </button>
                                  <button
                                    type="button"
                                    disabled={savingFigure || !canSaveFig}
                                    onClick={() => void saveFigureReview(fig)}
                                  >
                                    {savingFigure ? "Saving…" : "Save figure review"}
                                  </button>
                                </div>
                              </div>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="pages-review-text-btn"
                              onClick={() => openFigureDraft(fig.id)}
                            >
                              Add figure review
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}

            <div className="pages-review-annotate">
              {(pageReview.tags.length > 0 || pageReview.comments.length > 0) && (
                <div className="pages-review-saved">
                  <div className="pages-review-saved-head">
                    <h3>Saved on this page</h3>
                    <button
                      type="button"
                      className="pages-review-text-btn"
                      onClick={() => void clearPageReview()}
                    >
                      Clear all
                    </button>
                  </div>

                  {editingTags ? (
                    <div className="pages-review-edit-block">
                      <div className="pages-review-tags">
                        {PAGE_TAGS.map((tag) => {
                          const on = editTagDraft.includes(tag);
                          return (
                            <button
                              key={tag}
                              type="button"
                              className={on ? undefined : "secondary"}
                              aria-pressed={on}
                              onClick={() => toggleEditTag(tag)}
                            >
                              {tag.replaceAll("_", " ")}
                            </button>
                          );
                        })}
                      </div>
                      <div className="pages-review-edit-actions">
                        <button
                          type="button"
                          disabled={savingReview}
                          onClick={() => void saveEditTags()}
                        >
                          Save tags
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => setEditingTags(false)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : pageReview.tags.length > 0 ? (
                    <div className="pages-review-tags is-saved">
                      {pageReview.tags.map((tag) => (
                        <span key={tag} className="pages-review-tag-chip">
                          {tag.replaceAll("_", " ")}
                          <button
                            type="button"
                            className="pages-review-chip-x"
                            aria-label={`Remove tag ${tag}`}
                            onClick={() => void removeTag(tag)}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                      <button
                        type="button"
                        className="pages-review-text-btn"
                        onClick={startEditTags}
                      >
                        Edit tags
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="pages-review-text-btn"
                      onClick={startEditTags}
                    >
                      Add tags
                    </button>
                  )}

                  {pageReview.comments.length > 0 ? (
                    <ul className="pages-review-comments">
                      {pageReview.comments.map((c) => (
                        <li key={c.id} className="pages-review-comment-item">
                          {editingCommentId === c.id ? (
                            <div className="pages-review-edit-block">
                              <textarea
                                rows={2}
                                value={editingCommentText}
                                onChange={(e) => setEditingCommentText(e.target.value)}
                              />
                              <div className="pages-review-edit-actions">
                                <button
                                  type="button"
                                  disabled={
                                    savingReview || !editingCommentText.trim()
                                  }
                                  onClick={() => void saveEditComment()}
                                >
                                  Save
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={() => {
                                    setEditingCommentId(null);
                                    setEditingCommentText("");
                                  }}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="pages-review-comment-body">
                                <span className="muted mono">{c.ts.slice(0, 19)}</span>
                                <span> — {c.text}</span>
                              </div>
                              <div className="pages-review-comment-actions">
                                <button
                                  type="button"
                                  className="pages-review-text-btn"
                                  onClick={() => startEditComment(c)}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="pages-review-text-btn danger"
                                  onClick={() => void removeComment(c.id)}
                                >
                                  Delete
                                </button>
                              </div>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )}

              <div className="pages-review-add">
                <button
                  type="button"
                  className="pages-review-add-toggle secondary"
                  aria-expanded={addReviewOpen}
                  onClick={() => setAddReviewOpen((open) => !open)}
                >
                  <span>{addReviewOpen ? "Hide add page review" : "Add page review"}</span>
                  <span className="pages-review-add-chevron" aria-hidden>
                    {addReviewOpen ? "▴" : "▾"}
                  </span>
                </button>

                {addReviewOpen ? (
                  <div className="pages-review-add-body">
                    <p className="pages-review-annotate-help muted">
                      Select tags and/or write a note, then save. Nothing is written until
                      you click Save page review.
                    </p>
                    <div className="pages-review-tags">
                      {PAGE_TAGS.map((tag) => {
                        const on = draftTags.includes(tag);
                        return (
                          <button
                            key={tag}
                            type="button"
                            className={on ? undefined : "secondary"}
                            aria-pressed={on}
                            onClick={() => toggleDraftTag(tag)}
                          >
                            {tag.replaceAll("_", " ")}
                          </button>
                        );
                      })}
                    </div>
                    <div className="pages-review-comment-form">
                      <textarea
                        rows={2}
                        value={draftComment}
                        onChange={(e) => setDraftComment(e.target.value)}
                        placeholder="Note for structure / reassemble…"
                      />
                      <button
                        type="button"
                        disabled={!canSubmitPageReview || savingReview}
                        onClick={() => void addPageReview()}
                      >
                        {savingReview ? "Saving…" : "Save page review"}
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      </div>

      {reassembleOpen ? (
        <div
          className="pages-review-modal-backdrop"
          role="presentation"
          onClick={() => setReassembleOpen(false)}
        >
          <div
            className="pages-review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reassemble-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="pages-review-modal-head">
              <h2 id="reassemble-title">Reassemble document</h2>
              <button
                type="button"
                className="secondary"
                onClick={() => setReassembleOpen(false)}
              >
                Close
              </button>
            </header>
            <p className="muted pages-review-modal-help">
              Re-runs structure inference and code stitch from saved page IR. Pages tagged{" "}
              <span className="mono">figure issue</span>, or tag individual figures{" "}
              <span className="mono">recrop</span> / <span className="mono">wrong crop</span>,
              to vision-fix crops. Tag a figure <span className="mono">not useful</span> to
              drop it from the assembled document. Does not re-digest body text.
            </p>
            <label className="pages-review-prompt">
              <span>Assemble guidance</span>
              <textarea
                rows={3}
                value={restitchPrompt}
                onChange={(e) => setRestitchPrompt(e.target.value)}
                placeholder="e.g. Keep TOC entries; strip ‘Product Design Playbook’ footers…"
              />
            </label>
            <div className="pages-review-restitch-row">
              <button
                type="button"
                className="secondary"
                disabled={restitchBusy}
                onClick={() => void restitch("too_short")}
              >
                Too short
              </button>
              <button
                type="button"
                className="secondary"
                disabled={restitchBusy}
                onClick={() => void restitch("too_much_garbage")}
              >
                Too much garbage
              </button>
              <button
                type="button"
                className="secondary"
                disabled={restitchBusy}
                onClick={() => void restitch("bad_structure")}
              >
                Bad structure
              </button>
              <button
                type="button"
                disabled={restitchBusy}
                onClick={() => void restitch("default")}
              >
                {restitchBusy ? "Reassembling…" : "Reassemble"}
              </button>
            </div>
            {restitchMsg ? <p className="hint">{restitchMsg}</p> : null}
          </div>
        </div>
      ) : null}
    </main>
  );
}
