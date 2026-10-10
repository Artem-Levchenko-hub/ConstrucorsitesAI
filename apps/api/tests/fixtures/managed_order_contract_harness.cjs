// Run actual SDK/proxy source. Only framework/browser HTTP adapters are isolated.
const fs = require('node:fs'), vm = require('node:vm');
const { stripTypeScriptTypes } = require('node:module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const sent = [];
let source = stripTypeScriptTypes(fs.readFileSync(input.source, 'utf8'), { mode: 'transform' });
source = source.replace(/^import .*?;\n/gm, '').replace(/^export /gm, '');
const context = { Buffer, Date, URL, Request, Response, Headers, AbortSignal, TextEncoder,
  crypto: require('node:crypto').webcrypto, Map, Set,
  process: { env: { OMNIA_PROJECT_ID: input.project, MAX_BOT_TOKEN: 'test-max-token',
    OMNIA_PLATFORM_API_URL: 'https://yleum.ru' } },
  createHash: require('node:crypto').createHash, createHmac: require('node:crypto').createHmac,
  getMaxWebApp: () => ({ initData: 'synthetic-init-data' }),
  getMaxUser: async () => input.actor === null ? null : ({ id: input.actor || '42' }),
  NextResponse: class extends Response { static json(body, init) { return new this(JSON.stringify(body), init); } },
  fetch: async (url, options) => { sent.push({url, ...options});
    return new Response(JSON.stringify(input.result || {}), {status: input.status || 200,
      headers: {'content-type':'application/json'}}); },
};
vm.runInNewContext(source + `\nthis.api={${input.mode === 'sdk'
  ? 'getYleumOrders,getYleumOrderStatus,getYleumOrder,createYleumOrder' : 'POST'}};`, context);
(async () => {
  if(input.mode === 'sdk') {
    await context.api.getYleumOrders();
    await context.api.getYleumOrderStatus('qa-stable-order-intent');
    await context.api.getYleumOrder('00000000-0000-0000-0000-000000000009');
  } else {
    const response = await context.api.POST(new Request('https://app.test/api/omnia/integrations/'+input.operation,
      {method:'POST',body:JSON.stringify({payload:input.payload || {}})}),
      {params:Promise.resolve({path:[input.operation]})});
    console.log(JSON.stringify({sent,status:response.status}));return;
  }
  console.log(JSON.stringify({sent}));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
