import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { completeVisionChat, type ResolvedProviderConfig } from "../providers.js";
import type { PageImage } from "../normalize.js";
import type { DigestPlan } from "../types.js";
import { extractJsonObject } from "./parse.js";
import type { KeepBlock, KeepHeading, PageIr } from "./types.js";
import { getPageSectionPath, stripCatalogNumber } from "./sectionPath.js";

export type TocEntry = {
  title: string;
  /** 1-based depth in the TOC tree. */
  level: number;
  /** Printed page number from the TOC, when visible. */
  page?: number;
  children?: TocEntry[];
};

export type DocumentToc = {
  v: 1;
  /** Raster page numbers used for the TOC vision pass. */
  sourcePages: number[];
  /** printedPage + pageOffset ≈ raster pageNumber. */
  pageOffset?: number;
  entries: TocEntry[];
};

const tocEntrySchema: z.ZodType<TocEntry> = z.lazy(() =>
  z.object({
    title: z.string().min(1),
    level: z.number().int().min(1).max(6),
    page: z.number().int().positive().optional(),
    children: z.array(tocEntrySchema).optional(),
  }),
);

export const documentTocSchema = z.object({
  v: z.literal(1),
  sourcePages: z.array(z.number().int().positive()).default([]),
  pageOffset: z.number().int().optional(),
  entries: z.array(tocEntrySchema).default([]),
});

export function emptyDocumentToc(sourcePages: number[] = []): DocumentToc {
  return { v: 1, sourcePages, entries: [] };
}

export function tocPath(runDir: string): string {
  return path.join(runDir, "toc.json");
}

export async function writeTocJson(runDir: string, toc: DocumentToc): Promise<void> {
  await fs.writeFile(tocPath(runDir), JSON.stringify(toc, null, 2), "utf8");
}

export async function readTocJson(runDir: string): Promise<DocumentToc | null> {
  try {
    const raw = JSON.parse(await fs.readFile(tocPath(runDir), "utf8"));
    return documentTocSchema.parse(raw) as DocumentToc;
  } catch {
    return null;
  }
}

/** First N non-skip digest pages (default 5) as TOC vision candidates. */
export function detectTocCandidatePages(
  pages: PageImage[],
  plan: DigestPlan,
  maxPages = 5,
): PageImage[] {
  const digestNums = new Set(
    plan.pages.filter((p) => p.action === "digest").map((p) => p.pageNumber),
  );
  const ordered = [...pages]
    .filter((p) => digestNums.has(p.pageNumber))
    .sort((a, b) => a.pageNumber - b.pageNumber);
  return ordered.slice(0, Math.min(maxPages, ordered.length));
}

const TOC_SYSTEM = `You are Verdant, extracting a table of contents from early document page images.

Look for Contents / What's Inside / Index pages with indented entries, dotted leaders, or page numbers.

Return ONLY JSON:
{
  "v": 1,
  "entries": [
    {
      "title": "section title",
      "level": 1,
      "page": 12,
      "children": [{ "title": "subsection", "level": 2, "page": 14, "children": [] }]
    }
  ]
}

Rules:
1. Preserve nesting via children and level (1 = top).
2. Include printed page numbers when visible next to entries; omit "page" if unknown.
3. Prefer the full outline over a marketing "What's Inside" blurb with no hierarchy.
4. If there is no usable TOC, return {"v":1,"entries":[]}.
5. Do not invent sections that are not visible.
6. Omit leading list/catalog numbers from titles — use "Onboarding" not "1. Onboarding".`;

export async function extractDocumentToc(opts: {
  config: ResolvedProviderConfig;
  pageImages: PageImage[];
}): Promise<DocumentToc> {
  const { config, pageImages } = opts;
  const sourcePages = pageImages.map((p) => p.pageNumber);
  if (pageImages.length === 0) return emptyDocumentToc();

  const visionPages: Array<{ mimeType: string; base64: string }> = [];
  for (const page of pageImages) {
    const buf = await fs.readFile(page.path);
    const ext = path.extname(page.path).toLowerCase();
    const mimeType =
      page.mimeType ??
      (ext === ".jpg" || ext === ".jpeg"
        ? "image/jpeg"
        : ext === ".webp"
          ? "image/webp"
          : "image/png");
    visionPages.push({
      mimeType,
      base64: (page.base64 ?? buf.toString("base64")),
    });
  }

  try {
    const raw = await completeVisionChat({
      config,
      system: TOC_SYSTEM,
      userText: [
        `These are raster pages ${sourcePages.join(", ")} (early document pages).`,
        "Extract the table of contents if present. JSON only.",
      ].join("\n"),
      pages: visionPages,
      temperature: 0.1,
      maxTokens: 4096,
      observe: {
        name: "extract-document-toc",
        metadata: { kind: "toc", sourcePages: sourcePages.join(",") },
      },
    });
    const parsed = documentTocSchema.parse({
      ...JSON.parse(extractJsonObject(raw)),
      v: 1,
      sourcePages,
    });
    return {
      v: 1,
      sourcePages,
      pageOffset: parsed.pageOffset,
      entries: parsed.entries,
    };
  } catch {
    return emptyDocumentToc(sourcePages);
  }
}

export type FlatTocRow = {
  path: string[];
  /** Cleaned title (no leading list/chapter number) — used for matching. */
  title: string;
  /** Original TOC title, possibly including a catalog/chapter number. */
  rawTitle: string;
  printedPage?: number;
  level: number;
};

function normTitle(s: string): string {
  return stripCatalogNumber(s)
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^\p{L}\p{N}\s./\-]+/gu, "")
    .trim();
}

export function flattenToc(entries: TocEntry[], parentPath: string[] = []): FlatTocRow[] {
  const out: FlatTocRow[] = [];
  for (const e of entries) {
    const rawTitle = e.title.trim();
    const title = stripCatalogNumber(rawTitle);
    const pathSegs = [...parentPath, title];
    out.push({
      path: pathSegs,
      title,
      rawTitle,
      printedPage: e.page,
      level: e.level,
    });
    if (e.children?.length) {
      out.push(...flattenToc(e.children, pathSegs));
    }
  }
  return out;
}

/**
 * Map cleaned section title → best raw TOC title (with number) for display.
 * First occurrence wins.
 */
export function tocRawTitleByNorm(toc: DocumentToc | null | undefined): Map<string, string> {
  const map = new Map<string, string>();
  if (!toc?.entries?.length) return map;
  for (const row of flattenToc(toc.entries)) {
    const key = normTitle(row.title);
    if (!key || map.has(key)) continue;
    map.set(key, row.rawTitle);
  }
  return map;
}

export function formatTocBrief(toc: DocumentToc | null | undefined): string {
  if (!toc?.entries?.length) return "";
  const flat = flattenToc(toc.entries);
  const lines = flat.slice(0, 80).map((r) => {
    const indent = "  ".repeat(Math.max(0, r.level - 1));
    const pg = r.printedPage != null ? ` p.${r.printedPage}` : "";
    return `${indent}${r.title}${pg}`;
  });
  const offset =
    toc.pageOffset != null ? `pageOffset=${toc.pageOffset}` : "pageOffset=unknown";
  return `Document TOC (${offset}, sources=[${toc.sourcePages.join(",")}]:\n${lines.join("\n")}`;
}

/**
 * Match TOC titles against early-page headings/breadcrumbs to estimate
 * printedPage + offset ≈ raster pageNumber.
 */
export function estimatePageOffset(toc: DocumentToc, pages: PageIr[]): number | undefined {
  const flat = flattenToc(toc.entries).filter((r) => r.printedPage != null);
  if (flat.length === 0 || pages.length === 0) return undefined;

  const ordered = [...pages].sort((a, b) => a.page - b.page);
  const votes = new Map<number, number>();

  for (const row of flat.slice(0, 24)) {
    const target = normTitle(row.title);
    if (!target) continue;
    for (const page of ordered.slice(0, 12)) {
      const titles: string[] = [];
      for (const b of page.keep) {
        if (b.k === "heading") titles.push(b.t);
      }
      const crumb = getPageSectionPath(page);
      if (crumb.length) titles.push(crumb[crumb.length - 1]!);
      for (const t of titles) {
        const n = normTitle(t);
        if (!n) continue;
        if (n === target || n.includes(target) || target.includes(n)) {
          const offset = page.page - (row.printedPage as number);
          votes.set(offset, (votes.get(offset) ?? 0) + 1);
        }
      }
    }
  }

  let best: number | undefined;
  let bestN = 0;
  for (const [offset, n] of votes) {
    if (n > bestN) {
      best = offset;
      bestN = n;
    }
  }
  return bestN > 0 ? best : undefined;
}

export function applyTocPageOffset(toc: DocumentToc, pages: PageIr[]): DocumentToc {
  if (toc.pageOffset != null) return toc;
  const pageOffset = estimatePageOffset(toc, pages);
  if (pageOffset == null) return toc;
  return { ...toc, pageOffset };
}

function keepHeadingTitles(keep: KeepBlock[]): string[] {
  return keep
    .filter((b): b is KeepHeading => b.k === "heading")
    .map((b) => b.t.trim())
    .filter(Boolean);
}

/** Resolve a section path for raster pageNumber from TOC (page match, then fuzzy title). */
export function pathFromToc(
  toc: DocumentToc,
  pageNumber: number,
  keepHeadings: string[],
): string[] {
  const flat = flattenToc(toc.entries);
  if (flat.length === 0) return [];

  const offset = toc.pageOffset ?? 0;
  const byPage = flat.filter(
    (r) => r.printedPage != null && r.printedPage + offset === pageNumber,
  );
  if (byPage.length > 0) {
    // Deepest (longest path) wins for a given page.
    byPage.sort((a, b) => b.path.length - a.path.length);
    return byPage[0]!.path;
  }

  // Fuzzy: match last keep heading to TOC leaf titles.
  for (let i = keepHeadings.length - 1; i >= 0; i--) {
    const h = normTitle(keepHeadings[i]!);
    if (!h) continue;
    const hits = flat.filter((r) => {
      const t = normTitle(r.title);
      return t === h || t.includes(h) || h.includes(t);
    });
    if (hits.length === 1) return hits[0]!.path;
    if (hits.length > 1) {
      hits.sort((a, b) => b.path.length - a.path.length);
      return hits[0]!.path;
    }
  }

  return [];
}

/**
 * Resolve section path: breadcrumb → TOC → previous-page carry.
 */
export function resolveSectionPath(opts: {
  page: PageIr;
  prevPath: string[];
  toc?: DocumentToc | null;
}): string[] {
  const breadcrumb = getPageSectionPath(opts.page);
  if (breadcrumb.length > 0) return breadcrumb;

  if (opts.toc?.entries?.length) {
    const tocPathSegs = pathFromToc(
      opts.toc,
      opts.page.page,
      keepHeadingTitles(opts.page.keep),
    );
    if (tocPathSegs.length > 0) return tocPathSegs;
  }

  return opts.prevPath;
}
