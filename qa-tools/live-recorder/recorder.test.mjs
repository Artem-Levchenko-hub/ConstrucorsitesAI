import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const implementation = await import('./recorder.mjs').catch(e => {
  if (e.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw e;
});

test('redacts an unlabelled provider authorization code without hiding ordinary evidence', () => {
  const authorizationCode = 'def50200' + '0123456789abcdef'.repeat(48);
  const safe = implementation.redact(`paragraph: ${authorizationCode}\nПереговоры\nsha256: ${'a'.repeat(64)}`);
  assert.ok(!safe.includes(authorizationCode));
  assert.ok(safe.includes('Переговоры'));
  assert.ok(safe.includes('a'.repeat(64)), 'evidence hashes must remain inspectable');
});

test('persists observations without exposing credentials or granting automatic PASS', async () => {
  assert.equal(typeof implementation.createRecorder, 'function', 'recorder is not implemented');
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'yleum-recorder-test-'));
  try {
    const recorder = await implementation.createRecorder({publicDir:path.join(root,'public'), privateDir:path.join(root,'private'), secrets:['super-secret-password'], catalog:[{id:'Т01.3',expected:'Two owners'}]});
    recorder.scenario('Т01.3');
    await recorder.record('fill', {url:'https://yleum.ru/max/33a91138-9e8f-49a0-9add-afb3c362b234?token=private-token', dom:'user@example.com super-secret-password', logs:[{message:'Bearer abc.def.ghi'}], screenshot:Buffer.from('fixture')});
    const journal = await fs.readFile(path.join(root,'public','events.jsonl'),'utf8');
    assert.ok(!journal.includes('super-secret-password'));
    assert.ok(!journal.includes('user@example.com'));
    assert.ok(!journal.includes('private-token'));
    assert.ok(!journal.includes('33a91138-9e8f-49a0-9add-afb3c362b234'));
    const status = JSON.parse(await fs.readFile(path.join(root,'public','status.json'),'utf8'));
    assert.equal(status.scenarios['Т01.3'].status,'IN_PROGRESS');
    const event = JSON.parse(journal.trim());
    assert.equal(event.scenario,'Т01.3');
    assert.equal(event.captureComplete,true);
    assert.equal((await fs.readFile(path.join(root,'private',event.rawFile),'utf8')).includes('super-secret-password'),true);
    assert.equal((await fs.readFile(path.join(root,'private',event.screenshotFile))).toString(),'fixture');
  } finally { await fs.rm(root,{recursive:true,force:true}); }
});

test('records failed actions and incomplete captures without marking them successful', async () => {
  assert.equal(typeof implementation.createRecorder,'function','recorder is not implemented');
  const root = await fs.mkdtemp(path.join(os.tmpdir(),'yleum-recorder-test-'));
  try {
    const recorder = await implementation.createRecorder({publicDir:path.join(root,'public'),privateDir:path.join(root,'private'),catalog:[{id:'Т04.1'}]});
    recorder.scenario('Т04.1');
    await recorder.record('click',{error:'click failed', captureErrors:['screenshot unavailable']});
    const event = JSON.parse((await fs.readFile(path.join(root,'public','events.jsonl'),'utf8')).trim());
    assert.equal(event.actionSucceeded,false);
    assert.equal(event.captureComplete,false);
    await assert.rejects(recorder.finish('PASS',''),/evidence/i);
    await recorder.finish('PARTIAL','UI reached; screenshot unavailable');
    const status = JSON.parse(await fs.readFile(path.join(root,'public','status.json'),'utf8'));
    assert.equal(status.scenarios['Т04.1'].status,'PARTIAL');
    assert.throws(()=>recorder.scenario('Т99.99'),/Unknown/);
  } finally { await fs.rm(root,{recursive:true,force:true}); }
});
