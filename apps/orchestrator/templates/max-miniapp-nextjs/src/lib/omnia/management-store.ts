import { createHash, randomUUID } from "node:crypto";
import {
  ACTION_TYPES,
  ManagementError,
  canonicalJson,
  projectManagedRecord,
  allowedTransition,
  isoDate,
  uuid,
} from "@/lib/omnia/management-resources";
import type {
  ResourceId,
  ManagedSnapshot,
  ManagedRecord,
  RecordsQuery,
  RecordUpdate,
  OwnerStatus,
} from "@/lib/omnia/management-resources";
import type { OwnerIdentity } from "@/lib/omnia/management-auth";
import type { PoolClient } from "pg";
type Scope =
  | { actor: string; owner?: never }
  | { owner: OwnerIdentity; actor?: never };
const hash = (value: unknown) =>
  createHash("sha256").update(canonicalJson(value)).digest("hex");
function projectId(): string {
  try {
    return uuid(
      process.env.YLEUM_OWNER_PROJECT_ID || process.env.OMNIA_PROJECT_ID,
    );
  } catch {
    throw new ManagementError("unavailable", 503);
  }
}
async function schema(client: PoolClient): Promise<void> {
  const result = await client.query(
    "SELECT to_regclass('max_business_actions') AS actions, to_regclass('max_owner_records') AS records, to_regclass('max_business_receipts') AS customer_receipts, to_regclass('max_owner_receipts') AS owner_receipts, to_regclass('max_owner_audit') AS audit",
  );
  if (!result.rows[0] || Object.values(result.rows[0]).some((v) => !v))
    throw new ManagementError("unsupported", 409);
  // Describe must reject a partially upgraded/incompatible schema too. This is
  // read-only and only names the five platform-owned tables in this schema.
  const expected: Record<string, Record<string, string>> = {
    max_business_actions: {
      id: "uuid",
      max_user_id: "text",
      action_type: "text",
      payload: "jsonb",
      created_at: "timestamptz",
    },
    max_owner_records: {
      record_id: "uuid",
      project_id: "uuid",
      max_user_id: "text",
      resource: "text",
      status: "text",
      note: "text",
      revision: "text",
    },
    max_business_receipts: {
      max_user_id: "text",
      operation_key: "uuid",
      digest: "text",
      response: "jsonb",
    },
    max_owner_receipts: {
      project_id: "uuid",
      owner_id: "uuid",
      operation_key: "uuid",
      digest: "text",
      response: "jsonb",
    },
    max_owner_audit: {
      project_id: "uuid",
      owner_id: "uuid",
      record_id: "uuid",
      operation_key: "uuid",
      old_status: "text",
      new_status: "text",
      old_note: "text",
      new_note: "text",
    },
  };
  const columns = await client.query(
    "SELECT table_name,column_name,udt_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=ANY($1::text[])",
    [Object.keys(expected)],
  );
  for (const [table, fields] of Object.entries(expected)) {
    for (const [column, type] of Object.entries(fields)) {
      if (
        !columns.rows.some(
          (row) =>
            row.table_name === table &&
            row.column_name === column &&
            row.udt_name === type,
        )
      ) {
        throw new ManagementError("unsupported", 409);
      }
    }
  }
}
async function transaction<T>(
  scope: Scope | undefined,
  run: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const { pool } = await import("@/lib/db");
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(
      "SELECT set_config('statement_timeout','5000',true),set_config('lock_timeout','4000',true)",
    );
    await schema(client);
    if (scope?.owner) {
      await client.query(
        "SELECT set_config('app.owner_project_id',$1,true),set_config('app.owner_id',$2,true),set_config('app.max_user_id','',true)",
        [scope.owner.project_id, scope.owner.owner_id],
      );
    } else {
      await client.query(
        "SELECT set_config('app.max_user_id',$1,true),set_config('app.management_project_id',$2,true),set_config('app.owner_project_id','',true),set_config('app.owner_id','',true)",
        [scope?.actor || "", projectId()],
      );
    }
    const result = await run(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    if (error instanceof ManagementError) throw error;
    const code = (error as { code?: string }).code;
    if (code === "42P01" || code === "42703")
      throw new ManagementError("unsupported", 409);
    throw new ManagementError("unavailable", 503);
  } finally {
    client.release();
  }
}
export async function assertManagementSchema(): Promise<void> {
  await transaction(undefined, async () => undefined);
}
async function lock(client: PoolClient, scope: string, key: string) {
  await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
    `${scope}:${key}`,
  ]);
}
export async function createManagedRecord(
  actor: string,
  resource: ResourceId,
  input: Record<string, unknown>,
  prepare: () => Promise<ManagedSnapshot> | ManagedSnapshot,
): Promise<{ record: ManagedRecord; created: boolean }> {
  const key = uuid(input.operationKey),
    digest = hash({ resource, input });
  return transaction({ actor }, async (client) => {
    await lock(client, `customer:${actor}`, key);
    const receipt = await client.query(
      "SELECT digest,response FROM max_business_receipts WHERE max_user_id=$1 AND operation_key=$2",
      [actor, key],
    );
    if (receipt.rows.length) {
      if (receipt.rows[0].digest !== digest)
        throw new ManagementError("operation_conflict", 409);
      return {
        record: receipt.rows[0].response as ManagedRecord,
        created: false,
      };
    }
    const payload = await prepare(),
      id = randomUUID(),
      createdAt = new Date().toISOString(),
      revision = hash({ id, payload, status: "new", note: "" });
    await client.query(
      "INSERT INTO max_business_actions(id,max_user_id,action_type,status,payload,created_at,updated_at) VALUES($1,$2,$3,'new',$4,$5,$5)",
      [id, actor, ACTION_TYPES[resource], JSON.stringify(payload), createdAt],
    );
    await client.query(
      "INSERT INTO max_owner_records(record_id,project_id,max_user_id,resource,status,note,revision) VALUES($1,$2,$3,$4,'new','',$5)",
      [id, projectId(), actor, resource, revision],
    );
    const record = projectManagedRecord(
      { id, resource, created_at: createdAt, revision, status: "new", payload },
      false,
    );
    await client.query(
      "INSERT INTO max_business_receipts(max_user_id,operation_key,digest,response) VALUES($1,$2,$3,$4)",
      [actor, key, digest, JSON.stringify(record)],
    );
    return { record, created: true };
  });
}
const selection =
  "SELECT a.id,a.created_at,a.payload,m.resource,m.status,m.note,m.revision FROM max_business_actions a JOIN max_owner_records m ON m.record_id=a.id";
function cursorPosition(
  query: RecordsQuery,
  scope: Scope,
): { created_at: string; id: string } | undefined {
  if (!query.cursor) return;
  try {
    if (!/^[A-Za-z0-9_-]+$/.test(query.cursor)) throw Error();
    const value = JSON.parse(
      Buffer.from(query.cursor, "base64url").toString("utf8"),
    );
    const { cursor: _, ...filters } = query;
    if (
      value.filter_hash !== hash({ scope, filters }) ||
      Object.keys(value).sort().join(",") !== "created_at,filter_hash,id"
    )
      throw Error();
    return { created_at: isoDate(value.created_at), id: uuid(value.id) };
  } catch {
    throw new ManagementError("invalid_request");
  }
}
export async function listManagedRecords(
  scope: Scope,
  query: RecordsQuery,
): Promise<{ records: ManagedRecord[]; next_cursor: string | null }> {
  const position = cursorPosition(query, scope);
  return transaction(scope, async (client) => {
    const escaped = query.query.replace(/[\\%_]/g, "\\$&");
    const result = await client.query(
      `${selection} WHERE m.resource=$1 AND a.action_type=$2 AND a.payload->>'management_protocol'='1' AND a.payload->>'resource'=$1 AND ($3::text IS NULL OR a.max_user_id=$3) AND ($4::text IS NULL OR m.status=$4) AND ($5::timestamptz IS NULL OR a.created_at >= $5) AND ($6::timestamptz IS NULL OR a.created_at <= $6) AND ($7::text='' OR concat_ws(' ',a.payload->>'customer_name',a.payload->>'contact_phone',a.payload->>'contact_email',a.payload->>'comment',a.payload->>'service_title') ILIKE '%' || $7 || '%') AND ($8::timestamptz IS NULL OR (a.created_at,a.id)<($8::timestamptz,$9::uuid)) AND ($11::timestamptz IS NULL OR (a.payload->>'requested_for')::timestamptz >= $11) AND ($12::timestamptz IS NULL OR (a.payload->>'requested_for')::timestamptz <= $12) ORDER BY a.created_at DESC,a.id DESC LIMIT $10`,
      [
        query.resource,
        ACTION_TYPES[query.resource],
        scope.actor || null,
        query.status || null,
        query.created_from || null,
        query.created_to || null,
        escaped,
        position?.created_at || null,
        position?.id || null,
        query.limit + 1,
        query.requested_from || null,
        query.requested_to || null,
      ],
    );
    const rows = result.rows.slice(0, query.limit),
      last = rows.at(-1);
    const { cursor: _, ...filters } = query;
    const next_cursor =
      result.rows.length > query.limit && last
        ? Buffer.from(
            JSON.stringify({
              created_at: new Date(last.created_at).toISOString(),
              id: last.id,
              filter_hash: hash({ scope, filters }),
            }),
          ).toString("base64url")
        : null;
    return {
      records: rows.map((row) => projectManagedRecord(row, !!scope.owner)),
      next_cursor,
    };
  });
}
export async function updateManagedRecord(
  owner: OwnerIdentity,
  id: string,
  resource: ResourceId,
  update: RecordUpdate,
): Promise<ManagedRecord> {
  const digest = hash({ id, resource, update });
  return transaction({ owner }, async (client) => {
    await lock(
      client,
      `owner:${owner.project_id}:${owner.owner_id}`,
      update.operation_key,
    );
    const receipt = await client.query(
      "SELECT digest,response FROM max_owner_receipts WHERE project_id=$1 AND owner_id=$2 AND operation_key=$3",
      [owner.project_id, owner.owner_id, update.operation_key],
    );
    if (receipt.rows.length) {
      if (receipt.rows[0].digest !== digest)
        throw new ManagementError("operation_conflict", 409);
      return receipt.rows[0].response as ManagedRecord;
    }
    const result = await client.query(
      `${selection} WHERE a.id=$1 AND m.project_id=$2 AND m.resource=$3 AND a.action_type=$4 AND a.payload->>'management_protocol'='1' AND a.payload->>'resource'=$3 FOR UPDATE OF m`,
      [id, owner.project_id, resource, ACTION_TYPES[resource]],
    );
    const row = result.rows[0];
    if (!row) throw new ManagementError("not_found", 404);
    if (row.revision !== update.expected_revision)
      throw new ManagementError("revision_conflict", 409);
    const status = update.status ?? row.status,
      note = update.note ?? row.note;
    if (!allowedTransition(resource, row.status as OwnerStatus, status))
      throw new ManagementError("invalid_request");
    if (status !== row.status || note !== row.note) {
      const revision = hash({
        previous: row.revision,
        operation_key: update.operation_key,
        status,
        note,
      });
      await client.query(
        "UPDATE max_owner_records SET status=$1,note=$2,revision=$3 WHERE record_id=$4 AND project_id=$5",
        [status, note, revision, id, owner.project_id],
      );
      await client.query(
        "INSERT INTO max_owner_audit(project_id,owner_id,record_id,operation_key,old_status,new_status,old_note,new_note) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        [
          owner.project_id,
          owner.owner_id,
          id,
          update.operation_key,
          row.status,
          status,
          row.note,
          note,
        ],
      );
      row.status = status;
      row.note = note;
      row.revision = revision;
    }
    const record = projectManagedRecord(row, true);
    await client.query(
      "INSERT INTO max_owner_receipts(project_id,owner_id,operation_key,digest,response) VALUES($1,$2,$3,$4,$5)",
      [
        owner.project_id,
        owner.owner_id,
        update.operation_key,
        digest,
        JSON.stringify(record),
      ],
    );
    return record;
  });
}
