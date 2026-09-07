import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const DATA_DIR = process.env.DATA_DIR ?? "/data";

function safeRunId(runId: string): string | null {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(runId)) return null;
  return runId;
}

function pageFiles(runId: string, pageNumber: number) {
  const pad = String(pageNumber).padStart(3, "0");
  const runDir = path.join(DATA_DIR, "outputs", runId);
  return {
    runDir,
    md: path.join(runDir, "pages", `page-${pad}.md`),
    image: path.join(runDir, "artifacts", `page-${pad}.png`),
    progress: path.join(runDir, "page-progress.json"),
    markdown: path.join(runDir, "document.md"),
  };
}

async function fileChars(filePath: string): Promise<number | undefined> {
  try {
    const st = await fs.stat(filePath);
    return st.size;
  } catch {
    return undefined;
  }
}

async function countPageFigures(
  runDir: string,
  pageNumber: number,
): Promise<{ figureCount: number; figureMissing: number }> {
  const pad = String(pageNumber).padStart(3, "0");
  const irPath = path.join(runDir, "pages", `page-${pad}.ir.json`);
  try {
    const raw = JSON.parse(await fs.readFile(irPath, "utf8")) as {
      keep?: Array<{ k?: string; artifact?: string }>;
    };
    const figures = (raw.keep ?? []).filter((b) => b.k === "figure");
    let missing = 0;
    for (const fig of figures) {
      const name = fig.artifact?.replace(/^artifacts\//, "");
      if (!name) {
        missing += 1;
        continue;
      }
      try {
        await fs.access(path.join(runDir, "artifacts", name));
      } catch {
        missing += 1;
      }
    }
    if (figures.length > 0) {
      return { figureCount: figures.length, figureMissing: missing };
    }
  } catch {
    // fall through to artifact glob
  }

  try {
    const arts = await fs.readdir(path.join(runDir, "artifacts"));
    const re = new RegExp(`^figure-p${pad}-f\\d+\\.png$`, "i");
    const count = arts.filter((n) => re.test(n)).length;
    return { figureCount: count, figureMissing: 0 };
  } catch {
    return { figureCount: 0, figureMissing: 0 };
  }
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ runId: string }> },
) {
  const { runId: raw } = await ctx.params;
  const runId = safeRunId(raw);
  if (!runId) {
    return NextResponse.json({ error: "Invalid runId" }, { status: 400 });
  }

  const runDir = path.join(DATA_DIR, "outputs", runId);
  try {
    await fs.access(runDir);
  } catch {
    return NextResponse.json({ error: "Run not found" }, { status: 404 });
  }

  const pagesDir = path.join(runDir, "pages");
  const artifactsDir = path.join(runDir, "artifacts");
  let pageNames: string[] = [];
  try {
    pageNames = (await fs.readdir(pagesDir)).filter((f) => /^page-\d+\.md$/i.test(f));
  } catch {
    pageNames = [];
  }

  const progressPath = path.join(runDir, "page-progress.json");
  let progressByPage = new Map<
    number,
    { status?: string; chars?: number; error?: string; validation?: string[] }
  >();
  try {
    const rawProgress = JSON.parse(await fs.readFile(progressPath, "utf8")) as {
      pages?: Array<{
        pageNumber: number;
        status?: string;
        chars?: number;
        error?: string;
        validation?: string[];
      }>;
    };
    for (const p of rawProgress.pages ?? []) {
      progressByPage.set(p.pageNumber, p);
    }
  } catch {
    // optional
  }

  const pageNumbers = pageNames
    .map((name) => Number(name.match(/page-(\d+)\.md/i)?.[1]))
    .filter((n) => Number.isFinite(n) && n > 0)
    .sort((a, b) => a - b);

  // Include artifact-only pages (e.g. skipped/blank with image but no md)
  try {
    const arts = (await fs.readdir(artifactsDir)).filter((f) => /^page-\d+\.png$/i.test(f));
    for (const name of arts) {
      const n = Number(name.match(/page-(\d+)\.png/i)?.[1]);
      if (Number.isFinite(n) && n > 0 && !pageNumbers.includes(n)) pageNumbers.push(n);
    }
    pageNumbers.sort((a, b) => a - b);
  } catch {
    // optional
  }

  const pages = [];
  for (const pageNumber of pageNumbers) {
    const files = pageFiles(runId, pageNumber);
    const [mdChars, imageBytes, figs] = await Promise.all([
      fileChars(files.md),
      fileChars(files.image),
      countPageFigures(runDir, pageNumber),
    ]);
    const prog = progressByPage.get(pageNumber);
    pages.push({
      pageNumber,
      chars: prog?.chars ?? mdChars,
      hasMarkdown: mdChars != null,
      hasImage: imageBytes != null,
      figureCount: figs.figureCount,
      figureMissing: figs.figureMissing || undefined,
      status: prog?.status,
      error: prog?.error,
      validation: prog?.validation?.length ? prog.validation : undefined,
      markdownUrl: `/api/runs/${encodeURIComponent(runId)}/pages/${pageNumber}`,
      imageUrl: `/api/runs/${encodeURIComponent(runId)}/pages/${pageNumber}/image`,
    });
  }

  const documentChars = await fileChars(path.join(runDir, "document.md"));
  const pageCharsSum = pages.reduce((n, p) => n + (p.chars ?? 0), 0);

  return NextResponse.json({
    runId,
    pageCount: pages.length,
    documentChars,
    pageCharsSum,
    pages,
  });
}
