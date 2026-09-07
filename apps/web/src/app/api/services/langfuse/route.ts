import { NextResponse } from "next/server";
import { langfusePublicUrl, mintLangfuseSession } from "../../../../lib/serviceAuth";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  try {
    const { cookies, redirectTo } = await mintLangfuseSession();
    const res = NextResponse.redirect(redirectTo, 302);
    for (const c of cookies) {
      res.headers.append("Set-Cookie", c);
    }
    return res;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Langfuse login failed";
    const fallback = langfusePublicUrl();
    const dest = new URL("/services", req.url);
    dest.searchParams.set("error", message);
    dest.searchParams.set("target", "langfuse");
    dest.searchParams.set("fallback", fallback);
    return NextResponse.redirect(dest, 302);
  }
}
