import { randomUUID } from "node:crypto";
import { decryptPayload, encryptPayload, type KeyRing } from "./crypto";
import { decodeCursor, encodeCursor, SecureDataError, validateCollection, validateId, validateIdentity, validateLimit, validatePayload, validateRevision, type RecordPayload } from "./validation";

interface QueryResult { rows: Record<string, unknown>[]; rowCount: number | null; }
export interface QueryPort { query(text: string, values?: unknown[]): Promise<QueryResult>; }
export interface ClientPort extends QueryPort { release(): void; }
export interface PoolPort extends QueryPort { connect(): Promise<ClientPort>; }
export interface SecureRecord {
  id: string;
  collection: string;
  payload: RecordPayload;
  revision: number;
  createdAt: string;
  updatedAt: string;
}
export interface ListOptions { limit?: number; cursor?: string | null; }
export interface SecureRecordPage { records: SecureRecord[]; nextCursor: string | null; }

const COLUMNS = "id, collection, owner_id, ciphertext, revision, created_at, updated_at";
function notFound(): never { throw new SecureDataError("NOT_FOUND", "Secure record not found"); }
function conflict(): never { throw new SecureDataError("REVISION_CONFLICT", "Secure record revision changed"); }

/** Trusted core only. The actor must come from independently verified identity. */
export class SecureRecordStore {
  private readonly pool: PoolPort;
  private readonly projectId: string;
  private readonly ring: KeyRing;

  constructor(pool: PoolPort, projectId: string, ring: KeyRing) {
    validateIdentity(projectId);
    if (ring.projectId !== projectId) throw new SecureDataError("KEY_UNAVAILABLE", "Secure data key is unavailable");
    this.pool = pool;
    this.projectId = projectId;
    this.ring = ring;
  }

  private scope(actorId: string, collection: string, id?: string): void {
    validateIdentity(actorId);
    validateCollection(collection);
    if (id !== undefined) validateId(id);
  }
  private record(row: Record<string, unknown>): SecureRecord {
    const id = String(row.id);
    const collection = String(row.collection);
    const revision = Number(row.revision);
    return {
      id, collection, revision,
      payload: decryptPayload(String(row.ciphertext), { projectId: this.projectId, collection, id, ownerId: String(row.owner_id), revision }, this.ring),
      createdAt: new Date(row.created_at as string | Date).toISOString(),
      updatedAt: new Date(row.updated_at as string | Date).toISOString(),
    };
  }
  private async transaction<T>(operation: (client: ClientPort) => Promise<T>): Promise<T> {
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      const value = await operation(client);
      await client.query("COMMIT");
      return value;
    } catch (error) {
      try { await client.query("ROLLBACK"); } catch { /* Preserve the original operation failure. */ }
      throw error;
    } finally { client.release(); }
  }
  private async audit(client: ClientPort, actorId: string, collection: string, id: string, action: string, revision: number): Promise<void> {
    await client.query("INSERT INTO omnia_secure_record_audit(project_id,collection,record_id,actor_id,action,revision) VALUES($1,$2,$3,$4,$5,$6)", [this.projectId, collection, id, actorId, action, revision]);
  }
  async create(actorId: string, collection: string, payload: RecordPayload): Promise<SecureRecord> {
    this.scope(actorId, collection);
    validatePayload(payload);
    const id = randomUUID();
    const ciphertext = encryptPayload(payload, { projectId: this.projectId, collection, id, ownerId: actorId, revision: 1 }, this.ring);
    return this.transaction(async (client) => {
      const result = await client.query(`INSERT INTO omnia_secure_records(project_id,collection,id,owner_id,ciphertext,revision) VALUES($1,$2,$3,$4,$5,1) RETURNING ${COLUMNS}`, [this.projectId, collection, id, actorId, ciphertext]);
      await this.audit(client, actorId, collection, id, "create", 1);
      return this.record(result.rows[0]);
    });
  }
  async get(actorId: string, collection: string, id: string): Promise<SecureRecord> {
    this.scope(actorId, collection, id);
    const result = await this.pool.query(`SELECT ${COLUMNS} FROM omnia_secure_records WHERE project_id=$1 AND collection=$2 AND id=$3 AND owner_id=$4`, [this.projectId, collection, id, actorId]);
    if (!result.rows.length) notFound();
    return this.record(result.rows[0]);
  }
  async list(actorId: string, collection: string, options: ListOptions = {}): Promise<SecureRecordPage> {
    this.scope(actorId, collection);
    const limit = validateLimit(options.limit);
    const cursor = decodeCursor(options.cursor);
    const result = await this.pool.query(`SELECT ${COLUMNS} FROM omnia_secure_records WHERE project_id=$1 AND collection=$2 AND owner_id=$3 AND ($4::uuid IS NULL OR id > $4::uuid) ORDER BY id ASC LIMIT $5`, [this.projectId, collection, actorId, cursor, limit + 1]);
    const records = result.rows.slice(0, limit).map((row) => this.record(row));
    return { records, nextCursor: result.rows.length > limit ? encodeCursor(records[records.length - 1].id) : null };
  }
  /** Replaces the entire payload. Existing fields omitted by the caller are removed. */
  async update(actorId: string, collection: string, id: string, payload: RecordPayload, expectedRevision: number): Promise<SecureRecord> {
    this.scope(actorId, collection, id);
    id = id.toLowerCase();
    validateRevision(expectedRevision);
    validatePayload(payload);
    // Detach caller-owned mutable input before the first asynchronous boundary.
    const replacement = JSON.parse(JSON.stringify(payload)) as RecordPayload;
    return this.transaction(async (client) => {
      const existing = await client.query("SELECT revision FROM omnia_secure_records WHERE project_id=$1 AND collection=$2 AND id=$3 AND owner_id=$4 FOR UPDATE", [this.projectId, collection, id, actorId]);
      if (!existing.rows.length) notFound();
      if (existing.rows[0].revision !== expectedRevision) conflict();
      const revision = expectedRevision + 1;
      const ciphertext = encryptPayload(replacement, { projectId: this.projectId, collection, id, ownerId: actorId, revision }, this.ring);
      const updated = await client.query(`UPDATE omnia_secure_records SET ciphertext=$5,revision=$6,updated_at=now() WHERE project_id=$1 AND collection=$2 AND id=$3 AND owner_id=$4 AND revision=$7 RETURNING ${COLUMNS}`, [this.projectId, collection, id, actorId, ciphertext, revision, expectedRevision]);
      if (!updated.rows.length) conflict();
      await this.audit(client, actorId, collection, id, "update", revision);
      return this.record(updated.rows[0]);
    });
  }
  async delete(actorId: string, collection: string, id: string, expectedRevision: number): Promise<void> {
    this.scope(actorId, collection, id);
    validateRevision(expectedRevision);
    return this.transaction(async (client) => {
      const existing = await client.query("SELECT revision FROM omnia_secure_records WHERE project_id=$1 AND collection=$2 AND id=$3 AND owner_id=$4 FOR UPDATE", [this.projectId, collection, id, actorId]);
      if (!existing.rows.length) notFound();
      if (existing.rows[0].revision !== expectedRevision) conflict();
      const deleted = await client.query("DELETE FROM omnia_secure_records WHERE project_id=$1 AND collection=$2 AND id=$3 AND owner_id=$4 AND revision=$5", [this.projectId, collection, id, actorId, expectedRevision]);
      if (deleted.rowCount !== 1) conflict();
      await this.audit(client, actorId, collection, id, "delete", expectedRevision);
    });
  }
}
