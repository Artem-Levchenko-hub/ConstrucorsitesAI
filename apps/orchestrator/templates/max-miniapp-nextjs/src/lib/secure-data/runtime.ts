import { pool } from "@/lib/db";
import { readKeyRing } from "./crypto";
import { SecureRecordStore } from "./store";

/** Only the immutable MAX core receives this read-only key mount. */
export async function openSecureDataStore(): Promise<SecureRecordStore> {
  const projectId = process.env.OMNIA_PROJECT_ID;
  const keyFile = process.env.OMNIA_DATA_KEY_FILE;
  if (!projectId || !keyFile) throw new Error("Secure data keys are unavailable");
  return new SecureRecordStore(pool, projectId, await readKeyRing(keyFile, projectId));
}
