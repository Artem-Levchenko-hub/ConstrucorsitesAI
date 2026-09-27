// Disposable real-core HTTP acceptance. No production credentials or external MAX traffic.
// Run inside the exact candidate image after its trusted bootstrap, using runtime DSN only.
import assert from 'node:assert/strict';
import { createHmac, randomBytes, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';

const ORIGIN = 'https://core-role.example.test';
const BASE = 'http://127.0.0.1:3000';
const PROJECT = '00000000-0000-4000-8000-000000000001';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const cases = [];
let stage = 'preflight';
let requests = 0;

function safeDsn(raw) {
  const url = new URL(raw);
  assert.equal(url.protocol, 'postgresql:');
  assert.equal(url.hostname, 'qa-pg');
  assert.equal(url.port, '5432');
  assert.equal(url.pathname, '/qa_core_role_http');
  assert.equal(url.username, 'omnia_core_runtime');
  assert.ok(url.password.length >= 24 && !url.search && !url.hash);
  return raw;
}

function denial(response, expected, protectedValues) {
  assert.equal(response.status, expected);
  const serialized = JSON.stringify(response.data);
  for (const value of protectedValues) assert.ok(!serialized.includes(value));
}

function ownedAction(action, actor, marker) {
  assert.ok(action && UUID.test(action.id));
  assert.equal(action.maxUserId, actor);
  assert.equal(action.payload.marker, marker);
  return action;
}

function launch(token, actor) {
  const fields = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: randomUUID(), user: JSON.stringify({ id: Number(actor) }),
  };
  const key = createHmac('sha256', 'WebAppData').update(token).digest();
  const check = Object.keys(fields).sort().map(key => `${key}=${fields[key]}`).join('\n');
  return new URLSearchParams({ ...fields, hash: createHmac('sha256', key).update(check).digest('hex') }).toString();
}

async function request(path, { method = 'GET', cookie, body, headers = {} } = {}) {
  assert.ok(path.startsWith('/api/') && !path.includes('://'));
  requests++;
  const response = await fetch(BASE + path, {
    method, redirect: 'manual', signal: AbortSignal.timeout(10_000),
    headers: { Origin: ORIGIN, ...(cookie ? { Cookie: cookie } : {}),
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const reader = response.body?.getReader();
  const chunks = []; let bytes = 0;
  if (reader) {
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.length;
        assert.ok(bytes <= 262144);
        chunks.push(value);
      }
    } finally { await reader.cancel(); }
  }
  const text = Buffer.concat(chunks).toString('utf8');
  let data = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* denial also checks non-JSON bodies */ }
  return { status: response.status, data, headers: response.headers };
}

async function main() {
  assert.equal(process.env.OMNIA_DISPOSABLE_CORE_HTTP, '1');
  assert.equal(process.platform, 'linux');
  assert.equal(process.cwd(), '/app');
  const dsn = safeDsn(process.env.DATABASE_URL);
  assert.equal(process.env.CORE_RUNTIME_PASSWORD, undefined);
  const { default: pg } = await import('pg');
  const pool = new pg.Pool({ connectionString: dsn, max: 1,
    connectionTimeoutMillis: 3000, statement_timeout: 5000, query_timeout: 6000 });
  const auth = randomBytes(32).toString('hex');
  const token = randomBytes(32).toString('hex');
  const webhookSecret = randomBytes(32).toString('hex');
  let child;
  let result;
  let childStartFailed = false;
  let childLogBytes = 0;
  let mockCalls = 0;
  let mockFail = false;
  let mockBadRequest = false;
  const mock = createServer(async (req, res) => {
    try {
      let bytes = 0;
      for await (const chunk of req) { bytes += chunk.length; assert.ok(bytes <= 65536); }
      const url = new URL(req.url, 'http://127.0.0.1:3101');
      assert.equal(req.method, 'POST');
      assert.equal(url.pathname, '/messages');
      assert.ok(['10001', '10002'].includes(url.searchParams.get('user_id')));
      assert.equal(req.headers.authorization, token);
      mockCalls++;
      res.writeHead(mockFail ? 503 : 200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(mockFail ? { error: 'synthetic' } : { message: { mid: 'synthetic' } }));
    } catch {
      mockBadRequest = true; res.writeHead(400); res.end();
    }
  });
  const lifetime = setTimeout(() => process.exit(124), 180_000);
  lifetime.unref();

  async function actorSql(actor, sql, args = []) {
    const connection = await pool.connect();
    try {
      await connection.query('BEGIN');
      await connection.query("SELECT set_config('app.max_user_id',$1,true)", [actor]);
      const result = await connection.query(sql, args);
      await connection.query('COMMIT');
      return result;
    } catch (error) { await connection.query('ROLLBACK'); throw error; }
    finally { connection.release(); }
  }

  async function stop() {
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, 'exit');
    child.kill('SIGTERM');
    await Promise.race([exited, delay(3000)]);
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGKILL'); await Promise.race([exited, delay(3000)]);
    }
    assert.ok(child.exitCode !== null || child.signalCode !== null);
  }

  async function start(ownerPreview = false) {
    const env = {
      PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp', NODE_ENV: 'production',
      NEXT_TELEMETRY_DISABLED: '1', NODE_OPTIONS: '--max-old-space-size=384',
      HOSTNAME: '127.0.0.1', PORT: '3000', DATABASE_URL: dsn, AUTH_SECRET: auth,
      MAX_BOT_TOKEN: token, MAX_WEBHOOK_SECRET: webhookSecret,
      MAX_API_BASE_URL: 'http://127.0.0.1:3101', OMNIA_PROJECT_ID: PROJECT,
      ...(ownerPreview ? { OMNIA_OWNER_PREVIEW: '1' } : { OMNIA_PUBLIC_APP_ORIGIN: ORIGIN }),
    };
    assert.ok(!('CORE_RUNTIME_PASSWORD' in env));
    child = spawn(process.execPath, ['server.js'], { cwd: '/app', env, stdio: ['ignore', 'pipe', 'pipe'] });
    childStartFailed = false;
    child.on('error', () => { childStartFailed = true; });
    for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => {
      childLogBytes += chunk.length;
      if (childLogBytes > 4 * 1024 * 1024) child.kill('SIGKILL');
      // Drain only; Next/driver output may contain SQL, cookies or request bodies.
    });
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      assert.equal(childStartFailed, false);
      assert.equal(child.exitCode, null);
      try {
        const response = await request('/api/omnia/health');
        if (response.status === 200) {
          assert.deepEqual(response.data, { status: 'ok', platform: 'max-miniapp' }); return;
        }
      } catch { /* bounded boot/readiness wait */ }
      await delay(150);
    }
    throw new Error('readiness');
  }

  async function login(actor) {
    const response = await request('/api/max/session', { method: 'POST', body: { initData: launch(token, actor) } });
    assert.equal(response.status, 200); assert.equal(response.data.user.id, actor);
    const set = response.headers.getSetCookie().find(value => value.startsWith('__Host-max_session='));
    assert.ok(set && /; Secure/i.test(set) && /; HttpOnly/i.test(set));
    const cookie = set.split(';', 1)[0];
    const resumed = await request('/api/max/session', { cookie });
    assert.equal(resumed.status, 200); assert.equal(resumed.data.user.id, actor);
    assert.notEqual(resumed.data.mode, 'preview');
    return cookie;
  }

  async function list(cookie) {
    const response = await request('/api/omnia/actions', { cookie });
    assert.equal(response.status, 200); assert.ok(Array.isArray(response.data.actions));
    return response.data.actions;
  }

  function bootstrapPath() {
    const expires = String(Math.floor(Date.now() / 1000) + 60);
    const signature = createHmac('sha256', auth)
      .update(`omnia:max-preview-session:v1\n${PROJECT}\n${expires}`).digest('base64url');
    return '/api/omnia/preview-session?' + new URLSearchParams({ expires, signature });
  }

  async function authDenials() {
    const expired = Buffer.from(JSON.stringify({ id: 'preview', expiresAt: Math.floor(Date.now() / 1000) - 10 })).toString('base64url');
    const signedExpired = '__Host-max_session=' + expired + '.' + createHmac('sha256', auth).update(expired).digest('base64url');
    for (const cookie of ['', '__Host-max_session=unsigned', signedExpired]) {
      const response = await request('/api/max/session', { cookie });
      assert.equal(response.status, 401); assert.ok(!('user' in response.data));
      assert.equal(response.headers.getSetCookie().length, 0);
    }
    assert.equal((await request('/api/omnia/preview-session')).status, 404);
    denial(await request('/api/max/session', { method: 'POST', body: { initData: 'unsigned' } }), 401, []);
    // Keep the issued, valid payload intact: this falsifies a missing MAC comparison.
    const issued = await login('10001');
    const dot = issued.lastIndexOf('.');
    assert.ok(dot > 0 && issued.length > dot + 2);
    const signature = issued.slice(dot + 1);
    const tampered = issued.slice(0, dot + 1) + (signature[0] === 'A' ? 'B' : 'A') + signature.slice(1);
    const rejected = await request('/api/max/session', { cookie: tampered });
    assert.equal(rejected.status, 401); assert.ok(!('user' in rejected.data));
    assert.equal(rejected.headers.getSetCookie().length, 0);
  }

  try {
    const role = (await pool.query('SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user')).rows[0];
    assert.equal(role.current_user, 'omnia_core_runtime');
    assert.ok(['rolsuper','rolbypassrls','rolcreatedb','rolcreaterole','rolreplication'].every(key => role[key] === false));
    await new Promise((resolve, reject) => {
      mock.once('error', reject); mock.listen(3101, '127.0.0.1', resolve);
    });
    stage = 'owner_preview_auth';
    await start(true);
    const bootstrap = await request(bootstrapPath());
    assert.equal(bootstrap.status, 307); assert.equal(bootstrap.headers.get('location'), '/');
    const ownerCookie = bootstrap.headers.getSetCookie().find(value => value.startsWith('__Host-max_session='))?.split(';', 1)[0];
    assert.ok(ownerCookie);
    for (let i = 0; i < 2; i++) {
      const resume = await request('/api/max/session', { cookie: ownerCookie });
      assert.equal(resume.status, 200); assert.equal(resume.data.user.id, 'preview');
      assert.equal(resume.data.mode, 'preview'); assert.equal(resume.headers.getSetCookie().length, 0);
    }
    await authDenials(); await login('10001');
    cases.push('private_owner_preview_bootstrap_resume_denials_numeric_login');
    await stop();
    stage = 'public_preview_denied';
    await start(); cases.push('restricted_server_boot_health');
    assert.equal((await request(bootstrapPath())).status, 404);
    const publicResume = await request('/api/max/session', { cookie: ownerCookie });
    assert.equal(publicResume.status, 401); assert.ok(!('user' in publicResume.data));
    assert.equal(publicResume.headers.getSetCookie().length, 0);
    await authDenials();
    cases.push('public_same_secret_preview_denied');
    stage = 'authentication';
    denial(await request('/api/max/session', { method: 'POST', body: { initData: 'unsigned' } }), 401, []);
    const actors = ['10001', '10002'];
    const cookies = [await login(actors[0]), await login(actors[1])];
    for (const actor of actors) {
      const rows = (await actorSql(actor, 'SELECT max_user_id,first_name FROM max_users')).rows;
      assert.deepEqual(rows, [{ max_user_id: actor, first_name: '' }]);
    }
    cases.push('real_hmac_login_resume_parent_insert');
    for (const [path, method, body] of [
      ['/api/omnia/actions','GET',undefined], ['/api/omnia/actions','POST',{ actionType: 'qa' }],
      ['/api/omnia/consents','GET',undefined], ['/api/omnia/consents','POST',{}],
      ['/api/omnia/events','POST',{}],
    ]) denial(await request(path, { method, body }), 401, []);
    const saved = [];
    for (let i = 0; i < 2; i++) {
      stage = 'owner_crud_and_spoof';
      const actor = actors[i], other = actors[1-i], cookie = cookies[i];
      const marker = `qa-marker-${i}`;
      const created = await request('/api/omnia/actions', { method: 'POST', cookie,
        body: { actionType: 'qa_core_role', payload: { marker }, maxUserId: other, max_user_id: other, userId: other, user_id: other } });
      assert.equal(created.status, 201);
      const action = ownedAction(created.data.action, actor, marker); saved.push(action);
      assert.equal((await actorSql(actor, 'SELECT count(*)::int n FROM max_audit_log WHERE details->>\'actionId\'=$1', [action.id])).rows[0].n, 1);
      const own = await request(`/api/omnia/actions/${action.id}`, { cookie });
      assert.equal(own.status, 200); ownedAction(own.data.action, actor, marker);
      const before = JSON.stringify(own.data.action);
      for (const method of ['GET','PATCH','DELETE']) {
        const response = await request(`/api/omnia/actions/${action.id}`, { method, cookie: cookies[1-i],
          body: method === 'PATCH' ? { status: 'stolen' } : undefined });
        denial(response, 404, [action.id, marker]);
      }
      for (const cookieForSpoof of [cookie,cookies[1-i]]) {
        const response = await request(`/api/omnia/actions/${action.id}`, { method: 'PATCH', cookie: cookieForSpoof,
          body: { status: 'stolen', maxUserId: actor } });
        denial(response, 400, [action.id, marker]);
      }
      denial(await request(`/api/omnia/actions/${action.id}`), 401, [action.id, marker]);
      assert.equal(JSON.stringify((await request(`/api/omnia/actions/${action.id}`, { cookie })).data.action), before);
      const patched = await request(`/api/omnia/actions/${action.id}`, { method: 'PATCH', cookie,
        body: { status: 'done', payload: { marker } } });
      assert.equal(patched.status, 200); assert.equal(patched.data.action.status, 'done');
      ownedAction(patched.data.action, actor, marker);
    }
    cases.push('own_create_read_update_foreign_get_patch_delete_body_spoof_denied');
    stage = 'pool_context';
    for (let iteration = 0; iteration < 12; iteration++) {
      const lists = await Promise.all(cookies.map(cookie => list(cookie)));
      lists.forEach((rows, i) => { assert.equal(rows.length, 1); ownedAction(rows[0], actors[i], `qa-marker-${i}`); });
    }
    const connection = await pool.connect();
    try {
      for (const ending of ['COMMIT','ROLLBACK']) {
        await connection.query('BEGIN');
        await connection.query("SELECT set_config('app.max_user_id','10001',true)");
        assert.equal((await connection.query('SELECT count(*)::int n FROM max_business_actions')).rows[0].n, 1);
        await connection.query(ending);
        assert.equal((await connection.query('SELECT count(*)::int n FROM max_business_actions')).rows[0].n, 0);
      }
    } finally { connection.release(); }
    cases.push('http_alternating_concurrent_actors_and_runtime_pool_local_guc_reset');
    stage = 'consents_events';
    for (let i = 0; i < 2; i++) {
      const consent = await request('/api/omnia/consents', { method: 'POST', cookie: cookies[i],
        body: { consentType: 'qa', granted: true, policyVersion: 'qa-1', maxUserId: actors[1-i] } });
      assert.equal(consent.status, 201); assert.equal(consent.data.consent.maxUserId, actors[i]);
      const consents = await request('/api/omnia/consents', { cookie: cookies[i] });
      assert.equal(consents.status, 200); assert.equal(consents.data.consents.length, 1);
      assert.equal(consents.data.consents[0].maxUserId, actors[i]);
      assert.equal((await request('/api/omnia/events', { method: 'POST', cookie: cookies[i],
        body: { eventName: 'qa_event', properties: { synthetic: true }, maxUserId: actors[1-i] } })).status, 204);
      assert.equal((await actorSql(actors[i], 'SELECT count(*)::int n FROM max_analytics_events')).rows[0].n, 1);
    }
    cases.push('consents_events_owner_isolation');
    stage = 'restart_persistence';
    const beforeRestart = await Promise.all(cookies.map(cookie => list(cookie)));
    await stop(); await start();
    assert.deepEqual(await Promise.all(cookies.map(cookie => list(cookie))), beforeRestart);
    cases.push('real_server_restart_persistence');
    stage = 'delete_audit_cleanup';
    for (let i = 0; i < 2; i++) {
      const response = await request(`/api/omnia/actions/${saved[i].id}`, { method: 'DELETE', cookie: cookies[i] });
      assert.equal(response.status, 200); assert.equal(response.data.deleted, true);
      assert.equal(response.data.id, saved[i].id); assert.equal((await list(cookies[i])).length, 0);
      assert.equal((await actorSql(actors[i], 'SELECT count(*)::int n FROM max_audit_log')).rows[0].n, 0);
      assert.equal((await actorSql(actors[i], 'SELECT count(*)::int n FROM max_users')).rows[0].n, 1);
    }
    cases.push('own_delete_audit_cleanup_preserves_existing_parent');
    stage = 'health_probe_parent_cleanup';
    const probeActor = '10003', probeCookie = await login(probeActor);
    assert.equal((await actorSql(probeActor, 'DELETE FROM max_users WHERE max_user_id=$1', [probeActor])).rowCount, 1);
    const probe = await request('/api/omnia/actions', { method: 'POST', cookie: probeCookie,
      body: { actionType: 'omnia_health_qa', payload: { marker: 'health-only' } } });
    assert.equal(probe.status, 201); assert.equal(probe.data.probeUserCreated, true);
    const removed = await request(`/api/omnia/actions/${probe.data.action.id}`, { method: 'DELETE', cookie: probeCookie });
    assert.equal(removed.status, 200); assert.equal(removed.data.probeUserDeleted, true);
    assert.equal((await actorSql(probeActor, 'SELECT count(*)::int n FROM max_users')).rows[0].n, 0);
    assert.equal((await actorSql(probeActor, 'SELECT count(*)::int n FROM max_audit_log')).rows[0].n, 0);
    cases.push('health_fixture_parent_and_audit_cleanup');
    stage = 'webhook_dedup_failure_cleanup';
    const headers = { 'x-max-bot-api-secret': webhookSecret };
    const event = { update_id: 'qa-webhook-1', update_type: 'bot_started', user: { user_id: 10001 } };
    denial(await request('/api/max/webhook', { method: 'POST', body: event, headers: { 'x-max-bot-api-secret': 'invalid' } }), 401, []);
    assert.equal(mockCalls, 0);
    assert.equal((await request('/api/max/webhook', { method: 'POST', headers, body: event })).status, 200);
    assert.equal(mockCalls, 1);
    const duplicate = await request('/api/max/webhook', { method: 'POST', headers, body: event });
    assert.equal(duplicate.status, 200); assert.equal(duplicate.data.duplicate, true); assert.equal(mockCalls, 1);
    const retry = { update_id: 'qa-webhook-2', update_type: 'message_created', message: { sender: { user_id: 10002 }, body: { text: '/help' } } };
    mockFail = true;
    assert.equal((await request('/api/max/webhook', { method: 'POST', headers, body: retry })).status, 503);
    assert.equal((await pool.query("SELECT count(*)::int n FROM max_webhook_events WHERE event_key='qa-webhook-2'")).rows[0].n, 0);
    mockFail = false;
    assert.equal((await request('/api/max/webhook', { method: 'POST', headers, body: retry })).status, 200);
    assert.equal(mockCalls, 3); assert.equal(mockBadRequest, false);
    assert.equal((await pool.query('SELECT count(*)::int n FROM max_webhook_events')).rows[0].n, 2);
    assert.equal((await pool.query('SELECT count(*)::int n FROM max_business_actions')).rows[0].n, 0);
    cases.push('webhook_valid_secret_duplicate_failure_cleanup_retry_no_actor_guc');
    stage = 'catalog_config';
    const config = await request('/api/omnia/config');
    assert.equal(config.status, 200); assert.equal(config.data.app_name, 'Core role synthetic QA');
    assert.equal((await request('/api/health')).status, 200);
    cases.push('catalog_json_config_health');
    assert.ok(childLogBytes <= 4 * 1024 * 1024);
    result = { passed: true, cases, requests, mock_max_calls: mockCalls,
      server_has_admin_env: false, real_MAX_launch: 'NOT_RUN',
      pool_scope: 'runtime_role_test_pool_commit_rollback_plus_real_HTTP_concurrency' };
  } finally {
    await stop();
    mock.closeAllConnections();
    await new Promise(resolve => mock.close(resolve));
    await pool.end();
    clearTimeout(lifetime);
  }
  console.log(JSON.stringify(result));
}

function selfTest() {
  // Falsifying the harness itself: an error status must not hide a foreign body.
  assert.throws(() => denial({ status: 404, data: { escaped: 'foreign-marker' } }, 404, ['foreign-marker']));
  assert.throws(() => ownedAction({ id: randomUUID(), maxUserId: '10002', payload: { marker: 'x' } }, '10001', 'x'));
  for (const bad of ['postgresql://postgres:secret@qa-pg:5432/qa_core_role_http',
    'postgresql://omnia_core_runtime:synthetic-password-value-123@production:5432/qa_core_role_http']) {
    assert.throws(() => safeDsn(bad));
  }
  console.log(JSON.stringify({ offline_safety_checks: 'PASS', HTTP_runtime: 'NOT_RUN' }));
}

try {
  if (process.argv.includes('--self-test')) selfTest(); else await main();
} catch {
  // Assertion/driver/server errors may include private values. Emit only our closed stage.
  console.log(JSON.stringify({ passed: false, stage, cases }));
  process.exitCode = 1;
}
