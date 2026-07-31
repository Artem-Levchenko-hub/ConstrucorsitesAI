// Public web-process probe. `/api/*` belongs to FastAPI at the production edge,
// so this deliberately lives outside that prefix and remains independently
// checkable by an off-host monitor.
export const dynamic = "force-static";

export function GET(): Response {
  return Response.json({ status: "ok", service: "web" });
}
