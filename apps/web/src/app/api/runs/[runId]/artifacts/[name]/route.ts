import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const DATA_DIR = process.env.DATA_DIR ?? "/data";

function safeRunId(runId: string): string | null {
  if (!/^[a-zA-Z0-9._-]{1,64}$/.test(runId)) return null;
  return runId;
}

function safeArtifactName(name: string): string | null {
  if (!/^[a-zA-Z0-9._-]{1,128}$/.test(name)) return null;
  if (name.includes("..")) return null;
  return name;
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ runId: string; name: string }> },
) {
  const { runId: rawId, name: rawName } = await ctx.params;
  const runId = safeRunId(rawId);
  const name = safeArtifactName(rawName);
  if (!runId || !name) {
    return NextResponse.json({ error: "Invalid run or artifact" }, { status: 400 });
  }

  const filePath = path.join(DATA_DIR, "outputs", runId, "artifacts", name);
  try {
    const buf = await fs.readFile(filePath);
    const ext = path.extname(name).toLowerCase();
    const type =
      ext === ".png"
        ? "image/png"
        : ext === ".jpg" || ext === ".jpeg"
          ? "image/jpeg"
          : ext === ".webp"
            ? "image/webp"
            : "application/octet-stream";
    return new NextResponse(buf, {
      headers: {
        "Content-Type": type,
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch {
    return NextResponse.json({ error: "Artifact not found" }, { status: 404 });
  }
}
