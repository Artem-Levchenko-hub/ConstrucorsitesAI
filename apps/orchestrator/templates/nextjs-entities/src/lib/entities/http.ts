/**
 * Tiny wrapper that turns engine calls into JSON responses and maps
 * EngineError → the right HTTP status. Keeps the route handlers one-liners.
 * Fixed template file.
 */

import { NextResponse } from "next/server";

import { EngineError } from "@/lib/entities/engine";

export async function run(fn: () => Promise<Response>): Promise<Response> {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof EngineError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    console.error("[entities] unexpected error", e);
    return NextResponse.json({ error: "internal error" }, { status: 500 });
  }
}
