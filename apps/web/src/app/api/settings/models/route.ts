import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";

export async function GET(req: Request) {
  try {
    const url = new URL(req.url);
    const qs = url.searchParams.toString();
    const res = await fetch(`${WORKER_URL}/settings/models?${qs}`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
