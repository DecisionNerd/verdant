import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { KeepBlock, KeepHeading, KeepList, KeepParagraph, PageIr } from "./ir/types.js";
import { IR_VERSION } from "./ir/types.js";
import { proposeFromLine, stripListMarker } from "./ir/hierarchy.js";
import type { DigestPlan } from "./types.js";

const execFileAsync = promisify(execFile);

async function which(bin: string): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync("sh", ["-c", `command -v ${bin}`]);
    const p = stdout.trim();
    return p || null;
  } catch {
    return null;
  }
}

export type PdfTextMode = "auto" | "off" | "force";

export function pdfTextMode(override?: string | null): PdfTextMode {
  const raw = (override ?? process.env.VERDANT_PDF_TEXT ?? "auto")
    .toLowerCase()
    .trim();
  if (raw === "off" || raw === "0" || raw === "false") return "off";
  if (raw === "force") return "force";
  return "auto";
}

export type PdfTextAssessment = {
  usable: boolean;
  chars: number;
  words: number;
  reasons: string[];
};

const ING_ALLOW = new Set([
  "within",
  "again",
  "begin",
  "domain",
  "certain",
  "mountain",
  "captain",
  "curtain",
  "protein",
  "margin",
  "origin",
  "cabin",
  "latin",
  "cousin",
  "raisin",
  "vitamin",
  "admin",
  "login",
  "violin",
  "penguin",
  "dolphin",
  "pumpkin",
]);

/** Known incomplete stems from broken PDF encodings / cut columns. */
const TRUNC_STEM =
  /(?:tio|sio|cio|atio|mirrorin|progres|frictio|investmen|onboardin|activatio|retentio|engagemen|monetisatio|optimisatio|refinemen|buildin|shapin|formatio|feedbac|alignmen|personalisatio|disclosur|referra|experienc|curiosit|commitmen|bridg|paywal|shareabilit|meaningfull|inten)$/i;

function alphabeticWords(text: string): string[] {
  return text.match(/[A-Za-z][A-Za-z'-]{2,}/g) ?? [];
}

function columnarLineRatio(text: string): number {
  const lines = text.split(/\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 4) return 0;
  let columnar = 0;
  for (const line of lines) {
    // Two text runs separated by a wide gap — typical multi-column pdftotext -layout.
    if (/\S.{0,48}\s{3,}\S/.test(line)) columnar += 1;
  }
  return columnar / lines.length;
}

function truncatedWordRatio(words: string[]): number {
  if (words.length < 8) return 0;
  let bad = 0;
  for (const w of words) {
    const lower = w.toLowerCase().replace(/['-]/g, "");
    if (lower.length < 5) continue;
    if (TRUNC_STEM.test(lower)) {
      bad += 1;
      continue;
    }
    // -tion/-sion cut to -tio/-sio; -ing cut to -in
    if (/(?:tio|sio|cio)$/i.test(lower) && lower.length >= 5) {
      bad += 1;
      continue;
    }
    if (/in$/i.test(lower) && lower.length >= 6 && !ING_ALLOW.has(lower)) {
      // Prefer flagging only when it looks like a cut -ing (preceded by consonant cluster)
      if (/[b-df-hj-np-tv-z]in$/i.test(lower)) bad += 1;
    }
  }
  return bad / words.length;
}

/** Heuristic: is this page's extracted text good enough to skip vision OCR? */
export function assessExtractedPageText(text: string): PdfTextAssessment {
  const reasons: string[] = [];
  const trimmed = text.replace(/\s+/g, " ").trim();
  const chars = trimmed.length;
  const words = trimmed.split(/\s+/).filter(Boolean);
  const alpha = alphabeticWords(text);

  if (chars < 40) reasons.push("too_short");
  if (words.length < 8) reasons.push("too_few_words");

  const printable = (text.match(/[\x20-\x7E\n\r\t\u00A0-\u024F]/g) ?? []).length;
  if (text.length > 0 && printable / text.length < 0.55) {
    reasons.push("low_printable_ratio");
  }

  // Scanned/image-only pages often yield whitespace or a single stray character per "word".
  const oneCharWords = words.filter((w) => w.length === 1).length;
  if (words.length >= 5 && oneCharWords / words.length > 0.45) {
    reasons.push("fragmented");
  }

  // Multi-column pages mash left/right streams into unusable linear text.
  if (columnarLineRatio(text) >= 0.22) {
    reasons.push("columnar_layout");
  }

  // Broken encodings / cut columns drop final letters ("Introductio", "mirrorin").
  if (alpha.length >= 12 && truncatedWordRatio(alpha) >= 0.06) {
    reasons.push("truncated_words");
  }

  // Symbol / Private Use Area glyphs from icon fonts (not real content).
  const pua =
    (text.match(/[\uE000-\uF8FF]/g) ?? []).length +
    (text.match(/[\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/gu) ?? []).length;
  if (pua >= 2) reasons.push("private_use_glyphs");

  return {
    usable: reasons.length === 0,
    chars,
    words: words.length,
    reasons,
  };
}

/** Clean Poppler text and emit plain Markdown body text. */
export function normalizePdfTextToMarkdown(text: string): string {
  let s = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  // De-hyphenate line breaks: "docu-\nment" → "document"
  s = s.replace(/(\w)-\n(\w)/g, "$1$2");
  // Drop isolated Private Use Area icon glyphs (BMP + planes 15/16)
  s = s.replace(/[\uE000-\uF8FF]/g, "");
  s = s.replace(/[\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/gu, "");
  const lines = s.split("\n").map((line) => line.trimEnd());
  s = lines.join("\n");
  s = s.replace(/\n{3,}/g, "\n\n").trim();
  return s;
}

const SECTION_LABEL =
  /^(what it is|why it works|when to use it|make it yours|pair with|behind the data|how it works|examples?|notes?)[:.]?$/i;

/**
 * Build page IR from extracted PDF text.
 * Lines are classified via shared `proposeFromLine` — Title Case is a soft cue,
 * not final authority; assemble-time hierarchy resolves levels / list vs heading.
 */
export function pageIrFromExtractedText(
  text: string,
  page: number,
  pages: number,
): PageIr {
  const body = normalizePdfTextToMarkdown(text);
  const lines = body
    .split(/\n/)
    .map((l) => l.trim())
    .filter(Boolean);

  const keep: KeepBlock[] = [];
  let paraBuf: string[] = [];
  let listBuf: { ordered: boolean; items: string[] } | null = null;
  let i = 0;

  const flushPara = () => {
    if (paraBuf.length === 0) return;
    const t = paraBuf.join(" ").replace(/\s+/g, " ").trim();
    paraBuf = [];
    if (!t) return;
    const block: KeepParagraph = {
      id: `p${String(page).padStart(3, "0")}-para-${String(++i).padStart(2, "0")}`,
      k: "paragraph",
      z: "body",
      t,
    };
    keep.push(block);
  };

  const flushList = () => {
    if (!listBuf || listBuf.items.length === 0) {
      listBuf = null;
      return;
    }
    const block: KeepList = {
      id: `p${String(page).padStart(3, "0")}-list-${String(++i).padStart(2, "0")}`,
      k: "list",
      ordered: listBuf.ordered,
      items: listBuf.items,
      z: "body",
      t: listBuf.items.join("\n"),
    };
    keep.push(block);
    listBuf = null;
  };

  for (const line of lines) {
    const proposed = proposeFromLine(line);
    const best = proposed.reduce((a, b) => (b.score > a.score ? b : a), proposed[0]!);

    if (best.role === "ol_item" || best.role === "ul_item") {
      flushPara();
      const ordered = best.role === "ol_item";
      if (!listBuf || listBuf.ordered !== ordered) {
        flushList();
        listBuf = { ordered, items: [] };
      }
      listBuf.items.push(stripListMarker(line));
      continue;
    }

    if (best.role === "heading" && best.score >= 0.5) {
      flushPara();
      flushList();
      const h: KeepHeading = {
        id: `p${String(page).padStart(3, "0")}-h-${String(++i).padStart(2, "0")}`,
        k: "heading",
        // Soft level only — hierarchy resolver assigns final depth
        lvl: (best.level ?? (SECTION_LABEL.test(line.replace(/:$/, "")) ? 3 : 2)) as
          | 1
          | 2
          | 3
          | 4
          | 5
          | 6,
        t: line.replace(/:$/, ""),
        z: "body",
        // Mark weak Title-Case promotions as uncertain for assemble recheck
        ...(best.score < 0.7 ? { uncertain: true } : {}),
      };
      keep.push(h);
      continue;
    }

    flushList();
    paraBuf.push(line);
  }
  flushPara();
  flushList();

  // Fallback: blank-line paragraphs if line pass produced nothing useful
  if (keep.length === 0) {
    const chunks = body
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean);
    for (const t of chunks) {
      keep.push({
        id: `p${String(page).padStart(3, "0")}-para-${String(++i).padStart(2, "0")}`,
        k: "paragraph",
        z: "body",
        t,
      });
    }
  }

  return {
    v: IR_VERSION,
    page,
    pages,
    keep,
    discard: [],
  };
}

/**
 * Extract per-page text from a PDF using Poppler `pdftotext`.
 * Returns null when pdftotext is unavailable or the PDF has no text layer.
 */
export async function extractPdfPageTexts(
  pdfPath: string,
  expectedPages?: number,
): Promise<Map<number, string> | null> {
  const pdftotext = await which("pdftotext");
  if (!pdftotext) return null;

  try {
    const { stdout } = await execFileAsync(
      pdftotext,
      ["-layout", "-enc", "UTF-8", pdfPath, "-"],
      { maxBuffer: 64 * 1024 * 1024, encoding: "utf8" },
    );
    const parts = String(stdout).split("\f").map((p) => p.trim());
    if (parts.length === 0 || parts.every((p) => !p)) return null;

    const map = new Map<number, string>();
    const count =
      expectedPages != null && expectedPages > 0
        ? Math.min(parts.length, expectedPages)
        : parts.length;

    for (let i = 0; i < count; i++) {
      const text = parts[i] ?? "";
      if (text.trim()) map.set(i + 1, text);
    }
    return map.size > 0 ? map : null;
  } catch {
    return null;
  }
}

export function countPdfTextUsablePages(
  texts: Map<number, string>,
  pageNumbers: number[],
): number {
  let n = 0;
  for (const pageNumber of pageNumbers) {
    const text = texts.get(pageNumber);
    if (text && assessExtractedPageText(text).usable) n += 1;
  }
  return n;
}

/** Tag plan entries with pdf_text vs vision based on Poppler extraction quality. */
export function annotatePlanWithPdfText(
  plan: DigestPlan,
  texts: Map<number, string> | null,
  mode: PdfTextMode,
): { pdfTextPages: number; visionPages: number } {
  let pdfTextPages = 0;
  let pagesWithText = 0;

  for (const entry of plan.pages) {
    if (entry.action !== "digest") continue;

    if (!texts || mode === "off") {
      entry.extractSource = "vision";
      continue;
    }

    const raw = texts.get(entry.pageNumber);
    const assessment = raw ? assessExtractedPageText(raw) : null;
    if (raw) pagesWithText += 1;

    if (assessment?.usable) {
      entry.extractSource = "pdf_text";
      entry.reason = `PDF text layer (${assessment.words} words)`;
      pdfTextPages += 1;
    } else {
      entry.extractSource = "vision";
      if (raw && assessment) {
        entry.reason = `PDF text unusable (${assessment.reasons.join(", ")}) — vision fallback`;
      } else if (mode === "force") {
        entry.reason = "No usable PDF text — force mode will fail this page";
      }
    }
  }

  // Uneven text-layer quality → prefer vision for the whole document.
  if (
    mode === "auto" &&
    pagesWithText >= 5 &&
    pdfTextPages / pagesWithText < 0.75
  ) {
    pdfTextPages = 0;
    for (const entry of plan.pages) {
      if (entry.action !== "digest") continue;
      entry.extractSource = "vision";
      entry.reason =
        "PDF text quality uneven across pages — using vision for all pages";
    }
  }

  return {
    pdfTextPages,
    visionPages: plan.digestPages - pdfTextPages,
  };
}
