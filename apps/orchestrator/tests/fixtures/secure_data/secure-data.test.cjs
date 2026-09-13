const { test } = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { randomBytes } = require('node:crypto');

test('trusted encrypted store contract and actual AES-GCM behavior', async (t) => {
  const moduleFile = path.join(process.env.SECURE_DATA_MODULE_DIR, 'crypto.mjs');
  assert.ok(existsSync(moduleFile), 'trusted payload encryption module must exist');
  const crypto = await import(pathToFileURL(moduleFile).href);
  const validation = await import(pathToFileURL(path.join(process.env.SECURE_DATA_MODULE_DIR, 'validation.mjs')).href);
  const { SecureRecordStore } = await import(pathToFileURL(path.join(process.env.SECURE_DATA_MODULE_DIR, 'store.mjs')).href);
  assert.equal(typeof SecureRecordStore, 'function');
  const key1 = randomBytes(32).toString('base64');
  const key2 = randomBytes(32).toString('base64');
  const raw = JSON.stringify({ projectId: 'app-one', activeVersion: 'v1', keys: { v1: key1 } });
  const ring = crypto.parseKeyRing(raw, 'app-one');
  const context = { projectId: 'app-one', collection: 'orders', id: 'ae709608-857e-4eab-99ca-5d93f943b684', ownerId: 'user-one', revision: 1 };
  const payload = { name: 'Секретный клиент', amount: 42, nested: { tags: ['private', null, true] } };
  const encrypted = crypto.encryptPayload(payload, context, ring);
  await t.test('full payload round trip and fresh randomness', () => {
    assert.deepEqual(crypto.decryptPayload(encrypted, context, ring), payload);
    assert.notEqual(crypto.encryptPayload(payload, context, ring), encrypted);
    assert.ok(!encrypted.includes('Секретный клиент'));
  });
  await t.test('all record context is authenticated', () => {
    for (const [field, value] of Object.entries({ projectId: 'other', collection: 'tasks', id: 'be709608-857e-4eab-99ca-5d93f943b684', ownerId: 'user-two', revision: 2 })) {
      assert.throws(() => crypto.decryptPayload(encrypted, { ...context, [field]: value }, ring));
    }
  });
  await t.test('ciphertext, tag, nonce and key version tampering fail closed', () => {
    const envelope = JSON.parse(encrypted);
    for (const field of ['ciphertext', 'tag', 'nonce']) {
      const bytes = Buffer.from(envelope[field], 'base64'); bytes[0] ^= 1;
      assert.throws(() => crypto.decryptPayload(JSON.stringify({ ...envelope, [field]: bytes.toString('base64') }), context, ring));
    }
    assert.throws(() => crypto.decryptPayload(JSON.stringify({ ...envelope, keyVersion: 'missing' }), context, ring));
    const wrong = crypto.parseKeyRing(JSON.stringify({ projectId: 'app-one', activeVersion: 'v1', keys: { v1: key2 } }), 'app-one');
    assert.throws(() => crypto.decryptPayload(encrypted, context, wrong));
  });
  await t.test('rotation reads old records and writes new version', () => {
    const rotated = crypto.parseKeyRing(JSON.stringify({ projectId: 'app-one', activeVersion: 'v2', keys: { v1: key1, v2: key2 } }), 'app-one');
    assert.deepEqual(crypto.decryptPayload(encrypted, context, rotated), payload);
    assert.equal(JSON.parse(crypto.encryptPayload(payload, context, rotated)).keyVersion, 'v2');
  });
  await t.test('key file validation rejects duplicates, wrong project and malformed key material', async () => {
    for (const value of [raw.replace('app-one', 'app-two'), raw.replace(key1, 'bad'), raw.replace('"v1":', '"v1":"' + key2 + '","v1":'), raw.replace('"activeVersion":"v1"', '"activeVersion":"missing"'), '{}', 'x'.repeat(20000)]) {
      assert.throws(() => crypto.parseKeyRing(value, 'app-one'));
    }
    const temp = mkdtempSync(path.join(tmpdir(), 'omnia-keys-test-'));
    try {
      const file = path.join(temp, 'keys.json');
      writeFileSync(file, raw);
      assert.equal((await crypto.readKeyRing(file, 'app-one')).activeVersion, 'v1');
      writeFileSync(file, 'x'.repeat(20000));
      await assert.rejects(crypto.readKeyRing(file, 'app-one'));
      await assert.rejects(crypto.readKeyRing(path.join(temp, 'missing'), 'app-one'));
    } finally { rmSync(temp, { recursive: true, force: true }); }
  });
  await t.test('collection, revision, payload and cursor bounds', () => {
    const { validateCollection, validatePayload, validateRevision, decodeCursor } = validation;
    for (const value of ['', '../private', 'a'.repeat(65), 'Orders', 'a;DROP']) assert.throws(() => validateCollection(value));
    for (const value of [0, -1, 1.5, NaN, 2147483647]) assert.throws(() => validateRevision(value));
    for (const value of [[], null, { a: Infinity }, { large: 'x'.repeat(65537) }]) assert.throws(() => validatePayload(value));
    assert.throws(() => decodeCursor('%%%'));
  });
});

test('real PostgreSQL CRUD, ownership, optimistic concurrency and audit rollback', { skip: !process.env.SECURE_DATA_TEST_DB_URL, timeout: 120_000 }, async (t) => {
  const { createRequire } = require('node:module');
  const dependencyRoot = process.env.SECURE_DATA_PG_PACKAGE || path.resolve(__dirname, '../../../templates/max-miniapp-nextjs/package.json');
  const { Pool } = createRequire(dependencyRoot)('pg');
  const { SecureRecordStore } = await import(pathToFileURL(path.join(process.env.SECURE_DATA_MODULE_DIR, 'store.mjs')).href);
  const { parseKeyRing } = await import(pathToFileURL(path.join(process.env.SECURE_DATA_MODULE_DIR, 'crypto.mjs')).href);
  const { createSecureDataHandler } = await import(pathToFileURL(path.join(process.env.SECURE_DATA_MODULE_DIR, 'http.mjs')).href);
  const schema = `secure_test_${randomBytes(8).toString('hex')}`;
  const config = { connectionString: process.env.SECURE_DATA_TEST_DB_URL, connectionTimeoutMillis: 5000, query_timeout: 5000, statement_timeout: 5000, idleTimeoutMillis: 1000, allowExitOnIdle: true };
  const admin = new Pool(config);
  let pool;
  try {
    await admin.query(`CREATE SCHEMA ${schema}`);
    pool = new Pool({ ...config, options: `-c search_path=${schema}` });
    await t.test('actual additive migration creates encrypted tables and indexes', async () => {
      const migration = readFileSync(path.resolve(__dirname, '../../../templates/max-miniapp-nextjs/drizzle/0003_secure_records.sql'), 'utf8');
      await pool.query(migration);
      await pool.query(migration);
      const indexes = await pool.query('SELECT indexname FROM pg_indexes WHERE schemaname=$1', [schema]);
      assert.ok(indexes.rows.some(row => row.indexname === 'omnia_secure_records_owner_page'));
      assert.ok(indexes.rows.some(row => row.indexname === 'omnia_secure_record_audit_record'));
    });
    const keyFile = JSON.stringify({ projectId: 'app-one', activeVersion: 'v1', keys: { v1: randomBytes(32).toString('base64') } });
    let store = new SecureRecordStore(pool, 'app-one', parseKeyRing(keyFile, 'app-one'));
    const one = await store.create('alice', 'orders', { secret: 'first' });
    const two = await store.create('alice', 'orders', { secret: 'second' });
    await store.create('bob', 'orders', { secret: 'bob' });
    await t.test('persisted payload survives closing pool and recreating store with reloaded keyring', async () => {
      await pool.end();
      pool = new Pool({ ...config, options: `-c search_path=${schema}` });
      store = new SecureRecordStore(pool, 'app-one', parseKeyRing(keyFile, 'app-one'));
      const reloaded = await store.get('alice', 'orders', one.id);
      assert.deepEqual(reloaded.payload, { secret: 'first' });
      assert.equal(reloaded.revision, 1);
      assert.equal(reloaded.createdAt, one.createdAt);
    });
    await t.test('same actor in another project cannot fetch or list the first project records', async () => {
      const otherKeys = JSON.stringify({ projectId: 'app-two', activeVersion: 'v1', keys: { v1: randomBytes(32).toString('base64') } });
      const otherStore = new SecureRecordStore(pool, 'app-two', parseKeyRing(otherKeys, 'app-two'));
      await assert.rejects(otherStore.get('alice', 'orders', one.id), { code: 'NOT_FOUND' });
      assert.deepEqual(await otherStore.list('alice', 'orders'), { records: [], nextCursor: null });
      const otherRecord = await otherStore.create('alice', 'orders', { secret: 'other-project' });
      await assert.rejects(store.get('alice', 'orders', otherRecord.id), { code: 'NOT_FOUND' });
      assert.equal((await otherStore.list('alice', 'orders')).records.length, 1);
    });
    assert.deepEqual((await store.get('alice', 'orders', one.id)).payload, { secret: 'first' });
    await assert.rejects(store.get('bob', 'orders', one.id), { code: 'NOT_FOUND' });
    await assert.rejects(store.update('bob', 'orders', one.id, {}, 1), { code: 'NOT_FOUND' });
    await assert.rejects(store.delete('bob', 'orders', one.id, 1), { code: 'NOT_FOUND' });
    const first = await store.list('alice', 'orders', { limit: 1 });
    const second = await store.list('alice', 'orders', { limit: 1, cursor: first.nextCursor });
    assert.equal(first.records.length, 1); assert.equal(second.records.length, 1);
    assert.notEqual(first.records[0].id, second.records[0].id);
    assert.equal(second.nextCursor, null);
    const updated = await store.update('alice', 'orders', one.id.toUpperCase(), { replaced: true }, 1);
    assert.equal(updated.revision, 2); assert.deepEqual(updated.payload, { replaced: true });
    await assert.rejects(store.update('alice', 'orders', one.id, {}, 1), { code: 'REVISION_CONFLICT' });
    await assert.rejects(store.delete('alice', 'orders', one.id, 1), { code: 'REVISION_CONFLICT' });
    const concurrent = await Promise.allSettled([store.update('alice', 'orders', one.id, { winner: 1 }, 2), store.update('alice', 'orders', one.id, { winner: 2 }, 2)]);
    assert.equal(concurrent.filter((r) => r.status === 'fulfilled').length, 1);
    assert.equal(concurrent.filter((r) => r.status === 'rejected' && r.reason.code === 'REVISION_CONFLICT').length, 1);
    const raw = await pool.query('SELECT ciphertext FROM omnia_secure_records WHERE id=$1', [two.id]);
    assert.ok(!raw.rows[0].ciphertext.includes('second'));
    await pool.query("ALTER TABLE omnia_secure_record_audit ADD CONSTRAINT fail_audit CHECK(action <> 'update') NOT VALID");
    await assert.rejects(store.update('alice', 'orders', two.id, { bad: true }, 1));
    assert.deepEqual((await store.get('alice', 'orders', two.id)).payload, { secret: 'second' });
    await pool.query('ALTER TABLE omnia_secure_record_audit DROP CONSTRAINT fail_audit');
    await pool.query("ALTER TABLE omnia_secure_record_audit ADD CONSTRAINT fail_audit CHECK(action <> 'delete')");
    await assert.rejects(store.delete('alice', 'orders', two.id, 1));
    assert.deepEqual((await store.get('alice', 'orders', two.id)).payload, { secret: 'second' });
    await pool.query('ALTER TABLE omnia_secure_record_audit DROP CONSTRAINT fail_audit');
    await pool.query("ALTER TABLE omnia_secure_record_audit ADD CONSTRAINT fail_audit CHECK(action <> 'create') NOT VALID");
    await assert.rejects(store.create('alice', 'orders', { mustRollBack: true }));
    assert.equal((await store.list('alice', 'orders')).records.length, 2);
    await pool.query('ALTER TABLE omnia_secure_record_audit DROP CONSTRAINT fail_audit');
    await store.delete('alice', 'orders', two.id, 1);
    await assert.rejects(store.get('alice', 'orders', two.id), { code: 'NOT_FOUND' });
    const audits = await pool.query('SELECT action,revision FROM omnia_secure_record_audit WHERE record_id=$1 ORDER BY created_at', [two.id]);
    assert.deepEqual(audits.rows, [{ action: 'create', revision: 1 }, { action: 'delete', revision: 1 }]);
    await t.test('HTTP handler real CRUD preserves trusted identity and denies another owner', async () => {
      let authentications = 0;
      // These callbacks stand in for independently verified upstream identity.
      // No request header, body field or query parameter selects the actor.
      const alice = createSecureDataHandler(async () => { authentications++; return { id: 'verified-alice' }; }, () => store);
      const bob = createSecureDataHandler(async () => { authentications++; return { id: 'verified-bob' }; }, () => store);
      const request = (method, id, input) => new Request(`https://secure.example/api/omnia/data/http_orders${id ? '/' + id : ''}`, {
        method, headers: { origin: 'https://secure.example', 'content-type': 'application/json' },
        ...(input === undefined ? {} : { body: JSON.stringify(input) }),
      });
      const call = async (handler, method, id, input) => {
        const response = await handler(request(method, id, input), id ? ['http_orders', id] : ['http_orders']);
        assert.equal(response.headers.get('cache-control'), 'no-store');
        return response;
      };
      const created = await call(alice, 'POST', null, { payload: { secret: 'http-persisted' } });
      assert.equal(created.status, 201);
      const record = (await created.json()).record;
      assert.deepEqual(record.payload, { secret: 'http-persisted' });
      const read = await call(alice, 'GET', record.id);
      assert.equal(read.status, 200);
      assert.deepEqual((await read.json()).record.payload, record.payload);
      const listed = await call(alice, 'GET');
      assert.equal(listed.status, 200);
      assert.deepEqual((await listed.json()).records.map(item => item.id), [record.id]);
      const bobList = await call(bob, 'GET');
      assert.equal(bobList.status, 200);
      assert.deepEqual((await bobList.json()).records, []);
      for (const [method, input] of [['GET', undefined], ['PUT', { payload: { stolen: true }, revision: 1 }], ['DELETE', { revision: 1 }]]) {
        const denied = await call(bob, method, record.id, input);
        assert.equal(denied.status, 404);
        assert.deepEqual(await denied.json(), { error: 'NOT_FOUND' });
      }
      const updated = await call(alice, 'PUT', record.id, { payload: { secret: 'http-updated' }, revision: 1 });
      assert.equal(updated.status, 200);
      assert.equal((await updated.json()).record.revision, 2);
      const stale = await call(alice, 'DELETE', record.id, { revision: 1 });
      assert.equal(stale.status, 409);
      const persisted = await call(alice, 'GET', record.id);
      assert.deepEqual((await persisted.json()).record.payload, { secret: 'http-updated' });
      const deleted = await call(alice, 'DELETE', record.id, { revision: 2 });
      assert.equal(deleted.status, 204);
      assert.equal((await call(alice, 'GET', record.id)).status, 404);
      const rawAudit = await pool.query('SELECT actor_id,action,revision FROM omnia_secure_record_audit WHERE record_id=$1 ORDER BY id', [record.id]);
      assert.deepEqual(rawAudit.rows, [
        { actor_id: 'verified-alice', action: 'create', revision: 1 },
        { actor_id: 'verified-alice', action: 'update', revision: 2 },
        { actor_id: 'verified-alice', action: 'delete', revision: 2 },
      ]);
      assert.equal(authentications, 12);
    });
  } finally {
    try { if (pool && !pool.ended) await pool.end(); }
    finally {
      try { await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`); }
      finally { await admin.end(); }
    }
  }
});
