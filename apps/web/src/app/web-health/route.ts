// Public web-process probe. `/api/*` belongs to FastAPI at the production edge,
// so this deliberately lives outside that prefix and remains independently
// checkable by an off-host monitor.
import { normalizeReleaseSha } from "@/lib/release";

export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({
    status: "ok",
    service: "web",
    release_sha: normalizeReleaseSha(process.env.OMNIA_RELEASE_SHA),
  });
}
