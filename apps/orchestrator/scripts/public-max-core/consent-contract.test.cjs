/* Executes trusted core consent routes with real HMAC and isolated platform/DB boundaries. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { createRequire } = require('node:module');
const { createHash, createHmac, randomUUID } = require('node:crypto');
const vm = require('node:vm');
const root = process.env.OMNIA_CONSENT_TEST_TEMPLATE_ROOT || resolve(__dirname, '../../templates/max-miniapp-nextjs');
const qaRequire = createRequire(resolve(process.env.OMNIA_TEST_NODE_ROOT || root, 'package.json'));
const ts = qaRequire('typescript');
const activity = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root, 'src/lib/omnia/activity.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, activity);
let zod; try { zod = qaRequire('zod'); } catch { zod = qaRequire('next/dist/compiled/zod'); }
const project = '00000000-0000-4000-8000-000000000005';
const policy = { enabled: true, policy_version: '2026-10-11', policy_hash: 'a'.repeat(64) };
function fixture({ enabled = true, offline = false, lostResponse = false, invalidPolicy = false, revokeBeforeReply = false } = {}) {
  const rows = [], calls = [], writes = [], events = new Map();
  let state = { granted: false, policy_version: null, policy_hash: null, revision: 0 };
  let actor = '10001';
  let lose = lostResponse;
  const tables = { maxConsents: { id: 'id', maxUserId: 'maxUserId', createdAt: 'createdAt', consentType: 'consentType' } };
  const matches = (row, condition) => Array.isArray(condition) ? condition.every(item => matches(row, item)) :
    condition.not ? row[condition.column] !== condition.value : row[condition.column] === condition.value;
  const tx = {
    insert() { let value; return { values(v) { value = v; return this; }, onConflictDoNothing() { return this; }, async returning() { if (rows.some(row => row.id === value.id)) return []; const row = { id: randomUUID(), createdAt: new Date(), ...value }; rows.push(row); return [row]; } }; },
    select() { let conditions = []; const q = { from() { return this; }, where(value) { conditions = value; return this; }, orderBy() { return this; }, limit() { return this; }, then(a,b) { return Promise.resolve([...rows].filter(row => matches(row, conditions)).reverse()).then(a,b); } }; return q; },
  };
  async function fetch(url, init) {
    const path = new URL(url).pathname; calls.push({ path, init });
    assert.equal(init.headers['X-MAX-Init-Data'], undefined);
    const assertion = init.headers['X-Omnia-Integration-Assertion'];
    assert.ok(assertion, 'trusted HMAC required');
    const [version, encoded, signature] = assertion.split('.');
    assert.equal(version, 'v1');
    const key = createHmac('sha256', 'synthetic-core-test-token').update('omnia:integration-assertion:v1').digest();
    assert.equal(signature, createHmac('sha256', key).update(`${version}.${encoded}`).digest('hex'));
    const claims = JSON.parse(Buffer.from(encoded, 'base64url'));
    assert.equal(claims.max_user_id, actor); assert.equal(claims.project_id, project);
    assert.equal(claims.method, init.method); assert.equal(claims.path, path);
    assert.equal(claims.body_sha256, createHash('sha256').update(init.body || '').digest('hex'));
    if (offline) throw Error('synthetic platform offline');
    if (path.endsWith('/consent-policy')) return Response.json(invalidPolicy ? {} : { ...policy, enabled });
    if (init.method === 'GET') return Response.json(state);
    const body = JSON.parse(init.body); writes.push(body);
    const saved = events.get(body.event_id);
    if (saved && saved.body !== init.body) return Response.json({ error: { code: 'consent_event_conflict' } }, { status: 409 });
    if (!saved) {
      if (body.expected_revision !== state.revision) return Response.json({ error: { code: 'consent_revision_conflict' } }, { status: 409 });
      state = { granted: body.granted, policy_version: body.policy_version, policy_hash: body.policy_hash, revision: state.revision + 1 };
      events.set(body.event_id, { body: init.body, receipt: state });
    }
    if (lose) { lose = false; throw Error('synthetic lost committed response'); }
    if (revokeBeforeReply && body.granted) state = { ...state, granted: false, revision: state.revision + 1 };
    return Response.json(events.get(body.event_id).receipt);
  }
  const modules = {
    'node:crypto': require('node:crypto'), 'zod': zod,
    'next/server': { NextResponse: { json: (data, init) => Response.json(data, init) } },
    'drizzle-orm': { eq: (column, value) => ({ column, value }), ne: (column, value) => ({ column, value, not: true }), and: (...x) => x, or: (...x) => x, desc: x => x, lt: (...x) => x },
    '@/lib/max/session': { getMaxUser: async () => actor ? { id: actor } : null },
    '@/lib/db': { schema: tables, withMaxUser: async (_actor, run) => run(tx) },
  };
  function load(file) {
    const context = { exports: {}, require(name) { if (name === '@/lib/omnia/consent') return load('src/lib/omnia/consent.ts'); assert.ok(name in modules, name); return modules[name]; }, process: { env: { OMNIA_PROJECT_ID: project, MAX_BOT_TOKEN: 'synthetic-core-test-token', OMNIA_PLATFORM_API_URL: 'https://platform.invalid' } }, Buffer, Date, fetch, AbortSignal, URL, TextEncoder };
    vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
    return context.exports;
  }
  const route = load('src/app/api/omnia/consents/route.ts');
  const integrations = load('src/app/api/omnia/integrations/[...path]/route.ts');
  const post = (body) => route.POST(new Request('https://core.invalid/api/omnia/consents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
  const integration = operation => integrations.POST(new Request('https://core.invalid/api/omnia/integrations/' + operation, { method: 'POST', body: JSON.stringify({ initData: 'untrusted-input' }) }), { params: Promise.resolve({ path: [operation] }) });
  return { rows, calls, writes, post, integration, get: (platformOnly = true) => route.GET(new Request('https://core.invalid/api/omnia/consents' + (platformOnly ? '?platform=1' : ''))), state: () => state, setActor: x => { actor = x; } };
}
const input = { consentType: 'personal_data', granted: true, policyVersion: '1' };
test('explicit legacy grant maps to current authoritative policy with platform-only audit', async () => {
  const f = fixture(); const response = await f.post(input);
  assert.equal(response.status, 201); assert.equal(f.writes.length, 1);
  assert.equal(f.writes[0].policy_version, policy.policy_version); assert.equal(f.writes[0].policy_hash, policy.policy_hash);
  assert.equal(f.rows.length, 0); assert.equal(f.state().granted, true);
});
test('lost response retries frozen event/revision; revoke and signed old event replay cannot regrant', async () => {
  const f = fixture({ lostResponse: true });
  const eventId = randomUUID(); const original = { ...input, eventId, expectedRevision: 0 };
  assert.equal((await f.post(original)).status, 201); assert.equal(f.writes.length, 2);
  assert.deepEqual(f.writes[0], f.writes[1]); assert.equal(f.rows.length, 0);
  assert.equal((await f.post({ ...input, granted: false, eventId: randomUUID(), expectedRevision: 1 })).status, 201);
  assert.equal(f.state().granted, false); assert.equal(f.state().revision, 2);
  // Replay cannot append an old grant or label its receipt as current state.
  const replay = await f.post(original);
  assert.equal(replay.status, 201); assert.equal((await replay.json()).state.granted, false);
  assert.equal(f.state().granted, false); assert.equal(f.state().revision, 2); assert.equal(f.rows.length, 0);
});

test('delayed grant response after concurrent revoke uses fresh state and cannot append local grant', async () => {
  const f = fixture({ revokeBeforeReply: true });
  const response = await f.post(input);
  assert.equal(response.status, 201);
  assert.equal((await response.json()).state.granted, false);
  assert.equal(f.state().revision, 2); assert.equal(f.rows.length, 0);
  assert.equal(f.writes.length, 1, 'fresh state read never regrants');
});
test('revision conflict does not refresh baseline or create local consent', async () => {
  const f = fixture(); const response = await f.post({ ...input, eventId: randomUUID(), expectedRevision: 9 });
  assert.equal(response.status, 409); assert.equal(f.writes.length, 1); assert.equal(f.rows.length, 0);
});
test('platform failure fails closed before app-local audit; GET never migrates old grants', async () => {
  const f = fixture({ offline: true }); assert.equal((await f.post(input)).status, 503); assert.equal(f.rows.length, 0);
  const enabled = fixture(); enabled.rows.push({ id: randomUUID(), consentType: 'personal_data', granted: true, policyVersion: '1', maxUserId: '10001', createdAt: new Date() });
  const result = await enabled.get(); assert.equal(result.status, 200); assert.equal((await result.json()).state.granted, false); assert.equal(enabled.writes.length, 0);
  const history = await enabled.get(false);
  assert.equal(history.status, 200); assert.equal((await history.json()).consents.length, 0);
});
test('disabled policy preserves local consent; unrelated consent cannot grant personal data', async () => {
  const disabled = fixture({ enabled: false }); assert.equal((await disabled.post(input)).status, 201); assert.equal(disabled.rows[0].policyVersion, '1'); assert.equal(disabled.writes.length, 0);
  const other = fixture(); assert.equal((await other.post({ ...input, consentType: 'marketing' })).status, 201); assert.equal(other.writes.length, 0); assert.equal(other.state().granted, false);
});
test('stale explicit policy and anonymous requests cannot record consent', async () => {
  const f = fixture(); assert.equal((await f.post({ ...input, policyVersion: 'older-policy' })).status, 409); assert.equal(f.writes.length, 0); assert.equal(f.rows.length, 0);
  f.setActor(null); assert.equal((await f.post(input)).status, 401);
});

test('explicit revoke from an older policy remains possible after policy rotation', async () => {
  const f = fixture();
  assert.equal((await f.post(input)).status, 201);
  const response = await f.post({ ...input, granted: false, policyVersion: 'older-policy',
    policyHash: 'b'.repeat(64), eventId: randomUUID(), expectedRevision: 1 });
  assert.equal(response.status, 201); assert.equal(f.state().granted, false);
  assert.equal(f.writes.at(-1).policy_version, policy.policy_version);
  assert.equal(f.writes.at(-1).policy_hash, policy.policy_hash);
});

test('consent read capabilities require trusted session and reject malformed policy', async () => {
  const f = fixture();
  assert.equal((await f.integration('consent-policy')).status, 200);
  assert.equal((await f.integration('consent-state')).status, 200);
  assert.equal(f.writes.length, 0);
  f.setActor(null); const before = f.calls.length;
  assert.equal((await f.integration('consent-policy')).status, 401);
  assert.equal((await f.integration('consent-state')).status, 401);
  assert.equal(f.calls.length, before);
  const malformed = fixture({ invalidPolicy: true });
  assert.equal((await malformed.post(input)).status, 503);
  assert.equal(malformed.rows.length, 0); assert.equal(malformed.writes.length, 0);
});

function sdk(fetch) {
  const context = { exports: {}, require(name) {
    if (name === '@/lib/max/bridge') return { getMaxWebApp: () => null };
    if (name === '@/lib/omnia/integration-client') return {};
    if (name === '@/lib/omnia/activity') return activity.exports;
    throw Error('unexpected module ' + name);
  }, crypto: { randomUUID }, fetch, AbortSignal, AbortController, setTimeout, clearTimeout, URLSearchParams };
  vm.runInNewContext(ts.transpileModule(readFileSync(resolve(root, 'src/lib/omnia/client.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  return context.exports;
}

test('SDK retries one frozen explicit grant, preserves revoke, and never renews a 409 baseline', async () => {
  const calls = []; let revision = 5; let mode = 'lost';
  const client = sdk(async (path, init) => {
    calls.push({ path, init });
    if (path.endsWith('?platform=1')) return Response.json({ actor: '10001', policy, state: { revision } });
    if (mode === 'lost') { mode = 'ok'; revision++; throw Error('response lost'); }
    return Response.json({ state: { granted: JSON.parse(init.body).granted,
      policy_version: policy.policy_version, policy_hash: policy.policy_hash } },
    { status: mode === 'conflict' ? 409 : 201 });
  });
  await client.saveMaxConsent('personal_data', true);
  assert.equal(calls.length, 3);
  assert.equal(calls[1].init.body, calls[2].init.body);
  assert.equal(calls[1].init.headers['X-Omnia-Actor'], '10001');
  const grant = JSON.parse(calls[1].init.body);
  assert.equal(grant.expectedRevision, 5); assert.equal(grant.policyHash, policy.policy_hash);
  await client.saveMaxConsent('personal_data', false);
  const revoke = JSON.parse(calls[4].init.body);
  assert.equal(revoke.granted, false); assert.equal(revoke.expectedRevision, 6);
  assert.notEqual(revoke.eventId, grant.eventId);
  mode = 'conflict'; const before = calls.length;
  await assert.rejects(client.saveMaxConsent('personal_data', true), /Consent save failed/);
  assert.equal(calls.length - before, 2, 'one snapshot and one POST, no refresh/regrant');
});

test('SDK does not POST when authoritative state cannot be read', async () => {
  const calls = [];
  const client = sdk(async (path) => { calls.push(path); return Response.json({}, { status: 503 }); });
  await assert.rejects(client.saveMaxConsent('personal_data', true), /Consent save failed/);
  assert.deepEqual(calls, ['/api/omnia/consents?platform=1']);
});

test('SDK refuses a successful historical grant receipt when current state is revoked', async () => {
  const calls = [];
  const client = sdk(async (path, init) => {
    calls.push({ path, init });
    if (path.endsWith('?platform=1')) return Response.json({ actor: '10001', policy, state: { revision: 0 } });
    if (calls.length === 2) throw Error('original grant response lost');
    return Response.json({ state: { granted: false, policy_version: policy.policy_version, policy_hash: policy.policy_hash } }, { status: 201 });
  });
  await assert.rejects(client.saveMaxConsent('personal_data', true), /Consent save failed/);
  assert.equal(calls.length, 3); assert.equal(calls[1].init.body, calls[2].init.body);
});
