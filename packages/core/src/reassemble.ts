import fs from "node:fs/promises";
import path from "node:path";
import { resolveProviderConfig } from "./llm.js";
import { markdownToHtmlDocument } from "./html.js";
import { outputsDir, toHostRelative } from "./paths.js";
import { inspectRunOutputs } from "./outputs.js";
import { assembleDocumentFromIrWithReviewAsync, type StripMode } from "./ir/assemble.js";
import {
  applyFigureReviewFilters,
  fixPageFiguresWithVision,
  pageNeedsFigureFix,
  persistFixedPageIr,
  selectPagesForFigureFix,
} from "./ir/figureFix.js";
import { parsePageIr } from "./ir/parse.js";
import { inferDocumentStructure } from "./ir/structure.js";
import type { PageIr } from "./ir/types.js";
import type { DocumentStructure, RestitchPreset } from "./ir/structureTypes.js";
import {
  figureNeedsRecrop,
  lookupFigureReview,
} from "./ir/structureTypes.js";
import {
  applyTocPageOffset,
  emptyDocumentToc,
  extractDocumentToc,
  readTocJson,
  writeTocJson,
  type DocumentToc,
} from "./ir/toc.js";
import { readRunReview } from "./review.js";
import { withSpan } from "./observability.js";
import type { PageImage } from "./normalize.js";

function pageIrName(pageNumber: number): string {
  return `page-${String(pageNumber).padStart(3, "0")}.ir.json`;
}

function pagePngName(pageNumber: number): string {
  return `page-${String(pageNumber).padStart(3, "0")}.png`;
}

function stripModeForPreset(preset: RestitchPreset): StripMode {
  switch (preset) {
    case "too_short":
      return "lenient";
    case "too_much_garbage":
      return "aggressive";
    default:
      return "normal";
  }
}

async function loadPageIrs(runDir: string): Promise<PageIr[]> {
  const pagesDir = path.join(runDir, "pages");
  let names: string[];
  try {
    names = await fs.readdir(pagesDir);
  } catch {
    throw new Error("No pages/ directory — IR extract required before reassemble");
  }
  const irFiles = names.filter((n) => /^page-\d+\.ir\.json$/i.test(n)).sort();
  if (irFiles.length === 0) {
    throw new Error("No page-*.ir.json files — re-digest with VERDANT_EXTRACT_MODE=ir");
  }
  const pages: PageIr[] = [];
  for (const name of irFiles) {
    pages.push(parsePageIr(await fs.readFile(path.join(pagesDir, name), "utf8")));
  }
  return pages.sort((a, b) => a.page - b.page);
}

function figureNotesForPage(
  review: Awaited<ReturnType<typeof readRunReview>>,
  pageNumber: number,
  ir: PageIr,
  prompt?: string,
): string {
  const parts: string[] = [];
  const pr = review.pages[String(pageNumber)];
  if (pr?.comments?.length) {
    parts.push(...pr.comments.map((c) => c.text));
  }
  if (pr?.figures) {
    for (const block of ir.keep) {
      if (block.k !== "figure") continue;
      const fr = lookupFigureReview(pr, { id: block.id, artifact: block.artifact });
      if (!fr) continue;
      const tagBits = fr.tags.length ? `tags=${fr.tags.join(",")}` : "";
      const comments = fr.comments.map((c) => c.text).join("; ");
      const label = block.id || block.artifact || "figure";
      if (figureNeedsRecrop(fr) || comments || tagBits) {
        parts.push(
          `figure ${label}: ${[tagBits, comments].filter(Boolean).join(" · ")}`.trim(),
        );
      }
    }
  }
  if (review.document.comments?.length) {
    parts.push(...review.document.comments.map((c) => c.text));
  }
  if (prompt?.trim()) parts.push(prompt.trim());
  return parts.join(" · ");
}

function applyReviewFigureFilters(
  pages: PageIr[],
  review: Awaited<ReturnType<typeof readRunReview>>,
): PageIr[] {
  return pages.map((ir) =>
    applyFigureReviewFilters(ir, review.pages[String(ir.page)]),
  );
}

export type ReassembleOptions = {
  preset?: RestitchPreset;
  prompt?: string;
  /** Skip LLM structure and use code assemble only. */
  codeOnly?: boolean;
  /** Skip vision figure fix even when tags/prompt request it. */
  skipFigureFix?: boolean;
  writeHtml?: boolean;
};

export type ReassembleResult = {
  runId: string;
  markdown: string;
  html?: string;
  markdownChars: number;
  structure?: DocumentStructure;
  preset: RestitchPreset;
  figurePagesFixed?: number[];
  outputs: Awaited<ReturnType<typeof inspectRunOutputs>>;
};

/**
 * Re-run optional vision figure fix, structure inference, and code assemble.
 * Does not re-digest full page text unless figure fix replaces figure blocks.
 */
export async function reassembleRun(
  runId: string,
  opts: ReassembleOptions = {},
): Promise<ReassembleResult> {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(runId)) {
    throw new Error("Invalid runId");
  }
  const preset = opts.preset ?? "default";
  const runDir = path.join(outputsDir(), runId);
  let pages = await loadPageIrs(runDir);
  const review = await readRunReview(runId);
  const config = resolveProviderConfig();
  const stripMode = stripModeForPreset(preset);
  const artifactsDir = path.join(runDir, "artifacts");

  let documentToc: DocumentToc =
    (await readTocJson(runDir)) ?? emptyDocumentToc();
  if (!documentToc.entries.length) {
    // Attempt once if toc.json missing/empty and early rasters exist.
    const earlyImages: PageImage[] = [];
    for (let n = 1; n <= Math.min(5, pages.length); n++) {
      const png = path.join(artifactsDir, pagePngName(n));
      try {
        await fs.access(png);
        earlyImages.push({
          index: n - 1,
          pageNumber: n,
          path: png,
          mimeType: "image/png",
        });
      } catch {
        // skip missing
      }
    }
    if (earlyImages.length > 0) {
      documentToc = await withSpan("toc", async (span) => {
        span.update({
          input: { sourcePages: earlyImages.map((p) => p.pageNumber) },
        });
        const toc = await extractDocumentToc({ config, pageImages: earlyImages });
        await writeTocJson(runDir, toc);
        span.update({ output: { entries: toc.entries.length } });
        return toc;
      });
    } else if (!(await readTocJson(runDir))) {
      await writeTocJson(runDir, documentToc);
    }
  }

  documentToc = applyTocPageOffset(documentToc, pages);

  let figurePagesFixed: number[] = [];
  if (!opts.skipFigureFix) {
    const targets = await selectPagesForFigureFix({
      pages,
      review,
      prompt: opts.prompt,
      runDir,
    });
    if (targets.length > 0) {
      figurePagesFixed = await withSpan("figure-fix", async (span) => {
        span.update({ input: { pages: targets } });
        const fixedPages: number[] = [];
        for (const pageNumber of targets) {
          const idx = pages.findIndex((p) => p.page === pageNumber);
          if (idx < 0) continue;
          const pageImagePath = path.join(artifactsDir, pagePngName(pageNumber));
          try {
            await fs.access(pageImagePath);
          } catch {
            continue;
          }
          const beforeBroken = await pageNeedsFigureFix(pages[idx]!, runDir);
          const fixed = await fixPageFiguresWithVision({
            config,
            ir: pages[idx]!,
            pageImagePath,
            artifactsDir,
            note: figureNotesForPage(review, pageNumber, pages[idx]!, opts.prompt),
          });
          pages[idx] = fixed;
          await persistFixedPageIr({ runDir, ir: fixed });
          const afterBroken = await pageNeedsFigureFix(fixed, runDir);
          if (beforeBroken && !afterBroken) {
            fixedPages.push(pageNumber);
          } else if (
            fixed.keep.some(
              (b) => b.k === "figure" && typeof b.artifact === "string" && b.artifact.length > 0,
            )
          ) {
            // Crop wrote at least one artifact even if other figures remain broken.
            fixedPages.push(pageNumber);
          }
        }
        span.update({ output: { fixedPages } });
        return fixedPages;
      });
      // Reload from disk so assemble uses persisted IR
      pages = await loadPageIrs(runDir);
    }
  }

  // Drop figures tagged not_useful (in-memory only for this assemble; IR on disk stays).
  pages = applyReviewFigureFilters(pages, review);

  let structure: DocumentStructure | undefined;
  if (!opts.codeOnly && pages.length > 0) {
    structure = await withSpan("structure", async (span) => {
      span.update({ input: { pages: pages.length, preset } });
      const s = await inferDocumentStructure({
        config,
        pages,
        preset,
        prompt: opts.prompt,
        review,
        toc: documentToc,
      });
      await fs.writeFile(
        path.join(runDir, "structure.json"),
        JSON.stringify(s, null, 2),
        "utf8",
      );
      span.update({
        output: {
          sections: s.sections.length,
          chromePatterns: s.chromePatterns.length,
        },
      });
      return s;
    });
  }

  const markdown = await withSpan("stitch", async (span) => {
    span.update({
      input: {
        parts: pages.length,
        mode: "ir-reassemble",
        preset,
        stripMode,
        figurePagesFixed: figurePagesFixed.length,
      },
    });
    const { markdown: md, review: hierarchyReview } =
      await assembleDocumentFromIrWithReviewAsync(pages, {
        structure,
        stripMode,
        toc: documentToc,
        visionRecheck: {
          config,
          runDir,
        },
      });
    await fs.writeFile(
      path.join(runDir, "hierarchy-review.json"),
      JSON.stringify(hierarchyReview, null, 2),
      "utf8",
    );
    span.update({
      output: {
        chars: md.length,
        hierarchy: {
          promotions: hierarchyReview.listItemPromotions,
          jumpFixes: hierarchyReview.jumpFixes,
          uncertain: hierarchyReview.uncertainCount,
        },
      },
    });
    return md;
  });

  const mdPath = path.join(runDir, "document.md");
  await fs.writeFile(mdPath, markdown, "utf8");

  let htmlPath: string | undefined;
  const wantHtml =
    opts.writeHtml === true ||
    (await fs
      .access(path.join(runDir, "document.html"))
      .then(() => true)
      .catch(() => false));
  if (wantHtml) {
    htmlPath = path.join(runDir, "document.html");
    const html = await markdownToHtmlDocument(markdown, `Verdant ${runId}`);
    await fs.writeFile(htmlPath, html, "utf8");
  }

  await fs.writeFile(
    path.join(runDir, "reassemble.json"),
    JSON.stringify(
      {
        v: 1,
        at: new Date().toISOString(),
        preset,
        prompt: opts.prompt ?? "",
        stripMode,
        markdownChars: markdown.length,
        figurePagesFixed,
      },
      null,
      2,
    ),
    "utf8",
  );

  const outputs = await inspectRunOutputs(runId);
  return {
    runId,
    markdown: toHostRelative(mdPath),
    html: htmlPath ? toHostRelative(htmlPath) : undefined,
    markdownChars: markdown.length,
    structure,
    preset,
    figurePagesFixed,
    outputs,
  };
}

export { pageIrName };
