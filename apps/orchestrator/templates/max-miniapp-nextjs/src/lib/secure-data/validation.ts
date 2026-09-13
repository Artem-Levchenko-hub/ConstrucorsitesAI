export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type RecordPayload = { [key: string]: JsonValue };
export type SecureDataErrorCode = "INVALID_INPUT" | "NOT_FOUND" | "REVISION_CONFLICT" | "KEY_UNAVAILABLE" | "DECRYPTION_FAILED";

export class SecureDataError extends Error {
  readonly code: SecureDataErrorCode;
  constructor(code: SecureDataErrorCode, message: string) {
    super(message);
    this.name = "SecureDataError";
    this.code = code;
  }
}

export const MAX_PAYLOAD_BYTES = 65_536;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid(): never { throw new SecureDataError("INVALID_INPUT", "Invalid secure data request"); }

export function validateCollection(value: unknown): asserts value is string {
  if (typeof value !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/.test(value)) invalid();
}
export function validateIdentity(value: unknown): asserts value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > 256 || /[\u0000-\u001f\u007f]/.test(value)) invalid();
}
export function validateId(value: unknown): asserts value is string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
}
export function validateRevision(value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value >= 2_147_483_647) invalid();
}
export function validatePayload(value: unknown): asserts value is RecordPayload {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid();
  const ancestors = new Set<object>();
  let nodes = 0;
  const walk = (item: unknown, depth: number): void => {
    if (++nodes > 10_000 || depth > 32) invalid();
    if (item === null || typeof item === "boolean" || typeof item === "string") return;
    if (typeof item === "number" && Number.isFinite(item)) return;
    if (typeof item !== "object" || ancestors.has(item)) invalid();
    if (!Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null) invalid();
    ancestors.add(item);
    for (const child of Object.values(item)) walk(child, depth + 1);
    ancestors.delete(item);
  };
  walk(value, 0);
  if (Buffer.byteLength(JSON.stringify(value), "utf8") > MAX_PAYLOAD_BYTES) invalid();
}

export function validateLimit(value: unknown): number {
  if (value === undefined) return 50;
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 100) invalid();
  return value;
}

// UUID keyset pagination does not expose plaintext payload or require SQL offsets.
export function encodeCursor(id: string): string { validateId(id); return Buffer.from(id, "utf8").toString("base64url"); }
export function decodeCursor(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.length !== 48 || !/^[A-Za-z0-9_-]+$/.test(value)) invalid();
  const decoded = Buffer.from(value, "base64url").toString("utf8");
  validateId(decoded);
  if (encodeCursor(decoded) !== value) invalid();
  return decoded;
}
