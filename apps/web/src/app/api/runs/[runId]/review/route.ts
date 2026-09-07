import { NextResponse } from "next/server";

export const runtime = "nodejs";

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ runId: string }> },
) {
  try {
    const { runId } = await ctx.params;
    const res = await fetch(
      `${WORKER_URL}/runs/${encodeURIComponent(runId)}/review`,
      { cache: "no-store" },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}

export async function PUT(
  req: Request,
  ctx: { params: Promise<{ runId: string }> },
) {
  try {
    const { runId } = await ctx.params;
    const body = await req.json().catch(() => ({}));
    const res = await fetch(
      `${WORKER_URL}/runs/${encodeURIComponent(runId)}/review`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
