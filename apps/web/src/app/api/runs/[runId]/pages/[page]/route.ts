import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const DATA_DIR = process.env.DATA_DIR ?? "/data";

function safeRunId(runId: string): string | null {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(runId)) return null;
  return runId;
}

function safePageNumber(raw: string): number | null {
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1 || n > 10_000) return null;
  return n;
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ runId: string; page: string }> },
) {
  const { runId: rawId, page: rawPage } = await ctx.params;
  const runId = safeRunId(rawId);
  const pageNumber = safePageNumber(rawPage);
  if (!runId || pageNumber == null) {
    return NextResponse.json({ error: "Invalid run or page" }, { status: 400 });
  }

  const pad = String(pageNumber).padStart(3, "0");
  const mdPath = path.join(DATA_DIR, "outputs", runId, "pages", `page-${pad}.md`);
  const irPath = path.join(
    DATA_DIR,
    "outputs",
    runId,
    "pages",
    `page-${pad}.ir.json`,
  );
  const imagePath = path.join(DATA_DIR, "outputs", runId, "artifacts", `page-${pad}.png`);

  let markdown: string | null = null;
  try {
    markdown = await fs.readFile(mdPath, "utf8");
  } catch {
    markdown = null;
  }

  let discard: Array<{ k: string; t: string; z: string }> | null = null;
  let figures: Array<{
    id: string;
    t: string;
    caption?: string;
    artifact?: string;
    hasArtifact: boolean;
    imageUrl: string | null;
  }> = [];
  try {
    const raw = JSON.parse(await fs.readFile(irPath, "utf8")) as {
      discard?: Array<{ k?: string; t?: string; z?: string }>;
      keep?: Array<{
        k?: string;
        id?: string;
        t?: string;
        caption?: string;
        artifact?: string;
      }>;
    };
    if (Array.isArray(raw.discard)) {
      discard = raw.discard.map((d) => ({
        k: String(d.k ?? "other_chrome"),
        t: String(d.t ?? ""),
        z: String(d.z ?? "body"),
      }));
    }
    const keepFigs = (raw.keep ?? []).filter((b) => b.k === "figure");
    for (let i = 0; i < keepFigs.length; i++) {
      const f = keepFigs[i]!;
      const artifact = f.artifact?.trim() || undefined;
      const name = artifact?.replace(/^artifacts\//, "");
      let hasArtifact = false;
      if (name) {
        try {
          await fs.access(path.join(DATA_DIR, "outputs", runId, "artifacts", name));
          hasArtifact = true;
        } catch {
          hasArtifact = false;
        }
      }
      const id = (f.id?.trim() || name || `fig-${i + 1}`).slice(0, 64);
      figures.push({
        id,
        t: String(f.t ?? ""),
        caption: f.caption?.trim() || undefined,
        artifact,
        hasArtifact,
        imageUrl: hasArtifact && name
          ? `/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`
          : null,
      });
    }
  } catch {
    discard = null;
    figures = [];
  }

  // Fallback: orphan figure crops on disk without IR figure blocks
  if (figures.length === 0) {
    try {
      const pad = String(pageNumber).padStart(3, "0");
      const arts = await fs.readdir(path.join(DATA_DIR, "outputs", runId, "artifacts"));
      const re = new RegExp(`^figure-p${pad}-f\\d+\\.png$`, "i");
      for (const name of arts.filter((n) => re.test(n)).sort()) {
        figures.push({
          id: name,
          t: "",
          artifact: `artifacts/${name}`,
          hasArtifact: true,
          imageUrl: `/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`,
        });
      }
    } catch {
      // optional
    }
  }

  let hasImage = false;
  try {
    await fs.access(imagePath);
    hasImage = true;
  } catch {
    hasImage = false;
  }

  if (markdown == null && !hasImage && discard == null && figures.length === 0) {
    return NextResponse.json({ error: "Page not found" }, { status: 404 });
  }

  return NextResponse.json({
    runId,
    pageNumber,
    markdown,
    chars: markdown?.length ?? 0,
    discard,
    figures,
    figureCount: figures.length,
    hasImage,
    imageUrl: hasImage
      ? `/api/runs/${encodeURIComponent(runId)}/pages/${pageNumber}/image`
      : null,
  });
}
