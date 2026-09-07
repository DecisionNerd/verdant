import { z } from "zod";
import type { DiscardBlock, KeepBlock, PageIr } from "./types.js";

export const REVIEW_TAG_PRESETS = [
  "too_short",
  "too_much_garbage",
  "missing_content",
  "bad_structure",
  "chrome_leak",
  "figure_issue",
  "looks_good",
] as const;

/** Tags for an individual extracted figure crop. */
export const FIGURE_REVIEW_TAG_PRESETS = [
  "recrop",
  "not_useful",
  "wrong_crop",
  "looks_good",
] as const;

export type ReviewTag = (typeof REVIEW_TAG_PRESETS)[number] | string;
export type FigureReviewTag = (typeof FIGURE_REVIEW_TAG_PRESETS)[number] | string;

export type ReviewComment = {
  id: string;
  ts: string;
  text: string;
};

export type FigureReview = {
  tags: string[];
  comments: ReviewComment[];
};

export type PageReview = {
  tags: string[];
  comments: ReviewComment[];
  /**
   * Per-figure annotations, keyed by figure block `id` (preferred) or
   * artifact basename (e.g. `figure-p003-f01.png`).
   */
  figures?: Record<string, FigureReview>;
};

export type RunReview = {
  v: 1;
  updatedAt: string;
  pages: Record<string, PageReview>;
  document: PageReview;
};

export const RESTITCH_PRESETS = [
  "default",
  "too_short",
  "too_much_garbage",
  "bad_structure",
] as const;

export type RestitchPreset = (typeof RESTITCH_PRESETS)[number];

export type DocumentStructure = {
  v: 1;
  title?: string;
  sections: Array<{
    heading: string;
    level: number;
    fromPages?: number[];
    notes?: string;
  }>;
  /** Extra strings to treat as chrome when assembling. */
  chromePatterns: string[];
  /** Freeform assemble guidance (shown in traces; may influence heading rebase). */
  guidance?: string;
};

export const documentStructureSchema = z.object({
  v: z.literal(1),
  title: z.string().optional(),
  sections: z.array(
    z.object({
      heading: z.string(),
      level: z.number().int().min(1).max(6),
      fromPages: z.array(z.number().int().positive()).optional(),
      notes: z.string().optional(),
    }),
  ),
  chromePatterns: z.array(z.string()).default([]),
  guidance: z.string().optional(),
});

export function emptyPageReview(): PageReview {
  return { tags: [], comments: [], figures: {} };
}

export function emptyFigureReview(): FigureReview {
  return { tags: [], comments: [] };
}

/** True when a figure review asks for a vision re-crop. */
export function figureNeedsRecrop(fr: FigureReview | undefined): boolean {
  if (!fr) return false;
  return fr.tags.some((t) => t === "recrop" || t === "wrong_crop" || t === "figure_issue");
}

/** True when a figure should be dropped from assemble. */
export function figureMarkedNotUseful(fr: FigureReview | undefined): boolean {
  if (!fr) return false;
  return fr.tags.includes("not_useful");
}

/** Resolve a figure review by id or artifact path/basename. */
export function lookupFigureReview(
  pageReview: PageReview | undefined,
  opts: { id?: string; artifact?: string },
): FigureReview | undefined {
  if (!pageReview?.figures) return undefined;
  const figs = pageReview.figures;
  if (opts.id && figs[opts.id]) return figs[opts.id];
  const artifact = opts.artifact?.trim();
  if (!artifact) return undefined;
  if (figs[artifact]) return figs[artifact];
  const base = artifact.replace(/^artifacts\//, "");
  if (figs[base]) return figs[base];
  return undefined;
}

export function emptyRunReview(): RunReview {
  return {
    v: 1,
    updatedAt: new Date().toISOString(),
    pages: {},
    document: emptyPageReview(),
  };
}

/** Compact outline for structure LLM (token-efficient). */
export function buildStructureBrief(pages: PageIr[]): string {
  const parts: string[] = [];
  for (const page of [...pages].sort((a, b) => a.page - b.page)) {
    const keepHeadings = page.keep
      .filter((b): b is Extract<KeepBlock, { k: "heading" }> => b.k === "heading")
      .map((b) => `H${b.lvl}:${b.t.trim()}`)
      .slice(0, 12);
    const discardSummary = summarizeDiscard(page.discard);
    const breadcrumb = page.discard
      .filter((d) => d.k === "running_header" || d.k === "nav_chrome")
      .map((d) => (d.t || "").trim())
      .find((t) => />/.test(t));
    const keepKinds = page.keep.reduce<Record<string, number>>((acc, b) => {
      acc[b.k] = (acc[b.k] ?? 0) + 1;
      return acc;
    }, {});
    parts.push(
      `P${page.page} keep=${JSON.stringify(keepKinds)} headings=[${keepHeadings.join(" | ")}] breadcrumb=${breadcrumb ? JSON.stringify(breadcrumb) : "-"} discard=[${discardSummary}]`,
    );
  }
  return parts.join("\n");
}

function summarizeDiscard(discard: DiscardBlock[]): string {
  return discard
    .slice(0, 16)
    .map((d) => `${d.k}:${(d.t || "").trim().slice(0, 40)}`)
    .join(" ; ");
}
