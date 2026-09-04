import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const starterRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("MAX starter package satisfies portable machine contract", async () => {
  const packageJson = JSON.parse(await readFile(resolve(starterRoot, "package.json"), "utf8"));

  assert.equal(packageJson.private, true);
  assert.equal(packageJson.engines.node, ">=22");
  assert.equal(packageJson.packageManager, "pnpm@9.15.0");
  assert.equal(packageJson.scripts.build, "next build");
  assert.equal(packageJson.scripts.start, "next start --port 3000 --hostname 0.0.0.0");
  assert.equal(packageJson.scripts.test, "node --test tests/*.test.mjs");
});
