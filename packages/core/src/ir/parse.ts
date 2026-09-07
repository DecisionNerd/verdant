import { pageIrSchema, type PageIrParsed } from "./schema.js";
import type { PageIr } from "./types.js";

/** Pull the first JSON object from a model response (fences / preamble allowed). */
export function extractJsonObject(raw: string): string {
  let text = raw.trim();
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence?.[1]) text = fence[1].trim();

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) {
    throw new Error("No JSON object found in model response");
  }
  return text.slice(start, end + 1);
}

export function parsePageIr(raw: string): PageIr {
  const jsonText = extractJsonObject(raw);
  let data: unknown;
  try {
    data = JSON.parse(jsonText);
  } catch (err) {
    throw new Error(
      `Invalid JSON for PageIr: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  const parsed: PageIrParsed = pageIrSchema.parse(data);
  return parsed as PageIr;
}
