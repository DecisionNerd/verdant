import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { buildZip, type ZipEntry } from "../../../lib/zip";

export const runtime = "nodejs";

const DATA_DIR = process.env.DATA_DIR ?? "/data";

const RUN_ID_RE = /^[a-zA-Z0-9._-]{1,64}$/;

/** Figure files referenced from markdown/html (artifacts/…). */
function referencedArtifacts(content: string): Set<string> {
  const out = new Set<string>();
  const re =
    /(?:\(|"|'|=\s*|src=)(?:\.\/)?artifacts\/([A-Za-z0-9._-]+\.(?:png|jpe?g|gif|webp))/gi;
  for (const m of content.matchAll(re)) {
    if (m[1]) out.add(m[1]);
  }
  // Markdown image / link form without preceding quote
  const md = /\]\((?:\.\/)?artifacts\/([A-Za-z0-9._-]+\.(?:png|jpe?g|gif|webp))\)/gi;
  for (const m of content.matchAll(md)) {
    if (m[1]) out.add(m[1]);
  }
  return out;
}

/** Prefer figures referenced in the doc; fall back to all figure-* crops (not page rasters). */
async function collectArtifactFiles(
  artifactsDir: string,
  content: string,
): Promise<string[]> {
  let names: string[] = [];
  try {
    names = await fs.readdir(artifactsDir);
  } catch {
    return [];
  }

  const refs = referencedArtifacts(content);
  const picked =
    refs.size > 0
      ? names.filter((n) => refs.has(n))
      : names.filter((n) => /^figure-/i.test(n));

  const existing: string[] = [];
  for (const name of picked) {
    try {
      const st = await fs.stat(path.join(artifactsDir, name));
      if (st.isFile()) existing.push(name);
    } catch {
      // skip missing
    }
  }
  return existing.sort();
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const runId = url.searchParams.get("runId");
  const formatParam = (url.searchParams.get("format") ?? "").toLowerCase();

  if (!runId || !RUN_ID_RE.test(runId)) {
    return NextResponse.json({ error: "runId required" }, { status: 400 });
  }

  const runDir = path.join(DATA_DIR, "outputs", runId);
  const artifactsDir = path.join(runDir, "artifacts");

  let format: "markdown" | "html" =
    formatParam === "html" ? "html" : formatParam === "markdown" ? "markdown" : "markdown";

  // Auto-pick when format omitted or requested format missing.
  const mdPath = path.join(runDir, "document.md");
  const htmlPath = path.join(runDir, "document.html");
  let md: string | null = null;
  let html: string | null = null;
  try {
    md = await fs.readFile(mdPath, "utf8");
  } catch {
    md = null;
  }
  try {
    html = await fs.readFile(htmlPath, "utf8");
  } catch {
    html = null;
  }

  if (format === "html" && !html) {
    if (md) format = "markdown";
    else {
      return NextResponse.json(
        { error: "No document.html (or document.md) for this run" },
        { status: 404 },
      );
    }
  }
  if (format === "markdown" && !md) {
    if (html) format = "html";
    else {
      return NextResponse.json(
        { error: "No document.md (or document.html) for this run" },
        { status: 404 },
      );
    }
  }

  try {
    const entries: ZipEntry[] = [];
    const content = format === "html" ? html! : md!;
    const artifactNames = await collectArtifactFiles(artifactsDir, content);

    if (format === "markdown") {
      entries.push({ name: "document.md", data: Buffer.from(md!, "utf8") });
    } else {
      // index.html so the unzipped folder opens as a self-contained webpage
      entries.push({ name: "index.html", data: Buffer.from(html!, "utf8") });
    }

    for (const name of artifactNames) {
      const data = await fs.readFile(path.join(artifactsDir, name));
      entries.push({ name: `artifacts/${name}`, data });
    }

    const zip = buildZip(entries);
    const filename =
      format === "html"
        ? `verdant-${runId}-html.zip`
        : `verdant-${runId}-markdown.zip`;

    return new NextResponse(new Uint8Array(zip), {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${filename}"`,
        "Cache-Control": "no-store",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 500 },
    );
  }
}
