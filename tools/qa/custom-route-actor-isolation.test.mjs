import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:http';
import { AsyncLocalStorage } from 'node:async_hooks';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { createHmac } from 'node:crypto';
import { probeActorIsolation, validateFixture } from './custom-route-actor-isolation.mjs';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const template = new URL('../../apps/orchestrator/templates/max-miniapp-nextjs/', import.meta.url);
const context = new AsyncLocalStorage();
const token = 'synthetic-bot-token-local-only-12345';
const secret = 'synthetic-session-secret-local-only-12345';
function loadSource(relative, imports) {
  const source = readFileSync(new URL(relative, template), 'utf8');
  const module = { exports: {}, Buffer, process: { env: { MAX_BOT_TOKEN: token, AUTH_SECRET: secret } },
    require: name => imports[name] ?? require(name) };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText, module, { filename: fileURLToPath(new URL(relative, template)) });
  return module.exports;
}
const validator = loadSource('src/lib/max/validate-init-data.ts', {});
const session = loadSource('src/lib/max/session.ts', {
  '@/lib/max/validate-init-data': validator,
  'next/headers': {
    cookies: async () => ({ get: () => {
      const value = context.getStore().headers.cookie?.split('=', 2)[1];
      return value ? { value } : undefined;
    } }),
    headers: async () => new Headers(context.getStore().headers),
  },
});
function launch(actor, signingToken = token) {
  const fields = { auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: actor }) };
  const key = createHmac('sha256', 'WebAppData').update(signingToken).digest();
  const data = Object.keys(fields).sort().map(key => `${key}=${fields[key]}`).join('\n');
  return new URLSearchParams({ ...fields, hash: createHmac('sha256', key).update(data).digest('hex') }).toString();
}
const credentials = [10001, 10002].map(actor => ({
  id: String(actor), initData: launch(actor), itemPath: `/api/fixture-tasks/${actor}`,
  protectedValues: [`synthetic-marker-${actor}`, `synthetic-row-${actor}`],
}));
const previewCookie = `${session.MAX_SESSION_COOKIE}=${session.createMaxSession({ id: 'preview' }).value}`;

async function fixtureServer(broken = '') {
  const methods = [];
  const rows = credentials.map(actor => ({ id: actor.protectedValues[1], marker: actor.protectedValues[0], userId: actor.id }));
  const server = createServer((req, res) => context.run(req, async () => {
    methods.push(req.method);
    if (broken === 'redirect') { res.writeHead(302, { Location: 'http://production.example/' }); res.end(); return; }
    if (broken === 'oversize') { res.writeHead(200); res.end('x'.repeat(262145)); return; }
    const user = await session.getMaxUser();
    let status = 200, data;
    if (req.url === '/__omnia/identity') {
      if (user && /^[1-9][0-9]{0,19}$/.test(user.id)) data = { user_id: user.id, project_id: 'synthetic-project-A', epoch: 7 };
      else { status = 401; data = { error: 'Unauthorized' }; }
    } else if (req.url === '/api/max/session') {
      if (user && /^[1-9][0-9]{0,19}$/.test(user.id)) data = { user };
      else if (user?.id === 'preview') data = { user, mode: 'preview' };
      else { status = 401; data = { error: 'Unauthorized' }; }
    } else if (!user) { status = 401; data = { error: 'Unauthorized' }; }
    else if (req.url === '/api/fixture-tasks') {
      data = broken === 'list' ? rows : rows.filter(row => row.userId === user.id);
    } else {
      const owner = req.url.split('/').at(-1);
      const row = rows.find(row => row.userId === owner);
      if (row && row.userId === user.id) data = row;
      else { status = 404; data = ['denial-body', 'escaped-denial-body'].includes(broken) ? rows : { error: 'Not found' }; }
    }
    res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(broken === 'escaped-denial-body' ? JSON.stringify(data).replaceAll('synthetic', '\\u0073ynthetic') : JSON.stringify(data));
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const fixture = { schemaVersion: 1, disposable: true,
    baseUrl: `http://127.0.0.1:${server.address().port}`, collectionPath: '/api/fixture-tasks', actors: credentials,
    foreignProject: { initData: launch(10001, 'foreign-project-bot-token') },
    foreignOwner: { cookie: `${session.MAX_SESSION_COOKIE}=foreign-project-invalid-cookie` },
    ownerPreview: { cookie: previewCookie },
  };
  return { fixture, methods, close: () => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }) };
}

test('real HMAC MAX U/V isolation probe performs only GET and does not count preview as actor', async () => {
  const server = await fixtureServer();
  try {
    const result = await probeActorIsolation(server.fixture);
    assert.equal(result.passed, true);
    assert.equal(result.scope, 'disposable_custom_route_read_only');
    assert.ok(result.checks.includes('signed_numeric_U_V_identity'));
    assert.ok(result.checks.includes('owner_preview_excluded_from_signed_actor_proof'));
    assert.deepEqual([...new Set(server.methods)], ['GET']);
    const serialized = JSON.stringify(result);
    for (const value of [token, secret, ...credentials.flatMap(actor => actor.protectedValues)]) assert.ok(!serialized.includes(value));
  } finally { await server.close(); }
});
for (const broken of ['list', 'denial-body', 'escaped-denial-body', 'redirect', 'oversize']) test(`probe rejects ${broken} leakage`, async () => {
  const server = await fixtureServer(broken);
  try { await assert.rejects(probeActorIsolation(server.fixture)); }
  finally { await server.close(); }
});
test('pre-issued signed cookies support portable Cell transport without login POST', async () => {
  const server = await fixtureServer();
  try {
    server.fixture.actors = credentials.map(actor => ({ ...actor, initData: undefined,
      cookie: `${session.MAX_SESSION_COOKIE}=${session.createMaxSession({ id: actor.id }).value}` }));
    assert.equal((await probeActorIsolation(server.fixture)).passed, true);
    assert.deepEqual([...new Set(server.methods)], ['GET']);
  } finally { await server.close(); }
});
test('Cell identity binds signed U/V to exact project and excludes preview', async () => {
  const server = await fixtureServer();
  try {
    server.fixture.identityMode = 'cell'; server.fixture.projectId = 'synthetic-project-A';
    server.fixture.actors = credentials.map(actor => ({ ...actor, initData: undefined,
      cookie: `${session.MAX_SESSION_COOKIE}=${session.createMaxSession({ id: actor.id }).value}` }));
    server.fixture.foreignProject = { cookie: `${session.MAX_SESSION_COOKIE}=foreign-cookie.invalid` };
    assert.equal((await probeActorIsolation(server.fixture)).passed, true);
    server.fixture.projectId = 'foreign-project';
    await assert.rejects(probeActorIsolation(server.fixture));
  } finally { await server.close(); }
});
test('preview identity cannot satisfy genuine MAX actor identity acceptance', async () => {
  const server = await fixtureServer();
  try {
    server.fixture.actors = [ { ...credentials[0], initData: undefined, cookie: previewCookie }, credentials[1] ];
    await assert.rejects(probeActorIsolation(server.fixture));
  } finally { await server.close(); }
});
test('probe refuses production targets, duplicate actors and unsafe paths before traffic', () => {
  const good = { schemaVersion: 1, disposable: true, baseUrl: 'http://127.0.0.1:3000',
    collectionPath: '/api/fixture-tasks', actors: credentials,
    foreignProject: { initData: launch(10001, 'foreign') }, foreignOwner: { cookie: 'x=y' }, ownerPreview: { cookie: previewCookie } };
  for (const patch of [ { baseUrl: 'https://production.example' }, { disposable: false },
    { actors: [credentials[0], credentials[0]] }, { collectionPath: '//production.example/api/tasks' },
    { collectionPath: '/api/../admin' }, { baseUrl: 'http://localhost:3000/path' } ]) {
    assert.throws(() => validateFixture({ ...good, ...patch }));
  }
});
