import { NextResponse, type NextRequest } from "next/server";
import { authenticate } from "@/lib/access";
import { backendFetch } from "@/lib/backend";

/** Browser lands here after authorizing on Twitch/TikTok; the code is exchanged server-side by the API. */
export async function oauthCallback(request: NextRequest, provider: "twitch" | "tiktok") {
  const auth = await authenticate(request.headers);
  if (!auth.ok) return NextResponse.json({ detail: auth.reason }, { status: auth.status });
  const params = request.nextUrl.searchParams;
  const dest = new URL("/settings", request.nextUrl.origin);
  dest.searchParams.set("integration", provider);
  const error = params.get("error");
  const code = params.get("code");
  const state = params.get("state");
  if (error || !code || !state) {
    dest.searchParams.set("result", "error");
    dest.searchParams.set("message", (params.get("error_description") || error || "Authorization was cancelled").slice(0, 200));
    return NextResponse.redirect(dest);
  }
  const body = new TextEncoder().encode(JSON.stringify({ code, state }));
  const res = await backendFetch(auth.email, "POST", `/v1/integrations/${provider}/callback`, body, {
    "content-type": "application/json",
  });
  if (res.ok) {
    dest.searchParams.set("result", "connected");
  } else {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string };
    dest.searchParams.set("result", "error");
    dest.searchParams.set("message", String(detail.detail ?? `Connection failed (${res.status})`).slice(0, 200));
  }
  return NextResponse.redirect(dest);
}
