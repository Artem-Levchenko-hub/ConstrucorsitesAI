// Build/test copies only. Product migration selection belongs to the managed kit.
import { lstatSync, readdirSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const CORE_MIGRATIONS = Object.freeze([
  '0000_max_core.sql',
  '0001_business_core.sql',
  '0002_row_level_security.sql',
]);

export function selectCoreMigrations(directory) {
  const root = lstatSync(directory);
  if (!root.isDirectory() || root.isSymbolicLink()) throw new Error('core migration directory required');
  // Validate the entire required inventory before removing inherited/product files.
  for (const name of CORE_MIGRATIONS) {
    const file = lstatSync(join(directory, name));
    if (!file.isFile() || file.isSymbolicLink()) throw new Error('core migration file required');
  }
  for (const name of readdirSync(directory)) {
    if (!CORE_MIGRATIONS.includes(name)) rmSync(join(directory, name), { recursive: true });
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  if (process.argv.length !== 3) throw new Error('one core migration directory required');
  selectCoreMigrations(process.argv[2]);
}
