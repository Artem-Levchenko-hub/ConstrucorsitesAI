import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash, createHmac, webcrypto } from "node:crypto";
import { createRequire, stripTypeScriptTypes } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

test("product DB receipts use a purpose key that cannot call integrations", async () => {
  const app = runtime();
  const key = createHmac("sha256", app.env.MAX_BOT_TOKEN).update("yleum:business-analytics:v1").digest();
  delete app.env.MAX_BOT_TOKEN;
  app.env.YLEUM_BUSINESS_ANALYTICS_KEY = key.toString("hex");
  app.env.YLEUM_BUSINESS_ANALYTICS_RELAY = "http://analytics-relay:3128/__yleum/business-analytics";
  const analytics = app.load("lib/omnia/analytics.ts");
  assert.equal(await analytics.forwardMaxAnalytics("42", receipt, "action", "order_created"), true);
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].url, app.env.YLEUM_BUSINESS_ANALYTICS_RELAY);
  const assertion = app.calls[0].headers["X-Yleum-Business-Assertion"];
  const [version, encoded, signature] = assertion.split(".");
  assert.equal(signature, createHmac("sha256", key).update(`${version}.${encoded}`).digest("hex"));
  assert.equal(app.calls[0].headers["X-Omnia-Integration-Assertion"], undefined);
  assert.equal(await analytics.forwardMaxAnalytics("42", receipt, "open", "app_open"), false);
  assert.equal(app.calls.length, 1);
});

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../orchestrator/templates/max-miniapp-nextjs/src");
const require = createRequire(import.meta.url);
const receipt = "ABCDEFAB-1234-4123-8123-123456789ABC";
const types = {
  screen_view: "event", item_view: "event", button_click: "event",
  cart_add: "event", checkout_start: "event", form_submit: "event",
  order_created: "event", booking_created: "event", lead_created: "event",
};

// Run real TypeScript modules, session signatures and forwarding crypto. Only
// framework headers/cookies and external transport are isolated; DB import fails.
function runtime({ actor = "42", publicApp = true, corrupt = false, outcomes = [204] } = {}) {
  const calls = [], cache = new Map();
  let cookie, attempt = 0;
  const env = { OMNIA_PROJECT_ID: "11111111-1111-4111-8111-111111111111",
    MAX_BOT_TOKEN: "synthetic-bot-secret", MAX_SESSION_SECRET: "synthetic-session-secret",
    OMNIA_PUBLIC_APP_ORIGIN: publicApp ? "https://app.test" : "" };
  function load(path) {
    if (cache.has(path)) return cache.get(path);
    const exports = {};
    let source = stripTypeScriptTypes(readFileSync(resolve(root, path), "utf8"));
    source = source.replace(/export\s+\{[^}]*\}\s+from\s+["'][^"']+["'];?/g, "");
    source = source.replace(/import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
      (_, bindings, name) => `const {${bindings.replace(/\bas\b/g, ":")}} = require('${name}');`);
    const names = [];
    source = source.replace(/export\s+((?:async\s+)?(?:function|class|const|let|var)\s+(\w+))/g,
      (_, declaration, name) => { names.push(name); return declaration; });
    vm.runInNewContext(source + `\nObject.assign(exports, {${names.join(",")}});`, {
      exports, Buffer, URL, Date, Request, Response, Headers, TextEncoder,
      AbortController, AbortSignal, setTimeout, clearTimeout, crypto: webcrypto, process: { env },
      fetch: async (url, options) => {
        calls.push({ url, ...options });
        const outcome = outcomes[attempt++] ?? 204;
        if (outcome === "network") throw new Error("SYNTHETIC_PRIVATE_FAILURE");
        return new Response(null, { status: outcome });
      },
      require: name => {
        if (name === "@/lib/db") throw new Error("Telemetry must not depend on stock tables");
        if (name === "@/lib/max/bridge") return { getMaxWebApp: () => null };
        if (name === "next/headers") return {
          cookies: async () => ({ get: () => cookie ? { value: cookie } : undefined }),
          headers: async () => new Headers(),
        };
        if (name === "next/server") return { NextResponse: class extends Response {
          static json(body, options) { return new this(JSON.stringify(body), options); }
        } };
        if (name.startsWith("@/")) return load(name.slice(2) + ".ts");
        return require(name);
      },
    }, { filename: path });
    cache.set(path, exports);
    return exports;
  }
  if (actor !== null) cookie = load("lib/max/session.ts").createMaxSession({ id: actor }).value;
  if (corrupt) cookie += "tampered";
  return { calls, load, env, post: body => load("app/api/omnia/telemetry/route.ts").POST(
    new Request("https://app.test/api/omnia/telemetry", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })),
  };
}

test("typed SDK retries the same bounded receipt, omits arbitrary properties and supports replay", async () => {
  const first = runtime({ outcomes: ["network", 204] });
  await first.load("lib/omnia/client.ts").trackMaxActivity("cart_add", { eventId: receipt });
  assert.equal(first.calls.length, 2);
  assert.equal(first.calls[0].url, "/api/omnia/telemetry");
  assert.equal(first.calls[0].credentials, "include");
  assert.equal(first.calls[0].body, first.calls[1].body);
  assert.deepEqual(JSON.parse(first.calls[0].body), { eventType: "cart_add", eventId: receipt.toLowerCase() });
  const replay = runtime();
  await replay.load("lib/omnia/client.ts").trackMaxActivity("cart_add", { eventId: receipt });
  assert.equal(replay.calls[0].body, first.calls[0].body);
});

test("typed SDK generates one UUID across both delivery attempts", async () => {
  const app = runtime({ outcomes: [503, 204] });
  await app.load("lib/omnia/client.ts").trackMaxActivity("screen_view");
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[0].body, app.calls[1].body);
  assert.match(JSON.parse(app.calls[0].body).eventId, /^[a-f0-9-]{36}$/);
});

test("typed SDK rejects invented codes, opens, health probes and invalid receipts before dispatch", async () => {
  const app = runtime();
  const client = app.load("lib/omnia/client.ts");
  for (const type of ["app_open", "omnia_health_probe", "checkout", "toString", "order_Created"]) {
    await assert.rejects(client.trackMaxActivity(type), /Analytics event failed/);
  }
  await assert.rejects(client.trackMaxActivity("cart_add", { eventId: "bad" }), /Analytics event failed/);
  assert.equal(app.calls.length, 0);
});

test("typed SDK surfaces definitive rejection and exhausted retries without a false success", async () => {
  for (const status of [400, 401, 403, 422, 503]) {
    const app = runtime({ outcomes: [status, status] });
    await assert.rejects(app.load("lib/omnia/client.ts").trackMaxActivity("cart_add"), /Analytics event failed/);
    assert.equal(app.calls.length, status === 503 ? 2 : 1);
  }
});

for (const [eventType, kind] of Object.entries(types)) {
  test(`custom-schema telemetry forwards ${eventType} with strict category and full-body signature`, async () => {
    const app = runtime();
    const response = await app.post({ eventType, eventId: receipt });
    assert.equal(response.status, 204);
    assert.equal(app.calls.length, 1);
    const sent = app.calls[0], body = JSON.parse(sent.body);
    assert.deepEqual(Object.keys(body).sort(), ["event_id", "event_type", "kind"]);
    assert.equal(body.event_type, eventType);
    assert.equal(body.kind, kind);
    const [version, encoded, signature] = sent.headers["X-Omnia-Integration-Assertion"].split(".");
    const assertion = JSON.parse(Buffer.from(encoded, "base64url"));
    assert.equal(assertion.body_sha256, createHash("sha256").update(sent.body).digest("hex"));
    assert.equal(assertion.max_user_id, "42");
    const key = createHmac("sha256", app.env.MAX_BOT_TOKEN).update("omnia:integration-assertion:v1").digest();
    assert.equal(signature, createHmac("sha256", key).update(`${version}.${encoded}`).digest("hex"));
    const replay = await app.post({ eventType, eventId: receipt.toLowerCase() });
    assert.equal(replay.status, 204);
    assert.equal(app.calls[1].body, sent.body);
  });
}

test("telemetry excludes previews, rejects missing/forged auth and never dispatches health or arbitrary input", async () => {
  for (const config of [{ actor: null }, { corrupt: true }]) {
    const app = runtime(config);
    assert.equal((await app.post({ eventType: "cart_add", eventId: receipt })).status, 401);
    assert.equal(app.calls.length, 0);
  }
  for (const config of [{ actor: "preview", publicApp: false }, { publicApp: false }]) {
    const app = runtime(config);
    assert.equal((await app.post({ eventType: "cart_add", eventId: receipt })).status, 204);
    assert.equal(app.calls.length, 0);
  }
  for (const body of [null, { eventType: "app_open", eventId: receipt },
    { eventType: "omnia_health_probe", eventId: receipt }, { eventType: "checkout", eventId: receipt },
    { eventType: "cart_add", eventId: "bad" }, { eventType: "cart_add", eventId: receipt, properties: { email: "private" } }]) {
    const app = runtime();
    assert.equal((await app.post(body)).status, 400);
    assert.equal(app.calls.length, 0);
  }
});

test("telemetry returns retryable failure when bounded platform delivery fails", async () => {
  const app = runtime({ outcomes: ["network", 503] });
  assert.equal((await app.post({ eventType: "cart_add", eventId: receipt })).status, 503);
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[0].body, app.calls[1].body);
});

test("receipt forwarding fails closed after key revocation and without a relay", async () => {
  const app=runtime(); delete app.env.MAX_BOT_TOKEN;
  app.env.YLEUM_BUSINESS_ANALYTICS_RELAY="http://analytics-relay:3128/__yleum/business-analytics";
  const analytics=app.load("lib/omnia/analytics.ts");
  assert.equal(await analytics.forwardMaxAnalytics("42",receipt,"action","order_created"),false);
  assert.equal(app.calls.length,0);
  app.env.YLEUM_BUSINESS_ANALYTICS_KEY="ab".repeat(32);
  delete app.env.YLEUM_BUSINESS_ANALYTICS_RELAY;
  assert.equal(await analytics.forwardMaxAnalytics("42",receipt,"action","order_created"),false);
  assert.equal(app.calls.length,0);
});
