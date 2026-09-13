const { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { stripTypeScriptTypes } = require('node:module');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../../../templates/max-miniapp-nextjs/src/lib/secure-data');
const output = mkdtempSync(path.join(tmpdir(), 'omnia-secure-data-'));
try {
  for (const name of ['crypto', 'validation', 'store', 'http']) {
    const source = path.join(root, `${name}.ts`);
    if (!existsSync(source)) continue;
    const js = stripTypeScriptTypes(readFileSync(source, 'utf8'), { mode: 'strip' })
      .replace(/from "\.\/(crypto|validation)"/g, 'from "./$1.mjs"');
    writeFileSync(path.join(output, `${name}.mjs`), js);
  }
  const result = spawnSync(process.execPath, ['--test', path.join(__dirname, 'secure-data.test.cjs')], {
    env: { ...process.env, SECURE_DATA_MODULE_DIR: output }, stdio: 'inherit', timeout: 150_000,
  });
  if (result.error) console.error(`Secure data fixture process failed: ${result.error.code || 'SPAWN_FAILED'}`);
  process.exitCode = result.status ?? 1;
} finally { rmSync(output, { recursive: true, force: true }); }
