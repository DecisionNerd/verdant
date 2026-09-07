/**
 * Targeted vision recheck for uncertain hierarchy candidates.
 * Asks only for role/level — not a full page re-digest.
 */

import fs from "node:fs/promises";
import path from "node:path";
import type { ResolvedProviderConfig } from "./providers.js";
import { completeVisionChat } from "./providers.js";
import {
  applyVisionRecheckAnswers,
  type CandidateRole,
  type HeadingLevel,
  type LineCandidate,
  type VisionRecheckAnswer,
} from "./ir/hierarchy.js";

const HIERARCHY_RECHECK_SYSTEM = `You classify short document lines for structure.
For each line, return JSON only:
{"answers":[{"text":"...","role":"heading"|"ol_item"|"ul_item"|"paragraph","level":1-6?}]}
Rules:
- role heading: section/subsection titles
- role ol_item / ul_item: list entries (including "a."/"b."/"1." lines)
- role paragraph: body prose
- level only for headings (1-6)
- Do not rewrite the text; copy it exactly from the input list.`;

export type HierarchyVisionRecheckOpts = {
  config: ResolvedProviderConfig;
  candidates: LineCandidate[];
  /** run output dir containing artifacts/page-NNN.png */
  runDir: string;
  signal?: AbortSignal;
  /** Max uncertain lines to send (default 40). */
  maxLines?: number;
};

function pagePng(runDir: string, page: number): string {
  return path.join(
    runDir,
    "artifacts",
    `page-${String(page).padStart(3, "0")}.png`,
  );
}

async function loadPageImage(
  runDir: string,
  page: number,
): Promise<{ path: string; mimeType: "image/png"; base64: string } | null> {
  const png = pagePng(runDir, page);
  try {
    const buf = await fs.readFile(png);
    return {
      path: png,
      mimeType: "image/png",
      base64: buf.toString("base64"),
    };
  } catch {
    return null;
  }
}

function parseAnswers(raw: string): VisionRecheckAnswer[] {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start < 0 || end <= start) return [];
  try {
    const json = JSON.parse(raw.slice(start, end + 1)) as {
      answers?: Array<{
        text?: string;
        role?: string;
        level?: number;
      }>;
    };
    const out: VisionRecheckAnswer[] = [];
    for (const a of json.answers ?? []) {
      if (!a.text || !a.role) continue;
      const role = a.role as CandidateRole;
      if (
        role !== "heading" &&
        role !== "ol_item" &&
        role !== "ul_item" &&
        role !== "paragraph" &&
        role !== "chrome"
      ) {
        continue;
      }
      const level =
        a.level != null && a.level >= 1 && a.level <= 6
          ? (a.level as HeadingLevel)
          : undefined;
      out.push({ text: a.text, role, level });
    }
    return out;
  } catch {
    return [];
  }
}

/**
 * For uncertain candidates on pages without usable PDF text, batch a vision
 * role check (one call per page that has an image).
 * Mutates candidates via applyVisionRecheckAnswers; returns updates count.
 */
export async function recheckHierarchyWithVision(
  opts: HierarchyVisionRecheckOpts,
): Promise<number> {
  const maxLines = opts.maxLines ?? 40;
  const uncertain = opts.candidates
    .filter((c) => c.uncertain && !c.passthrough && !c.listItems)
    .slice(0, maxLines);
  if (uncertain.length === 0) return 0;

  const byPage = new Map<number, LineCandidate[]>();
  for (const c of uncertain) {
    const arr = byPage.get(c.page) ?? [];
    arr.push(c);
    byPage.set(c.page, arr);
  }

  let updated = 0;
  for (const [page, lines] of byPage) {
    const img = await loadPageImage(opts.runDir, page);
    if (!img) continue;
    const listing = lines.map((l, i) => `${i + 1}. ${l.text}`).join("\n");
    const userText = `Page ${page}. Classify each line:\n${listing}`;
    try {
      const raw = await completeVisionChat({
        config: opts.config,
        system: HIERARCHY_RECHECK_SYSTEM,
        userText,
        pages: [
          {
            mimeType: "image/png",
            base64: img.base64,
          },
        ],
        temperature: 0,
        maxTokens: 1024,
        observe: {
          name: "hierarchy-recheck",
          metadata: { page, lines: lines.length },
        },
        signal: opts.signal,
      });
      const answers = parseAnswers(raw);
      updated += applyVisionRecheckAnswers(opts.candidates, answers);
    } catch {
      // Non-fatal: leave candidates uncertain
    }
  }
  return updated;
}
