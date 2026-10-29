import { open } from "node:fs/promises";
import { join } from "node:path";
import {
  ManagementError,
  parseManagementContract,
} from "@/lib/omnia/management-resources";
import type { ManagementContract } from "@/lib/omnia/management-resources";

/** Bundled, platform-locked manifest; never take a request-supplied path. */
export async function getManagementContract(): Promise<ManagementContract> {
  let file: Awaited<ReturnType<typeof open>> | undefined;
  try {
    file = await open(join(process.cwd(), ".omnia", "management.json"), "r");
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > 65536) throw Error();
    const bytes = Buffer.alloc(65537);
    let length = 0;
    while (length < bytes.length) {
      const { bytesRead } = await file.read(
        bytes,
        length,
        bytes.length - length,
        null,
      );
      if (!bytesRead) break;
      length += bytesRead;
    }
    if (length > 65536) throw Error();
    return parseManagementContract(
      JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(
          bytes.subarray(0, length),
        ),
      ),
    );
  } catch {
    throw new ManagementError("unsupported", 409);
  } finally {
    await file?.close().catch(() => undefined);
  }
}
