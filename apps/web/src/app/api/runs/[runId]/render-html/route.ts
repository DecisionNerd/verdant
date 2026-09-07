import { NextResponse } from "next/server";

export const runtime = "nodejs";

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";

export async function POST(
  _req: Request,
  ctx: { params: Promise<{ runId: string }> },
) {
  try {
    const { runId } = await ctx.params;
    const res = await fetch(
      `${WORKER_URL}/runs/${encodeURIComponent(runId)}/render-html`,
      { method: "POST" },
    );
    const text = await res.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: `Worker returned non-JSON (${res.status})` };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
