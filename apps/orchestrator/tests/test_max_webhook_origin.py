"""Execute the managed webhook: public app links cannot inherit a proxy host."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_ROUTE = _REPO / (
    "apps/orchestrator/templates/max-miniapp-nextjs/src/app/api/max/webhook/route.ts"
)
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (_REPO / "apps/web/node_modules/typescript").exists(),
    reason="Node.js and installed web TypeScript required for real webhook execution",
)
_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ts = require(input.typescript);
const output = ts.transpileModule(fs.readFileSync(input.route, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
}).outputText;
const sent = [];
let inserts = 0;
const db = {
  insert: () => ({values: () => ({onConflictDoNothing: () => ({
    returning: async () => {inserts++; return [{id: 'event-one'}];}
  })})}),
  delete: () => ({where: async () => {}}),
};
const modules = {
  'node:crypto': require('node:crypto'),
  'drizzle-orm': {eq: () => true},
  'next/server': {NextResponse: {
    json: (body, options) => ({body, status: options?.status || 200})
  }},
  '@/lib/db': {db, schema: {maxWebhookEvents: {eventKey: 'key'}}},
  '@/lib/max/bot-api': {
    sendMaxWelcome: async (user, origin) => sent.push({user, origin}),
    sendMaxHelp: async (user, origin) => sent.push({user, origin}),
  },
};
const context = {
  exports: {}, require: name => {
    if (!(name in modules)) throw new Error('Unexpected import: ' + name);
    return modules[name];
  },
  process: {env: {MAX_WEBHOOK_SECRET: 'test-webhook-secret', ...input.env}},
  Buffer, URL,
};
vm.runInNewContext(output, context);
const event = {update_type: input.event_type, user: {user_id: 123},
               message: {body: {text: '/start'}, sender: {user_id: 123}}};
const request = {
  headers: new Headers({'x-max-bot-api-secret': 'test-webhook-secret'}),
  nextUrl: new URL('http://untrusted-proxy.test/api/max/webhook'),
  text: async () => JSON.stringify(event),
};
context.exports.POST(request).then(result => {
  process.stdout.write(JSON.stringify({result, sent, inserts}));
}).catch(error => {console.error(error); process.exitCode = 1;});
"""


def run_webhook(env, event_type="bot_started"):
    result = subprocess.run(
        ["node", "-e", _HARNESS],
        input=json.dumps({
            "typescript": str(_REPO / "apps/web/node_modules/typescript"),
            "route": str(_ROUTE), "env": env, "event_type": event_type,
        }), text=True, capture_output=True, check=True, timeout=15,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("event_type", ["bot_started", "message_created"])
def test_public_webhook_uses_configured_https_origin(event_type):
    result = run_webhook({"OMNIA_PUBLIC_APP_ORIGIN": "https://app.example/"}, event_type)
    assert result["result"]["status"] == 200
    assert result["sent"] == [{"user": "123", "origin": "https://app.example"}]


@pytest.mark.parametrize("origin", [
    "http://app.example", "https://user:secret@app.example", "https://app.example/path",
    "https://app.example?host=evil.test", "https://app.example#fragment", "garbage",
])
def test_invalid_public_webhook_origin_fails_before_recording_or_sending(origin):
    result = run_webhook({"OMNIA_PUBLIC_APP_ORIGIN": origin})
    assert result["result"]["status"] == 503
    assert result["inserts"] == 0
    assert result["sent"] == []


def test_legacy_webhook_without_public_origin_preserves_existing_behavior():
    result = run_webhook({})
    assert result["result"]["status"] == 200
    assert result["sent"] == [{"user": "123", "origin": "http://untrusted-proxy.test"}]
