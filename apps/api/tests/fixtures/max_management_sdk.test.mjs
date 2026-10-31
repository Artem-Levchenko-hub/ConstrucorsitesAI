import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createHash, createHmac, webcrypto } from "node:crypto";
import { createRequire, stripTypeScriptTypes } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";
const base = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../orchestrator/templates/max-miniapp-nextjs",
);
const nativeRequire = createRequire(import.meta.url);
const fixture = JSON.parse(
  readFileSync(
    new URL("./max_management_contract_v1.json", import.meta.url),
    "utf8",
  ),
);
const ids = [
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
];
function runtime(overrides = {}, transport, browser, initialActor = "42") {
  const env = {
    YLEUM_OWNER_ASSERTION_KEY: "ab".repeat(32),
    YLEUM_OWNER_PROJECT_ID: ids[0],
    YLEUM_OWNER_ID: ids[1],
    YLEUM_OWNER_PUBLICATION_ID: ids[2],
    ...overrides,
  };
  const cache = new Map(),
    calls = [],
    dependencies = new Map();
  function load(path) {
    if (cache.has(path)) return cache.get(path);
    assert.ok(
      existsSync(resolve(base, "src", path)),
      `Managed module missing: ${path}`,
    );
    let source = stripTypeScriptTypes(
      readFileSync(resolve(base, "src", path), "utf8"),
    );
    source = source
      .replace(
        /import\s+\{([^}]+)\}\s+from\s+["']([^"']+)["'];?/g,
        (_, b, n) => `const {${b.replace(/\bas\b/g, ":")}}=require('${n}');`,
      )
      .replace(
        /await import\(\s*["']([^"']+)["']\s*\)/g,
        (_, n) => `require('${n}')`,
      );
    const names = [];
    source = source.replace(
      /export\s+((?:async\s+)?(?:function|class|const|let|var)\s+(\w+))/g,
      (_, d, n) => {
        names.push(n);
        return d;
      },
    );
    const exports = {};
    cache.set(path, exports);
    vm.runInNewContext(
      source + `\nObject.assign(exports,{${names}});`,
      {
        exports,
        Buffer,
        TextDecoder,
        TextEncoder,
        URL,
        URLSearchParams,
        Date,
        Request,
        Response,
        Headers,
        AbortSignal,
        setTimeout,
        clearTimeout,
        ...(browser ? { window: browser } : {}),
        crypto: webcrypto,
        process: { env, cwd: () => base },
        fetch: async (url, opts) => {
          calls.push({ url, ...opts });
          if (transport) return transport(url, opts);
          return new Response(
            JSON.stringify({
              error: { code: "quote_changed", message: "Changed" },
              quote: { configVersion: 9 },
            }),
            { status: 409 },
          );
        },
        require: (n) => {
          if (dependencies.has(n)) return dependencies.get(n);
          if (n === "@/lib/db") throw Error("DB opened before authentication");
          if (n.startsWith("@/")) return load(n.slice(2) + ".ts");
          return nativeRequire(n);
        },
      },
      { filename: path },
    );
    return exports;
  }
  let identityOwner;
  const identity = () => load("lib/omnia/identity-scope.ts");
  const setActor = (actor, mode = "max") => {
    const scope = identity();
    identityOwner ||= scope.createIdentityController();
    identityOwner.commit(identityOwner.begin(), { actor, mode });
  };
  if (browser) setActor(initialActor);
  return { env, load, calls, dependencies, setActor,
    identitySnapshot: () => identity().getIdentitySnapshot() };
}
function sign(app, body, patch = {}) {
  const now = Math.floor(Date.now() / 1000),
    claims = {
      project_id: ids[0],
      owner_id: ids[1],
      publication_id: ids[2],
      contract_hash: fixture.contract_hash,
      method: "POST",
      path: "/api/omnia/owner",
      body_sha256: createHash("sha256").update(body).digest("hex"),
      iat: now,
      exp: now + 60,
      ...patch,
    };
  const encoded = Buffer.from(JSON.stringify(claims)).toString("base64url");
  return `1.${encoded}.${createHmac("sha256", Buffer.from(app.env.YLEUM_OWNER_ASSERTION_KEY, "hex")).update(`1.${encoded}`).digest("base64url")}`;
}
const lead = { operationKey: ids[0], customerName: "Анна", comment: "Запрос" };
function memoryStorage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) };
}
function intentBrowser(origin = "https://one.app.test", localStorage = memoryStorage(), sessionStorage = memoryStorage()) {
  return { location: { origin }, localStorage, sessionStorage };
}
function leadReceipt(id = ids[2]) {
  return { id, resource: "leads", created_at: "2026-10-29T09:00:00Z", revision: "a".repeat(64), status: "new", customer_name: "Анна", items: [] };
}
function verifiedTransport(post, actor = () => "42") {
  return async (url, options) => url === "/api/max/session"
    ? Response.json({ user: { id: actor() } }) : post(url, options);
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function deferredJson() {
  const reading = deferred(); let stream;
  const response = new Response(new ReadableStream({ start(controller) { stream = controller; } }),
    { headers: { "Content-Type": "application/json" } });
  const json = response.json.bind(response);
  response.json = () => { reading.resolve(); return json(); };
  return { response, reading: reading.promise,
    resolve(value) { stream.enqueue(new TextEncoder().encode(JSON.stringify(value))); stream.close(); } };
}

for (const restoreActor of [false, true]) for (const duringDecode of [false, true])
test(`late POST cannot expose A receipt after ${restoreActor ? "A-B-A" : "A-B"} during ${duringDecode ? "decode" : "fetch"}`, async () => {
  const browser = intentBrowser(), response = deferred(), sent = deferred();
  const decoded = deferredJson();
  let actor = "42", posts = 0;
  const app = runtime({}, verifiedTransport((_url, options) => {
    posts++;
    if (JSON.parse(options.body).operationKey === ids[0]) { sent.resolve(); return duringDecode ? decoded.response : response.promise; }
    throw Error("B response unknown");
  }, () => actor), browser);
  const client = app.load("lib/omnia/business-client.ts");
  const pending = client.createManagedLead(lead);
  await sent.promise;
  if (duringDecode) await decoded.reading;
  actor = "43"; app.setActor(actor);
  await assert.rejects(client.createManagedLead({ ...lead, operationKey: ids[1], customerName: "B" }));
  const before = [...browser.localStorage.values.entries()];
  if (restoreActor) { actor = "42"; app.setActor(actor); }
  if (duringDecode) decoded.resolve(leadReceipt());
  else response.resolve(Response.json(leadReceipt()));
  await assert.rejects(pending, error => error.code === "actor_changed");
  assert.deepEqual([...browser.localStorage.values.entries()], before);
  assert.equal(posts, 2, "one POST per deliberate intent; no automatic replay");
});

for (const duringDecode of [false, true]) test(`history is fenced after ${duringDecode ? "JSON decode" : "fetch"}`, async () => {
  const sent = deferred(), response = deferred(), decoded = deferredJson();
  const app = runtime({}, () => {
    sent.resolve();
    return duringDecode ? decoded.response : response.promise;
  }, intentBrowser());
  const pending = app.load("lib/omnia/business-client.ts").getManagedRecords("leads");
  await sent.promise;
  if (duringDecode) await decoded.reading;
  app.setActor("43");
  const payload = { records: [leadReceipt()], next_cursor: null };
  if (duringDecode) decoded.resolve(payload);
  else response.resolve(Response.json(payload));
  await assert.rejects(pending, error => error.code === "actor_changed");
  assert.equal(app.calls.length, 1, "history does not add session lookups");
  assert.equal(app.calls[0].headers["X-Max-Expected-Actor"], "42");
});

test("late A 401 cannot invalidate B; plain current 401/403 are classified without JSON", async () => {
  const response = deferred(), sent = deferred();
  const app = runtime({}, () => { sent.resolve(); return response.promise; }, intentBrowser());
  const pending = app.load("lib/omnia/business-client.ts").getManagedRecords("leads");
  await sent.promise; app.setActor("43");
  response.resolve(new Response("expired", { status: 401 }));
  await assert.rejects(pending, error => error.code === "actor_changed");
  assert.equal(app.identitySnapshot().principal.actor, "43");
  for (const [status, code] of [[401, "unauthorized"], [403, "forbidden"]]) {
    const current = runtime({}, () => new Response("denied", { status }), intentBrowser());
    await assert.rejects(current.load("lib/omnia/business-client.ts").getManagedRecords("leads"),
      error => error.status === status && error.code === code);
    if (status === 401) assert.equal(current.identitySnapshot().principal, null);
  }
});

for (const duringDecode of [false, true]) test(`late session lookup during ${duringDecode ? "decode" : "fetch"} cannot read A pending input in B`, async () => {
  const browser = intentBrowser(), response = deferred(), sent = deferred(), decoded = deferredJson();
  let sessions = 0, reads = 0;
  const app = runtime({}, (url) => {
    if (url === "/api/max/session") {
      if (++sessions === 2) { sent.resolve(); return duringDecode ? decoded.response : response.promise; }
      return Response.json({ user: { id: "42" } });
    }
    throw Error("unknown POST outcome");
  }, browser);
  const client = app.load("lib/omnia/business-client.ts");
  await assert.rejects(client.createManagedLead(lead));
  for (const store of [browser.localStorage, browser.sessionStorage]) {
    const get = store.getItem; store.getItem = key => { reads++; return get(key); };
  }
  const before = [...browser.localStorage.values.entries()];
  const pending = client.loadPendingManagedIntent("leads");
  await sent.promise;
  if (duringDecode) await decoded.reading;
  app.setActor("43");
  if (duringDecode) decoded.resolve({ user: { id: "42" } });
  else response.resolve(Response.json({ user: { id: "42" } }));
  await assert.rejects(pending, error => error.code === "actor_changed");
  assert.equal(reads, 0);
  assert.deepEqual([...browser.localStorage.values.entries()], before);
});
test("durable browser intent replays committed lost response after remount with one record", async () => {
  const browser = intentBrowser(), rows = new Map(), posted = [], rawBodies = []; let lost = true;
  const transport = async (url, options) => {
    if (url === "/api/max/session") return Response.json({ user: { id: "42" } });
    const body = JSON.parse(options.body); posted.push(body); rawBodies.push(options.body);
    if (!rows.has(body.operationKey)) rows.set(body.operationKey, leadReceipt());
    if (lost) { lost = false; throw Error("response lost after commit"); }
    return Response.json(rows.get(body.operationKey), { status: 201 });
  };
  await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(lead), e => e.code === "unavailable");
  await runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead({ ...lead, operationKey: ids[1] });
  assert.equal(posted[1].operationKey, posted[0].operationKey);
  assert.equal(rawBodies[1], rawBodies[0]);
  assert.equal(rows.size, 1);
  assert.equal(browser.localStorage.values.size, 0);
  assert.equal(browser.sessionStorage.values.size, 0);
});
test("pending restoration verifies actor first and isolates actors and origins without discarding old keys", async () => {
  const browser = intentBrowser(); let actor = "42", reads = 0, verified = false;
  const original = browser.localStorage.getItem;
  browser.localStorage.getItem = key => { assert.equal(verified, true); reads++; return original(key); };
  const transport = async (url, options) => {
    if (url === "/api/max/session") { verified = true; return Response.json({ user: { id: actor } }); }
    assert.equal(options.headers["X-Max-Expected-Actor"], actor); throw Error("lost");
  };
  const client = () => runtime({}, transport, browser, actor).load("lib/omnia/business-client.ts");
  await assert.rejects(client().createManagedLead(lead));
  const originalKeys = [...browser.localStorage.values.keys()];
  actor = "43"; verified = false;
  assert.equal(await client().loadPendingManagedIntent("leads"), null);
  assert.deepEqual([...browser.localStorage.values.keys()], originalKeys);
  actor = "42"; verified = false;
  const pending = await client().loadPendingManagedIntent("leads");
  assert.equal(pending.operationKey, lead.operationKey); assert.equal(Object.isFrozen(pending), true);
  browser.location.origin = "https://two.app.test"; verified = false;
  assert.equal(await client().loadPendingManagedIntent("leads"), null);
  assert.deepEqual([...browser.localStorage.values.keys()], originalKeys);
  assert.ok(reads > 0);
  const noSession = runtime({}, () => Response.json({ error: "auth" }, { status: 401 }), browser);
  const before = reads;
  await assert.rejects(noSession.load("lib/omnia/business-client.ts").loadPendingManagedIntent("leads"), e => e.code === "unauthorized");
  assert.equal(reads, before);
});
test("loaded and attempted intent provenance denies cross-actor recovery before touching the new actor storage", async () => {
  for (const fromLoad of [false, true]) {
    const browser = intentBrowser(), rows = new Map(), posted = [], reads = [];
    for (const storage of [browser.localStorage, browser.sessionStorage]) {
      const read = storage.getItem; storage.getItem = key => { reads.push(key); return read(key); };
    }
    let actor = "42", lost = true;
    const transport = verifiedTransport((_, options) => {
      const body = JSON.parse(options.body), key = `${actor}:${body.operationKey.toLowerCase()}`;
      posted.push({ actor, key: body.operationKey });
      if (!rows.has(key)) rows.set(key, leadReceipt(actor === "42" ? ids[1] : ids[2]));
      if (lost) { lost = false; throw Error("response lost after commit"); }
      return Response.json(rows.get(key));
    }, () => actor);
    const original = { ...lead, operationKey: "abcdefab-1234-4123-8123-abcdefabcdef" };
    const producerApp = runtime({}, transport, browser);
    const producer = producerApp.load("lib/omnia/business-client.ts");
    await assert.rejects(producer.createManagedLead(original));
    const clientApp = fromLoad ? runtime({}, transport, browser) : producerApp;
    const client = clientApp.load("lib/omnia/business-client.ts");
    const pending = fromLoad ? await client.loadPendingManagedIntent("leads") : original;
    const preserved = [...browser.localStorage.values.entries()], before = reads.length;
    actor = "43";
    clientApp.setActor(actor);
    await assert.rejects(client.createManagedLead({ ...pending, operationKey: pending.operationKey.toUpperCase() }), e => e.code === "actor_changed" && e.status === 409);
    assert.equal(reads.length, before); assert.equal(posted.length, 1); assert.deepEqual([...browser.localStorage.values.entries()], preserved);
    assert.equal(rows.size, 1);
    actor = "42";
    clientApp.setActor(actor);
    assert.equal((await client.createManagedLead({ ...pending })).id, ids[1]);
    assert.equal(rows.size, 1); assert.equal(posted[1].key.toLowerCase(), original.operationKey);
    // A genuinely new B form has its own key and may create its own record.
    actor = "43";
    clientApp.setActor(actor);
    assert.equal((await client.createManagedLead({ ...lead, operationKey: ids[2], customerName: "New B form" })).id, ids[2]);
    assert.equal(rows.size, 2); assert.deepEqual(posted.map(p => p.actor), ["42", "42", "43"]);
  }
});
test("a loaded typed intent cannot be adopted by a different verified project origin", async () => {
  const browser = intentBrowser(); let posts = 0, lost = true;
  const transport = verifiedTransport(() => { posts++; if (lost) { lost = false; throw Error("lost"); } return Response.json(leadReceipt()); });
  await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(lead));
  const client = runtime({}, transport, browser).load("lib/omnia/business-client.ts"), pending = await client.loadPendingManagedIntent("leads");
  const original = [...browser.localStorage.values.entries()]; browser.location.origin = "https://two.app.test";
  await assert.rejects(client.createManagedLead({ ...pending }), e => e.code === "actor_changed");
  assert.equal(posts, 1); assert.deepEqual([...browser.localStorage.values.entries()], original);
  browser.location.origin = "https://one.app.test";
  assert.equal((await client.createManagedLead({ ...pending })).id, ids[2]); assert.equal(posts, 2);
});
test("pending matching ignores a new key and property order but changed body cannot POST or overwrite", async () => {
  const browser = intentBrowser(); let posts = 0;
  const transport = verifiedTransport(() => { posts++; throw Error("lost"); });
  const client = () => runtime({}, transport, browser).load("lib/omnia/business-client.ts");
  await assert.rejects(client().createManagedLead(lead));
  const original = [...browser.localStorage.values.values()];
  await assert.rejects(client().createManagedLead({ ...lead, operationKey: ids[1], comment: "Different" }), e => e.code === "pending_intent");
  assert.equal(posts, 1); assert.deepEqual([...browser.localStorage.values.values()], original);
  await assert.rejects(client().createManagedLead({ comment: lead.comment, customerName: lead.customerName, operationKey: ids[1] }));
  assert.equal(posts, 2); assert.deepEqual([...browser.localStorage.values.values()], original);
});
test("malformed unknown storage blocks, never expires or silently starts a new intent", async () => {
  for (const corrupt of [() => "{", () => " ".repeat(65537), v => JSON.stringify({ ...v, version: 2 }),
    v => JSON.stringify({ ...v, origin: "https://foreign.test" }), v => JSON.stringify({ ...v, actor: "other" }),
    v => JSON.stringify({ ...v, input: { ...v.input, operationKey: "wrong" } }),
    v => JSON.stringify({ ...v, input: { ...v.input, unexpected: true } })]) {
    const browser = intentBrowser(); let posts = 0;
    const transport = verifiedTransport(() => { posts++; throw Error("lost"); });
    const first = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
    await assert.rejects(first.createManagedLead(lead));
    const key = [...browser.localStorage.values.keys()][0], raw = corrupt(JSON.parse(browser.localStorage.values.get(key)));
    browser.localStorage.setItem(key, raw);
    const next = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
    await assert.rejects(next.loadPendingManagedIntent("leads"), e => e.code === "pending_intent");
    await assert.rejects(next.createManagedLead({ ...lead, operationKey: ids[1] }), e => e.code === "pending_intent");
    assert.equal(posts, 1); assert.equal(browser.localStorage.getItem(key), raw);
  }
});
test("storage fallback is durable and both private stores unavailable fail before POST", async () => {
  const denied = { getItem() { throw Error("private"); }, setItem() { throw Error("private"); }, removeItem() { throw Error("private"); } };
  for (const local of [{ getItem: () => null, setItem() { throw Error("quota"); }, removeItem() {} }]) {
    const browser = intentBrowser(undefined, local); let posted;
    const transport = verifiedTransport((_, options) => { posted = JSON.parse(options.body); throw Error("lost"); });
    await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(lead));
    assert.equal(posted.operationKey, lead.operationKey);
    const restored = await runtime({}, transport, browser).load("lib/omnia/business-client.ts").loadPendingManagedIntent("leads");
    assert.equal(restored.operationKey, lead.operationKey);
    browser.localStorage = denied;
    assert.equal((await runtime({}, transport, browser).load("lib/omnia/business-client.ts").loadPendingManagedIntent("leads")).operationKey, lead.operationKey);
  }
  const browser = intentBrowser(undefined, denied, denied); let posts = 0;
  await assert.rejects(runtime({}, verifiedTransport(() => { posts++; }), browser).load("lib/omnia/business-client.ts").createManagedLead(lead), e => e.code === "intent_storage_unavailable");
  assert.equal(posts, 0);
  const writeDenied = { getItem: () => null, setItem() { throw Error("quota"); }, removeItem() {} };
  await assert.rejects(runtime({}, verifiedTransport(() => { posts++; }), intentBrowser(undefined, writeDenied, writeDenied)).load("lib/omnia/business-client.ts").createManagedLead(lead), e => e.code === "intent_storage_unavailable");
  assert.equal(posts, 0);
});
test("inaccessible primary cannot strand a previously unknown key and create a new secondary intent", async () => {
  const local = memoryStorage(), secondary = memoryStorage(), write = secondary.setItem;
  secondary.setItem = () => { throw Error("quota"); };
  const browser = intentBrowser(undefined, local, secondary); let posts = 0;
  const transport = verifiedTransport(() => { posts++; throw Error("lost after commit"); });
  await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(lead));
  assert.equal(local.values.size, 1); assert.equal(secondary.values.size, 0);
  secondary.setItem = write;
  browser.localStorage = { getItem() { throw Error("blocked later"); } };
  const next = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
  await assert.rejects(next.createManagedLead({ ...lead, operationKey: ids[1] }), e => e.code === "intent_storage_unavailable");
  assert.equal(posts, 1); assert.equal(secondary.values.size, 0);
});
test("storage cleanup failure after a valid committed acknowledgement still returns the receipt", async () => {
  const browser = intentBrowser(); let posts = 0;
  browser.localStorage.removeItem = browser.sessionStorage.removeItem = () => { throw Error("cleanup denied"); };
  const transport = verifiedTransport(() => { posts++; return Response.json(leadReceipt()); });
  const client = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
  assert.equal((await client.createManagedLead(lead)).id, ids[2]);
  await assert.rejects(client.loadPendingManagedIntent("leads"), e => e.code === "intent_storage_unavailable");
  assert.equal(posts, 1);
  const remount = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
  const recovered = await remount.loadPendingManagedIntent("leads");
  assert.equal(recovered.operationKey, lead.operationKey); assert.equal(posts, 1);
  assert.equal((await remount.createManagedLead(recovered)).id, ids[2]);
  await assert.rejects(remount.createManagedLead({ ...lead, operationKey: ids[1] }), e => e.code === "intent_storage_unavailable");
  assert.equal(posts, 2);
});
test("two deliberate identical intents never turn a known acknowledged cleanup failure into false second success", async () => {
  for (const resource of ["leads", "orders"]) for (const cleanupFails of [false, true]) {
    const browser = intentBrowser(), localRemove = browser.localStorage.removeItem, sessionRemove = browser.sessionStorage.removeItem;
    if (cleanupFails) browser.localStorage.removeItem = browser.sessionStorage.removeItem = () => { throw Error("cleanup denied"); };
    const rows = new Map(); let posts = 0;
    const client = runtime({}, verifiedTransport((_, options) => {
      posts++; const body = JSON.parse(options.body);
      if (!rows.has(body.operationKey)) rows.set(body.operationKey, { ...leadReceipt(rows.size ? ids[2] : ids[1]), resource, ...(resource === "orders" ? { total_kopecks: 25000, currency: "RUB" } : {}) });
      return Response.json(rows.get(body.operationKey));
    }), browser).load("lib/omnia/business-client.ts");
    const input = resource === "leads" ? lead : { ...lead, configVersion: 1, items: [{ catalogId: "coffee", quantity: 1, unitPriceKopecks: 25000 }] };
    const create = resource === "leads" ? client.createManagedLead : client.createManagedOrder;
    assert.equal((await create(input)).id, ids[1]);
    if (cleanupFails) {
      await assert.rejects(create({ ...input, operationKey: ids[1] }), e => e.code === "intent_storage_unavailable");
      assert.equal(posts, 1); assert.equal(rows.size, 1);
    }
    browser.localStorage.removeItem = localRemove; browser.sessionStorage.removeItem = sessionRemove;
    assert.equal((await create({ ...input, operationKey: ids[1] })).id, ids[2]);
    assert.equal(posts, 2); assert.equal(rows.size, 2);
  }
});
test("only first fresh validation and post-receipt quote rejection clear pending; unknown outcomes retain", async () => {
  for (const [status, code] of [[500, "unavailable"], [409, "operation_conflict"], [401, "unauthorized"], [409, "actor_changed"], [409, "unsupported"], [400, "unknown_code"]]) {
    const browser = intentBrowser(), transport = verifiedTransport(() => Response.json({ error: { code } }, { status }));
    const app = runtime({}, transport, browser), client = app.load("lib/omnia/business-client.ts");
    await assert.rejects(client.createManagedLead(lead));
    // Authentication failures preserve the pending body but require a newly
    // verified Provider scope before it may be restored by the same actor.
    assert.equal(browser.localStorage.values.size, 1);
    app.setActor("42");
    assert.equal((await client.loadPendingManagedIntent("leads")).operationKey, lead.operationKey);
  }
  for (const code of ["invalid_request", "quote_changed"]) {
    const browser = intentBrowser(); let reject = true;
    const transport = verifiedTransport(() => reject ? Response.json({ error: { code } }, { status: code === "invalid_request" ? 400 : 409 }) : Response.json(leadReceipt()));
    const client = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
    await assert.rejects(client.createManagedLead(lead));
    assert.equal(await client.loadPendingManagedIntent("leads"), null);
    reject = false;
    assert.equal((await client.createManagedLead({ ...lead, operationKey: ids[1], comment: "Corrected" })).id, ids[2]);
  }
  const browser = intentBrowser(); let recovered = false;
  const transport = verifiedTransport(() => { if (!recovered) throw Error("lost"); return Response.json({ error: { code: "invalid_request" } }, { status: 400 }); });
  await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(lead));
  recovered = true;
  const client = runtime({}, transport, browser).load("lib/omnia/business-client.ts");
  await assert.rejects(client.createManagedLead(lead));
  assert.equal((await client.loadPendingManagedIntent("leads")).operationKey, lead.operationKey);
});
test("browser malformed receipts retain frozen order and booking bodies without automatic retries", async () => {
  for (const resource of ["orders", "bookings"]) {
    const browser = intentBrowser(); let posts = 0;
    const input = resource === "orders" ? { ...lead, configVersion: 1, items: [{ catalogId: "coffee", quantity: 1, unitPriceKopecks: 25000 }], fulfillment: { type: "pickup", delayMinutes: 15 } }
      : { ...lead, serviceId: "consult", requestedFor: "2030-06-10T08:00:00Z" };
    const client = runtime({}, verifiedTransport(() => { posts++; return Response.json({}); }), browser).load("lib/omnia/business-client.ts");
    await assert.rejects(client[resource === "orders" ? "createManagedOrder" : "createManagedBooking"](input), e => e.code === "unavailable");
    const saved = await client.loadPendingManagedIntent(resource);
    assert.equal(saved.operationKey, input.operationKey); assert.equal(Object.isFrozen(saved), true);
    if (resource === "orders") { assert.equal(Object.isFrozen(saved.items), true); assert.equal(Object.isFrozen(saved.items[0]), true); assert.equal(Object.isFrozen(saved.fulfillment), true); }
    assert.equal(posts, 1);
  }
});
test("browser validates bounded strict intent bodies and history does not perform session lookups", async () => {
  for (const input of [null, "text", { ...lead, operationKey: "bad" }, { ...lead, customerName: " " }, { ...lead, comment: "x".repeat(2001) },
    { ...lead, private: true }, { ...lead, fulfillment: { type: "pickup", delayMinutes: 15 } }]) {
    const browser = intentBrowser(); let posts = 0;
    const client = runtime({}, verifiedTransport(() => { posts++; }), browser).load("lib/omnia/business-client.ts");
    await assert.rejects(client.createManagedLead(input), e => e.code === "invalid_request");
    assert.equal(posts, 0); assert.equal(browser.localStorage.values.size, 0);
  }
  const app = runtime({}, (url) => { assert.equal(url, "/api/omnia/business/leads"); return Response.json({ records: [], next_cursor: null }); }, intentBrowser());
  await app.load("lib/omnia/business-client.ts").getManagedRecords("leads");
  assert.equal(app.calls.length, 1);
});
test("customer actor pin rejects a session switch before body parsing or database access and preserves old clients", async () => {
  const app = runtime(); let writes = 0;
  app.dependencies.set("@/lib/max/session", { getMaxUser: async () => ({ id: "43" }) });
  app.dependencies.set("@/lib/omnia/management-config", { getManagementContract: async () => fixture });
  app.dependencies.set("@/lib/omnia/business-server", { createManagedBusinessRecord: async (actor) => { assert.equal(actor, "43"); writes++; return leadReceipt(); } });
  const route = app.load("app/api/omnia/business/[resource]/route.ts"), ctx = { params: Promise.resolve({ resource: "leads" }) };
  const rejected = await route.POST(new Request("https://one.app.test/api/omnia/business/leads", { method: "POST", headers: { "X-Max-Expected-Actor": "42" }, body: "not json" }), ctx);
  assert.equal(rejected.status, 409); assert.equal((await rejected.json()).error.code, "actor_changed"); assert.equal(writes, 0);
  for (const headers of [{}, { "X-Max-Expected-Actor": "43" }])
    assert.equal((await route.POST(new Request("https://one.app.test/api/omnia/business/leads", { method: "POST", headers, body: JSON.stringify(lead) }), ctx)).status, 201);
  assert.equal(writes, 2);
});

test("history actor pin rejects a session switch before database reads", async () => {
  const app = runtime(); let reads = 0;
  app.dependencies.set("@/lib/max/session", { getMaxUser: async () => ({ id: "43" }) });
  app.dependencies.set("@/lib/omnia/management-config", { getManagementContract: async () => fixture });
  app.dependencies.set("@/lib/omnia/management-store", { listManagedRecords: async ({ actor }) => {
    assert.equal(actor, "43"); reads++; return { records: [], next_cursor: null };
  } });
  const route = app.load("app/api/omnia/business/[resource]/route.ts"), ctx = { params: Promise.resolve({ resource: "leads" }) };
  const rejected = await route.GET(new Request("https://one.app.test/api/omnia/business/leads", { headers: { "X-Max-Expected-Actor": "42" } }), ctx);
  assert.equal(rejected.status, 409); assert.equal((await rejected.json()).error.code, "actor_changed"); assert.equal(reads, 0);
  for (const headers of [{}, { "X-Max-Expected-Actor": "43" }])
    assert.equal((await route.GET(new Request("https://one.app.test/api/omnia/business/leads", { headers }), ctx)).status, 200);
  assert.equal(reads, 2);
});
test("canonical fixture matches independent TS registry and rejects rehashed capabilities", () => {
  const m = runtime().load("lib/omnia/management-resources.ts");
  assert.equal(
    m.parseManagementContract(fixture).contract_hash,
    fixture.contract_hash,
  );
  const changed = structuredClone(fixture);
  changed.resources[0].action_type = "private_diary";
  changed.contract_hash = m.managementHash(changed);
  assert.throws(() => m.parseManagementContract(changed));
});
test("owner assertion binds every authority dimension and never accepts customer/session keys", () => {
  const app = runtime(),
    m = app.load("lib/omnia/management-auth.ts"),
    body = Buffer.from('{"op":"describe"}');
  const req = new Request("https://app.test/api/omnia/owner", {
    method: "POST",
    headers: { "X-Yleum-Owner-Assertion": sign(app, body) },
  });
  assert.equal(m.verifyOwnerAssertion(req, body, fixture).owner_id, ids[1]);
  for (const patch of [
    { project_id: ids[1] },
    { owner_id: ids[0] },
    { publication_id: ids[0] },
    { contract_hash: "a".repeat(64) },
    { method: "GET" },
    { path: "/elsewhere" },
    { iat: true },
    { exp: 1 },
    { extra: "x" },
    { iat: Math.floor(Date.now() / 1000) + 6 },
  ]) {
    req.headers.set("X-Yleum-Owner-Assertion", sign(app, body, patch));
    assert.throws(() => m.verifyOwnerAssertion(req, body, fixture));
  }
  req.headers.set("X-Yleum-Owner-Assertion", sign(app, body));
  assert.throws(() => m.verifyOwnerAssertion(req, Buffer.from("{}"), fixture));
});
test("strict input rejects private fields, invalid money/options/dates and preserves textual booking price", () => {
  const m = runtime().load("lib/omnia/business-server.ts");
  const config = {
    configVersion: 2,
    config: {
      content: [
        {
          id: "s",
          title: "Сервис",
          price: "от 700 ₽",
          active: true,
          availability: "on_request",
          options: [],
        },
      ],
    },
  };
  assert.throws(() =>
    m.prepareManagedSnapshot("leads", { ...lead, note: "private" }, config),
  );
  assert.equal(
    m.prepareManagedSnapshot(
      "bookings",
      { ...lead, serviceId: "s", requestedFor: "2026-11-01T10:00:00+03:00" },
      config,
    ).service_price_label,
    "от 700 ₽",
  );
  for (const price of ["от 100", "1,000", "-1", "NaN", "1.001"])
    assert.throws(() => m.parseRubPrice(price));
  assert.equal(m.parseRubPrice("0"), 0);
  assert.equal(m.parseRubPrice("10,50 ₽"), 1050);
  assert.throws(() =>
    m.prepareManagedSnapshot(
      "bookings",
      { ...lead, serviceId: "s", requestedFor: "1000" },
      config,
    ),
  );
});
test("order quote changes require explicit second confirmation and historical payload is a snapshot", () => {
  const m = runtime().load("lib/omnia/business-server.ts");
  const config = {
    configVersion: 3,
    config: {
      content: [
        {
          id: "s",
          title: "Услуга",
          price: "12.50",
          active: true,
          availability: "in_stock",
          options: ["Большая"],
        },
      ],
    },
  };
  const input = {
    ...lead,
    configVersion: 2,
    items: [
      {
        catalogId: "s",
        quantity: 2,
        unitPriceKopecks: 1000,
        option: "Большая",
      },
    ],
  };
  let error;
  try {
    m.prepareManagedSnapshot("orders", input, config);
  } catch (e) {
    error = e;
  }
  assert.equal(error.code, "quote_changed");
  assert.equal(error.quote.total_kopecks, 2500);
  const snapshot = m.prepareManagedSnapshot(
    "orders",
    {
      ...input,
      configVersion: 3,
      items: [{ ...input.items[0], unitPriceKopecks: 1250 }],
    },
    config,
  );
  config.config.content[0].title = "Переименована";
  assert.equal(snapshot.items[0].title, "Услуга");
  assert.equal(snapshot.total_kopecks, 2500);
  assert.throws(() =>
    m.prepareManagedSnapshot(
      "orders",
      {
        ...input,
        configVersion: 3,
        items: [{ ...input.items[0], quantity: Number.MAX_SAFE_INTEGER }],
      },
      config,
    ),
  );
});
test("fixed projections exclude raw payload, MAX actor and internal note from customer result", () => {
  const m = runtime().load("lib/omnia/management-resources.ts"),
    row = {
      id: ids[0],
      resource: "leads",
      created_at: new Date("2026-10-29T12:00:00Z"),
      status: "new",
      revision: "a".repeat(64),
      note: "secret",
      payload: {
        management_protocol: 1,
        resource: "leads",
        customer_name: "Анна",
        items: [],
        private: "secret",
        note: "forged",
        max_user_id: "42",
      },
    };
  const owner = m.projectManagedRecord(row, true),
    customer = m.projectManagedRecord(row, false);
  assert.equal(owner.note, "secret");
  assert.equal(customer.note, undefined);
  assert.equal(customer.private, undefined);
  assert.equal(customer.max_user_id, undefined);
  assert.equal(customer.customer_name, "Анна");
});
test("SDK preserves caller receipt and quote conflict without automatic POST retry", async () => {
  const app = runtime();
  const m = app.load("lib/omnia/business-client.ts");
  await assert.rejects(
    m.createManagedLead(lead),
    (e) => e.code === "quote_changed" && e.quote.configVersion === 9,
  );
  assert.equal(app.calls.length, 1);
  assert.equal(JSON.parse(app.calls[0].body).operationKey, ids[0]);
});
test("unauthenticated owner route fails before database imports", async () => {
  const app = runtime();
  const route = app.load("app/api/omnia/owner/route.ts");
  const result = await route.POST(
    new Request("https://app.test/api/omnia/owner", {
      method: "POST",
      body: '{"op":"describe"}',
    }),
  );
  assert.equal(result.status, 401);
  assert.equal(
    JSON.stringify(await result.json()).includes("DB opened"),
    false,
  );
});
export { runtime, fixture, ids };

// The same production SQL runs locally in PostgreSQL WASM, or in CI against
// native PostgreSQL with six independent runtime connections. Never accept an
// external/database name that could refer to a customer or production database.
const pgliteModule = process.env.YLEUM_MANAGEMENT_PGLITE;
const nativePgUrl = process.env.YLEUM_MANAGEMENT_TEST_DATABASE_URL;
async function managementDatabase() {
  if (nativePgUrl) {
    const target = new URL(nativePgUrl);
    assert.ok(["localhost", "127.0.0.1", "[::1]"].includes(target.hostname));
    assert.match(target.pathname, /^\/owner_management_test[a-z0-9_]*$/);
    const { Pool } = createRequire(resolve(base, "package.json"))("pg");
    const adminPool = new Pool({ connectionString: nativePgUrl, max: 1 });
    const admin = await adminPool.connect();
    const name = `managed_${process.pid}_${Date.now()}`;
    const role = `${name}_runtime`,
      schema = `${name}_test`;
    const replace = (text) =>
      text
        .replaceAll("managed_runtime", role)
        .replaceAll("managed_test", schema);
    const clients = new Pool({ connectionString: nativePgUrl, max: 6 });
    const pool = {
      connect: async () => {
        const client = await clients.connect();
        await client.query(
          `SET ROLE ${role}; SET search_path TO ${schema},public`,
        );
        return client;
      },
    };
    return {
      pool,
      engine: {
        exec: (text) => admin.query(replace(text)),
        query: (text, params) => admin.query(text, params),
        close: async () => {
          await clients.end();
          await admin.query(
            `RESET ROLE; DROP SCHEMA ${schema} CASCADE; DROP ROLE ${role}`,
          );
          admin.release();
          await adminPool.end();
        },
      },
    };
  }
  const { PGlite } = await import(pgliteModule);
  const engine = new PGlite();
  let release = Promise.resolve();
  const pool = {
    connect: async () => {
      const prev = release;
      let unlock;
      release = new Promise((r) => (unlock = r));
      await prev;
      return {
        query: async (text, params = []) => {
          const result = await engine.query(text, params);
          return {
            ...result,
            rowCount: result.affectedRows ?? result.rows.length,
          };
        },
        release: () => unlock(),
      };
    },
  };
  return { engine, pool };
}
test(
  "managed store: receipts, RLS, snapshots, private forgery, concurrency, owner revisions and stable pages",
  { skip: !pgliteModule && !nativePgUrl },
  async (t) => {
    assert.ok(
      existsSync(resolve(base, "drizzle/0003_owner_management.sql")),
      "Managed migration missing",
    );
    assert.ok(
      existsSync(resolve(base, "src/lib/omnia/management-store.ts")),
      "Managed store missing",
    );
    const { engine, pool } = await managementDatabase();
    t.after(() => engine.close());
    await engine.exec(
      `CREATE ROLE managed_runtime; CREATE SCHEMA managed_test; SET search_path TO managed_test,public;`,
    );
    // Store tests start from portable product schema: deliberately no max_users.
    const migration = readFileSync(
      resolve(base, "drizzle/0003_owner_management.sql"),
      "utf8",
    );
    await engine.exec(migration.replaceAll("--> statement-breakpoint", ""));
    await engine.exec(
      "GRANT USAGE ON SCHEMA managed_test TO managed_runtime; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA managed_test TO managed_runtime; SET ROLE managed_runtime",
    );
    const app = runtime();
    app.dependencies.set("@/lib/db", { pool });
    const store = app.load("lib/omnia/management-store.ts");
    const resources = app.load("lib/omnia/management-resources.ts");
    const owner = {
      project_id: ids[0],
      owner_id: ids[1],
      publication_id: ids[2],
    };
    const booking = await store.createManagedRecord("42", "bookings", {operationKey: ids[2], customerName:"Tomorrow"}, () => ({management_protocol:1,resource:"bookings",customer_name:"Tomorrow",requested_for:"2030-06-10T08:00:00.000Z",service_title:"Service",items:[]}));
    const bookingQuery = {resource:"bookings",limit:1,query:"",requested_from:"2030-06-10T00:00:00Z",requested_to:"2030-06-10T23:59:59Z"};
    assert.equal((await store.listManagedRecords({owner},bookingQuery)).records[0].id,booking.record.id);
    assert.equal((await store.listManagedRecords({owner},{...bookingQuery,requested_from:"2030-06-11T00:00:00Z",requested_to:"2030-06-11T23:59:59Z"})).records.length,0);
    assert.equal((await store.listManagedRecords({owner},{...bookingQuery,created_from:"2030-06-10T00:00:00Z"})).records.length,0);
    const snapshot = () => ({
      management_protocol: 1,
      resource: "leads",
      customer_name: "Анна",
      items: [],
      comment: "История",
    });
    const first = await store.createManagedRecord(
      "42",
      "leads",
      lead,
      snapshot,
    );
    assert.equal(first.created, true);
    const replay = await store.createManagedRecord("42", "leads", lead, () => {
      throw Error("must replay before catalog");
    });
    assert.equal(replay.record.id, first.record.id);
    assert.equal(replay.created, false);
    await assert.rejects(
      store.createManagedRecord(
        "42",
        "leads",
        { ...lead, comment: "changed" },
        snapshot,
      ),
      (e) => e.code === "operation_conflict",
    );
    const op = { ...lead, operationKey: ids[1] };
    const concurrent = await Promise.all(
      Array.from({ length: 5 }, () =>
        store.createManagedRecord("42", "leads", op, snapshot),
      ),
    );
    assert.equal(new Set(concurrent.map((r) => r.record.id)).size, 1);
    assert.equal(concurrent.filter((r) => r.created).length, 1);
    await store.createManagedRecord("43", "leads", lead, () => ({
      ...snapshot(),
      customer_name: "Другой",
    }));
    const query = resources.parseRecordsQuery({ resource: "leads" });
    assert.equal(
      (await store.listManagedRecords({ actor: "42" }, query)).records.length,
      2,
    );
    assert.equal(
      (await store.listManagedRecords({ actor: "44" }, query)).records.length,
      0,
    );
    // A canonical-looking payload alone must never grant owner access.
    const client = await pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("select set_config('app.max_user_id','42',true)");
      await client.query(
        "INSERT INTO max_business_actions(id,max_user_id,action_type,payload) VALUES($1,'42','lead_created',$2)",
        [ids[2], JSON.stringify(snapshot())],
      );
      await client.query(
        "INSERT INTO max_business_actions(id,max_user_id,action_type,payload) VALUES('44444444-4444-4444-8444-444444444444','42','private_diary',$1)",
        [JSON.stringify({ secret: "private" })],
      );
      await client.query("COMMIT");
    } finally {
      client.release();
    }
    const rls = await pool.connect();
    try {
      await rls.query("BEGIN");
      await rls.query("SELECT set_config('app.max_user_id','42',true)");
      // Deliberately no application WHERE clause: this assertion proves RLS.
      const visible = await rls.query(
        "SELECT DISTINCT max_user_id FROM max_business_actions",
      );
      assert.deepEqual(
        visible.rows.map((row) => row.max_user_id),
        ["42"],
      );
      assert.equal(
        (await rls.query("SELECT * FROM max_owner_audit")).rows.length,
        0,
      );
      await rls.query("COMMIT");
    } finally {
      rls.release();
    }
    const all = await store.listManagedRecords({ owner }, query);
    assert.equal(all.records.length, 3);
    assert.equal(
      all.records.some((r) => r.id === ids[2]),
      false,
    );
    const update = {
      expected_revision: first.record.revision,
      operation_key: ids[0],
      status: "in_progress",
      note: "Внутренняя",
    };
    const changed = await store.updateManagedRecord(
      owner,
      first.record.id,
      "leads",
      update,
    );
    assert.equal(changed.status, "in_progress");
    assert.equal(changed.note, "Внутренняя");
    assert.equal(changed.comment, "История");
    const again = await store.updateManagedRecord(
      owner,
      first.record.id,
      "leads",
      update,
    );
    assert.equal(again.revision, changed.revision);
    await assert.rejects(
      store.updateManagedRecord(owner, first.record.id, "leads", {
        ...update,
        operation_key: ids[2],
      }),
      (e) => e.code === "revision_conflict",
    );
    await assert.rejects(
      store.updateManagedRecord(owner, first.record.id, "leads", {
        ...update,
        note: "changed",
      }),
      (e) => e.code === "operation_conflict",
    );
    const customer = await store.listManagedRecords({ actor: "42" }, query);
    assert.equal(
      customer.records.find((r) => r.id === first.record.id).note,
      undefined,
    );
    const page = await store.listManagedRecords(
      { owner },
      { ...query, limit: 1 },
    );
    assert.ok(page.next_cursor);
    const next = await store.listManagedRecords(
      { owner },
      { ...query, limit: 1, cursor: page.next_cursor },
    );
    assert.notEqual(next.records[0].id, page.records[0].id);
    const empty = await store.listManagedRecords(
      { owner },
      { ...query, query: "No Match" },
    );
    assert.equal(empty.records.length, 0);
    const finish = await store.updateManagedRecord(
      owner,
      first.record.id,
      "leads",
      {
        expected_revision: changed.revision,
        operation_key: "55555555-5555-4555-8555-555555555555",
        status: "completed",
      },
    );
    await assert.rejects(
      store.updateManagedRecord(owner, first.record.id, "leads", {
        expected_revision: finish.revision,
        operation_key: "66666666-6666-4666-8666-666666666666",
        status: "new",
      }),
      (e) => e.code === "invalid_request",
    );
    // DB immutability protects historical snapshots even if a generic action route forgets its guard.
    const immutable = await pool.connect();
    try {
      await immutable.query("BEGIN");
      await immutable.query("select set_config('app.max_user_id','42',true)");
      await assert.rejects(
        immutable.query(
          "UPDATE max_business_actions SET payload='{}' WHERE id=$1",
          [first.record.id],
        ),
      );
      await immutable.query("ROLLBACK");
    } finally {
      immutable.release();
    }
    await engine.exec("RESET ROLE");
    const audits = await engine.query("SELECT * FROM max_owner_audit");
    assert.equal(audits.rows.length, 2);
    await engine.exec("SET ROLE managed_runtime");
    app.dependencies.set("@/lib/omnia/management-config", {
      getManagementContract: async () => fixture,
    });
    app.dependencies.set("@/lib/max/session", {
      getMaxUser: async () => ({ id: "42" }),
    });
    let forwards = 0;
    app.dependencies.set("@/lib/omnia/analytics", {
      forwardMaxAnalytics: async () => {
        forwards++;
        throw Error("private transport failure");
      },
    });
    const businessRoute = app.load(
      "app/api/omnia/business/[resource]/route.ts",
    );
    const routeInput = {
      ...lead,
      operationKey: "77777777-7777-4777-8777-777777777777",
    };
    const create = () =>
      businessRoute.POST(
        new Request("https://app.test/api/omnia/business/leads", {
          method: "POST",
          body: JSON.stringify(routeInput),
        }),
        { params: Promise.resolve({ resource: "leads" }) },
      );
    const created = await create();
    assert.equal(created.status, 201);
    const routeRecord = await created.json();
    assert.equal((await (await create()).json()).id, routeRecord.id);
    assert.equal(forwards, 2);
    const customerResponse = await businessRoute.GET(
      new Request("https://app.test/api/omnia/business/leads"),
      { params: Promise.resolve({ resource: "leads" }) },
    );
    assert.equal(customerResponse.status, 200);
    assert.ok(
      (await customerResponse.json()).records.some(
        (record) => record.id === routeRecord.id,
      ),
    );
    const ownerRoute = app.load("app/api/omnia/owner/route.ts"),
      body = JSON.stringify({ op: "list", query: { resource: "leads" } });
    const ownerResponse = await ownerRoute.POST(
      new Request("https://app.test/api/omnia/owner", {
        method: "POST",
        headers: { "X-Yleum-Owner-Assertion": sign(app, Buffer.from(body)) },
        body,
      }),
    );
    assert.equal(ownerResponse.status, 200);
    assert.ok(
      (await ownerResponse.json()).records.some(
        (record) => record.id === routeRecord.id,
      ),
    );
    const preview = runtime({
      MAX_SESSION_SECRET: "synthetic-preview-session",
      YLEUM_OWNER_ASSERTION_KEY: "",
      YLEUM_OWNER_PUBLICATION_ID: "",
    });
    preview.dependencies.set("@/lib/db", { pool });
    preview.dependencies.set("@/lib/omnia/management-config", {
      getManagementContract: async () => fixture,
    });
    let previewCookie;
    preview.dependencies.set("next/headers", {
      cookies: async () => ({
        get: () => (previewCookie ? { value: previewCookie } : undefined),
      }),
      headers: async () => new Headers(),
    });
    previewCookie = preview
      .load("lib/max/session.ts")
      .createMaxSession({ id: "owner-preview-42" }).value;
    const previewRoute = preview.load(
      "app/api/omnia/business/[resource]/route.ts",
    );
    const previewResponse = await previewRoute.POST(
      new Request("https://preview.test/api/omnia/business/leads", {
        method: "POST",
        body: JSON.stringify({
          ...lead,
          operationKey: "88888888-8888-4888-8888-888888888888",
        }),
      }),
      { params: Promise.resolve({ resource: "leads" }) },
    );
    assert.equal(previewResponse.status, 201);
    const previewRecord = await previewResponse.json();
    const previewList = await previewRoute.GET(
      new Request("https://preview.test/api/omnia/business/leads"),
      { params: Promise.resolve({ resource: "leads" }) },
    );
    assert.deepEqual(
      (await previewList.json()).records.map((record) => record.id),
      [previewRecord.id],
    );
    assert.equal(
      (await store.listManagedRecords({ actor: "42" }, query)).records.some(
        (record) => record.id === previewRecord.id,
      ),
      false,
    );
    assert.equal(preview.calls.length, 0);
    await engine.exec(
      "RESET ROLE; ALTER TABLE max_owner_records RENAME COLUMN revision TO old_revision; SET ROLE managed_runtime",
    );
    await assert.rejects(
      store.assertManagementSchema(),
      (e) => e.code === "unsupported",
    );
  },
);

test("missing owner key with retained binding rejects a formerly valid assertion before DB or fetch", async () => {
  const app = runtime(),
    body = Buffer.from('{"op":"describe"}'),
    request = new Request("https://app.test/api/omnia/owner", {
      method: "POST",
      headers: { "X-Yleum-Owner-Assertion": sign(app, body) },
      body,
    });
  assert.equal(
    app.load("lib/omnia/management-auth.ts")
      .verifyOwnerAssertion(request, body, fixture).owner_id,
    ids[1],
  );
  delete app.env.YLEUM_OWNER_ASSERTION_KEY;
  assert.deepEqual(
    [
      app.env.YLEUM_OWNER_PROJECT_ID,
      app.env.YLEUM_OWNER_ID,
      app.env.YLEUM_OWNER_PUBLICATION_ID,
    ],
    ids,
  );
  app.dependencies.set("@/lib/omnia/management-config", {
    getManagementContract: async () => fixture,
  });
  let databaseImports = 0;
  app.dependencies.set("@/lib/db", {
    get pool() {
      databaseImports += 1;
      throw Error("DB opened after capability revocation");
    },
  });
  const result = await app.load("app/api/omnia/owner/route.ts").POST(request);
  assert.equal(result.status, 503);
  assert.equal((await result.json()).error.code, "unavailable");
  assert.equal(databaseImports, 0);
  assert.equal(app.calls.length, 0);
});

test("auth expiration boundary, invalid calendar days and one decimal prices fail closed", () => {
  const app = runtime(),
    auth = app.load("lib/omnia/management-auth.ts"),
    body = Buffer.from('{"op":"describe"}'),
    now = Math.floor(Date.now() / 1000);
  const req = new Request("https://app.test/api/omnia/owner", {
    method: "POST",
    headers: {
      "X-Yleum-Owner-Assertion": sign(app, body, { iat: now - 60, exp: now }),
    },
  });
  assert.throws(() => auth.verifyOwnerAssertion(req, body, fixture));
});
test("owner route accepts only signed strict RPC and body limits, never imports DB after rejected auth", async () => {
  const app = runtime();
  app.dependencies.set("@/lib/omnia/management-config", {
    getManagementContract: async () => fixture,
  });
  const route = app.load("app/api/omnia/owner/route.ts");
  for (const body of [
    "{}",
    '{"op":"list"}',
    JSON.stringify({ op: "describe", private: "x" }),
  ]) {
    const raw = Buffer.from(body);
    const result = await route.POST(
      new Request("https://app.test/api/omnia/owner", {
        method: "POST",
        headers: {
          "X-Yleum-Owner-Assertion": sign(app, raw, { owner_id: ids[0] }),
        },
        body,
      }),
    );
    assert.equal(result.status, 401);
  }
  const raw = Buffer.from(" ".repeat(65537));
  const result = await route.POST(
    new Request("https://app.test/api/omnia/owner", {
      method: "POST",
      headers: { "X-Yleum-Owner-Assertion": sign(app, raw) },
      body: raw,
    }),
  );
  assert.equal(result.status, 400);
});
test("customer business route rejects unverified MAX identity and unsupported resource before SQL", async () => {
  const app = runtime();
  app.dependencies.set("@/lib/max/session", { getMaxUser: async () => null });
  app.dependencies.set("@/lib/omnia/management-config", {
    getManagementContract: async () => fixture,
  });
  const route = app.load("app/api/omnia/business/[resource]/route.ts");
  const req = new Request("https://app.test/api/omnia/business/leads");
  assert.equal(
    (await route.GET(req, { params: Promise.resolve({ resource: "leads" }) }))
      .status,
    401,
  );
  app.dependencies.set("@/lib/max/session", {
    getMaxUser: async () => ({ id: "42" }),
  });
  // Fresh module context because real imports are captured on first evaluation.
  const next = runtime();
  next.dependencies.set("@/lib/max/session", {
    getMaxUser: async () => ({ id: "42" }),
  });
  next.dependencies.set("@/lib/omnia/management-config", {
    getManagementContract: async () => ({ ...fixture, resources: [] }),
  });
  assert.equal(
    (
      await next
        .load("app/api/omnia/business/[resource]/route.ts")
        .GET(req, { params: Promise.resolve({ resource: "leads" }) })
    ).status,
    409,
  );
});

test("rejects normalized invalid calendar date", () =>
  assert.throws(() =>
    runtime()
      .load("lib/omnia/management-resources.ts")
      .isoDate("2026-02-30T10:00:00Z"),
  ));
test("rejects ambiguous one decimal catalog price", () =>
  assert.throws(() =>
    runtime().load("lib/omnia/business-server.ts").parseRubPrice("1.5"),
  ));

for (const body of [() => new Response('{"id":', {status:201}),
  () => ({ok:true,status:201,json:async()=>{throw new TypeError("body lost");}}),
  () => Response.json({}), () => Response.json({records: [], next_cursor: 42})]) {
  test("customer rejects ambiguous/invalid success without retry and preserves same-key replay", async () => {
    const receipt = {id: ids[1],resource:"leads",created_at:"2026-10-29T09:00:00Z",revision:"a".repeat(64),status:"new",customer_name:"Анна",items:[]};
    let replay = false;
    const app = runtime({}, () => replay ? Response.json(receipt) : body());
    const client = app.load("lib/omnia/business-client.ts");
    const original = JSON.stringify(lead);
    await assert.rejects(client.createManagedLead(lead), e => e.code === "unavailable" && e.status === 503);
    assert.equal(app.calls.length, 1);
    assert.equal(JSON.stringify(lead), original);
    replay = true;
    assert.equal((await client.createManagedLead(lead)).id, receipt.id);
    assert.equal(app.calls[0].body, app.calls[1].body);
  });
}
test("customer history rejects malformed success pages", async () => {
  for (const data of [{}, {records:[{}],next_cursor:null}, {records:[],next_cursor:42}]) {
    const app=runtime({},()=>Response.json(data));
    await assert.rejects(app.load("lib/omnia/business-client.ts").getManagedRecords("leads"), e=>e.code==="unavailable");
  }
});
test("requested date filters are explicit, ordered and booking only", () => {
  const m=runtime().load("lib/omnia/management-resources.ts");
  const q={resource:"bookings",requested_from:"2026-10-30T00:00:00+03:00",requested_to:"2026-10-30T23:59:59+03:00"};
  assert.equal(m.parseRecordsQuery(q).requested_from,"2026-10-29T21:00:00.000Z");
  assert.throws(()=>m.parseRecordsQuery({...q,resource:"leads"}));
  assert.throws(()=>m.parseRecordsQuery({...q,requested_to:"2026-10-28T00:00:00Z"}));
});

test("order pickup is optional, validated and scheduled only by the injected server clock", () => {
  const server = runtime().load("lib/omnia/business-server.ts");
  const catalog = { configVersion: 1, config: { content: [
    { id: "coffee", title: "Coffee", price: "250 RUB", active: true, availability: "in_stock", options: [] },
  ] } };
  const order = { ...lead, configVersion: 1, items: [
    { catalogId: "coffee", quantity: 2, unitPriceKopecks: 25000 },
  ] };
  const instant = Date.parse("2030-06-10T08:00:00.000Z");
  for (const delayMinutes of [0, 15, 30, 60, 1440]) {
    let clockCalls = 0;
    const snapshot = server.prepareManagedSnapshot("orders", {
      ...order, fulfillment: { type: "pickup", delayMinutes },
    }, catalog, () => { clockCalls++; return instant; });
    assert.deepEqual(JSON.parse(JSON.stringify(snapshot.fulfillment)), {
      type: "pickup", delay_minutes: delayMinutes,
      scheduled_for: new Date(instant + delayMinutes * 60000).toISOString(),
    });
    assert.equal(clockCalls, 1);
  }
  const ordinary = server.prepareManagedSnapshot("orders", order, catalog, () => {
    throw Error("ordinary order must not ask for a pickup clock");
  });
  assert.equal(Object.hasOwn(ordinary, "fulfillment"), false);
  for (const fulfillment of [null, {}, { type: "delivery", delayMinutes: 15 },
    ...[true, "15", -1, 1.5, 1441, Infinity].map(delayMinutes => ({ type: "pickup", delayMinutes })),
    { type: "pickup", delayMinutes: 15, scheduled_for: "2030-01-01T00:00:00Z" },
  ]) assert.throws(() => server.validateManagedInput("orders", { ...order, fulfillment }));
  for (const resource of ["leads", "bookings"])
    assert.throws(() => server.validateManagedInput(resource, {
      ...lead, fulfillment: { type: "pickup", delayMinutes: 15 },
    }));
  for (const instant of [NaN, Infinity, 8.64e15])
    assert.throws(() => server.prepareManagedSnapshot("orders", {
      ...order, fulfillment: { type: "pickup", delayMinutes: 15 },
    }, catalog, () => instant), error => error.code === "unavailable");
  assert.throws(() => server.prepareManagedSnapshot("orders", {
    ...order, configVersion: 0, fulfillment: { type: "pickup", delayMinutes: 15 },
  }, catalog, () => { throw Error("clock must not run for a rejected quote"); }), error => error.code === "quote_changed");
});

test("pickup snapshot projection keeps fulfillment for customers and never exposes owner note", () => {
  const resources = runtime().load("lib/omnia/management-resources.ts");
  const fulfillment = { type: "pickup", delay_minutes: 30, scheduled_for: "2030-06-10T08:30:00.000Z" };
  const row = { id: ids[0], resource: "orders", created_at: "2030-06-10T08:00:00Z",
    revision: "a".repeat(64), status: "new", note: "private owner note",
    payload: { management_protocol: 1, resource: "orders", customer_name: "Customer", items: [], fulfillment },
  };
  const customer = resources.projectManagedRecord(row, false);
  assert.ok(customer.fulfillment, "customer history must preserve pickup snapshot metadata");
  assert.deepEqual(JSON.parse(JSON.stringify(customer.fulfillment)), fulfillment);
  assert.equal(Object.hasOwn(customer, "note"), false);
  assert.equal(resources.projectManagedRecord(row, true).note, row.note);
  for (const payload of [{ ...row.payload, fulfillment: { ...fulfillment, note: "private" } },
    { ...row.payload, fulfillment: { ...fulfillment, delay_minutes: true } }])
    assert.throws(() => resources.projectManagedRecord({ ...row, payload }, false), error => error.code === "unavailable");
  assert.throws(() => resources.projectManagedRecord({ ...row, resource: "leads" }, false));
});

test("customer rejects malformed pickup receipts without discarding or retrying the operation key", async () => {
  const valid = { type: "pickup", delay_minutes: 15, scheduled_for: "2030-06-10T08:15:00Z" };
  const record = { id: ids[0], resource: "orders", created_at: "2030-06-10T08:00:00Z",
    revision: "a".repeat(64), status: "new", customer_name: "Customer", items: [], total_kopecks: 0, currency: "RUB" };
  const input = { ...lead, configVersion: 1, items: [], fulfillment: { type: "pickup", delayMinutes: 15 } };
  for (const fulfillment of [null, {}, { ...valid, type: "delivery" },
    ...[true, "15", -1, 1.5, 1441].map(delay_minutes => ({ ...valid, delay_minutes })),
    { ...valid, scheduled_for: "2030-06-10T08:15:00" },
    { ...valid, scheduled_for: "2030-02-31T08:15:00Z" }, { ...valid, note: "private" },
  ]) {
    let replay = false;
    const app = runtime({}, () => Response.json({ ...record, fulfillment: replay ? valid : fulfillment }));
    const client = app.load("lib/omnia/business-client.ts");
    await assert.rejects(client.createManagedOrder(input), error => error.code === "unavailable");
    assert.equal(app.calls.length, 1);
    replay = true;
    assert.equal((await client.createManagedOrder(input)).id, record.id);
    assert.equal(app.calls[0].body, app.calls[1].body);
    assert.equal(input.operationKey, lead.operationKey);
  }
  const app = runtime({}, () => Response.json({ ...record, resource: "leads", fulfillment: valid }));
  await assert.rejects(app.load("lib/omnia/business-client.ts").createManagedLead(lead));
  const leaked = runtime({}, () => Response.json({ ...record, fulfillment: valid, note: "private" }));
  await assert.rejects(leaked.load("lib/omnia/business-client.ts").createManagedOrder(input));
});

function legacyMirrorSql() {
  const source = readFileSync(resolve(base, "compat/legacy-orders-owner-mirror.sql.template"), "utf8");
  // Only namespace substitution differs in the isolated PostgreSQL test schema.
  return source.replaceAll("__PROJECT_ID__", ids[0]).replaceAll("__ORDERS_TABLE__", "sever_orders")
    .replaceAll("public.", "managed_test.").replaceAll("'public'", "'managed_test'");
}
async function legacyManagementDatabase(t, count = 13) {
  const db = await managementDatabase();
  t.after(() => db.engine.close());
  await db.engine.exec("CREATE ROLE managed_runtime; CREATE SCHEMA managed_test; SET search_path TO managed_test,public;");
  await db.engine.exec(readFileSync(resolve(base, "drizzle/0003_owner_management.sql"), "utf8"));
  await db.engine.exec(`CREATE TABLE sever_orders (
    id text PRIMARY KEY, owner_id text NOT NULL, customer_name text NOT NULL, items jsonb NOT NULL,
    total_rub integer NOT NULL, status text NOT NULL, operation_key text NOT NULL,
    created_at timestamptz NOT NULL, pickup_option text, pickup_at timestamptz, barista_comment text,
    UNIQUE(owner_id,operation_key));`);
  const legacy = [];
  for (let i = 1; i <= count; i++) {
    const option = [null, "asap", "m15", "m30", "h1"][i % 5];
    const delay = { asap: 0, m15: 15, m30: 30, h1: 60 }[option];
    const created = new Date(Date.UTC(2026, 0, 1, 0, 0, i));
    const row = { id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
      actor: i <= 9 ? "42" : "99", created,
      pickup: option ? new Date(created.getTime() + delay * 60000) : null,
      option, itemOption: i % 3 === 0 ? undefined : i % 3 === 1 ? "oat" : "",
      comment: i % 2 ? `Original customer comment ${i}` : null };
    await db.engine.query("INSERT INTO sever_orders VALUES($1,$2,$3,$4,$5,'accepted',$6,$7,$8,$9,$10)",
      [row.id, row.actor, `Customer ${i}`, JSON.stringify([{ id: "old_coffee", name: "Historical coffee", price: 270, qty: 2,
        ...(row.itemOption !== undefined ? { option: row.itemOption } : {}) }]),
        540, `old-operation-${i}`, created, option, row.pickup, row.comment]);
    legacy.push(row);
  }
  await db.engine.exec("GRANT USAGE ON SCHEMA managed_test TO managed_runtime; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA managed_test TO managed_runtime;");
  return { ...db, legacy };
}

test("legacy order migration preserves all rows, mirrors atomically with RLS, and never overwrites owner workflow",
  { skip: !pgliteModule && !nativePgUrl }, async t => {
  const { engine, pool, legacy } = await legacyManagementDatabase(t);
  const original = (await engine.query("SELECT to_jsonb(s) value FROM sever_orders s ORDER BY id")).rows;
  await engine.exec(`BEGIN; ${legacyMirrorSql()} COMMIT;`);
  assert.deepEqual((await engine.query("SELECT to_jsonb(s) value FROM sever_orders s ORDER BY id")).rows, original);
  const snapshots = (await engine.query("SELECT id,max_user_id,created_at,updated_at,payload FROM max_business_actions ORDER BY id")).rows;
  assert.equal(snapshots.length, 13);
  for (let i = 0; i < legacy.length; i++) {
    const old = legacy[i], snapshot = snapshots[i];
    assert.equal(snapshot.id, old.id);
    assert.equal(snapshot.max_user_id, old.actor);
    assert.equal(new Date(snapshot.created_at).getTime(), old.created.getTime());
    assert.equal(new Date(snapshot.updated_at).getTime(), old.created.getTime());
    assert.equal(snapshot.payload.total_kopecks, 54000);
    assert.equal(snapshot.payload.items[0].unit_price_kopecks, 27000);
    assert.equal(snapshot.payload.items[0].option, old.itemOption);
    assert.equal(Object.hasOwn(snapshot.payload.items[0], "option"), old.itemOption !== undefined);
    assert.equal(snapshot.payload.comment, old.comment ?? undefined);
    if (old.option) {
      assert.equal(snapshot.payload.fulfillment.delay_minutes, { asap: 0, m15: 15, m30: 30, h1: 60 }[old.option]);
      assert.equal(Date.parse(snapshot.payload.fulfillment.scheduled_for), old.pickup.getTime());
    } else assert.equal(Object.hasOwn(snapshot.payload, "fulfillment"), false);
  }
  await engine.exec("SET ROLE managed_runtime;");
  const app = runtime(); app.dependencies.set("@/lib/db", { pool });
  const store = app.load("lib/omnia/management-store.ts");
  const owner = { project_id: ids[0], owner_id: ids[1], publication_id: ids[2] };
  const query = { resource: "orders", limit: 100, query: "" };
  assert.equal((await store.listManagedRecords({ owner }, query)).records.length, 13);
  const own = await store.listManagedRecords({ actor: "42" }, query);
  assert.equal(own.records.length, 9);
  const record = own.records.find(row => row.id === legacy[0].id);
  const updated = await store.updateManagedRecord(owner, record.id, "orders", {
    expected_revision: record.revision, operation_key: ids[0], status: "in_progress", note: "Private owner note",
  });
  assert.equal(updated.status, "in_progress");
  assert.equal((await store.listManagedRecords({ actor: "42" }, query)).records.find(row => row.id === record.id).note, undefined);
  await engine.query("SELECT max_legacy_orders_owner_mirror_v1($1)", [record.id]);
  await engine.exec("RESET ROLE;");
  await engine.exec(`BEGIN; ${legacyMirrorSql()} COMMIT;`);
  const metadata = (await engine.query("SELECT status,note,revision FROM max_owner_records WHERE record_id=$1", [record.id])).rows[0];
  assert.equal(metadata.status, "in_progress"); assert.equal(metadata.note, "Private owner note");
  assert.equal(metadata.revision, updated.revision);
  assert.deepEqual((await engine.query("SELECT to_jsonb(s) value FROM sever_orders s ORDER BY id")).rows, original);
  await engine.exec("SET ROLE managed_runtime;");
  const client = await pool.connect();
  const added = "00000000-0000-4000-8000-000000000100";
  try {
    await client.query("BEGIN");
    await client.query("SELECT set_config('app.max_user_id','99',true),set_config('app.management_project_id',$1,true),set_config('app.owner_project_id',$2,true),set_config('app.owner_id',$3,true)", [ids[1], ids[2], ids[1]]);
    const settings = "SELECT current_setting('app.max_user_id',true) actor,current_setting('app.management_project_id',true) project,current_setting('app.owner_project_id',true) owner_project,current_setting('app.owner_id',true) owner";
    const before = (await client.query(settings)).rows;
    await client.query("INSERT INTO sever_orders SELECT $1,'42','New legacy customer',$2::jsonb,270,'accepted','new-legacy-operation',now(),NULL,NULL,NULL",
      [added, JSON.stringify([{ id: "old_coffee", name: "Historical coffee", price: 270, qty: 1, option: "v".repeat(40) }])]);
    assert.deepEqual((await client.query(settings)).rows, before);
    const visible = (await client.query("SELECT DISTINCT max_user_id FROM max_business_actions")).rows;
    assert.deepEqual(visible.map(row => row.max_user_id), ["99"]);
    await client.query("COMMIT");
  } finally { client.release(); }
  assert.equal((await store.listManagedRecords({ owner }, query)).records.length, 14);
  assert.equal((await store.listManagedRecords({ owner }, query)).records.find(row => row.id === added).items[0].option, "v".repeat(40));
  assert.equal((await store.listManagedRecords({ actor: "42" }, query)).records.length, 10);
  await assert.rejects(engine.query("INSERT INTO sever_orders SELECT '00000000-0000-4000-8000-000000000101','42','Invalid','[{\"id\":\"old_coffee\",\"name\":\"Old\",\"price\":270,\"qty\":1}]'::jsonb,1,'accepted','bad-total',now(),NULL,NULL,NULL"));
  assert.equal((await engine.query("SELECT count(*)::integer count FROM sever_orders WHERE id='00000000-0000-4000-8000-000000000101'")).rows[0].count, 0);
  await assert.rejects(engine.query("INSERT INTO sever_orders SELECT '00000000-0000-4000-8000-000000000102','42','Invalid pickup','[{\"id\":\"old_coffee\",\"name\":\"Old\",\"price\":270,\"qty\":1}]'::jsonb,270,'accepted','bad-pickup',now(),'m15',NULL,NULL"));
  assert.equal((await engine.query("SELECT count(*)::integer count FROM sever_orders WHERE id='00000000-0000-4000-8000-000000000102'")).rows[0].count, 0);
  await assert.rejects(engine.query("INSERT INTO sever_orders SELECT '00000000-0000-4000-8000-000000000103','42','Long comment','[{\"id\":\"old_coffee\",\"name\":\"Old\",\"price\":270,\"qty\":1}]'::jsonb,270,'accepted','long-comment',now(),NULL,NULL,repeat('x',2001)"));
  assert.equal((await engine.query("SELECT count(*)::integer count FROM sever_orders WHERE id='00000000-0000-4000-8000-000000000103'")).rows[0].count, 0);
  for (const [index, extra] of [null, 1, true, {}, "v".repeat(41)].map(option => ({ option }))
    .concat([{ option: "valid", unexpected: true }]).entries()) {
    const id = `00000000-0000-4000-8000-${String(200 + index).padStart(12, "0")}`;
    const item = { id: "old_coffee", name: "Historical coffee", price: 270, qty: 1, ...extra };
    await assert.rejects(engine.query("INSERT INTO sever_orders SELECT $1,'42','Invalid option',$2::jsonb,270,'accepted',$3,now(),NULL,NULL,NULL",
      [id, JSON.stringify([item]), `invalid-option-${index}`]), error => error.code === "22023" && error.message.includes("legacy_order_item"));
    assert.equal((await engine.query("SELECT count(*)::integer count FROM sever_orders WHERE id=$1", [id])).rows[0].count, 0);
  }
});

test("legacy backfill collision aborts the whole migration and leaves every historical value intact",
  { skip: !pgliteModule && !nativePgUrl }, async t => {
  const { engine, legacy } = await legacyManagementDatabase(t, 2);
  const original = (await engine.query("SELECT to_jsonb(s) value FROM sever_orders s ORDER BY id")).rows;
  await engine.query("INSERT INTO max_business_actions(id,max_user_id,action_type,status,payload) VALUES($1,'42','order_created','new','{}')", [legacy[1].id]);
  await assert.rejects(engine.exec(`BEGIN; ${legacyMirrorSql()} COMMIT;`), error => error.code === "55000" && error.message.includes("legacy_order_collision"));
  await engine.exec("ROLLBACK;");
  assert.equal((await engine.query("SELECT count(*)::integer count FROM max_business_actions")).rows[0].count, 1);
  assert.equal((await engine.query("SELECT count(*)::integer count FROM max_owner_records")).rows[0].count, 0);
  assert.deepEqual((await engine.query("SELECT to_jsonb(s) value FROM sever_orders s ORDER BY id")).rows, original);
  assert.equal((await engine.query("SELECT count(*)::integer count FROM pg_trigger WHERE tgrelid='sever_orders'::regclass AND NOT tgisinternal")).rows[0].count, 0);
});

test("pickup replay returns the saved schedule before consulting catalog or server clock",
  { skip: !pgliteModule && !nativePgUrl }, async t => {
  const { engine, pool } = await legacyManagementDatabase(t, 0);
  await engine.exec("SET ROLE managed_runtime;");
  const app = runtime(); app.dependencies.set("@/lib/db", { pool });
  let quoteCalls = 0, clockCalls = 0;
  app.dependencies.set("@/lib/omnia/catalog-server", { getYleumServerAppConfig: async () => {
    quoteCalls++; return { configVersion: 1, config: { content: [
      { id: "coffee", title: "Coffee", price: "250 RUB", active: true, availability: "in_stock", options: [] },
    ] } };
  } });
  app.dependencies.set("@/lib/omnia/analytics", { forwardMaxAnalytics: async () => undefined });
  const server = app.load("lib/omnia/business-server.ts");
  const input = { ...lead, configVersion: 1, items: [{ catalogId: "coffee", quantity: 1, unitPriceKopecks: 25000 }],
    fulfillment: { type: "pickup", delayMinutes: 30 } };
  const first = await server.createManagedBusinessRecord("42", "orders", input, () => {
    clockCalls++; return Date.parse("2030-06-10T08:00:00Z");
  });
  app.dependencies.set("@/lib/omnia/catalog-server", { getYleumServerAppConfig: async () => { throw Error("catalog must not run on replay"); } });
  const replay = await server.createManagedBusinessRecord("42", "orders", input, () => { throw Error("clock must not run on replay"); });
  assert.equal(replay.id, first.id);
  assert.equal(replay.fulfillment.scheduled_for, "2030-06-10T08:30:00.000Z");
  assert.equal(quoteCalls, 1); assert.equal(clockCalls, 1);
  // The actual store receipt survives losing the HTTP response and a fresh browser module.
  const browser = intentBrowser(); let lost = true; const keys = [];
  const transport = verifiedTransport(async (_, options) => {
    const body = JSON.parse(options.body); keys.push(body.operationKey);
    const receipt = await server.createManagedBusinessRecord("42", "leads", body);
    if (lost) { lost = false; throw Error("response lost after real database commit"); }
    return Response.json(receipt, { status: 201 });
  });
  const originalLead = { ...lead, operationKey: ids[1] };
  await assert.rejects(runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead(originalLead));
  await runtime({}, transport, browser).load("lib/omnia/business-client.ts").createManagedLead({ ...originalLead, operationKey: ids[2] });
  assert.deepEqual(keys, [ids[1], ids[1]]);
  await engine.exec("RESET ROLE;");
  assert.equal((await engine.query("SELECT count(*)::integer count FROM max_business_actions WHERE action_type='lead_created'")).rows[0].count, 1);
  assert.equal((await engine.query("SELECT count(*)::integer count FROM max_owner_records WHERE resource='leads'")).rows[0].count, 1);
});
