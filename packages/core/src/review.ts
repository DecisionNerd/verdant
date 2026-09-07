import { randomUUID } from "node:crypto";
import { ensureDb } from "./db/client.js";
import {
  emptyFigureReview,
  emptyRunReview,
  type FigureReview,
  type PageReview,
  type ReviewComment,
  type RunReview,
} from "./ir/structureTypes.js";

function clonePageReview(pr: PageReview | undefined): PageReview {
  const figures: Record<string, FigureReview> = {};
  for (const [k, fr] of Object.entries(pr?.figures ?? {})) {
    figures[k] = {
      tags: [...(fr.tags ?? [])],
      comments: [...(fr.comments ?? [])],
    };
  }
  return {
    tags: [...(pr?.tags ?? [])],
    comments: [...(pr?.comments ?? [])],
    figures,
  };
}

function isPageReviewEmpty(pr: PageReview): boolean {
  const figureEntries = Object.entries(pr.figures ?? {}).filter(
    ([, fr]) => (fr.tags?.length ?? 0) > 0 || (fr.comments?.length ?? 0) > 0,
  );
  return pr.tags.length === 0 && pr.comments.length === 0 && figureEntries.length === 0;
}

function pruneEmptyFigures(pr: PageReview): void {
  if (!pr.figures) return;
  for (const [key, fr] of Object.entries(pr.figures)) {
    if ((fr.tags?.length ?? 0) === 0 && (fr.comments?.length ?? 0) === 0) {
      delete pr.figures[key];
    }
  }
  if (Object.keys(pr.figures).length === 0) delete pr.figures;
}

function normalizeReview(data: RunReview): RunReview {
  const pages: Record<string, PageReview> = {};
  for (const [k, pr] of Object.entries(data.pages ?? {})) {
    pages[k] = clonePageReview(pr);
  }
  return {
    v: 1,
    updatedAt: data.updatedAt ?? new Date().toISOString(),
    pages,
    document: clonePageReview(data.document ?? { tags: [], comments: [] }),
  };
}

export async function readRunReview(runId: string): Promise<RunReview> {
  const client = await ensureDb();
  const rs = await client.execute({
    sql: "SELECT review_json FROM reviews WHERE run_id = ?",
    args: [runId],
  });
  const row = rs.rows[0];
  if (!row?.review_json) return emptyRunReview();
  try {
    const data = JSON.parse(String(row.review_json)) as RunReview;
    if (data?.v !== 1) return emptyRunReview();
    return normalizeReview(data);
  } catch {
    return emptyRunReview();
  }
}

export async function writeRunReview(runId: string, review: RunReview): Promise<RunReview> {
  const next: RunReview = {
    ...review,
    v: 1,
    updatedAt: new Date().toISOString(),
  };
  const client = await ensureDb();
  await client.execute({
    sql: `INSERT INTO reviews (run_id, review_json, updated_at) VALUES (?, ?, ?)
          ON CONFLICT(run_id) DO UPDATE SET review_json = excluded.review_json, updated_at = excluded.updated_at`,
    args: [runId, JSON.stringify(next), next.updatedAt],
  });
  return next;
}

type ReviewPatch = {
  tags?: string[];
  addComment?: string;
  removeCommentId?: string;
  updateComment?: { id: string; text: string };
  removeTag?: string;
  clearPage?: boolean;
};

function applyReviewPatch(
  current: { tags: string[]; comments: ReviewComment[] },
  patch: ReviewPatch,
): { tags: string[]; comments: ReviewComment[] } {
  let tags = [...current.tags];
  let comments = [...current.comments];

  if (patch.clearPage) {
    return { tags: [], comments: [] };
  }
  if (patch.tags) {
    tags = [...new Set(patch.tags.map((t) => t.trim()).filter(Boolean))];
  }
  if (patch.removeTag?.trim()) {
    const tag = patch.removeTag.trim();
    tags = tags.filter((t) => t !== tag);
  }
  if (patch.addComment?.trim()) {
    const comment: ReviewComment = {
      id: randomUUID().slice(0, 8),
      ts: new Date().toISOString(),
      text: patch.addComment.trim().slice(0, 2000),
    };
    comments = [...comments, comment];
  }
  if (patch.removeCommentId) {
    comments = comments.filter((c) => c.id !== patch.removeCommentId);
  }
  if (patch.updateComment?.id) {
    const text = patch.updateComment.text.trim().slice(0, 2000);
    comments = comments.map((c) =>
      c.id === patch.updateComment!.id
        ? { ...c, text, ts: new Date().toISOString() }
        : c,
    );
  }
  return { tags, comments };
}

export async function patchPageReview(
  runId: string,
  pageNumber: number | "document",
  patch: ReviewPatch & {
    /** When set, patch applies to this figure key instead of the page. */
    figure?: string;
    clearFigure?: boolean;
  },
): Promise<RunReview> {
  const review = await readRunReview(runId);
  const key = pageNumber === "document" ? null : String(pageNumber);

  if (key == null && patch.figure) {
    throw new Error("Figure reviews are only supported on pages, not document");
  }

  if (key != null && patch.figure?.trim()) {
    const figureKey = patch.figure.trim();
    const page = clonePageReview(review.pages[key]);
    const figures = { ...(page.figures ?? {}) };
    let current: FigureReview = figures[figureKey]
      ? {
          tags: [...figures[figureKey]!.tags],
          comments: [...figures[figureKey]!.comments],
        }
      : emptyFigureReview();

    if (patch.clearFigure || patch.clearPage) {
      delete figures[figureKey];
    } else {
      const next = applyReviewPatch(current, patch);
      current = { tags: next.tags, comments: next.comments };
      if (current.tags.length === 0 && current.comments.length === 0) {
        delete figures[figureKey];
      } else {
        figures[figureKey] = current;
      }
    }

    page.figures = figures;
    pruneEmptyFigures(page);

    if (isPageReviewEmpty(page)) delete review.pages[key];
    else review.pages[key] = page;

    return writeRunReview(runId, review);
  }

  let current: PageReview =
    key == null
      ? clonePageReview(review.document)
      : clonePageReview(review.pages[key]);

  if (patch.clearPage) {
    current = { tags: [], comments: [], figures: {} };
  } else {
    const next = applyReviewPatch(current, patch);
    current = {
      tags: next.tags,
      comments: next.comments,
      figures: current.figures,
    };
  }

  if (key == null) {
    review.document = {
      tags: current.tags,
      comments: current.comments,
    };
  } else {
    pruneEmptyFigures(current);
    if (isPageReviewEmpty(current)) delete review.pages[key];
    else review.pages[key] = current;
  }

  return writeRunReview(runId, review);
}
