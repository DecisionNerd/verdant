import type { DiscardBlock, KeepBlock, KeepHeading, PageIr } from "./types.js";

function normSeg(s: string): string {
  return s
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^\p{L}\p{N}\s./\-]+/gu, "")
    .trim();
}

/**
 * Strip leading catalog / TOC list numbers ("1. Onboarding", "28) Fail Safe").
 * Matching always ignores these; display may keep them when they are real
 * chapter numbers that ascend in document order (see shouldPreserveCatalogNumbers).
 */
export function stripCatalogNumber(title: string): string {
  return title.replace(/^\d{1,3}[.)]\s+/, "").trim();
}

/** Leading "N." / "N)" catalog or chapter number, if present. */
export function parseCatalogNumber(
  title: string,
): { n: number; rest: string } | null {
  const m = /^(\d{1,3})[.)]\s+(.+)$/.exec(title.trim());
  if (!m) return null;
  return { n: Number(m[1]), rest: m[2]!.trim() };
}

/**
 * Decide whether leading numbers on section titles are real chapter numbers
 * (preserve for “see Chapter 7”) vs shuffled TOC list indices (strip).
 *
 * Preserves when numbers never go backwards except a restart at 1 (new part).
 * Strips when document/page order shows many out-of-order catalog indices
 * (e.g. play cards listed 1…33 in the TOC but appearing in a different order).
 */
export function shouldPreserveCatalogNumbers(numsInDocOrder: number[]): boolean {
  if (numsInDocOrder.length < 2) return true;
  let inversions = 0;
  let transitions = 0;
  for (let i = 1; i < numsInDocOrder.length; i++) {
    const prev = numsInDocOrder[i - 1]!;
    const cur = numsInDocOrder[i]!;
    transitions += 1;
    if (cur < prev && cur !== 1) inversions += 1;
  }
  if (inversions === 0) return true;
  // Tolerate a rare glitch; playbook-style shuffle fails this hard.
  return inversions / transitions < 0.1;
}

/** Normalize a section title for equality (catalog numbers ignored). */
export function normalizeSectionTitle(title: string): string {
  return normSeg(stripCatalogNumber(title));
}

/** Parse "Play > Fail Safe > Make it Yours" into section segments. */
export function parseBreadcrumbText(text: string): string[] {
  const t = text.trim();
  if (!t || !/>/.test(t)) return [];
  return t
    .split(/\s*>\s*/)
    .map((s) => stripCatalogNumber(s.trim()))
    .filter(Boolean);
}

/** Best-effort section path from page chrome (running_header / nav_chrome). */
export function parseSectionPathFromDiscard(discard: DiscardBlock[]): string[] {
  for (const d of discard) {
    if (d.k !== "running_header" && d.k !== "nav_chrome") continue;
    if (d.z !== "top" && d.z !== "margin" && d.z !== "overlay") continue;
    const path = parseBreadcrumbText(d.t);
    if (path.length > 0) return path;
  }
  return [];
}

export function getPageSectionPath(page: PageIr): string[] {
  return parseSectionPathFromDiscard(page.discard);
}

/** Headings to inject when the breadcrumb path changes (only new/changed tail). */
export function diffPathHeadings(
  prevPath: string[],
  currPath: string[],
  opts?: {
    /** Prefer a display title (e.g. numbered chapter) for a cleaned path segment. */
    displayTitle?: (cleanedSegment: string) => string | undefined;
  },
): Array<{ lvl: KeepHeading["lvl"]; t: string }> {
  if (currPath.length === 0) return [];
  let diverge = 0;
  while (
    diverge < prevPath.length &&
    diverge < currPath.length &&
    normalizeSectionTitle(prevPath[diverge]!) ===
      normalizeSectionTitle(currPath[diverge]!)
  ) {
    diverge += 1;
  }
  if (diverge >= currPath.length) return [];
  const out: Array<{ lvl: KeepHeading["lvl"]; t: string }> = [];
  for (let i = diverge; i < currPath.length; i++) {
    const lvl = Math.min(6, i + 1) as KeepHeading["lvl"];
    const cleaned = stripCatalogNumber(currPath[i]!);
    const preferred = opts?.displayTitle?.(cleaned);
    out.push({ lvl, t: preferred?.trim() || cleaned });
  }
  return out;
}

/** Drop keep headings that duplicate breadcrumb segments (avoid Play/Fail Safe twice). */
export function stripHeadingsMatchingPath(
  keep: KeepBlock[],
  path: string[],
): KeepBlock[] {
  if (path.length === 0) return keep;
  const pathNorms = new Set(path.map(normalizeSectionTitle));
  return keep.filter((b) => {
    if (b.k !== "heading") return true;
    return !pathNorms.has(normalizeSectionTitle(b.t));
  });
}

/**
 * Rebase in-page headings under the breadcrumb depth.
 * path depth 2 → first body heading becomes H3, etc.
 *
 * @deprecated Prefer `resolveHierarchy` in hierarchy.ts — additive path stacking
 * produced insane H4/H5 depths on playbook-style docs. Kept for unit tests.
 */
export function rebasePageHeadings(
  keep: KeepBlock[],
  pathDepth: number,
): KeepBlock[] {
  if (pathDepth <= 0) return keep;
  const headingLvls = keep
    .filter((b): b is KeepHeading => b.k === "heading")
    .map((b) => b.lvl);
  if (headingLvls.length === 0) return keep;
  const minLvl = Math.min(...headingLvls);
  const base = Math.min(6, pathDepth + 1) as KeepHeading["lvl"];
  return keep.map((b) => {
    if (b.k !== "heading") return b;
    const offset = b.lvl - minLvl;
    const lvl = Math.min(6, base + offset) as KeepHeading["lvl"];
    return { ...b, lvl };
  });
}

export function injectPathHeadings(
  keep: KeepBlock[],
  prevPath: string[],
  currPath: string[],
  pageNumber: number,
): KeepBlock[] {
  const injected = diffPathHeadings(prevPath, currPath).map((h, i) => ({
    id: `sp${pageNumber}-${i}`,
    k: "heading" as const,
    lvl: h.lvl,
    t: h.t,
    z: "body" as const,
  }));
  return [...injected, ...keep];
}
