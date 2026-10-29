import { getManagementContract } from "@/lib/omnia/management-config";
import { verifyOwnerAssertion } from "@/lib/omnia/management-auth";
import {
  ManagementError,
  object,
  requireResource,
  uuid,
  parseRecordsQuery,
  parseRecordUpdate,
  readManagementBody,
  parseManagementBody,
  managementResponse,
} from "@/lib/omnia/management-resources";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: Request): Promise<Response> {
  try {
    // Reject unauthenticated requests before filesystem/schema/DB access.
    if (!request.headers.get("X-Yleum-Owner-Assertion"))
      throw new ManagementError("unauthorized", 401);
    const raw = await readManagementBody(request);
    const contract = await getManagementContract();
    const identity = verifyOwnerAssertion(request, raw, contract);
    const value = object(parseManagementBody(raw), [
      "op",
      "query",
      "record_id",
      "resource",
      "update",
    ]);
    const { assertManagementSchema, listManagedRecords, updateManagedRecord } =
      await import("@/lib/omnia/management-store");
    if (value.op === "describe") {
      object(value, ["op"]);
      await assertManagementSchema();
      return Response.json(
        { publication_id: identity.publication_id, contract },
        { headers: { "Cache-Control": "no-store" } },
      );
    }
    if (value.op === "list") {
      object(value, ["op", "query"]);
      const query = parseRecordsQuery(value.query);
      requireResource(contract, query.resource);
      return Response.json(
        await listManagedRecords({ owner: identity }, query),
        { headers: { "Cache-Control": "no-store" } },
      );
    }
    if (value.op === "update") {
      object(value, ["op", "record_id", "resource", "update"]);
      const resource = requireResource(contract, value.resource);
      return Response.json(
        await updateManagedRecord(
          identity,
          uuid(value.record_id),
          resource,
          parseRecordUpdate(value.update),
        ),
        { headers: { "Cache-Control": "no-store" } },
      );
    }
    throw new ManagementError("invalid_request");
  } catch (error) {
    return managementResponse(error);
  }
}
