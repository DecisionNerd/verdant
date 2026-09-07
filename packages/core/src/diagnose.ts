import {
  classifyPageImage,
  inputKindFromPath,
  type PageImage,
} from "./normalize.js";
import type { DigestPlan, PagePlanEntry } from "./types.js";

const DEFAULT_CONCURRENCY = Math.max(
  1,
  Number(process.env.VERDANT_PAGE_CONCURRENCY ?? 2),
);

/**
 * Diagnose rasterized pages and build a processing plan:
 * how many pages, which to digest vs skip (blank), concurrency, strategy.
 */
export async function diagnoseDocument(opts: {
  inputPath: string;
  pages: PageImage[];
  concurrency?: number;
}): Promise<DigestPlan> {
  const concurrency = opts.concurrency ?? DEFAULT_CONCURRENCY;
  const pageEntries: PagePlanEntry[] = [];

  for (const page of opts.pages) {
    const { kind } = await classifyPageImage(page.path);
    if (kind === "blank") {
      pageEntries.push({
        pageNumber: page.pageNumber,
        kind,
        action: "skip",
        reason: "Near-blank page (no substantive content detected)",
      });
    } else {
      pageEntries.push({
        pageNumber: page.pageNumber,
        kind,
        action: "digest",
        reason:
          kind === "sparse"
            ? "Sparse page — will digest but may be short"
            : "Content page",
      });
    }
  }

  const digestPages = pageEntries.filter((p) => p.action === "digest").length;
  const skippedBlank = pageEntries.filter((p) => p.action === "skip").length;

  // Never skip everything — if all look blank, digest all (classification may be wrong).
  if (digestPages === 0 && pageEntries.length > 0) {
    for (const entry of pageEntries) {
      entry.action = "digest";
      entry.reason = "Fallback: digest all pages (blank detection uncertain)";
    }
  }

  const finalDigest = pageEntries.filter((p) => p.action === "digest").length;

  return {
    inputKind: inputKindFromPath(opts.inputPath),
    pageCount: opts.pages.length,
    digestPages: finalDigest,
    skippedBlank: pageEntries.filter((p) => p.action === "skip").length,
    concurrency: Math.min(concurrency, Math.max(finalDigest, 1)),
    strategy: opts.pages.length > 1 ? "multipage" : "single",
    pages: pageEntries,
  };
}

export function summarizePlan(plan: DigestPlan): string {
  const parts = [
    `${plan.pageCount} page(s)`,
    `${plan.digestPages} to digest`,
  ];
  const pdfText = plan.pages.filter((p) => p.extractSource === "pdf_text").length;
  const vision = plan.pages.filter(
    (p) => p.action === "digest" && p.extractSource !== "pdf_text",
  ).length;
  if (pdfText > 0) parts.push(`${pdfText} pdf-text`);
  if (vision > 0 && pdfText > 0) parts.push(`${vision} vision`);
  if (plan.skippedBlank > 0) parts.push(`${plan.skippedBlank} blank skipped`);
  parts.push(`concurrency ${plan.concurrency}`);
  parts.push(plan.strategy);
  return parts.join(" · ");
}
