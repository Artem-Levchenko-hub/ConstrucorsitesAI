import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, rename, rmdir, unlink, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "package.json"));
const ts = require("typescript");
function load(relative, dependencies = {}) {
  const filename = join(root, relative);
  const code = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    name => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name), module, module.exports,
  );
  return module.exports;
}
const config = load("src/lib/omnia/max-config.ts").omniaMaxConfig;
const item = { id: "cocoa", title: "Какао", category: "Напитки", description: "", price: "260 ₽", availability: "in_stock", options: [], image_url: "", action_label: "Заказать", active: true };
const envelope = (version = 1, content = [item]) => ({ config_version: version, config: { ...config, content } });

test("API-valid Unicode limits stay valid in the app catalog", () => fixture(async filename => {
  const product = { ...item, title: "🍰".repeat(120), category: "📦".repeat(80),
    description: "🙂".repeat(600), price: "💰".repeat(80),
    options: ["🔵".repeat(40)], action_label: "👉".repeat(40) };
  await writeFile(filename, JSON.stringify(envelope(1, [product])));
  const { getYleumServerAppConfig } = load("src/lib/omnia/catalog-server.ts");
  assert.deepEqual((await getYleumServerAppConfig()).config.content[0], product);
  await writeFile(filename, JSON.stringify(envelope(2, [{ ...product, title: "🍰".repeat(121) }])));
  await assert.rejects(getYleumServerAppConfig(), { code: "catalog_unavailable" });
}));
async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), "yleum-catalog-sdk-"));
  const filename = join(directory, "business.json");
  const previous = process.env.YLEUM_APP_CONFIG_PATH;
  process.env.YLEUM_APP_CONFIG_PATH = filename;
  try { await run(filename, directory); }
  finally {
    if (previous === undefined) delete process.env.YLEUM_APP_CONFIG_PATH;
    else process.env.YLEUM_APP_CONFIG_PATH = previous;
    for (const name of ["business.json", "next.json"]) {
      await unlink(join(directory, name)).catch(error => { if (error.code !== "ENOENT") throw error; });
    }
    await rmdir(directory);
  }
}

test("catalog server reloads the same atomic controller file without a module cache", () => fixture(async (filename, directory) => {
  const { getYleumServerAppConfig } = load("src/lib/omnia/catalog-server.ts");
  await writeFile(filename, JSON.stringify(envelope()));
  const first = await getYleumServerAppConfig();
  assert.equal(first.configVersion, 1);
  assert.equal(first.config.content[0].price, "260 ₽");
  await writeFile(join(directory, "next.json"), JSON.stringify(envelope(2, [{ ...item, price: "По запросу" }])));
  await rename(join(directory, "next.json"), filename);
  const second = await getYleumServerAppConfig();
  assert.equal(second.configVersion, 2);
  assert.equal(second.config.content[0].price, "По запросу");
  assert.equal(first.config.content[0].price, "260 ₽", "historical snapshots stay unchanged");
}));

test("catalog server fails closed without a controller file, never bundled fallback", () => fixture(async filename => {
  const { getYleumServerAppConfig } = load("src/lib/omnia/catalog-server.ts");
  await assert.rejects(getYleumServerAppConfig(), error => error.code === "catalog_unavailable" && !error.message.includes(filename));
  delete process.env.YLEUM_APP_CONFIG_PATH;
  await assert.rejects(getYleumServerAppConfig(), error => error.code === "catalog_unavailable");
}));

test("catalog rejects oversized, malformed, invalid UTF8 and non-object files", () => fixture(async filename => {
  const { getYleumServerAppConfig } = load("src/lib/omnia/catalog-server.ts");
  for (const body of [Buffer.alloc(1024 * 1024 + 1, 32), "{broken", Buffer.from([0xff]), "null", "[]"]) {
    await writeFile(filename, body);
    await assert.rejects(getYleumServerAppConfig(), error => error.code === "catalog_unavailable");
  }
}));

test("catalog rejects conflicting IDs, invalid versions and corrupt business shapes", () => fixture(async filename => {
  const { getYleumServerAppConfig } = load("src/lib/omnia/catalog-server.ts");
  const invalid = [envelope(-1), envelope(1.5), envelope(Number.MAX_SAFE_INTEGER + 1), envelope(1, [item, item]), envelope(1, [{ ...item, active: "yes" }]), { config_version: 1, config: {} }];
  for (const value of invalid) {
    await writeFile(filename, JSON.stringify(value));
    await assert.rejects(getYleumServerAppConfig(), error => error.code === "catalog_unavailable");
  }
  await writeFile(filename, JSON.stringify(envelope(0, [])));
  assert.equal((await getYleumServerAppConfig()).configVersion, 0);
}));

test("catalog server action exposes only active positions, keeps unavailable status and hides config details", () => fixture(async filename => {
  const server = load("src/lib/omnia/catalog-server.ts");
  const { getYleumCatalog } = load("src/lib/omnia/catalog-actions.ts", { "@/lib/omnia/catalog-server": server });
  await writeFile(filename, JSON.stringify(envelope(4, [item, { ...item, id: "hidden", active: false }, { ...item, id: "later", availability: "out_of_stock" }])));
  const result = await getYleumCatalog();
  assert.equal(result.ok, true);
  assert.equal(result.configVersion, 4);
  assert.deepEqual(result.items.map(entry => entry.id), ["cocoa", "later"]);
  assert.equal(result.items[1].availability, "out_of_stock");
  assert.equal("config" in result, false);
  assert.equal("operator" in result, false);
  await unlink(filename);
  assert.deepEqual(await getYleumCatalog(), { ok: false, code: "catalog_unavailable" });
}));
