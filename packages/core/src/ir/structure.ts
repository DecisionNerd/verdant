import { completeVisionChat, type ResolvedProviderConfig } from "../providers.js";
import { extractJsonObject } from "./parse.js";
import {
  buildStructureBrief,
  documentStructureSchema,
  type DocumentStructure,
  type RestitchPreset,
} from "./structureTypes.js";
import type { PageIr } from "./types.js";
import type { RunReview } from "./structureTypes.js";
import { formatTocBrief, type DocumentToc } from "./toc.js";
import { isProtectedSectionLabel } from "./hierarchy.js";

const STRUCTURE_SYSTEM = `You are Verdant, inferring reading structure for a multipage document from dual-stream IR briefs.

Input gives per-page keep headings + discard chrome (running headers/footers/page numbers/branding), and optionally a document TOC.

Rules:
1. Build a clean document outline for assembly — real sections only.
2. When a Document TOC is provided, treat it as hierarchy ground truth. Do not invent levels that contradict the TOC. Breadcrumb trails refine "you are here"; TOC defines the global nest (e.g. Play → Fail Safe → Make it Yours).
3. Treat repeated discard running_header/footer/branding as chrome, NOT section titles — EXCEPT when running_header is a breadcrumb trail with " > " (e.g. "Play > Fail Safe > Make it Yours" or "Combo Play > Trust Building > Insights & Metrics"). Those trails define the section hierarchy; encode each segment at the correct level.
4. Playbook-style docs often nest: top level "Play" or "Combo Play", then play name, then sub-play (e.g. "Make it Yours"), then in-page subsections. Continuation pages extend the trail (page 95 adds "Make it Yours" under "Play > Fail Safe"); new pages may swap a segment (page 96: "Play > Limited Offer").
5. Prefer fewer, clearer levels over mirroring every page chrome line.
6. chromePatterns: short strings the assembler should strip from edges (from discard evidence) — do NOT include breadcrumb or TOC segment text that should become headings. Never list content section labels such as "Make it Yours", "Do:", "Don't:", "What it is:", "Insights & Metrics", or "Behind the Data".
7. Output ONLY JSON:
{
  "v": 1,
  "title": "<optional doc title>",
  "sections": [{ "heading": "...", "level": 1-6, "fromPages": [n], "notes": "" }],
  "chromePatterns": ["..."],
  "guidance": "<one short paragraph for the code assembler>"
}`;

function presetGuidance(preset: RestitchPreset): string {
  switch (preset) {
    case "too_short":
      return "Prioritize completeness: retain unique body content; be conservative stripping keep blocks; expand outline to cover all pages.";
    case "too_much_garbage":
      return "Prioritize cleanliness: aggressive chromePatterns from discard; drop page-edge duplicates and branding phrases;";
    case "bad_structure":
      return "Prioritize hierarchy: rebuild a coherent outline; collapse repeated chrome titles; fix heading levels.";
    default:
      return "Balance completeness and cleanliness using discard evidence for chrome and keep headings for body structure.";
  }
}

export async function inferDocumentStructure(opts: {
  config: ResolvedProviderConfig;
  pages: PageIr[];
  preset?: RestitchPreset;
  prompt?: string;
  review?: RunReview | null;
  toc?: DocumentToc | null;
}): Promise<DocumentStructure> {
  const brief = buildStructureBrief(opts.pages);
  const preset = opts.preset ?? "default";
  const reviewNotes: string[] = [];
  if (opts.review) {
    for (const [page, pr] of Object.entries(opts.review.pages)) {
      if (pr.tags.length || pr.comments.length) {
        reviewNotes.push(
          `page ${page} tags=${pr.tags.join(",") || "-"} comments=${pr.comments.map((c) => c.text).join(" / ") || "-"}`,
        );
      }
      for (const [figKey, fr] of Object.entries(pr.figures ?? {})) {
        if (!fr.tags.length && !fr.comments.length) continue;
        reviewNotes.push(
          `page ${page} figure ${figKey} tags=${fr.tags.join(",") || "-"} comments=${fr.comments.map((c) => c.text).join(" / ") || "-"}`,
        );
      }
    }
    if (opts.review.document.tags.length || opts.review.document.comments.length) {
      reviewNotes.push(
        `document tags=${opts.review.document.tags.join(",") || "-"} comments=${opts.review.document.comments.map((c) => c.text).join(" / ") || "-"}`,
      );
    }
  }

  const tocBrief = formatTocBrief(opts.toc);

  const userText = [
    `Preset: ${preset}`,
    `Preset guidance: ${presetGuidance(preset)}`,
    opts.prompt?.trim() ? `Operator prompt: ${opts.prompt.trim()}` : "",
    reviewNotes.length ? `Review annotations:\n${reviewNotes.join("\n")}` : "",
    tocBrief ? tocBrief : "",
    "Page IR briefs:",
    brief,
  ]
    .filter(Boolean)
    .join("\n\n");

  const raw = await completeVisionChat({
    config: opts.config,
    system: STRUCTURE_SYSTEM,
    userText,
    pages: [],
    temperature: 0.1,
    maxTokens: 4096,
    observe: {
      name: "infer-document-structure",
      metadata: {
        kind: "structure",
        preset,
        pages: opts.pages.length,
        hasToc: Boolean(opts.toc?.entries?.length),
      },
    },
  });

  const json = extractJsonObject(raw);
  const parsed = documentStructureSchema.parse(JSON.parse(json));
  return sanitizeStructure(parsed as DocumentStructure);
}

/** Drop content section labels the model often misclassifies as chrome. */
export function sanitizeStructure(structure: DocumentStructure): DocumentStructure {
  return {
    ...structure,
    chromePatterns: (structure.chromePatterns ?? []).filter(
      (p) => !isProtectedSectionLabel(p),
    ),
  };
}
