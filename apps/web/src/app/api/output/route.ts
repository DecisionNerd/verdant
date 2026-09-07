import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

export const runtime = "nodejs";

const DATA_DIR = process.env.DATA_DIR ?? "/data";

function resolveDataPath(input: string): string {
  const normalized = input.replace(/\\/g, "/").replace(/^\.\//, "");
  if (normalized.startsWith("data/")) {
    return path.join(DATA_DIR, normalized.slice("data/".length));
  }
  if (path.isAbsolute(input)) return input;
  return path.join(DATA_DIR, normalized);
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const p = url.searchParams.get("path");
  if (!p) return NextResponse.json({ error: "path required" }, { status: 400 });

  try {
    const content = await fs.readFile(resolveDataPath(p), "utf8");
    return NextResponse.json({ path: p, content });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 404 },
    );
  }
}
