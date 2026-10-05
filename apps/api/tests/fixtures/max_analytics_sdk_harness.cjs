// Execute the actual exported SDK function; only browser transport is isolated.
const fs = require('node:fs'), vm = require('node:vm');
const { stripTypeScriptTypes } = require('node:module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const text = fs.readFileSync(input.client, 'utf8');
const begin = text.indexOf('export async function trackMaxEvent(');
const end = text.indexOf('\nexport async function saveMaxConsent', begin);
if (begin < 0 || end < 0) throw new Error('SDK function boundary missing');
const calls = [], timeouts = [], cleared = [];
let attempt = 0;
const context = {
  crypto: require('node:crypto').webcrypto, AbortController,
  setTimeout: (fn, delay) => {
    timeouts.push(delay);
    return setTimeout(fn, input.mode === 'timeout' ? 5 : delay);
  },
  clearTimeout: timer => { cleared.push(true); clearTimeout(timer); },
  fetch: async (url, options) => {
    calls.push({url, method: options.method, credentials: options.credentials,
      headers: options.headers, body: options.body});
    const outcome = (input.outcomes || [204])[attempt++] ?? 204;
    if (input.mutateProperties && attempt === 1) input.properties.flow = "changed-after-dispatch";
    if (input.mode === 'timeout') return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('SYNTHETIC_PRIVATE_TIMEOUT')), {once: true});
    });
    if (outcome === 'network') throw new Error('SYNTHETIC_PRIVATE_NETWORK_SECRET');
    return new Response(null, {status: outcome});
  },
};
context.post = (url, payload) => context.fetch(url, {method:'POST', credentials:'include',
  headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
const source = stripTypeScriptTypes(text.slice(begin, end)).replace('export async function', 'async function');
vm.runInNewContext(source + '\nthis.trackMaxEvent = trackMaxEvent;', context);
context.trackMaxEvent(input.name || 'checkout', input.properties || {}, input.options || {})
  .then(() => console.log(JSON.stringify({calls,timeouts,cleared,error:null})))
  .catch(error => console.log(JSON.stringify({calls,timeouts,cleared,error:error.message})));
