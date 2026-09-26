/* Run after prepare.mjs in the trusted build tree, before next build. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { createHmac } = require('node:crypto');
const vm = require('node:vm');
const ts = require('typescript');

const source = readFileSync('src/app/api/omnia/preview-session/route.ts', 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const secret = 'disposable-unit-test-secret';
const project = '11111111-1111-4111-8111-111111111111';

async function bootstrap(env, { expired = false, badSignature = false, wrongProject = false,
  resume = false, corruptCookie = false, resumeEnv = {} } = {}) {
  let writes = 0, cookie, cookieSets = 0;
  class NextResponse extends Response {
    constructor(body, options) {
      super(body, options);
      this.cookies = { set(_name, value) { cookie = value; cookieSets++; } };
    }
    static json(body, options) { return new NextResponse(JSON.stringify(body), options); }
  }
  const db = { insert() { writes++; return { values() {
    return { onConflictDoNothing: async () => {} };
  } }; } };
  const modules = {
    'node:crypto': require('node:crypto'),
    'next/server': { NextResponse },
    'next/headers': {
      cookies: async () => ({ get: () => cookie ? { value: cookie } : undefined }),
      headers: async () => new Headers(),
    },
    '@/lib/db': { db, withMaxUser: async (_id, run) => run(db),
      schema: { maxUsers: { maxUserId: 'id' } } },
  };
  function load(name) {
    if (name in modules) return modules[name];
    assert.ok(name.startsWith('@/'), `Unexpected import ${name}`);
    const moduleContext = { ...context, exports: {} };
    vm.runInNewContext(ts.transpileModule(readFileSync(`src/${name.slice(2)}.ts`, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, moduleContext);
    return modules[name] = moduleContext.exports;
  }
  const context = { exports: {}, Buffer, URL, process: { env: {
    NODE_ENV: 'production', AUTH_SECRET: secret, OMNIA_PROJECT_ID: project, ...env,
  } }, require: load };
  vm.runInNewContext(code, context);
  const expires = String(Math.floor(Date.now() / 1000) + (expired ? -5 : 60));
  const signature = badSignature ? 'invalid' : createHmac('sha256', secret)
    .update(`omnia:max-preview-session:v1\n${wrongProject ? 'other-project' : project}\n${expires}`)
    .digest('base64url');
  const result = await context.exports.GET(new Request(
    `https://preview.example.test/api/omnia/preview-session?expires=${expires}&signature=${signature}`,
  ));
  const resultSummary = { status: result.status, location: result.headers.get('Location'), writes };
  if (!resume) return resultSummary;
  if (corruptCookie) cookie += 'bad';
  Object.assign(context.process.env, resumeEnv);
  const resumed = await load('@/app/api/max/session/route').GET();
  return { ...resultSummary, resumeStatus: resumed.status, body: await resumed.json(),
    writesAfterResume: writes, cookieSets };
}

test('compiled signed bootstrap resumes the same preview identity without writes or renewal', async () => {
  assert.deepEqual(await bootstrap({ OMNIA_OWNER_PREVIEW: '1' }, { resume: true }), {
    status: 307, location: '/', writes: 1, resumeStatus: 200,
    body: { user: { id: 'preview' }, mode: 'preview' }, writesAfterResume: 1, cookieSets: 1,
  });
});
test('compiled owner resume rejects a corrupted bootstrap cookie', async () => {
  const result = await bootstrap({ OMNIA_OWNER_PREVIEW: '1' }, { resume: true, corruptCookie: true });
  assert.equal(result.resumeStatus, 401);
  assert.equal(result.writesAfterResume, 1);
  assert.equal(result.cookieSets, 1);
});
for (const resumeEnv of [{ OMNIA_OWNER_PREVIEW: '' }, { OMNIA_PUBLIC_APP_ORIGIN: 'https://public.example.test' }]) {
  test(`compiled public resume rejects even a signed preview cookie ${JSON.stringify(resumeEnv)}`, async () => {
    const result = await bootstrap({ OMNIA_OWNER_PREVIEW: '1' }, { resume: true, resumeEnv });
    assert.equal(result.resumeStatus, 401);
    assert.equal(result.writesAfterResume, 1);
    assert.equal(result.cookieSets, 1);
  });
}

test('compiled owner preview accepts a valid short-lived project signature', async () => {
  assert.deepEqual(await bootstrap({ OMNIA_OWNER_PREVIEW: '1' }), {
    status: 307, location: '/', writes: 1,
  });
});
for (const invalid of [{ expired: true }, { badSignature: true }, { wrongProject: true }]) {
  test(`compiled owner preview rejects ${Object.keys(invalid)[0]}`, async () => {
    assert.deepEqual(await bootstrap({ OMNIA_OWNER_PREVIEW: '1' }, invalid), {
      status: 404, location: null, writes: 0,
    });
  });
}
for (const env of [{}, { OMNIA_OWNER_PREVIEW: '1', OMNIA_PUBLIC_APP_ORIGIN: 'https://public.example.test' }]) {
  test(`public core refuses owner bootstrap ${JSON.stringify(env)}`, async () => {
    assert.deepEqual(await bootstrap(env), { status: 404, location: null, writes: 0 });
  });
}

test('only an unconfigured private preview can use initial metadata', () => {
  const configCode = ts.transpileModule(readFileSync('src/lib/omnia/runtime-config.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const initial = { app_name: 'Initial trusted config' };
  function load(env, file) {
    const modules = {
      'node:fs': { existsSync: () => file !== undefined, readFileSync() {
        if (file === undefined) throw new Error('missing');
        return file;
      } },
      './max-config': { omniaMaxConfig: initial },
    };
    const context = { exports: {}, process: { env }, require: name => modules[name] };
    vm.runInNewContext(configCode, context);
    return context.exports.getMaxConfig();
  }
  assert.equal(load({ OMNIA_OWNER_PREVIEW: '1' }), initial);
  assert.throws(() => load({}), /missing/);
  assert.throws(() => load({ OMNIA_OWNER_PREVIEW: '1', OMNIA_PUBLIC_APP_ORIGIN: 'https://public.example.test' }), /missing/);
  assert.equal(load({ OMNIA_OWNER_PREVIEW: '1' }, '{"app_name":"Saved"}').app_name, 'Saved');
  assert.throws(() => load({ OMNIA_OWNER_PREVIEW: '1' }, 'corrupted'));
});
