import type { NextRequest } from "next/server";
import { oauthCallback } from "../../callback";

export const GET = (r: NextRequest) => oauthCallback(r, "tiktok");
