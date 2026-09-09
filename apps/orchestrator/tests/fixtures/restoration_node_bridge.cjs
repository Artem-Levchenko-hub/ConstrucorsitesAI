'use strict';
// Runs only against explicit disposable NODE_BRIDGE_DB_URL. No application data.
const assert = require('node:assert/strict');
const http = require('node:http');
const crypto = require('node:crypto');
const pg = require(process.env.NODE_BRIDGE_PG_MODULE || 'pg');
const connectionString = process.env.NODE_BRIDGE_DB_URL;
if (!connectionString) throw new Error('Disposable PostgreSQL connection required');
const pool = new pg.Pool({ connectionString, max: 1 });
const shared = new pg.Client({ connectionString });
const token = user => {
  const payload = Buffer.from(JSON.stringify({ user_id: user })).toString('hex');
  return payload + '.' + crypto.createHmac('sha256', 'fixture-only').update(payload).digest('hex');
};
const one = token('one'), two = token('two');
const query = "SELECT current_setting('omnia.actor_token', true) AS token";
let checks = 0;
const callbackQuery = (client, config, values) => new Promise((resolve, reject) =>
  client.query(config, values, (error, result) => error ? reject(error) : resolve(result)));
const server = http.createServer(async (req, res) => {
  try {
    let result;
    if (req.url === '/pool') result = await pool.query(query);
    else if (req.url === '/pool-callback') result = await callbackQuery(pool, query);
    else if (req.url === '/shared') result = await shared.query(
      "SELECT pg_sleep(0.02), current_setting('omnia.actor_token', true) AS token");
    else if (req.url === '/begin') { await shared.query('BEGIN'); result = await shared.query(query); }
    else if (req.url === '/rollback') { await shared.query('ROLLBACK'); result = await shared.query(query); }
    else if (req.url === '/error') {
      await shared.query('BEGIN');
      await assert.rejects(shared.query('SELECT 1/0'));
      await shared.query('ROLLBACK');
      result = await shared.query(query);
    } else if (req.url === '/client-callback') {
      const client = new pg.Client({ connectionString });
      await new Promise((resolve, reject) => client.connect(error => error ? reject(error) : resolve()));
      try { result = await callbackQuery(client, { text: query, rowMode: 'array' }); }
      finally { await client.end(); }
      result.rows = [{ token: result.rows[0][0] }];
    } else if (req.url === '/config-callback') {
      result = await new Promise((resolve, reject) => shared.query({ text: query,
        callback: (error, value) => error ? reject(error) : resolve(value) }));
    } else if (req.url === '/transaction') {
      const client = await pool.connect();
      try {
        await client.query('BEGIN'); result = await client.query(query); await client.query('COMMIT');
      } finally { client.release(); }
    } else if (req.url === '/unsupported') {
      await assert.rejects(shared.query(new pg.Query(query)), /Streaming/);
      result = { rows: [{ token: 'rejected' }] };
    } else throw new Error('Unknown fixture path');
    res.end(JSON.stringify({ token: result.rows[0].token }));
  } catch (error) { res.statusCode = 409; res.end(JSON.stringify({ error: error.message })); }
});

async function main() {
  await shared.connect();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = 'http://127.0.0.1:' + server.address().port;
  async function get(path, credential) {
    const headers = credential ? { 'X-Omnia-Data-Token': credential } : {};
    const response = await fetch(url + path, { headers });
    return { status: response.status, ...await response.json() };
  }
  for (const path of ['/pool', '/pool-callback', '/client-callback', '/config-callback', '/transaction']) {
    assert.deepEqual(await get(path, one), { status: 200, token: one }); checks++;
  }
  const concurrent = await Promise.all(Array.from({ length: 12 }, (_, index) => get('/shared', index % 2 ? two : one)));
  concurrent.forEach((result, index) => assert.deepEqual(result, { status: 200, token: index % 2 ? two : one })); checks++;
  const pooled = await Promise.all(Array.from({ length: 12 }, (_, index) => get('/pool-callback', index % 2 ? two : one)));
  pooled.forEach((result, index) => assert.deepEqual(result, { status: 200, token: index % 2 ? two : one })); checks++;
  assert.deepEqual(await get('/pool'), { status: 200, token: '' }); checks++;
  assert.deepEqual(await get('/error', one), { status: 200, token: one }); checks++;
  assert.deepEqual(await get('/begin', one), { status: 200, token: one });
  assert.equal((await get('/shared', two)).status, 409);
  assert.deepEqual(await get('/rollback', one), { status: 200, token: one }); checks++;
  assert.deepEqual(await get('/unsupported', one), { status: 200, token: 'rejected' }); checks++;
  assert.deepEqual(await shared.query("SELECT current_setting('omnia.actor_token', true) AS token").then(r => r.rows[0]), { token: '' }); checks++;
  console.log(JSON.stringify({ passed: checks }));
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  server.closeAllConnections(); server.close(); await shared.end(); await pool.end();
});
