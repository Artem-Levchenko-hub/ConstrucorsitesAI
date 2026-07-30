import { NextResponse } from "next/server";

import { omniaMaxConfig } from "@/lib/omnia/max-config";

export const dynamic = "force-static";

export function GET() {
  return NextResponse.json(omniaMaxConfig, {
    headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" },
  });
}
