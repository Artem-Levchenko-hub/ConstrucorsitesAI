import { NextResponse } from "next/server";

import { omniaMaxConfig } from "@/lib/omnia/max-config";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(omniaMaxConfig, {
    headers: { "Cache-Control": "no-store" },
  });
}
