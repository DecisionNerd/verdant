import { NextResponse } from "next/server";

export const runtime = "nodejs";

function openObservePublicUrl() {
  return (process.env.OPENOBSERVE_URL || "http://localhost:18706").replace(/\/$/, "");
}

/** Redirect to OpenObserve UI (local root user from Compose defaults). */
export async function GET() {
  const dest = openObservePublicUrl();
  return NextResponse.redirect(dest, { status: 302 });
}
