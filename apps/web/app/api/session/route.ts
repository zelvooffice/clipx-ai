import { NextResponse, type NextRequest } from "next/server";
import { authenticate } from "@/lib/access";

export async function GET(request: NextRequest) {
  const auth = await authenticate(request.headers);
  if (!auth.ok) return NextResponse.json({ detail: auth.reason }, { status: auth.status });
  return NextResponse.json({ email: auth.email, via: auth.via }, { headers: { "cache-control": "no-store" } });
}
