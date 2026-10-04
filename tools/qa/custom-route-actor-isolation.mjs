// Read-only custom-route acceptance on an explicitly disposable loopback app.
// No login POST, SQL, provider calls, token minting or production targets.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const MAX_BYTES = 262144;
const DENIALS = new Set([401, 403, 404]);
function credential(value) {
  assert.ok(value && typeof value === 'object');
  const fields = ['initData', 'cookie'].filter(key => typeof value[key] === 'string' && value[key]);
  assert.equal(fields.length, 1);
  assert.ok(value[fields[0]].length <= 16384 && !/[\r\n]/.test(value[fields[0]]));
}
function route(path) {
  assert.ok(typeof path === 'string' && /^\/api\/[a-zA-Z0-9_/-]+$/.test(path));
  assert.ok(!path.includes('//') && !path.includes('..'));
}
export function validateFixture(fixture) {
  assert.equal(fixture.schemaVersion, 1);
  assert.equal(fixture.disposable, true);
  const url = new URL(fixture.baseUrl);
  assert.equal(url.protocol, 'http:');
  assert.ok(['127.0.0.1', '[::1]'].includes(url.hostname));
  assert.ok(url.port && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/');
  assert.ok(fixture.identityMode === undefined || fixture.identityMode === 'cell');
  if (fixture.identityMode === 'cell') assert.ok(typeof fixture.projectId === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(fixture.projectId));
  route(fixture.collectionPath);
  assert.ok(Array.isArray(fixture.actors) && fixture.actors.length === 2);
  for (const actor of fixture.actors) {
    assert.ok(typeof actor.id === 'string' && /^[1-9][0-9]{0,19}$/.test(actor.id));
    credential(actor);
    route(actor.itemPath);
    assert.ok(Array.isArray(actor.protectedValues) && actor.protectedValues.length >= 2);
    assert.ok(actor.protectedValues.every(value => typeof value === 'string' && value.length >= 8 && value.length <= 256));
    assert.equal(new Set(actor.protectedValues).size, actor.protectedValues.length);
  }
  assert.notEqual(fixture.actors[0].id, fixture.actors[1].id);
  assert.notEqual(fixture.actors[0].itemPath, fixture.actors[1].itemPath);
  assert.ok(fixture.actors[0].protectedValues.every(value => !fixture.actors[1].protectedValues.includes(value)));
  credential(fixture.foreignProject);
  credential(fixture.foreignOwner);
  assert.ok(fixture.foreignOwner.cookie && !fixture.foreignOwner.initData);
  credential(fixture.ownerPreview);
  assert.ok(fixture.ownerPreview.cookie && !fixture.ownerPreview.initData);
  for (const actor of fixture.actors) {
    if (fixture.foreignProject.initData) assert.notEqual(fixture.foreignProject.initData, actor.initData);
    if (fixture.foreignProject.cookie) assert.notEqual(fixture.foreignProject.cookie, actor.cookie);
  }
  assert.notEqual(fixture.foreignOwner.cookie, fixture.ownerPreview.cookie);
}
export async function probeActorIsolation(fixture) {
  validateFixture(fixture);
  const checks = [];
  let requests = 0;
  const allProtected = fixture.actors.flatMap(actor => actor.protectedValues);
  const identityPath = fixture.identityMode === 'cell' ? '/__omnia/identity' : '/api/max/session';
  async function get(path, auth) {
    if (path !== '/__omnia/identity') route(path);
    requests++;
    const response = await fetch(new URL(path, fixture.baseUrl), {
      method: 'GET', redirect: 'manual', signal: AbortSignal.timeout(5000),
      headers: auth?.initData ? { 'x-omnia-max-init-data': auth.initData }
        : auth?.cookie ? { Cookie: auth.cookie } : {},
    });
    assert.ok(response.status < 300 || response.status >= 400, 'redirect refused');
    const reader = response.body?.getReader();
    const chunks = []; let bytes = 0;
    if (reader) {
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.length;
          assert.ok(bytes <= MAX_BYTES, 'response bound exceeded');
          chunks.push(value);
        }
      } finally { await reader.cancel(); }
    }
    const text = Buffer.concat(chunks).toString('utf8');
    let data;
    try { data = JSON.parse(text); } catch { data = null; }
    return { status: response.status, text, data };
  }
  function excludes(response, values) {
    // Decode JSON so escaped values cannot pass a raw-body substring check.
    const body = response.data === null ? response.text : JSON.stringify(response.data);
    for (const value of values) assert.ok(!body.includes(value), 'protected value disclosed');
  }
  function denied(response) {
    assert.ok(DENIALS.has(response.status), 'explicit denial required');
    excludes(response, allProtected);
  }
  function owns(response, values) {
    assert.equal(response.status, 200);
    assert.notEqual(response.data, null, 'JSON success required');
    const body = JSON.stringify(response.data);
    for (const value of values) assert.ok(body.includes(value), 'own fixture absent');
  }
  for (const actor of fixture.actors) {
    const identity = await get(identityPath, actor);
    assert.equal(identity.status, 200);
    if (fixture.identityMode === 'cell') {
      assert.equal(identity.data?.user_id, actor.id);
      assert.equal(identity.data?.project_id, fixture.projectId);
      assert.ok(Number.isSafeInteger(identity.data?.epoch) && identity.data.epoch >= 0);
    } else assert.equal(identity.data?.user?.id, actor.id);
    assert.notEqual(identity.data?.mode, 'preview');
  }
  checks.push('signed_numeric_U_V_identity');
  for (const auth of [undefined, fixture.foreignProject, fixture.foreignOwner]) {
    denied(await get(identityPath, auth));
    denied(await get(fixture.collectionPath, auth));
    for (const actor of fixture.actors) denied(await get(actor.itemPath, auth));
  }
  checks.push('anonymous_foreign_project_foreign_owner_denied_without_fixture_disclosure');
  const previewIdentity = await get(identityPath, fixture.ownerPreview);
  if (previewIdentity.status === 200) {
    assert.notEqual(fixture.identityMode, 'cell', 'public Cell owner preview must be denied');
    assert.equal(previewIdentity.data?.user?.id, 'preview');
    assert.equal(previewIdentity.data?.mode, 'preview');
  } else denied(previewIdentity);
  const previewList = await get(fixture.collectionPath, fixture.ownerPreview);
  if (previewList.status === 200) excludes(previewList, allProtected);
  else denied(previewList);
  for (const actor of fixture.actors) denied(await get(actor.itemPath, fixture.ownerPreview));
  checks.push('owner_preview_excluded_from_signed_actor_proof');
  for (let iteration = 0; iteration < 3; iteration++) {
    // Concurrent alternating actor reads exercise the real app's request/pool boundary.
    const lists = await Promise.all(fixture.actors.map(actor => get(fixture.collectionPath, actor)));
    for (let index = 0; index < fixture.actors.length; index++) {
      const actor = fixture.actors[index], other = fixture.actors[1 - index];
      owns(lists[index], actor.protectedValues);
      excludes(lists[index], other.protectedValues);
      const own = await get(actor.itemPath, actor);
      owns(own, actor.protectedValues); excludes(own, other.protectedValues);
      denied(await get(other.itemPath, actor));
    }
  }
  checks.push('same_project_U_V_collection_item_cross_actor_denial_concurrent_reads');
  return { passed: true, scope: 'disposable_custom_route_read_only', checks, requests,
    writes: 0, providerCalls: 0,
    limitations: ['GET_only_no_write_authorization_proof', 'no_deployed_Cell_promotion_evidence',
      'database_least_privilege_requires_separate_runtime_proof'] };
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    assert.equal(process.argv.length, 3);
    const raw = readFileSync(process.argv[2]);
    assert.ok(raw.length <= 65536);
    console.log(JSON.stringify(await probeActorIsolation(JSON.parse(raw.toString('utf8')))));
  } catch {
    // Do not print assertion/HTTP errors: they may contain private fixture data.
    console.log(JSON.stringify({ passed: false, scope: 'disposable_custom_route_read_only', reason: 'fixture_or_isolation_check_failed' }));
    process.exitCode = 1;
  }
}
