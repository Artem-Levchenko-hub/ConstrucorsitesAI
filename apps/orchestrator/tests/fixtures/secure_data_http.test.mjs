import assert from 'node:assert/strict';
import test from 'node:test';
import { createSecureDataHandler } from '../../templates/max-miniapp-nextjs/src/lib/secure-data/http.ts';

const url = 'https://app.example/api/omnia/data/notes';
const actor = async () => ({ id: 'trusted-user' });
const failStore = () => { throw new Error('storage must not be opened'); };

test('unauthenticated request is rejected before body or storage access', async () => {
  const handler = createSecureDataHandler(async () => null, failStore);
  const response = await handler(new Request(url, { method: 'POST', body: 'broken' }), ['notes']);
  assert.equal(response.status, 401);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});

test('cross-origin mutation and oversize body fail before database access', async () => {
  const handler = createSecureDataHandler(actor, failStore);
  const response = await handler(new Request(url, {
    method: 'POST', headers: { origin: 'https://evil.example', 'content-type': 'application/json' }, body: '{}',
  }), ['notes']);
  assert.equal(response.status, 403);
  const oversized = await handler(new Request(url, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({payload:{note:'a'.repeat(262145)}}),
  }), ['notes']);
  assert.equal(oversized.status, 413);
});

test('identity cannot be supplied through mutation body', async () => {
  const handler = createSecureDataHandler(actor, failStore);
  const response = await handler(new Request(url, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({payload:{},ownerId:'victim'}),
  }), ['notes']);
  assert.equal(response.status, 400);
});

test('unsafe paths, unsupported verbs and invalid revisions fail closed', async () => {
  const handler = createSecureDataHandler(actor, failStore);
  assert.equal((await handler(new Request(url), ['..'])).status, 400);
  assert.equal((await handler(new Request(url, {method:'PATCH'}), ['notes','not-a-uuid'])).status, 400);
  const id = '7f00f184-67e8-4eab-8c36-4741461f16cd';
  assert.equal((await handler(new Request(url, {method:'POST'}), ['notes',id])).status, 405);
  const response = await handler(new Request(url, {
    method:'PUT', headers:{'content-type':'application/json'}, body:JSON.stringify({payload:{},revision:0}),
  }), ['notes',id]);
  assert.equal(response.status,400);
});

test('provider errors never expose credentials, SQL, key paths or payloads', async () => {
  const handler = createSecureDataHandler(actor, () => { throw new Error('credential-sensitive-message'); });
  const response = await handler(new Request(url), ['notes']);
  assert.equal(response.status,503);
  assert.equal((await response.text()).includes('credential-sensitive-message'),false);
});

test('database errors are generic and revision conflicts have a stable status', async () => {
  const handler = createSecureDataHandler(actor, () => ({
    list: async () => { throw Object.assign(new Error('private SQL'),{code:'REVISION_CONFLICT'}); },
  }));
  const response = await handler(new Request(url), ['notes']);
  assert.equal(response.status,409);
  assert.deepEqual(await response.json(), {error:'REVISION_CONFLICT'});
});

test('trusted gateway external origin permits browser write despite internal core Host', async () => {
  const handler = createSecureDataHandler(actor, () => ({
    create: async () => ({ id: 'created' }),
  }), { trustedGateway: true });
  const request = new Request('http://10.20.0.4:3000/api/omnia/data/notes', {
    method: 'POST', headers: {host:'10.20.0.4:3000', origin:'https://app.example',
      'x-omnia-request-origin':'https://app.example', 'content-type':'application/json'}, body:'{"payload":{}}',
  });
  const response = await handler(request, ['notes']);
  assert.equal(response.status,201);
  assert.deepEqual(await response.json(),{record:{id:'created'}});
});

test('trusted gateway mutations require matching origin even for private previews', async () => {
  const handler = createSecureDataHandler(actor, failStore, {trustedGateway:true});
  for (const origin of [undefined, 'https://evil.example']) {
    const headers = {'x-omnia-request-origin':'https://app.example','content-type':'application/json'};
    if (origin) headers.origin = origin;
    assert.equal((await handler(new Request(url,{method:'POST',headers,body:'{"payload":{}}'}),['notes'])).status,403);
  }
});
