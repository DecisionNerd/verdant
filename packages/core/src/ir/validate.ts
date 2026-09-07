import type { KeepBlock, PageIr } from "./types.js";

export type PageIrValidation = {
  ok: boolean;
  reasons: string[];
  keepChars: number;
  discardCount: number;
};

function keepTextLen(block: KeepBlock): number {
  switch (block.k) {
    case "list":
      return block.items.join(" ").length + block.t.length;
    case "table": {
      const cells =
        (block.headers?.join(" ") ?? "") +
        (block.rows?.flat().join(" ") ?? "") +
        block.t;
      return cells.length;
    }
    case "mermaid":
      return block.src.length + block.t.length;
    case "figure":
      return block.t.length + (block.caption?.length ?? 0);
    default:
      return block.t.length;
  }
}

function looksLikePageNumber(t: string): boolean {
  return /^(#{1,6}\s*)?(page\s*\d+(\s*(of|\/)\s*\d+)?|\d+\s*\/\s*\d+|\d{1,4})$/i.test(
    t.trim(),
  );
}

/**
 * Validate dual-stream IR.
 * Soft failure when multipage content pages omit discard (chrome must be extracted, not omitted).
 */
export function validatePageIr(
  ir: PageIr,
  expected?: { page: number; pages: number },
): PageIrValidation {
  const reasons: string[] = [];
  const keepChars = ir.keep.reduce((n, b) => n + keepTextLen(b), 0);
  const discardCount = ir.discard.length;

  if (expected) {
    if (ir.page !== expected.page) reasons.push("page_mismatch");
    if (ir.pages !== expected.pages) reasons.push("pages_mismatch");
  }

  if (ir.keep.length === 0 && ir.discard.length === 0) {
    reasons.push("empty");
  }

  if (keepChars > 0 && keepChars < 12) reasons.push("keep_too_short");

  const keepOnlyChrome =
    ir.keep.length > 0 &&
    ir.keep.every((b) => looksLikePageNumber(b.t) || !b.t.trim());
  if (keepOnlyChrome && keepChars > 0) reasons.push("keep_chrome_only");

  // Content pages on multipage docs must extract chrome into discard (exhaustiveness signal).
  if (expected && expected.pages > 1 && keepChars >= 24 && discardCount === 0) {
    reasons.push("discard_empty");
  }

  // Page-number-like text must not live only in keep when discard is empty.
  const leakedPageNum =
    discardCount === 0 &&
    ir.keep.some(
      (b) =>
        (b.z === "top" || b.z === "bottom" || b.edge) && looksLikePageNumber(b.t),
    );
  if (leakedPageNum) reasons.push("page_number_in_keep");

  return {
    ok: reasons.length === 0,
    reasons,
    keepChars,
    discardCount,
  };
}
