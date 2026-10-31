import { getMaxUser } from "@/lib/max/session";
import { getManagementContract } from "@/lib/omnia/management-config";
import {
  ManagementError,
  requireResource,
  parseRecordsQuery,
  readManagementBody,
  parseManagementBody,
  managementResponse,
} from "@/lib/omnia/management-resources";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ resource: string }> };
async function context(request: Request, ctx: Context) {
  const user = await getMaxUser();
  // getMaxUser verifies the launch/session or the trusted gateway identity.
  // Owner previews use a signed synthetic actor, isolated by the same text key.
  if (
    !user ||
    typeof user.id !== "string" ||
    !user.id.trim() ||
    user.id.length > 128 ||
    /[\u0000-\u001f\u007f]/.test(user.id)
  )
    throw new ManagementError("unauthorized", 401);
  const expectedActor = request.headers.get("X-Max-Expected-Actor");
  if (expectedActor !== null && expectedActor !== user.id)
    throw new ManagementError("actor_changed", 409);
  const contract = await getManagementContract();
  return {
    actor: user.id,
    resource: requireResource(contract, (await ctx.params).resource),
  };
}
export async function POST(request: Request, ctx: Context): Promise<Response> {
  try {
    const { actor, resource } = await context(request, ctx);
    const body = parseManagementBody(await readManagementBody(request));
    const { createManagedBusinessRecord } = await import(
      "@/lib/omnia/business-server"
    );
    return Response.json(
      await createManagedBusinessRecord(actor, resource, body),
      { status: 201, headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return managementResponse(error);
  }
}
export async function GET(request: Request, ctx: Context): Promise<Response> {
  try {
    const { actor, resource } = await context(request, ctx);
    const params = new URL(request.url).searchParams;
    if (
      [...params.keys()].some((k) => !["limit", "cursor"].includes(k)) ||
      [...params.keys()].some((k) => params.getAll(k).length !== 1)
    )
      throw new ManagementError("invalid_request");
    const query = parseRecordsQuery({
      resource,
      ...(params.has("limit") ? { limit: Number(params.get("limit")) } : {}),
      ...(params.has("cursor") ? { cursor: params.get("cursor") } : {}),
    });
    const { listManagedRecords } = await import("@/lib/omnia/management-store");
    return Response.json(await listManagedRecords({ actor }, query), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return managementResponse(error);
  }
}
