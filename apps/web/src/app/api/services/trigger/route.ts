import { NextResponse } from "next/server";
import { mintTriggerMagicRedirect, triggerPublicUrl } from "../../../../lib/serviceAuth";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  try {
    const magicUrl = await mintTriggerMagicRedirect();
    return NextResponse.redirect(magicUrl, 302);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Trigger login failed";
    const fallback = `${triggerPublicUrl()}/login/magic`;
    const dest = new URL("/services", req.url);
    dest.searchParams.set("error", message);
    dest.searchParams.set("target", "trigger");
    dest.searchParams.set("fallback", fallback);
    return NextResponse.redirect(dest, 302);
  }
}
