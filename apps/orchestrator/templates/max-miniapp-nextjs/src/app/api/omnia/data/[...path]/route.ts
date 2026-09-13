import { getMaxUser } from "@/lib/max/session";
import { createSecureDataHandler } from "@/lib/secure-data/http";
import { openSecureDataStore } from "@/lib/secure-data/runtime";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const handle = createSecureDataHandler(getMaxUser, openSecureDataStore, {
  trustedGateway: process.env.OMNIA_TRUSTED_GATEWAY === "1",
});
type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, context: Context) {
  return handle(request, (await context.params).path);
}
export const POST = GET;
export const PUT = GET;
export const DELETE = GET;
