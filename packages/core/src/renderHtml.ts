import fs from "node:fs/promises";
import path from "node:path";
import { markdownToHtmlDocument } from "./html.js";
import { outputsDir, toHostRelative } from "./paths.js";

/** Convert an existing run's document.md → document.html (no LLM). */
export async function renderRunHtml(runId: string): Promise<{
  runId: string;
  markdown: string;
  html: string;
  markdownChars: number;
}> {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(runId)) {
    throw new Error("Invalid runId");
  }
  const runDir = path.join(outputsDir(), runId);
  const mdPath = path.join(runDir, "document.md");
  const htmlPath = path.join(runDir, "document.html");

  let markdown: string;
  try {
    markdown = await fs.readFile(mdPath, "utf8");
  } catch {
    throw new Error("document.md not found for this run — digest Markdown first");
  }
  if (!markdown.trim()) {
    throw new Error("document.md is empty");
  }

  const html = await markdownToHtmlDocument(markdown, `Verdant ${runId}`);
  await fs.writeFile(htmlPath, html, "utf8");

  return {
    runId,
    markdown: toHostRelative(mdPath),
    html: toHostRelative(htmlPath),
    markdownChars: markdown.length,
  };
}
