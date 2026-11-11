import { NextResponse, type NextRequest } from "next/server";
import { authenticate } from "./lib/access";

/**
 * Every page and API route is private. Cloudflare Access sits in front of the Worker; this proxy verifies the
 * Access JWT again (defence in depth) and fails closed if authentication is not configured.
 */
export async function proxy(request: NextRequest) {
  const auth = await authenticate(request.headers);
  if (!auth.ok) {
    const isApi = request.nextUrl.pathname.startsWith("/api/");
    if (isApi) return NextResponse.json({ detail: auth.reason }, { status: auth.status });
    return new NextResponse(deniedPage(auth.reason), {
      status: auth.status,
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  }
  const res = NextResponse.next();
  if (!request.cookies.get("clipx_csrf")) {
    const token = [...crypto.getRandomValues(new Uint8Array(24))].map((b) => b.toString(16).padStart(2, "0")).join("");
    res.cookies.set("clipx_csrf", token, { httpOnly: false, secure: true, sameSite: "strict", path: "/" });
  }
  res.headers.set("x-frame-options", "DENY");
  res.headers.set("x-content-type-options", "nosniff");
  res.headers.set("referrer-policy", "same-origin");
  res.headers.set("permissions-policy", "camera=(), microphone=(), geolocation=()");
  res.headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  return res;
}

function deniedPage(reason: string) {
  const safe = reason.replace(/[<>&"]/g, "");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>CLIPX AI · Access required</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#050506;color:#f4f4f5;font:15px/1.5 Inter,ui-sans-serif,system-ui,sans-serif}
main{max-width:420px;padding:32px;border:1px solid rgba(255,255,255,.08);border-radius:16px;background:#0e0e10}
h1{font-size:18px;margin:0 0 8px}p{color:#a1a1aa;margin:0}</style></head>
<body><main><h1>Access required</h1><p>${safe}</p></main></body></html>`;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
