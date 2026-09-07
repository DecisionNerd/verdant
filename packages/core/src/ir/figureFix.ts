import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { completeVisionChat, type ResolvedProviderConfig } from "../providers.js";
import { extractJsonObject } from "./parse.js";
import { compilePageMarkdown } from "./compile.js";
import {
  cropPageFigures,
  figureY,
  type FigureBBox,
} from "./figures.js";
import type { KeepFigure, PageIr } from "./types.js";
import {
  figureMarkedNotUseful,
  figureNeedsRecrop,
  lookupFigureReview,
  type PageReview,
  type RunReview,
} from "./structureTypes.js";

const FIGURE_FIX_SYSTEM = `You are Verdant, fixing figure crops for a document page image.

Find every informational figure, photo, diagram, chart, or illustration that should become a cropped artifact (not decorative chrome/logos that repeat on every page).

Return ONLY JSON:
{
  "figures": [
    {
      "t": "short description of what the figure shows",
      "caption": "optional caption text if visible (name/handle for portraits)",
      "bbox": { "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1 },
      "z": "top|body|bottom"
    }
  ]
}

Rules:
1. bbox MUST use normalized 0–1 fractions of the page image (origin top-left). Never pixels, never 0–1000 grids.
   Example bottom-left circular headshot: {"x":0.05,"y":0.70,"w":0.14,"h":0.12}
2. Prefer a few accurate crops over many tiny boxes.
3. Skip pure branding logos / watermarks / page chrome.
4. Profile / headshot crops must be TIGHT around the photo only (roughly square on the circle/face).
   Do NOT include the person's name, @handle, signature flourish, or bio text in the bbox —
   put those in "caption" / leave them as separate text blocks.
5. Set z to "bottom" for author/byline photos near the page bottom, "top" for hero images, else "body".
6. If there are no real figures, return {"figures":[]}.
7. Do not invent content that is not visible.`;

const figuresResponseSchema = z.object({
  figures: z.array(
    z.object({
      t: z.string().optional().default(""),
      caption: z.string().optional(),
      bbox: z.object({
        x: z.number(),
        y: z.number(),
        w: z.number(),
        h: z.number(),
      }),
      z: z.enum(["top", "body", "bottom", "margin", "overlay"]).optional(),
    }),
  ),
});

export function mentionsFigures(text: string): boolean {
  return /figure|image|photo|diagram|screenshot|illustration|artwork|crop|bbox|artifact|\bimg\b/i.test(
    text,
  );
}

async function artifactExists(runDir: string, artifactRel?: string): Promise<boolean> {
  if (!artifactRel) return false;
  const name = artifactRel.replace(/^artifacts\//, "");
  try {
    await fs.access(path.join(runDir, "artifacts", name));
    return true;
  } catch {
    return false;
  }
}

export async function pageNeedsFigureFix(
  ir: PageIr,
  runDir: string,
): Promise<boolean> {
  const figures = ir.keep.filter((b): b is KeepFigure => b.k === "figure");
  if (figures.length === 0) return false;
  for (const fig of figures) {
    if (!fig.bbox) return true;
    if (!(await artifactExists(runDir, fig.artifact))) return true;
  }
  return false;
}

function pageHasFigureRecropRequest(pr: PageReview | undefined): boolean {
  if (!pr?.figures) return false;
  return Object.values(pr.figures).some((fr) => figureNeedsRecrop(fr));
}

/**
 * Pages to run vision figure-fix on:
 * - any page tagged `figure_issue`
 * - pages whose comments mention figures/images
 * - pages with a figure tagged `recrop` / `wrong_crop`
 * - if the reassemble prompt mentions figures: also pages with broken/missing figure
 *   artifacts, or (if none) every page
 */
export async function selectPagesForFigureFix(opts: {
  pages: PageIr[];
  review: RunReview;
  prompt?: string;
  runDir: string;
}): Promise<number[]> {
  const { pages, review, prompt, runDir } = opts;
  const targets = new Set<number>();

  for (const [pageKey, pr] of Object.entries(review.pages)) {
    const n = Number(pageKey);
    if (!Number.isFinite(n)) continue;
    if (pr.tags.includes("figure_issue")) targets.add(n);
    if (pr.comments.some((c) => mentionsFigures(c.text))) targets.add(n);
    if (pageHasFigureRecropRequest(pr)) targets.add(n);
  }

  const docFigureIssue =
    review.document.tags.includes("figure_issue") ||
    review.document.comments.some((c) => mentionsFigures(c.text));

  if (docFigureIssue) {
    for (const p of pages) targets.add(p.page);
  }

  if (prompt && mentionsFigures(prompt)) {
    const broken: number[] = [];
    for (const p of pages) {
      if (await pageNeedsFigureFix(p, runDir)) broken.push(p.page);
    }
    if (broken.length > 0) {
      for (const n of broken) targets.add(n);
    } else if (targets.size === 0) {
      for (const p of pages) targets.add(p.page);
    }
  }

  return [...targets].filter((n) => pages.some((p) => p.page === n)).sort((a, b) => a - b);
}

/**
 * Drop figures tagged `not_useful` from keep (and matching caption blocks that
 * only describe that figure). Used at reassemble before structure/assemble.
 */
export function applyFigureReviewFilters(ir: PageIr, pageReview: PageReview | undefined): PageIr {
  if (!pageReview?.figures || Object.keys(pageReview.figures).length === 0) {
    return ir;
  }

  const dropIds = new Set<string>();
  const dropArtifacts = new Set<string>();

  for (const block of ir.keep) {
    if (block.k !== "figure") continue;
    const fr = lookupFigureReview(pageReview, { id: block.id, artifact: block.artifact });
    if (!figureMarkedNotUseful(fr)) continue;
    dropIds.add(block.id);
    if (block.artifact) {
      dropArtifacts.add(block.artifact);
      dropArtifacts.add(block.artifact.replace(/^artifacts\//, ""));
    }
  }

  if (dropIds.size === 0) return ir;

  const keep = ir.keep.filter((b) => {
    if (b.k === "figure" && dropIds.has(b.id)) return false;
    if (b.k === "caption") {
      // Drop orphan captions that only pointed at a removed figure via text match.
      const t = (b.t || "").toLowerCase();
      if (!t) return true;
      for (const art of dropArtifacts) {
        if (t.includes(art.toLowerCase())) return false;
      }
    }
    return true;
  });

  return { ...ir, keep };
}

/**
 * Replace figure blocks from vision. Reading order is applied later by
 * cropPageFigures → reorderFiguresByBBox (spatial), not by stuffing after H1.
 */
export function mergeFiguresIntoIr(ir: PageIr, figures: KeepFigure[]): PageIr {
  if (figures.length === 0) return ir;

  const nonFigures = ir.keep.filter((b) => b.k !== "figure");
  const oldFigs = ir.keep.filter((b): b is KeepFigure => b.k === "figure");

  const nextFigs: KeepFigure[] = figures.map((f, i) => {
    const prev = oldFigs[i];
    const y = figureY({ ...f, bbox: f.bbox });
    const z =
      f.z ??
      prev?.z ??
      (y > 0.65 ? "bottom" : y < 0.35 ? "top" : "body");
    return {
      id: prev?.id || f.id,
      k: "figure" as const,
      t: f.t || prev?.t || f.caption || "Figure",
      caption: f.caption ?? prev?.caption,
      z,
      edge: f.edge ?? (z === "bottom" ? "bottom" : prev?.edge),
      bbox: f.bbox,
      // Force re-crop; spatial reorder happens in cropPageFigures.
      artifact: undefined,
    };
  });

  return { ...ir, keep: [...nonFigures, ...nextFigs] };
}

export async function fixPageFiguresWithVision(opts: {
  config: ResolvedProviderConfig;
  ir: PageIr;
  pageImagePath: string;
  artifactsDir: string;
  note?: string;
}): Promise<PageIr> {
  const { config, ir, pageImagePath, artifactsDir, note } = opts;
  const buf = await fs.readFile(pageImagePath);
  const ext = path.extname(pageImagePath).toLowerCase();
  const mimeType =
    ext === ".jpg" || ext === ".jpeg"
      ? "image/jpeg"
      : ext === ".webp"
        ? "image/webp"
        : "image/png";

  const existing = ir.keep
    .filter((b): b is KeepFigure => b.k === "figure")
    .map((f, i) => `#${i + 1} t=${f.t || ""} caption=${f.caption || ""} bbox=${f.bbox ? "yes" : "no"} artifact=${f.artifact || "-"}`)
    .join("\n");

  const raw = await completeVisionChat({
    config,
    system: FIGURE_FIX_SYSTEM,
    userText: [
      `Page ${ir.page} of ${ir.pages}. Detect figure crops for this page image.`,
      note?.trim() ? `Operator notes: ${note.trim()}` : "",
      existing ? `Current IR figures (may be wrong/missing crops):\n${existing}` : "No figure blocks in IR yet.",
      'Return JSON only: {"figures":[...]}',
    ]
      .filter(Boolean)
      .join("\n\n"),
    pages: [{ mimeType, base64: buf.toString("base64") }],
    temperature: 0.1,
    maxTokens: 2048,
    observe: {
      name: `fix-figures-page-${ir.page}`,
      metadata: {
        kind: "figure-fix",
        pageNumber: ir.page,
      },
    },
  });

  const parsed = figuresResponseSchema.parse(JSON.parse(extractJsonObject(raw)));
  const figures: KeepFigure[] = parsed.figures.map((f, i) => ({
    id: `fig${i + 1}`,
    k: "figure" as const,
    t: f.t || f.caption || "Figure",
    caption: f.caption,
    z: f.z ?? "body",
    bbox: f.bbox as FigureBBox,
  }));

  const merged = mergeFiguresIntoIr(ir, figures);
  return cropPageFigures({
    ir: merged,
    pageImagePath,
    artifactsDir,
  });
}

export async function persistFixedPageIr(opts: {
  runDir: string;
  ir: PageIr;
}): Promise<void> {
  const pagesDir = path.join(opts.runDir, "pages");
  const pad = String(opts.ir.page).padStart(3, "0");
  const irPath = path.join(pagesDir, `page-${pad}.ir.json`);
  const mdPath = path.join(pagesDir, `page-${pad}.md`);
  await fs.writeFile(irPath, JSON.stringify(opts.ir), "utf8");
  await fs.writeFile(mdPath, compilePageMarkdown(opts.ir), "utf8");
}
