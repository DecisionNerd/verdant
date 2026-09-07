import { NextResponse } from "next/server";

export const runtime = "nodejs";

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";

export async function POST(
  req: Request,
  ctx: { params: Promise<{ runId: string }> },
) {
  try {
    const { runId } = await ctx.params;
    const body = await req.json().catch(() => ({}));
    const res = await fetch(`${WORKER_URL}/jobs/${encodeURIComponent(runId)}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const raw = await res.text();
    let data: unknown;
    try {
      data = JSON.parse(raw);
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
