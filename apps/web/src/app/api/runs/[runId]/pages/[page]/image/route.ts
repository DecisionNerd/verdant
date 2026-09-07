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
  const imagePath = path.join(DATA_DIR, "outputs", runId, "artifacts", `page-${pad}.png`);

  try {
    const bytes = await fs.readFile(imagePath);
    return new NextResponse(new Uint8Array(bytes), {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch {
    return NextResponse.json({ error: "Image not found" }, { status: 404 });
  }
}
