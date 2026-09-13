import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { open } from "node:fs/promises";
import { MAX_PAYLOAD_BYTES, SecureDataError, validatePayload, type RecordPayload } from "./validation";

export interface KeyRing {
  readonly projectId: string;
  readonly activeVersion: string;
  readonly keys: ReadonlyMap<string, Buffer>;
}
export interface RecordContext {
  projectId: string;
  collection: string;
  id: string;
  ownerId: string;
  revision: number;
}

const MAX_KEY_FILE_BYTES = 16_384;
const VERSION = /^[A-Za-z0-9_-]{1,64}$/;
function keyError(): never { throw new SecureDataError("KEY_UNAVAILABLE", "Secure data key is unavailable"); }
function decryptError(): never { throw new SecureDataError("DECRYPTION_FAILED", "Secure data could not be decrypted"); }
function object(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}
function base64(value: unknown, length?: number): Buffer {
  if (typeof value !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new Error("Invalid encoding");
  const decoded = Buffer.from(value, "base64");
  if (decoded.toString("base64") !== value || (length !== undefined && decoded.length !== length)) throw new Error("Invalid encoding");
  return decoded;
}

// JSON.parse alone silently accepts duplicate keys. Scan its already-valid token
// stream first, rejecting duplicate object members (including escaped spellings).
function parseUniqueJson(source: string): unknown {
  const result: unknown = JSON.parse(source);
  const tokens = source.match(/"(?:[^"\\]|\\.)*"|[{}\[\]:,]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ?? [];
  const stack: { object: boolean; expectingKey: boolean; keys: Set<string> }[] = [];
  for (const token of tokens) {
    const current = stack[stack.length - 1];
    if (token === "{" || token === "[") {
      if (stack.length > 32) throw new Error("Invalid nesting");
      stack.push({ object: token === "{", expectingKey: token === "{", keys: new Set() });
    } else if (token === "}" || token === "]") {
      stack.pop();
    } else if (token === "," && current?.object) {
      current.expectingKey = true;
    } else if (token.startsWith('"') && current?.object && current.expectingKey) {
      const key: string = JSON.parse(token);
      if (current.keys.has(key)) throw new Error("Duplicate member");
      current.keys.add(key);
      current.expectingKey = false;
    }
  }
  return result;
}

export function parseKeyRing(source: string, expectedProjectId: string): KeyRing {
  try {
    if (typeof source !== "string" || Buffer.byteLength(source, "utf8") > MAX_KEY_FILE_BYTES || !expectedProjectId) keyError();
    const value = parseUniqueJson(source);
    if (!object(value) || !exactKeys(value, ["projectId", "activeVersion", "keys"]) || value.projectId !== expectedProjectId || typeof value.activeVersion !== "string" || !VERSION.test(value.activeVersion) || !object(value.keys)) keyError();
    const entries = Object.entries(value.keys);
    if (entries.length < 1 || entries.length > 32) keyError();
    const keys = new Map<string, Buffer>();
    for (const [version, encoded] of entries) {
      if (!VERSION.test(version)) keyError();
      keys.set(version, base64(encoded, 32));
    }
    if (!keys.has(value.activeVersion)) keyError();
    return { projectId: expectedProjectId, activeVersion: value.activeVersion, keys };
  } catch { return keyError(); }
}

export async function readKeyRing(filePath: string, expectedProjectId: string): Promise<KeyRing> {
  try {
    if (!filePath) keyError();
    const file = await open(filePath, "r");
    try {
      const stat = await file.stat();
      if (!stat.isFile() || stat.size > MAX_KEY_FILE_BYTES) keyError();
      // Fixed-size read also bounds a file changed after stat, unlike readFile.
      const bytes = Buffer.alloc(MAX_KEY_FILE_BYTES + 1);
      let length = 0;
      while (length < bytes.length) {
        const read = await file.read(bytes, length, bytes.length - length, length);
        if (read.bytesRead === 0) break;
        length += read.bytesRead;
      }
      if (length > MAX_KEY_FILE_BYTES) keyError();
      return parseKeyRing(bytes.subarray(0, length).toString("utf8"), expectedProjectId);
    } finally { await file.close(); }
  } catch { return keyError(); }
}

function aad(context: RecordContext, keyVersion: string): Buffer {
  return Buffer.from(JSON.stringify(["omnia-secure-record", 1, keyVersion, context.projectId, context.collection, context.id, context.ownerId, context.revision]), "utf8");
}
function keyFor(ring: KeyRing, context: RecordContext, version: string): Buffer {
  if (ring.projectId !== context.projectId) keyError();
  const key = ring.keys.get(version);
  if (!key || key.length !== 32 || !VERSION.test(version)) keyError();
  return key;
}

export function encryptPayload(payload: RecordPayload, context: RecordContext, ring: KeyRing): string {
  validatePayload(payload);
  const key = keyFor(ring, context, ring.activeVersion);
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, nonce, { authTagLength: 16 });
  cipher.setAAD(aad(context, ring.activeVersion));
  const ciphertext = Buffer.concat([cipher.update(JSON.stringify(payload), "utf8"), cipher.final()]);
  return JSON.stringify({ version: 1, keyVersion: ring.activeVersion, nonce: nonce.toString("base64"), tag: cipher.getAuthTag().toString("base64"), ciphertext: ciphertext.toString("base64") });
}

export function decryptPayload(encoded: string, context: RecordContext, ring: KeyRing): RecordPayload {
  try {
    if (typeof encoded !== "string" || encoded.length > 2 * MAX_PAYLOAD_BYTES) decryptError();
    const envelope = parseUniqueJson(encoded);
    if (!object(envelope) || !exactKeys(envelope, ["version", "keyVersion", "nonce", "tag", "ciphertext"]) || envelope.version !== 1 || typeof envelope.keyVersion !== "string") decryptError();
    const key = keyFor(ring, context, envelope.keyVersion);
    const ciphertext = base64(envelope.ciphertext);
    if (ciphertext.length > MAX_PAYLOAD_BYTES) decryptError();
    const decipher = createDecipheriv("aes-256-gcm", key, base64(envelope.nonce, 12), { authTagLength: 16 });
    decipher.setAAD(aad(context, envelope.keyVersion));
    decipher.setAuthTag(base64(envelope.tag, 16));
    const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    const payload: unknown = JSON.parse(plaintext.toString("utf8"));
    validatePayload(payload);
    return payload;
  } catch { return decryptError(); }
}
