import type { DiscardBlock, KeepBlock, PageIr } from "./types.js";
import { compileKeepMarkdown } from "./compile.js";
import type { DocumentStructure } from "./structureTypes.js";
import {
  resolveHierarchy,
  resolveHierarchyAsync,
  isProtectedSectionLabel,
  type HierarchyResolveOptions,
  type HierarchyReview,
} from "./hierarchy.js";
import type { DocumentToc } from "./toc.js";
import type { ResolvedProviderConfig } from "../providers.js";

export type StripMode = "normal" | "aggressive" | "lenient";

export type AssembleOptions = {
  structure?: DocumentStructure | null;
  stripMode?: StripMode;
  toc?: DocumentToc | null;
  /** Optional per-page PDF text for hierarchy recheck. */
  pdfTextByPage?: HierarchyResolveOptions["pdfTextByPage"];
  /** When set with runDir, uncertain lines get a targeted vision role check. */
  visionRecheck?: {
    config: ResolvedProviderConfig;
    runDir: string;
    signal?: AbortSignal;
  };
};

export type AssembleResult = {
  markdown: string;
  review: HierarchyReview;
};

function emptyReview(): HierarchyReview {
  return {
    v: 1,
    at: new Date().toISOString(),
    headingCount: 0,
    listItemPromotions: 0,
    jumpFixes: 0,
    catalogNumbersStripped: 0,
    catalogNumbersPreserved: false,
    duplicateHeadingsRemoved: 0,
    templateNormalized: 0,
    uncertainCount: 0,
    uncertainPages: [],
    issues: [],
  };
}

export function normalizeChromeText(t: string): string {
  return t
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^\p{L}\p{N}\s./\-]+/gu, "")
    .trim();
}

function discardFingerprint(block: DiscardBlock): string | null {
  const norm = normalizeChromeText(block.t);
  if (!norm && block.k !== "branding" && block.k !== "decorative") return null;
  return `${block.k}:${norm || "(mark)"}`;
}

export function buildChromeFingerprints(
  pages: PageIr[],
  opts?: { stripMode?: StripMode; extraChrome?: string[] },
): Set<string> {
  const stripMode = opts?.stripMode ?? "normal";
  const counts = new Map<string, number>();
  const strong = new Set<string>();

  for (const page of pages) {
    const seenOnPage = new Set<string>();
    for (const d of page.discard) {
      const fp = discardFingerprint(d);
      if (!fp || seenOnPage.has(fp)) continue;
      seenOnPage.add(fp);
      counts.set(fp, (counts.get(fp) ?? 0) + 1);
      if (
        d.k === "page_number" ||
        d.k === "running_header" ||
        d.k === "running_footer" ||
        d.k === "folio" ||
        d.k === "branding"
      ) {
        strong.add(fp);
      }
      if (stripMode === "aggressive") {
        strong.add(fp);
      }
    }
  }

  const out = new Set<string>();
  for (const [fp, n] of counts) {
    if (stripMode === "lenient") {
      if (fp.startsWith("page_number:") || n >= 3) out.add(fp);
      continue;
    }
    if (n >= 2 || strong.has(fp)) out.add(fp);
  }

  for (const extra of opts?.extraChrome ?? []) {
    const norm = normalizeChromeText(extra);
    if (!norm) continue;
    // Structure LLM often lists real card sections as "chrome" — never fingerprint them.
    if (isProtectedSectionLabel(extra) || isProtectedSectionLabel(norm)) continue;
    out.add(`extra:${norm}`);
  }

  return out;
}

function keepLooksLikeFingerprint(block: KeepBlock, fingerprints: Set<string>): boolean {
  if (isProtectedSectionLabel(block.t)) return false;
  const norm = normalizeChromeText(block.t);
  if (!norm) return false;

  for (const fp of fingerprints) {
    const text = fp.includes(":") ? fp.slice(fp.indexOf(":") + 1) : fp;
    if (text && text === norm) return true;
  }

  if (
    (block.z === "top" || block.z === "bottom" || block.edge) &&
    /^(page\s*\d+(\s*(of|\/)\s*\d+)?|\d{1,4}|\d+\s*\/\s*\d+)$/i.test(norm)
  ) {
    return true;
  }

  return false;
}

function isEdgeCandidate(block: KeepBlock, side: "start" | "end"): boolean {
  if (side === "start") {
    return (
      block.edge === "top" ||
      block.z === "top" ||
      block.z === "margin" ||
      block.z === "overlay"
    );
  }
  return (
    block.edge === "bottom" ||
    block.z === "bottom" ||
    block.z === "margin" ||
    block.z === "overlay"
  );
}

export function stripChromeFromKeep(
  keep: KeepBlock[],
  fingerprints: Set<string>,
  stripMode: StripMode = "normal",
): KeepBlock[] {
  let start = 0;
  let end = keep.length;
  const allowInner = stripMode === "aggressive";

  while (start < end) {
    const b = keep[start]!;
    if (
      (isEdgeCandidate(b, "start") || allowInner) &&
      keepLooksLikeFingerprint(b, fingerprints)
    ) {
      start += 1;
      continue;
    }
    if (b.k === "heading" && keepLooksLikeFingerprint(b, fingerprints) && start === 0) {
      start += 1;
      continue;
    }
    break;
  }

  while (end > start) {
    const b = keep[end - 1]!;
    if (
      (isEdgeCandidate(b, "end") || allowInner) &&
      keepLooksLikeFingerprint(b, fingerprints)
    ) {
      end -= 1;
      continue;
    }
    break;
  }

  return keep.slice(start, end);
}

export function mergeContinuations(pagesKeep: KeepBlock[][]): KeepBlock[] {
  const out: KeepBlock[] = [];

  for (const pageKeep of pagesKeep) {
    for (const block of pageKeep) {
      const prev = out[out.length - 1];
      if (
        prev &&
        block.k === prev.k &&
        (prev as { cont?: string }).cont === "start" &&
        ((block as { cont?: string }).cont === "end" ||
          (block as { cont?: string }).cont === "mid") &&
        (block.k === "paragraph" || block.k === "list" || block.k === "table")
      ) {
        if (block.k === "paragraph" && prev.k === "paragraph") {
          prev.t = `${prev.t.trimEnd()} ${block.t.trimStart()}`;
          prev.cont =
            (block as { cont?: string }).cont === "end" ? undefined : "start";
          continue;
        }
        if (block.k === "list" && prev.k === "list") {
          prev.items = [...prev.items, ...block.items];
          prev.cont =
            (block as { cont?: string }).cont === "end" ? undefined : "start";
          continue;
        }
        if (block.k === "table" && prev.k === "table") {
          prev.rows = [...(prev.rows ?? []), ...(block.rows ?? [])];
          if (!prev.t.trim() && block.t.trim()) prev.t = block.t;
          prev.cont =
            (block as { cont?: string }).cont === "end" ? undefined : "start";
          continue;
        }
      }
      out.push(structuredClone(block));
    }
  }

  for (const b of out) {
    if ("cont" in b) delete (b as { cont?: string }).cont;
  }

  return out;
}

function applyStructureHeadingHints(
  keep: KeepBlock[],
  structure: DocumentStructure | null | undefined,
): KeepBlock[] {
  if (!structure?.sections?.length) return keep;
  const byNorm = new Map(
    structure.sections.map((s) => [normalizeChromeText(s.heading), s.level as 1|2|3|4|5|6]),
  );
  return keep.map((b) => {
    if (b.k !== "heading") return b;
    const lvl = byNorm.get(normalizeChromeText(b.t));
    if (!lvl) return b;
    return { ...b, lvl };
  });
}

/** Assemble final Markdown from page IRs — discard never compiled; code-only join. */
export function assembleDocumentFromIr(
  pages: PageIr[],
  options?: AssembleOptions,
): string {
  return assembleDocumentFromIrWithReview(pages, options).markdown;
}

/**
 * Same as assembleDocumentFromIr, but also returns hierarchy-review stats
 * (for writing hierarchy-review.json from digest/reassemble).
 * Sync path — no vision recheck (use async variant when visionRecheck is set).
 */
export function assembleDocumentFromIrWithReview(
  pages: PageIr[],
  options?: AssembleOptions,
): AssembleResult {
  if (pages.length === 0) {
    return { markdown: "", review: emptyReview() };
  }
  const ordered = [...pages].sort((a, b) => a.page - b.page);
  const stripMode = options?.stripMode ?? "normal";
  const chromePatterns = (options?.structure?.chromePatterns ?? []).filter(
    (p) => !isProtectedSectionLabel(p),
  );
  const fingerprints = buildChromeFingerprints(ordered, {
    stripMode,
    extraChrome: chromePatterns,
  });
  const stripped = ordered.map((p) =>
    stripChromeFromKeep(p.keep, fingerprints, stripMode),
  );

  const { pages: pathAwarePages, review } = resolveHierarchy(ordered, stripped, {
    toc: options?.toc,
    structure: options?.structure,
    pdfTextByPage: options?.pdfTextByPage,
  });

  return finishAssemble(pathAwarePages, review, options);
}

/** Async assemble with optional targeted vision hierarchy recheck. */
export async function assembleDocumentFromIrWithReviewAsync(
  pages: PageIr[],
  options?: AssembleOptions,
): Promise<AssembleResult> {
  if (pages.length === 0) {
    return { markdown: "", review: emptyReview() };
  }
  const ordered = [...pages].sort((a, b) => a.page - b.page);
  const stripMode = options?.stripMode ?? "normal";
  const chromePatterns = (options?.structure?.chromePatterns ?? []).filter(
    (p) => !isProtectedSectionLabel(p),
  );
  const fingerprints = buildChromeFingerprints(ordered, {
    stripMode,
    extraChrome: chromePatterns,
  });
  const stripped = ordered.map((p) =>
    stripChromeFromKeep(p.keep, fingerprints, stripMode),
  );

  const vision = options?.visionRecheck;
  const { pages: pathAwarePages, review } = await resolveHierarchyAsync(
    ordered,
    stripped,
    {
      toc: options?.toc,
      structure: options?.structure,
      pdfTextByPage: options?.pdfTextByPage,
      visionRecheck: vision
        ? async (candidates) => {
            const uncertain = candidates.some((c) => c.uncertain);
            if (!uncertain) return;
            const { recheckHierarchyWithVision } = await import(
              "../hierarchyRecheck.js"
            );
            // Only recheck pages that still lack PDF text guidance
            const pdf = options?.pdfTextByPage;
            const pdfMap =
              pdf instanceof Map
                ? pdf
                : pdf
                  ? new Map(
                      Object.entries(pdf).map(([k, v]) => [
                        Number(k),
                        v,
                      ] as [number, string]),
                    )
                  : new Map<number, string>();
            for (const c of candidates) {
              if (!c.uncertain) continue;
              if (pdfMap.get(c.page)?.trim()) {
                // PDF already had a chance; leave for pattern pass unless still close
                continue;
              }
            }
            const needVision = candidates.filter(
              (c) => c.uncertain && !pdfMap.get(c.page)?.trim(),
            );
            if (needVision.length === 0) return;
            await recheckHierarchyWithVision({
              config: vision.config,
              candidates,
              runDir: vision.runDir,
              signal: vision.signal,
            });
          }
        : undefined,
    },
  );

  return finishAssemble(pathAwarePages, review, options);
}

function finishAssemble(
  pathAwarePages: KeepBlock[][],
  review: HierarchyReview,
  options?: AssembleOptions,
): AssembleResult {
  let merged = mergeContinuations(pathAwarePages);
  merged = applyStructureHeadingHints(merged, options?.structure);

  let md = compileKeepMarkdown(merged);
  if (options?.structure?.title?.trim()) {
    const title = options.structure.title.trim();
    if (!md.startsWith(`# ${title}`)) {
      md = `# ${title}\n\n${md}`;
    }
  }
  return { markdown: md, review };
}
