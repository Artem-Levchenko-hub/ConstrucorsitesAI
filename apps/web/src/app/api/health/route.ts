// Lightweight liveness probe for the docker healthcheck. Does NOT touch the
// api, db, or any external resource — its sole job is to confirm that the
// Next.js process is accepting connections. The root path "/" 307s to the
// landing on a different host which makes `wget --spider /` follow the
// redirect off-box and time out.
export const dynamic = "force-static";

export function GET(): Response {
  return new Response("ok", {
    status: 200,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}
