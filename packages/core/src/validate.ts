export type PageValidation = {
  ok: boolean;
  reasons: string[];
  chars: number;
};

/**
 * Heuristic validation for a single-page digest.
 * Soft failures (too_short on sparse pages) are still flagged so the pipeline can retry once.
 */
export function validatePageDigest(markdown: string): PageValidation {
  const reasons: string[] = [];
  const trimmed = markdown.trim();
  const chars = trimmed.length;

  if (!trimmed) {
    reasons.push("empty");
    return { ok: false, reasons, chars };
  }

  if (chars < 24) reasons.push("too_short");

  const fenceCount = (trimmed.match(/```/g) ?? []).length;
  if (fenceCount % 2 !== 0) reasons.push("unbalanced_fences");

  // Entire page is only page-number / chrome-like lines
  const lines = trimmed
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length > 0) {
    const chromeLike = lines.every((line) =>
      /^(#{1,6}\s*)?(page\s*\d+(\s*(of|\/)\s*\d+)?|\d+\s*\/\s*\d+|-+)$/i.test(line),
    );
    if (chromeLike) reasons.push("chrome_only");
  }

  return { ok: reasons.length === 0, reasons, chars };
}
