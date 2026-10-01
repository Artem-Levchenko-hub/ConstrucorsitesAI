import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const starterRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function installFixture(lock, pnpmSettings) {
  const directory = await mkdtemp(join(tmpdir(), "max-starter-lock-"));
  try {
    await writeFile(join(directory, "package.json"), JSON.stringify({
      name: "max-starter-lock-fixture", private: true, version: "1.0.0", dependencies: {},
      packageManager: "pnpm@9.15.0",
      ...(pnpmSettings ? { pnpm: pnpmSettings } : {}),
    }));
    await writeFile(join(directory, "pnpm-lock.yaml"), lock);
    const dockerfile = await readFile(resolve(starterRoot, "Dockerfile.dev"), "utf8");
    // Execute the real dependency-install instruction. Reintroducing fallback
    // must turn frozen refusal into success and rewrite this fixture's lock.
    const install = dockerfile.match(/^RUN (pnpm install[^\n]*)$/m);
    assert.ok(install, "Dockerfile must expose its dependency-install instruction");
    const result = spawnSync("sh", ["-c", install[1]], {
      cwd: directory, encoding: "utf8", timeout: 30_000,
      env: { ...process.env, CI: "false", npm_config_update_notifier: "false" },
    });
    assert.ifError(result.error);
    return { ...result, lockAfter: await readFile(join(directory, "pnpm-lock.yaml"), "utf8") };
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("kit install refuses mismatched lock instead of silently resolving and rewriting it", async () => {
  // The real starter lock is valid, but intentionally incompatible with the
  // empty fixture manifest. No dependency download is needed by this probe.
  const lock = await readFile(resolve(starterRoot, "pnpm-lock.yaml"), "utf8");
  const starterPackage = JSON.parse(await readFile(resolve(starterRoot, "package.json"), "utf8"));
  const result = await installFixture(lock, starterPackage.pnpm);
  assert.notEqual(result.status, 0, "a frozen-lock refusal must remain a build failure");
  assert.match(result.stdout + result.stderr, /ERR_PNPM_OUTDATED_LOCKFILE/);
  assert.equal(result.lockAfter, lock, "failed install must preserve the exact reviewed lock");
});

test("kit install accepts a matching lock without rewriting it", async () => {
  const lock = "lockfileVersion: '9.0'\n\nsettings:\n  autoInstallPeers: true\n  excludeLinksFromLockfile: false\n\nimporters:\n\n  .: {}\n";
  const result = await installFixture(lock);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(result.lockAfter, lock);
});
