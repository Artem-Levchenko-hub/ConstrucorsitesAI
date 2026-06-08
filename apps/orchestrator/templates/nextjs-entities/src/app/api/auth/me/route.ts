/**
 * Current-user endpoint for the SDK's `auth.me()`. Returns the signed-in user
 * (id, email, name, role) or null. Fixed template file.
 */

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

import { NextResponse } from "next/server";

import { getCurrentUser } from "@/lib/session";

export async function GET() {
  const user = await getCurrentUser();
  return NextResponse.json({ data: user });
}
