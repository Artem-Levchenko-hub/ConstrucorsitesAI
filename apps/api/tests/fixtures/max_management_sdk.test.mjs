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
function runtime(overrides = {}) {
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
        Date,
        Request,
        Response,
        Headers,
        AbortSignal,
        setTimeout,
        clearTimeout,
        crypto: webcrypto,
        process: { env, cwd: () => base },
        fetch: async (url, opts) => {
          calls.push({ url, ...opts });
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
  return { env, load, calls, dependencies };
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
    assert.equal(forwards, 1);
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
