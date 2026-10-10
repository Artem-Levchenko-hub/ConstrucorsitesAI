// Actual managed SDK/proxy; isolate only framework and browser HTTP adapters.
const fs = require('node:fs'), vm = require('node:vm');
const { stripTypeScriptTypes } = require('node:module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const sent = [];
let source = stripTypeScriptTypes(fs.readFileSync(input.source, 'utf8'), { mode: 'transform' });
source = source.replace(/^import .*?;\n/gm, '').replace(/^export /gm, '');
const context = {
  Buffer, Date, URL, Request, Response, Headers, AbortSignal, TextEncoder, Map, Set,
  crypto: require('node:crypto').webcrypto,
  process: { env: { OMNIA_PROJECT_ID: input.project, MAX_BOT_TOKEN: 'synthetic-max-token',
    OMNIA_PLATFORM_API_URL: 'https://yleum.ru' } },
  createHash: require('node:crypto').createHash, createHmac: require('node:crypto').createHmac,
  getMaxWebApp: () => ({ initData: input.initData || '' }),
  getMaxUser: async () => input.actor == null ? null : ({ id: input.actor }),
  window: { sessionStorage: {
    getItem() { throw new Error('Receipt lookup cannot read browser write state'); },
    setItem() { throw new Error('Receipt lookup cannot persist a new intent'); },
    removeItem() { throw new Error('Receipt lookup cannot forget an old intent'); },
  } },
  NextResponse: class extends Response {
    static json(body, init) { return new this(JSON.stringify(body), init); }
  },
  fetch: async (url, options) => {
    sent.push({ url, ...options });
    return new Response(JSON.stringify(input.result || {}), {
      status: input.status || 200, headers: { 'content-type': 'application/json' },
    });
  },
};
vm.runInNewContext(source + `\nthis.api={${input.mode === 'sdk' ? 'getYleumLeadReceipt' : 'POST'}};`, context);
(async () => {
  if (input.mode === 'sdk') {
    try {
      const value = await context.api.getYleumLeadReceipt(input.key, input.expectations ?? undefined);
      console.log(JSON.stringify({ sent, value }));
    } catch (error) {
      console.log(JSON.stringify({ sent, error: { name: error.name, message: error.message,
        code: error.code, status: error.status } }));
    }
    return;
  }
  const response = await context.api.POST(new Request(
    'https://app.test/api/omnia/integrations/' + (input.operation || 'lead-receipt'), {
      method: 'POST', body: JSON.stringify({ initData: input.initData || '', payload: input.payload }),
    }), { params: Promise.resolve({ path: [input.operation || 'lead-receipt'] }) });
  console.log(JSON.stringify({ sent, status: response.status, result: await response.json() }));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
