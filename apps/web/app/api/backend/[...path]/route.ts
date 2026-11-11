import { NextResponse, type NextRequest } from "next/server";
import { authenticate } from "@/lib/access";
import { backendFetch } from "@/lib/backend";

const ALLOWED_PREFIXES = ["v1/"];
const PASS_HEADERS = ["content-type", "content-length", "content-range", "accept-ranges", "cache-control", "etag"];

async function handle(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const auth = await authenticate(request.headers);
  if (!auth.ok) return NextResponse.json({ detail: auth.reason }, { status: auth.status });

  const { path } = await ctx.params;
  const joined = path.map(encodeURIComponent).join("/");
  if (!ALLOWED_PREFIXES.some((p) => joined.startsWith(p)) || path.some((s) => s === ".." || s === ".")) {
    return NextResponse.json({ detail: "not found" }, { status: 404 });
  }
  const method = request.method.toUpperCase();
  if (!["GET", "HEAD"].includes(method)) {
    // CSRF: same-origin + double-submit token (cookie value must be echoed in a header).
    const origin = request.headers.get("origin");
    if (!origin || new URL(origin).host !== request.nextUrl.host) {
      return NextResponse.json({ detail: "cross-origin request blocked" }, { status: 403 });
    }
    const cookie = request.cookies.get("clipx_csrf")?.value;
    if (!cookie || request.headers.get("x-clipx-csrf") !== cookie) {
      return NextResponse.json({ detail: "missing or invalid CSRF token" }, { status: 403 });
    }
  }
  const body = ["GET", "HEAD"].includes(method) ? new Uint8Array() : new Uint8Array(await request.arrayBuffer());
  if (body.byteLength > 1_000_000) return NextResponse.json({ detail: "request too large" }, { status: 413 });
  const extra: Record<string, string> = {};
  const ct = request.headers.get("content-type");
  if (ct) extra["content-type"] = ct;
  const range = request.headers.get("range");
  if (range) extra["range"] = range;
  const upstream = await backendFetch(auth.email, method, `/${joined}${request.nextUrl.search}`, body, extra);
  const headers = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("cache-control", upstream.headers.get("cache-control") ?? "no-store");
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
