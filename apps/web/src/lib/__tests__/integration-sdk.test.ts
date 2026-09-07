import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import { resolve } from 'node:path';
import ts from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';

const source = readFileSync(resolve(process.cwd(), '../orchestrator/templates/max-miniapp-nextjs/src/lib/omnia/integration-client.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function sdk(fetch: ReturnType<typeof vi.fn>, initData: string | null = 'signed') {
  const exports: Record<string, (...args: unknown[]) => Promise<unknown>> = {};
  new Function('exports', 'require', 'fetch', 'crypto', 'window', compiled)(exports,
    () => ({getMaxWebApp: () => ({initData})}), fetch, webcrypto, window);
  return exports;
}

describe('generated integration SDK', () => {
  afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); });
  it('retains the same lead operation key after a lost response and page reload', async () => {
    sessionStorage.clear();
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError('network response lost'))
      .mockResolvedValue({ok:true,json:async()=>({provider:'bitrix24',id:'123'})});
    const input = {name:'Test customer'};
    await expect(sdk(fetch).createOmniaLead(input)).rejects.toThrow();
    await sdk(fetch).createOmniaLead(input);
    const first = JSON.parse(fetch.mock.calls[0][1].body).payload.idempotency_key;
    const second = JSON.parse(fetch.mock.calls[1][1].body).payload.idempotency_key;
    expect(typeof first).toBe('string');
    expect(first.length).toBeGreaterThanOrEqual(16);
    expect(second).toBe(first);
    await sdk(fetch).createOmniaLead(input);
    expect(JSON.parse(fetch.mock.calls[2][1].body).payload.idempotency_key).not.toBe(first);
  });
  it('allows a deliberate same-payload retry after a confirmed provider rejection', async () => {
    sessionStorage.clear();
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok:false,status:422,json:async()=>({error:{code:'integration_request_rejected',message:'Request rejected'}})})
      .mockResolvedValueOnce({ok:true,json:async()=>({provider:'bitrix24',id:'124'})});
    const input = {name:'Retry customer'};
    const client = sdk(fetch);
    await expect(client.createOmniaLead(input)).rejects.toMatchObject({code:'integration_request_rejected'});
    expect(fetch).toHaveBeenCalledTimes(1);
    await client.createOmniaLead(input);
    const first = JSON.parse(fetch.mock.calls[0][1].body).payload.idempotency_key;
    const second = JSON.parse(fetch.mock.calls[1][1].body).payload.idempotency_key;
    expect(second).not.toBe(first);
    expect(sessionStorage.length).toBe(0);
  });
  it.each([
    [409, 'integration_operation_unknown'],
    [503, 'integration_provider_unavailable'],
    [500, 'integration_request_rejected'],
  ])('preserves the key after an ambiguous %s / %s response', async (status, code) => {
    sessionStorage.clear();
    const fetch = vi.fn()
      .mockResolvedValueOnce({ok:false,status,json:async()=>({error:{code,message:'Not confirmed'}})})
      .mockResolvedValueOnce({ok:true,json:async()=>({provider:'bitrix24',id:'125'})});
    const client = sdk(fetch);
    await expect(client.createOmniaLead({name:'Uncertain customer'})).rejects.toThrow();
    await client.createOmniaLead({name:'Uncertain customer'});
    expect(JSON.parse(fetch.mock.calls[1][1].body).payload.idempotency_key)
      .toBe(JSON.parse(fetch.mock.calls[0][1].body).payload.idempotency_key);
  });
  it('coalesces concurrent identical pending writes into one provider request', async () => {
    sessionStorage.clear();
    let finish!: (value: unknown) => void;
    const response = new Promise(resolve => { finish = resolve; });
    const fetch = vi.fn().mockReturnValue(response);
    const client = sdk(fetch);
    const first = client.createOmniaLead({name:'Concurrent customer'});
    const second = client.createOmniaLead({name:'Concurrent customer'});
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    finish({ok:true,json:async()=>({provider:'bitrix24',id:'126'})});
    expect(await Promise.all([first, second])).toEqual([
      {provider:'bitrix24',id:'126'}, {provider:'bitrix24',id:'126'},
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('does not dispatch when the operation key cannot be persisted', async () => {
    sessionStorage.clear();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    const fetch = vi.fn();
    await expect(sdk(fetch).createOmniaLead({name:'Blocked customer'})).rejects.toThrow('Storage blocked');
    expect(fetch).not.toHaveBeenCalled();
  });
  it('lets the trusted core authenticate a restored cookie-only session', async () => {
    const fetch = vi.fn().mockResolvedValue({ok:true,json:async()=>({providers:[]})});
    await expect(sdk(fetch, null).getOmniaIntegrations()).resolves.toEqual({providers:[]});
    expect(fetch.mock.calls[0][0]).toBe('/api/omnia/integrations/status');
  });
});


it('reads saved app data afresh without MAX launch data or integration access', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ok: true, json: async () => ({app_name: 'До', content: []})})
    .mockResolvedValueOnce({ok: true, json: async () => ({app_name: 'После', content: [{id:'tea', title:'Чай', active:true}]})});
  const client = sdk(fetch, null);
  expect(typeof client.getOmniaAppConfig).toBe('function');
  expect(await client.getOmniaAppConfig()).toMatchObject({app_name:'До'});
  expect(await client.getOmniaAppConfig()).toMatchObject({app_name:'После',content:[{id:'tea'}]});
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/omnia/config','/api/omnia/config']);
  expect(fetch.mock.calls[1][1]).toMatchObject({cache:'no-store',credentials:'include'});
});
