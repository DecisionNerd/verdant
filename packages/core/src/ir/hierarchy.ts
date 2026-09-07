/**
 * Document-wide hierarchy resolver (line candidates → roles/levels).
 * Replaces additive breadcrumb depth stacking with propose → resolve → pattern-fix.
 */

import type {
  KeepBlock,
  KeepHeading,
  KeepList,
  KeepParagraph,
  PageIr,
} from "./types.js";
import {
  diffPathHeadings,
  getPageSectionPath,
  normalizeSectionTitle,
  parseCatalogNumber,
  shouldPreserveCatalogNumbers,
  stripCatalogNumber,
} from "./sectionPath.js";
import {
  applyTocPageOffset,
  resolveSectionPath,
  tocRawTitleByNorm,
  type DocumentToc,
} from "./toc.js";
import type { DocumentStructure } from "./structureTypes.js";

function normalizeChromeText(t: string): string {
  return t
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^\p{L}\p{N}\s./\-]+/gu, "")
    .trim();
}

export type CandidateRole =
  | "heading"
  | "ol_item"
  | "ul_item"
  | "paragraph"
  | "chrome";

export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

export type RoleProposal = {
  role: CandidateRole;
  level?: HeadingLevel;
  score: number;
};

export type LineCandidate = {
  page: number;
  blockId?: string;
  /** Original keep kind when sourced from IR (list/table/… pass through). */
  sourceKind?: KeepBlock["k"];
  text: string;
  /** For list blocks: individual items become separate candidates. */
  listItems?: string[];
  orderedList?: boolean;
  /** Non-rewritable blocks (figure, table, …) carried through. */
  passthrough?: KeepBlock;
  proposed: RoleProposal[];
  chosen?: { role: CandidateRole; level?: HeadingLevel };
  uncertain?: boolean;
  reasons?: string[];
};

export type HierarchyReview = {
  v: 1;
  at: string;
  headingCount: number;
  listItemPromotions: number;
  jumpFixes: number;
  catalogNumbersStripped: number;
  /** True when leading N. titles looked like real chapter numbers in doc order. */
  catalogNumbersPreserved: boolean;
  duplicateHeadingsRemoved: number;
  templateNormalized: number;
  uncertainCount: number;
  uncertainPages: number[];
  issues: Array<{ page: number; text: string; kind: string; detail?: string }>;
};

export type HierarchyResolveOptions = {
  toc?: DocumentToc | null;
  structure?: DocumentStructure | null;
  /** Optional per-page raw PDF text for re-scoring uncertain lines. */
  pdfTextByPage?: Map<number, string> | Record<number, string>;
  /**
   * Optional async hook after PDF recheck and before structural resolve.
   * Used for targeted vision role checks on remaining uncertain lines.
   */
  visionRecheck?: (candidates: LineCandidate[]) => void | Promise<void>;
};

export type HierarchyResolveResult = {
  pages: KeepBlock[][];
  review: HierarchyReview;
  candidates: LineCandidate[];
};

/** Playbook-style repeating subsection labels — fixed relative slot under parent. */
export const TEMPLATE_SECTION_LABEL =
  /^(what it is|why it works|when to use it|make it yours|pair with|behind the data|how it works|examples?|notes?|do|don'?t|founder tip|supporting plays|insights\s*&\s*metrics)[:.]?$/i;

/** Peer labels that must share one heading level (Do vs Don't, What it is, …). */
export const TEMPLATE_PEER_LABEL =
  /^(what it is|why it works|when to use it|do|don'?t|founder tip)[:.]?$/i;

/** Major in-play section that should stay visible (not chrome). */
export const TEMPLATE_BLOCK_LABEL =
  /^(make it yours|pair with|behind the data|supporting plays|insights\s*&\s*metrics)[:.]?$/i;

/** Content section labels that must never be stripped as running chrome. */
export function isProtectedSectionLabel(text: string): boolean {
  const t = foldApostrophes(text).replace(/:$/, "").trim();
  return TEMPLATE_SECTION_LABEL.test(t);
}

function foldApostrophes(s: string): string {
  return s.replace(/[\u2018\u2019\u02BC]/g, "'");
}

function templateLockKey(text: string): string {
  const t = foldApostrophes(text).replace(/:$/, "").trim();
  if (TEMPLATE_PEER_LABEL.test(t)) return "peer:card-slots";
  if (TEMPLATE_BLOCK_LABEL.test(t)) return "block:make-section";
  return normalizeChromeText(t);
}

const OL_LETTER = /^([a-z])[.)]\s+(.+)$/i;
const OL_NUMBER = /^(\d{1,3})[.)]\s+(.+)$/;
const OL_PAREN = /^\((\d{1,3}|[a-z])\)\s+(.+)$/i;
const UL_MARK = /^[-*•]\s+(.+)$/;

function clampLvl(n: number): HeadingLevel {
  return Math.min(6, Math.max(1, Math.round(n))) as HeadingLevel;
}

export function stripListMarker(text: string): string {
  const t = text.trim();
  let m = UL_MARK.exec(t);
  if (m) return m[1]!.trim();
  m = OL_LETTER.exec(t);
  if (m) return m[2]!.trim();
  m = OL_NUMBER.exec(t);
  if (m) return m[2]!.trim();
  m = OL_PAREN.exec(t);
  if (m) return m[2]!.trim();
  return t;
}

/** Strip leading "N. " from ordered-list item text when compile will re-number. */
export function stripOrderedPrefix(item: string): string {
  return item.replace(/^\d{1,3}[.)]\s+/, "").trim();
}

function titleCaseRatio(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return 0;
  const titled = words.filter((w) => /^[\p{Lu}0-9]/u.test(w)).length;
  return titled / words.length;
}

/**
 * Score competing roles for a single line of text.
 * Shared by PDF text extract and assemble-time resolution.
 */
export function proposeFromLine(text: string): RoleProposal[] {
  const t = text.trim();
  const proposed: RoleProposal[] = [];
  if (!t) {
    proposed.push({ role: "paragraph", score: 0.1 });
    return proposed;
  }

  if (UL_MARK.test(t)) {
    proposed.push({ role: "ul_item", score: 0.95 });
    proposed.push({ role: "paragraph", score: 0.1 });
    return proposed;
  }

  if (OL_LETTER.test(t) || OL_PAREN.test(t)) {
    const body = stripListMarker(t);
    // Short lettered lines are almost always list items; long ones may be headings.
    const score = body.length <= 80 ? 0.92 : 0.55;
    proposed.push({ role: "ol_item", score });
    if (body.length <= 60 && titleCaseRatio(body) >= 0.5) {
      proposed.push({ role: "heading", level: 3, score: 0.35 });
    }
    proposed.push({ role: "paragraph", score: 0.15 });
    return proposed;
  }

  if (OL_NUMBER.test(t)) {
    const body = stripListMarker(t);
    // Long numbered prose → paragraph; short → ol or heading
    if (t.length > 90) {
      proposed.push({ role: "paragraph", score: 0.7 });
      proposed.push({ role: "ol_item", score: 0.35 });
    } else {
      proposed.push({ role: "ol_item", score: 0.75 });
      if (titleCaseRatio(body) >= 0.6 && body.length <= 50) {
        proposed.push({ role: "heading", level: 2, score: 0.45 });
      }
      proposed.push({ role: "paragraph", score: 0.2 });
    }
    return proposed;
  }

  if (TEMPLATE_SECTION_LABEL.test(t.replace(/:$/, ""))) {
    proposed.push({ role: "heading", level: 3, score: 0.9 });
    proposed.push({ role: "paragraph", score: 0.1 });
    return proposed;
  }

  // Prose signals
  if (/\.\s*$/.test(t) && t.length > 48) {
    proposed.push({ role: "paragraph", score: 0.85 });
    proposed.push({ role: "heading", level: 2, score: 0.1 });
    return proposed;
  }

  const words = t.split(/\s+/);
  if (words.length >= 12) {
    proposed.push({ role: "paragraph", score: 0.8 });
    proposed.push({ role: "heading", level: 2, score: 0.15 });
    return proposed;
  }

  if (t.length >= 2 && t.length <= 90 && words.length <= 8) {
    const ratio = titleCaseRatio(t);
    if (ratio >= 0.6) {
      proposed.push({
        role: "heading",
        level: t.length <= 28 ? 2 : 2,
        score: 0.55 + ratio * 0.2,
      });
      proposed.push({ role: "paragraph", score: 0.35 });
      return proposed;
    }
  }

  proposed.push({ role: "paragraph", score: 0.6 });
  proposed.push({ role: "heading", level: 2, score: 0.25 });
  return proposed;
}

function bestProposal(proposed: RoleProposal[]): RoleProposal {
  return proposed.reduce((a, b) => (b.score > a.score ? b : a), proposed[0]!);
}

export function proposeFromKeepBlock(
  block: KeepBlock,
  page: number,
): LineCandidate[] {
  if (
    block.k === "figure" ||
    block.k === "table" ||
    block.k === "mermaid" ||
    block.k === "math" ||
    block.k === "code" ||
    block.k === "callout" ||
    block.k === "caption"
  ) {
    return [
      {
        page,
        blockId: block.id,
        sourceKind: block.k,
        text: block.t,
        passthrough: block,
        proposed: [{ role: "paragraph", score: 1 }],
        chosen: { role: "paragraph" },
      },
    ];
  }

  if (block.k === "list") {
    return [
      {
        page,
        blockId: block.id,
        sourceKind: "list",
        text: block.items.join("\n"),
        listItems: block.items,
        orderedList: block.ordered,
        proposed: [
          {
            role: block.ordered ? "ol_item" : "ul_item",
            score: 0.99,
          },
        ],
        chosen: {
          role: block.ordered ? "ol_item" : "ul_item",
        },
      },
    ];
  }

  if (block.k === "heading") {
    const fromText = proposeFromLine(block.t);
    // Prefer IR heading level; text cues may still demote to list.
    const boosted = fromText.map((p) =>
      p.role === "heading"
        ? {
            ...p,
            level: block.lvl,
            score: Math.min(1, p.score + 0.25),
          }
        : p,
    );
    // Ensure heading is present
    if (!boosted.some((p) => p.role === "heading")) {
      boosted.push({ role: "heading", level: block.lvl, score: 0.7 });
    }
    // Letter/number list markers win over heading even if IR said heading
    const ol = boosted.find((p) => p.role === "ol_item");
    const ul = boosted.find((p) => p.role === "ul_item");
    if (ol && ol.score >= 0.7) {
      // keep ol competitive
    } else if (!ol && !ul) {
      const h = boosted.find((p) => p.role === "heading");
      if (h) h.score = Math.max(h.score, 0.8);
    }

    return [
      {
        page,
        blockId: block.id,
        sourceKind: "heading",
        text: block.t.replace(/:$/, ""),
        proposed: boosted,
      },
    ];
  }

  // paragraph
  const proposed = proposeFromLine(block.t);
  // Prefer paragraph when IR said paragraph unless list/heading cues are strong
  const top = bestProposal(proposed);
  if (top.role === "heading" && top.score < 0.7) {
    for (const p of proposed) {
      if (p.role === "paragraph") p.score = Math.max(p.score, 0.75);
    }
  }
  return [
    {
      page,
      blockId: block.id,
      sourceKind: "paragraph",
      text: block.t,
      proposed,
    },
  ];
}

function pdfMap(
  src: HierarchyResolveOptions["pdfTextByPage"],
): Map<number, string> {
  if (!src) return new Map();
  if (src instanceof Map) return src;
  return new Map(
    Object.entries(src).map(([k, v]) => [Number(k), v] as [number, string]),
  );
}

/**
 * Body heading base under a breadcrumb leaf.
 * leafDepth 0 → H1; 1 → H2; 2 → H3; 3 → H4; … clamped at H6.
 * Does not artificially cap at H3 — chapters/sections may use H4/H5 for detail.
 */
export function softBodyBase(leafDepth: number): HeadingLevel {
  if (leafDepth <= 0) return 1;
  return clampLvl(leafDepth + 1);
}

function pickRole(c: LineCandidate): RoleProposal {
  if (c.chosen) {
    return {
      role: c.chosen.role,
      level: c.chosen.level,
      score: 1,
    };
  }
  return bestProposal(c.proposed);
}

function isTemplateHeading(text: string): boolean {
  return TEMPLATE_SECTION_LABEL.test(
    foldApostrophes(text).replace(/:$/, "").trim(),
  );
}

/**
 * Recheck uncertain candidates using PDF text lines on the same page.
 * Returns number of candidates updated.
 */
export function recheckCandidatesWithPdfText(
  candidates: LineCandidate[],
  pdfTextByPage: Map<number, string> | Record<number, string>,
): number {
  const map = pdfMap(pdfTextByPage);
  let updated = 0;
  for (const c of candidates) {
    if (!c.uncertain || c.passthrough || c.listItems) continue;
    const raw = map.get(c.page);
    if (!raw?.trim()) continue;
    const lines = raw
      .split(/\n/)
      .map((l) => l.trim())
      .filter(Boolean);
    const norm = normalizeChromeText(c.text);
    const hit = lines.find((l) => normalizeChromeText(l) === norm);
    if (!hit) {
      // Nearby fuzzy: contained
      const fuzzy = lines.find(
        (l) =>
          normalizeChromeText(l).includes(norm) ||
          norm.includes(normalizeChromeText(l)),
      );
      if (!fuzzy) continue;
      const fresh = proposeFromLine(fuzzy);
      c.proposed = fresh;
      const best = bestProposal(fresh);
      c.chosen = { role: best.role, level: best.level };
      c.uncertain = best.score < 0.55;
      c.reasons = [...(c.reasons ?? []), "recheck_pdf_fuzzy"];
      updated += 1;
      continue;
    }
    const fresh = proposeFromLine(hit);
    c.proposed = fresh;
    const best = bestProposal(fresh);
    c.chosen = { role: best.role, level: best.level };
    c.uncertain = false;
    c.reasons = [...(c.reasons ?? []), "recheck_pdf"];
    updated += 1;
  }
  return updated;
}

export type VisionRecheckAnswer = {
  text: string;
  role: CandidateRole;
  level?: HeadingLevel;
};

/**
 * Apply batched vision recheck answers (role-only) onto uncertain candidates.
 */
export function applyVisionRecheckAnswers(
  candidates: LineCandidate[],
  answers: VisionRecheckAnswer[],
): number {
  let updated = 0;
  for (const ans of answers) {
    const norm = normalizeChromeText(ans.text);
    const c = candidates.find(
      (x) =>
        x.uncertain &&
        !x.passthrough &&
        normalizeChromeText(x.text) === norm,
    );
    if (!c) continue;
    c.chosen = { role: ans.role, level: ans.level };
    c.proposed = [
      { role: ans.role, level: ans.level, score: 0.95 },
      ...c.proposed,
    ];
    c.uncertain = false;
    c.reasons = [...(c.reasons ?? []), "recheck_vision"];
    updated += 1;
  }
  return updated;
}

function buildCandidatesFromPages(
  pages: PageIr[],
  strippedKeep: KeepBlock[][],
): LineCandidate[] {
  const out: LineCandidate[] = [];
  for (let i = 0; i < pages.length; i++) {
    const page = pages[i]!;
    const keep = strippedKeep[i] ?? [];
    for (const block of keep) {
      out.push(...proposeFromKeepBlock(block, page.page));
    }
  }
  return out;
}

/**
 * Resolve candidates into per-page KeepBlock arrays with sane hierarchy.
 */
export function resolveHierarchy(
  pages: PageIr[],
  strippedKeep: KeepBlock[][],
  options?: Omit<HierarchyResolveOptions, "visionRecheck">,
): HierarchyResolveResult {
  const prepared = prepareHierarchyCandidates(pages, strippedKeep, options);
  return finalizeHierarchy(prepared, options);
}

export async function resolveHierarchyAsync(
  pages: PageIr[],
  strippedKeep: KeepBlock[][],
  options?: HierarchyResolveOptions,
): Promise<HierarchyResolveResult> {
  const prepared = prepareHierarchyCandidates(pages, strippedKeep, options);
  if (options?.visionRecheck) {
    await options.visionRecheck(prepared.candidates);
  }
  return finalizeHierarchy(prepared, options);
}

type PreparedHierarchy = {
  ordered: PageIr[];
  candidates: LineCandidate[];
  pathByPageIndex: string[][];
};

function prepareHierarchyCandidates(
  pages: PageIr[],
  strippedKeep: KeepBlock[][],
  options?: HierarchyResolveOptions,
): PreparedHierarchy {
  const ordered = [...pages].sort((a, b) => a.page - b.page);
  const toc = options?.toc
    ? applyTocPageOffset(options.toc, ordered)
    : null;

  const candidates = buildCandidatesFromPages(ordered, strippedKeep);

  for (const c of candidates) {
    if (c.passthrough || c.listItems) continue;
    const best = pickRole(c);
    c.chosen = { role: best.role, level: best.level };
    const second = [...c.proposed].sort((a, b) => b.score - a.score)[1];
    if (
      second &&
      best.score - second.score < 0.12 &&
      best.role !== second.role
    ) {
      c.uncertain = true;
      c.reasons = [...(c.reasons ?? []), "close_scores"];
    }
    if (best.score < 0.5) {
      c.uncertain = true;
      c.reasons = [...(c.reasons ?? []), "low_score"];
    }
  }

  if (options?.pdfTextByPage) {
    recheckCandidatesWithPdfText(candidates, options.pdfTextByPage);
  }

  const pathByPageIndex: string[][] = [];
  let prevPath: string[] = [];
  for (let i = 0; i < ordered.length; i++) {
    const page = ordered[i]!;
    const path = resolveSectionPath({ page, prevPath, toc });
    pathByPageIndex.push(path);
    prevPath = path;
  }

  return { ordered, candidates, pathByPageIndex };
}

function finalizeHierarchy(
  prepared: PreparedHierarchy,
  options?: HierarchyResolveOptions,
): HierarchyResolveResult {
  const { ordered, candidates, pathByPageIndex } = prepared;

  const review: HierarchyReview = {
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

  promoteListRuns(candidates, review);
  assignHeadingLevels(candidates, ordered, pathByPageIndex, review);
  fixOddPatterns(candidates, review);

  if (options?.structure?.sections?.length) {
    const byNorm = new Map(
      options.structure.sections.map((s) => [
        normalizeChromeText(s.heading),
        clampLvl(s.level),
      ]),
    );
    for (const c of candidates) {
      if (c.chosen?.role !== "heading") continue;
      const lvl = byNorm.get(normalizeChromeText(c.text));
      if (lvl) c.chosen.level = lvl;
    }
  }

  const rawByNorm = tocRawTitleByNorm(options?.toc);
  const tocNumsInOrder: number[] = [];
  for (let pi = 0; pi < ordered.length; pi++) {
    const path = pathByPageIndex[pi] ?? [];
    if (path.length === 0) continue;
    const leaf = path[path.length - 1]!;
    const raw = rawByNorm.get(normalizeSectionTitle(leaf));
    const parsed = raw ? parseCatalogNumber(raw) : null;
    if (parsed) tocNumsInOrder.push(parsed.n);
  }
  const preserveCatalog = shouldPreserveCatalogNumbers(tocNumsInOrder);
  review.catalogNumbersPreserved = preserveCatalog;

  const pageKeeps = materializeKeep(
    candidates,
    ordered,
    pathByPageIndex,
    review,
    {
      preserveCatalogNumbers: preserveCatalog,
      tocRawByNorm: rawByNorm,
    },
  );

  const uncertainPages = new Set<number>();
  for (const c of candidates) {
    if (c.uncertain) {
      review.uncertainCount += 1;
      uncertainPages.add(c.page);
    }
  }
  review.uncertainPages = [...uncertainPages].sort((a, b) => a - b);
  review.headingCount = candidates.filter(
    (c) => c.chosen?.role === "heading",
  ).length;

  return { pages: pageKeeps, review, candidates };
}

function looksLikeNumberedPromptHeading(text: string): boolean {
  const t = text.trim();
  // "2. To frame the opportunity:" — subsection prompts on Make it Yours pages
  if (/^\d+[.)]\s+To\s+/i.test(t)) return true;
  if (/^\d+[.)]\s+.{10,}/.test(t) && /:$/.test(t)) return true;
  return false;
}

function promoteListRuns(
  candidates: LineCandidate[],
  review: HierarchyReview,
): void {
  // Detect runs of ol_item / lettered headings and force ol_item
  let i = 0;
  while (i < candidates.length) {
    const c = candidates[i]!;
    if (c.passthrough || c.listItems) {
      i += 1;
      continue;
    }
    if (looksLikeNumberedPromptHeading(c.text)) {
      i += 1;
      continue;
    }
    const role = c.chosen?.role;
    const letter = OL_LETTER.exec(c.text.trim());
    const looksOl =
      role === "ol_item" ||
      (role === "heading" && letter != null && c.text.length <= 80);

    if (!looksOl) {
      i += 1;
      continue;
    }

    // Grow run while consecutive lettered/numbered short lines
    let j = i;
    const run: LineCandidate[] = [];
    while (j < candidates.length) {
      const x = candidates[j]!;
      if (x.passthrough || x.listItems) break;
      if (x.page !== c.page && run.length > 0) {
        break;
      }
      if (looksLikeNumberedPromptHeading(x.text)) break;
      const r = x.chosen?.role;
      const isOlCue =
        r === "ol_item" ||
        OL_LETTER.test(x.text) ||
        (OL_NUMBER.test(x.text) && x.text.length <= 80 && !looksLikeNumberedPromptHeading(x.text));
      if (!isOlCue && run.length > 0) break;
      if (!isOlCue) break;
      run.push(x);
      j += 1;
    }

    if (run.length >= 2 || (run.length === 1 && letter && letter[1]!.toLowerCase() === "a")) {
      for (const x of run) {
        const wasHeading =
          x.sourceKind === "heading" || x.chosen?.role === "heading";
        if (wasHeading) {
          review.listItemPromotions += 1;
          review.issues.push({
            page: x.page,
            text: x.text.slice(0, 80),
            kind: "heading_to_ol",
          });
        }
        x.chosen = { role: "ol_item" };
        x.uncertain = false;
      }
    }
    i = Math.max(j, i + 1);
  }
}

function assignHeadingLevels(
  candidates: LineCandidate[],
  pages: PageIr[],
  pathByPageIndex: string[][],
  review: HierarchyReview,
): void {
  const pageIndexByNum = new Map(pages.map((p, i) => [p.page, i]));
  let prevHeadingLvl: HeadingLevel | null = null;
  /** Template label → locked level once first seen under a parent. */
  const templateLevel = new Map<string, HeadingLevel>();
  let lastNonTemplateLvl: HeadingLevel = 1;

  // Group by page for path-aware body base
  for (const c of candidates) {
    if (c.passthrough || c.listItems) continue;
    if (c.chosen?.role !== "heading") continue;

    const pi = pageIndexByNum.get(c.page) ?? 0;
    const path = pathByPageIndex[pi] ?? [];
    const pathNorms = new Set(path.map((s) => normalizeSectionTitle(s)));
    // Never drop playbook template sections as path chrome — they are real content
    // (e.g. "Make it Yours") even when a breadcrumb leaf repeats the same words.
    if (
      pathNorms.has(normalizeSectionTitle(c.text)) &&
      !isProtectedSectionLabel(c.text)
    ) {
      // Will be represented by path inject; drop as body heading
      c.chosen = { role: "chrome" };
      continue;
    }

    const visionLvl =
      c.proposed.find((p) => p.role === "heading")?.level ??
      c.chosen.level ??
      pickRole(c).level ??
      2;
    const base = softBodyBase(path.length);

    let lvl: HeadingLevel;
    if (isTemplateHeading(c.text)) {
      const key = templateLockKey(c.text);
      const locked = templateLevel.get(key);
      if (locked) {
        lvl = locked;
        review.templateNormalized += 1;
      } else if (
        TEMPLATE_BLOCK_LABEL.test(foldApostrophes(c.text).replace(/:$/, "").trim())
      ) {
        // Make it Yours etc. sit at body base under the play leaf
        lvl = base;
        templateLevel.set(key, lvl);
        review.templateNormalized += 1;
      } else {
        // Do / Don't / What it is — peers share one level under the play
        lvl = clampLvl(base + 1);
        templateLevel.set(key, lvl);
        review.templateNormalized += 1;
      }
    } else {
      // Relative to page's vision levels: place min at leaf+1, preserve offsets
      const pageHeadings = candidates.filter(
        (x) =>
          x.page === c.page &&
          (x.chosen?.role === "heading" ||
            x.proposed.some((p) => p.role === "heading")) &&
          !pathNorms.has(normalizeSectionTitle(x.text)) &&
          x.chosen?.role !== "ol_item" &&
          x.chosen?.role !== "ul_item" &&
          x.chosen?.role !== "chrome",
      );
      const visionLvls = pageHeadings.map(
        (x) =>
          x.proposed.find((p) => p.role === "heading")?.level ??
          x.chosen?.level ??
          2,
      );
      const minVision = visionLvls.length ? Math.min(...visionLvls) : visionLvl;
      const offset = Math.max(0, visionLvl - minVision);
      lvl = clampLvl(base + offset);
      lastNonTemplateLvl = lvl;
    }

    // Allow skipped levels (H1→H3 is valid). Only flag steep jumps (> +2) for review.
    if (prevHeadingLvl != null && lvl > prevHeadingLvl + 2) {
      review.jumpFixes += 1;
      review.issues.push({
        page: c.page,
        text: c.text.slice(0, 80),
        kind: "level_jump_steep",
        detail: `${prevHeadingLvl}→${lvl}`,
      });
      // Do not auto-compress — real docs use H4/H5 under chapters.
    } else if (prevHeadingLvl != null && lvl > prevHeadingLvl + 1) {
      review.issues.push({
        page: c.page,
        text: c.text.slice(0, 80),
        kind: "level_skip",
        detail: `${prevHeadingLvl}→${lvl}`,
      });
    }

    c.chosen = { role: "heading", level: lvl };
    prevHeadingLvl = lvl;
  }

  // Path inject levels also advance prevHeadingLvl when materializing
}

function fixOddPatterns(
  candidates: LineCandidate[],
  review: HierarchyReview,
): void {
  let prevHeading: LineCandidate | null = null;
  for (const c of candidates) {
    if (c.chosen?.role !== "heading") continue;

    // Duplicate adjacent (ignore catalog number differences)
    if (
      prevHeading &&
      normalizeSectionTitle(prevHeading.text) === normalizeSectionTitle(c.text) &&
      prevHeading.chosen?.level === c.chosen.level
    ) {
      c.chosen = { role: "chrome" };
      review.duplicateHeadingsRemoved += 1;
      review.issues.push({
        page: c.page,
        text: c.text.slice(0, 80),
        kind: "duplicate_adjacent",
      });
      continue;
    }

    // Conflicting levels for same title later — align to first
    if (prevHeading) {
      // handled via template map already
    }

    // Flag steep jumps only; do not auto-flatten (H1→H3 and H3→H5 are allowed)
    if (
      prevHeading?.chosen?.level != null &&
      c.chosen.level != null &&
      c.chosen.level > prevHeading.chosen.level + 2
    ) {
      review.jumpFixes += 1;
      review.issues.push({
        page: c.page,
        text: c.text.slice(0, 80),
        kind: "level_jump_steep",
        detail: `${prevHeading.chosen.level}→${c.chosen.level}`,
      });
    }

    prevHeading = c.chosen.role === "heading" ? c : prevHeading;
  }

  // Same title at conflicting levels → align to mode/min
  const byTitle = new Map<string, HeadingLevel[]>();
  for (const c of candidates) {
    if (c.chosen?.role !== "heading" || c.chosen.level == null) continue;
    const k = normalizeChromeText(c.text);
    const arr = byTitle.get(k) ?? [];
    arr.push(c.chosen.level);
    byTitle.set(k, arr);
  }
  for (const [title, lvls] of byTitle) {
    const uniq = [...new Set(lvls)];
    if (uniq.length <= 1) continue;
    const target = Math.min(...uniq) as HeadingLevel;
    for (const c of candidates) {
      if (c.chosen?.role !== "heading") continue;
      if (normalizeChromeText(c.text) !== title) continue;
      if (c.chosen.level !== target) {
        c.chosen.level = target;
        review.issues.push({
          page: c.page,
          text: c.text.slice(0, 80),
          kind: "level_conflict",
          detail: `→${target}`,
        });
      }
    }
  }
}

function materializeKeep(
  candidates: LineCandidate[],
  pages: PageIr[],
  pathByPageIndex: string[][],
  review: HierarchyReview,
  catalogOpts?: {
    preserveCatalogNumbers: boolean;
    tocRawByNorm: Map<string, string>;
  },
): KeepBlock[][] {
  const byPage = new Map<number, LineCandidate[]>();
  for (const c of candidates) {
    const arr = byPage.get(c.page) ?? [];
    arr.push(c);
    byPage.set(c.page, arr);
  }

  const preserve = catalogOpts?.preserveCatalogNumbers ?? false;
  const tocRaw = catalogOpts?.tocRawByNorm ?? new Map<string, string>();
  const displayTitle = (cleaned: string): string | undefined => {
    if (!preserve) return undefined;
    const raw = tocRaw.get(normalizeSectionTitle(cleaned));
    if (!raw) return undefined;
    return parseCatalogNumber(raw) ? raw : undefined;
  };

  const out: KeepBlock[][] = [];
  let prevPath: string[] = [];
  let idSeq = 0;
  const nextId = (page: number, kind: string) =>
    `h${page}-${kind}-${++idSeq}`;

  for (let pi = 0; pi < pages.length; pi++) {
    const page = pages[pi]!;
    const path = pathByPageIndex[pi] ?? [];
    const pageCand = byPage.get(page.page) ?? [];
    const keep: KeepBlock[] = [];

    // Soft path inject: only segments not already present as resolved headings
    const existingNorms = new Set(
      pageCand
        .filter((c) => c.chosen?.role === "heading")
        .map((c) => normalizeSectionTitle(c.text)),
    );
    const injected = diffPathHeadings(prevPath, path, { displayTitle }).filter(
      (h) => !existingNorms.has(normalizeSectionTitle(h.t)),
    );
    for (let i = 0; i < injected.length; i++) {
      const h = injected[i]!;
      keep.push({
        id: nextId(page.page, "sp"),
        k: "heading",
        lvl: h.lvl,
        t: h.t,
        z: "body",
      });
    }

    // Emit candidates; merge consecutive ol/ul into lists
    let li = 0;
    while (li < pageCand.length) {
      const c = pageCand[li]!;
      if (c.passthrough) {
        keep.push(structuredClone(c.passthrough));
        li += 1;
        continue;
      }
      if (c.chosen?.role === "chrome") {
        li += 1;
        continue;
      }

      if (c.listItems) {
        const items = c.listItems.map((it) =>
          c.orderedList ? stripOrderedPrefix(it) : it.trim(),
        );
        const list: KeepList = {
          id: c.blockId ?? nextId(page.page, "list"),
          k: "list",
          ordered: c.orderedList,
          items,
          z: "body",
          t: items.join("\n"),
        };
        keep.push(list);
        li += 1;
        continue;
      }

      const role = c.chosen?.role ?? "paragraph";
      if (role === "ol_item" || role === "ul_item") {
        const ordered = role === "ol_item";
        const items: string[] = [];
        while (li < pageCand.length) {
          const x = pageCand[li]!;
          if (x.passthrough || x.listItems) break;
          if (x.chosen?.role !== role) break;
          items.push(
            ordered
              ? stripOrderedPrefix(stripListMarker(x.text))
              : stripListMarker(x.text),
          );
          li += 1;
        }
        keep.push({
          id: nextId(page.page, ordered ? "ol" : "ul"),
          k: "list",
          ordered,
          items,
          z: "body",
          t: items.join("\n"),
        });
        continue;
      }

      if (role === "heading") {
        const rawTitle = c.text.replace(/:$/, "").trim();
        const parsed = parseCatalogNumber(rawTitle);
        let title = rawTitle;
        if (parsed) {
          // Numbers that appear on the page IR are source text — keep them.
          // Numbers we would only invent from a shuffled TOC are not on IR.
          title = rawTitle;
        } else if (preserve) {
          // Prefer TOC chapter number when sequence is coherent ("see Chapter 7").
          const numbered = displayTitle(rawTitle);
          if (numbered) title = numbered;
        }
        if (title !== rawTitle && parseCatalogNumber(title)) {
          // Attached a coherent chapter number from TOC
        } else if (parsed && !preserve) {
          // Shuffled TOC world: still keep IR-sourced numbers (page fidelity).
          // If we ever need to strip IR numbers, gate on a stronger signal.
        }
        // When !preserve and path inject already used cleaned titles, leave IR as-is.
        const h: KeepHeading = {
          id: c.blockId ?? nextId(page.page, "h"),
          k: "heading",
          lvl: c.chosen?.level ?? 2,
          t: title,
          z: "body",
        };
        keep.push(h);
        li += 1;
        continue;
      }

      const p: KeepParagraph = {
        id: c.blockId ?? nextId(page.page, "p"),
        k: "paragraph",
        t: c.text.trim(),
        z: "body",
      };
      keep.push(p);
      li += 1;
    }

    // Do not strip path-injected headings — strip only applied earlier via
    // chrome-role on path-matching body candidates.
    out.push(keep);
    prevPath = path;
  }

  void review;
  return out;
}

/** Convenience: resolve from full PageIr[] after chrome strip is done externally. */
export function resolvePagesHierarchy(
  pages: PageIr[],
  strippedKeep: KeepBlock[][],
  options?: HierarchyResolveOptions,
): HierarchyResolveResult {
  return resolveHierarchy(pages, strippedKeep, options);
}

/** Expose path helper for tests. */
export function pagePathForTests(page: PageIr): string[] {
  return getPageSectionPath(page);
}
