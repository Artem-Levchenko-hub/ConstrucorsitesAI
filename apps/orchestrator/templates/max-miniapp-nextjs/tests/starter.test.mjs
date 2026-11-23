import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const starterRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);

test("MAX starter package satisfies portable machine contract", async () => {
  const packageJson = JSON.parse(await readFile(resolve(starterRoot, "package.json"), "utf8"));

  assert.equal(packageJson.private, true);
  assert.equal(packageJson.engines.node, ">=22");
  assert.equal(packageJson.packageManager, "pnpm@9.15.0");
  assert.equal(packageJson.scripts.build, "next build");
  assert.equal(packageJson.scripts.start, "next start --port 3000 --hostname 0.0.0.0");
  assert.equal(packageJson.scripts.test, "node --test tests/*.test.mjs");
});

test("activation action cleanup removes only a user created by that fixture", async () => {
  const createRoute = await readFile(
    resolve(starterRoot, "src/app/api/omnia/actions/route.ts"),
    "utf8",
  );
  const deleteRoute = await readFile(
    resolve(starterRoot, "src/app/api/omnia/actions/[id]/route.ts"),
    "utf8",
  );

  assert.match(createRoute, /probeUserCreated: createdUsers\.length === 1/);
  assert.match(deleteRoute, /audit\?\.details\?\.probeUserCreated === true/);
  assert.match(createRoute, /healthProbe: input\.actionType\.startsWith\("omnia_health_"\)/);
  assert.match(deleteRoute, /audit\?\.details\?\.healthProbe === true/);
  assert.doesNotMatch(deleteRoute, /action\.actionType\.startsWith\("omnia_health_"\)/);
  assert.match(deleteRoute, /NOT EXISTS \(/);
  assert.ok(
    deleteRoute.indexOf("delete(schema.maxAuditLog)") <
      deleteRoute.indexOf("delete(schema.maxUsers)"),
  );
});

test("managed AI SDK forwards explicit provider options and preserves existing requests", async (t) => {
  const ts = require("typescript");
  const code = await readFile(resolve(starterRoot, "src/lib/omnia/integration-client.ts"), "utf8");
  const { outputText } = ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const dependencies = {
    "@/lib/max/bridge": { getMaxWebApp: () => ({ initData: "test-viewer" }) },
    "@/lib/omnia/identity-scope": {},
  };
  const sdk = { exports: {} };
  new Function("require", "exports", outputText)(name => dependencies[name], sdk.exports);
  const payloads = [];
  t.mock.method(globalThis, "fetch", async (url, request) => {
    assert.equal(url, "/api/omnia/integrations/ai");
    const { initData, payload } = JSON.parse(request.body);
    assert.equal(initData, "test-viewer");
    payloads.push(payload);
    return Response.json({ model: "test-model", answer: "Готово" });
  });

  const result = await sdk.exports.requestOmniaAI({
    message: "Составь рацион",
    instructions: "Верни меню",
    provider: "aitunnel",
    jsonMode: false,
    idempotencyKey: "meal-plan-1",
  });
  assert.deepEqual(result, { model: "test-model", answer: "Готово", text: "Готово" });
  assert.deepEqual(payloads[0], {
    message: "Составь рацион", instructions: "Верни меню", provider: "aitunnel",
    json_mode: false, idempotency_key: "meal-plan-1",
  });

  await sdk.exports.requestOmniaAI({ prompt: "Гречка", context: { task: "nutrition_estimate", portion_g: 150 } });
  assert.deepEqual(payloads[1], { message: "Гречка", context: { task: "nutrition_estimate", portion_g: 150 } });
});
