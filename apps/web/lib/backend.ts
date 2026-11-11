import { env } from "./env";
import { signRequest } from "./signing";

/** Server-side call to the FastAPI backend, signed as the authenticated user. */
export async function backendFetch(
  email: string,
  method: string,
  pathWithQuery: string,
  body: Uint8Array<ArrayBuffer> = new Uint8Array(),
  extra: Record<string, string> = {},
): Promise<Response> {
  const origin = env("API_ORIGIN").replace(/\/$/, "");
  const secret = env("INTERNAL_API_SECRET");
  if (!origin || !secret) {
    return Response.json({ detail: "API_ORIGIN / INTERNAL_API_SECRET are not configured on the web Worker." }, { status: 503 });
  }
  const headers: Record<string, string> = { ...extra, ...(await signRequest(secret, method, pathWithQuery, email, body)) };
  // When the API is published through a Cloudflare Tunnel protected by an Access service-token policy:
  if (env("CF_ACCESS_CLIENT_ID")) {
    headers["CF-Access-Client-Id"] = env("CF_ACCESS_CLIENT_ID");
    headers["CF-Access-Client-Secret"] = env("CF_ACCESS_CLIENT_SECRET");
  }
  return fetch(`${origin}${pathWithQuery}`, {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : body,
    redirect: "manual",
  });
}
