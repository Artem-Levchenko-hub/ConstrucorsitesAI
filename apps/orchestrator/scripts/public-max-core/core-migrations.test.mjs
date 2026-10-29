import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { CORE_MIGRATIONS, selectCoreMigrations } from './core-migrations.mjs';

const template = fileURLToPath(new URL('../../templates/max-miniapp-nextjs/drizzle/', import.meta.url));
const selector = fileURLToPath(new URL('./core-migrations.mjs', import.meta.url));
const expected = ['0000_max_core.sql', '0001_business_core.sql', '0002_row_level_security.sql'];
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'yleum-core-migrations-'));
  cpSync(template, root, { recursive: true });
  return root;
}

test('actual CLI retains exact core bytes and excludes current/future product migrations', () => {
  const root = fixture();
  const product = readFileSync(join(template, '0003_owner_management.sql'));
  try {
    writeFileSync(join(root, '9999_future_product.sql'), 'CREATE TABLE future_product(id int);');
    mkdirSync(join(root, 'inherited-kit-artifact'));
    writeFileSync(join(root, 'inherited-kit-artifact', 'migration.sql'), 'private build artifact');
    const result = spawnSync(process.execPath, [selector, root], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(CORE_MIGRATIONS, expected);
    assert.deepEqual(readdirSync(root).sort(), expected);
    for (const name of expected) assert.deepEqual(readFileSync(join(root, name)), readFileSync(join(template, name)));
    selectCoreMigrations(root); // The build selection is repeatable.
    assert.deepEqual(readdirSync(root).sort(), expected);
    assert.deepEqual(readFileSync(join(template, '0003_owner_management.sql')), product);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

for (const name of expected) {
  test(`missing required ${name} fails before pruning the copied inventory`, () => {
    const root = fixture();
    try {
      rmSync(join(root, name));
      const before = readdirSync(root).sort();
      assert.throws(() => selectCoreMigrations(root));
      assert.deepEqual(readdirSync(root).sort(), before);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}

test('required core migration must be a regular file', () => {
  const root = fixture();
  try {
    rmSync(join(root, expected[0]));
    mkdirSync(join(root, expected[0]));
    assert.throws(() => selectCoreMigrations(root), /core migration file required/);
    assert.ok(readdirSync(root).includes('0003_owner_management.sql'));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
