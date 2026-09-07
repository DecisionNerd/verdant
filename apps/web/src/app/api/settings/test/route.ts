import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

const WORKER_URL = process.env.WORKER_URL ?? "http://worker:8791";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const res = await fetch(`${WORKER_URL}/settings/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : String(err) },
      { status: 502 },
    );
  }
}
