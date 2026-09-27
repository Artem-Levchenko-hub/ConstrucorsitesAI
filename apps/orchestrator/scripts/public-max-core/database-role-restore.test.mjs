// Phases are orchestrated only against two explicitly disposable PG clusters.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import pg from 'pg';

if (process.env.OMNIA_DISPOSABLE_CORE_TEST !== '1') throw new Error('disposable opt-in required');
const admin = new pg.Client({ connectionString: process.env.DATABASE_URL });
let runtime;
function bootstrap(args = []) {
  const result = spawnSync(process.execPath, ['scripts/bootstrap-database.mjs', ...args], {
    env: process.env, stdio: 'pipe', timeout: 45000,
  });
  assert.equal(result.status, 0, 'bootstrap failed');
}
try {
  await admin.connect();
  const phase = process.argv[2];
  if (phase === 'source') {
    bootstrap();
    await admin.query("INSERT INTO max_users(max_user_id,first_name) VALUES ('qa-a','A'),('qa-b','B')");
    await admin.query("INSERT INTO max_business_actions(max_user_id,action_type) VALUES ('qa-a','restore'),('qa-b','restore')");
  } else if (phase === 'role-only') {
    assert.equal((await admin.query("SELECT count(*)::int n FROM pg_roles WHERE rolname='omnia_core_runtime'")).rows[0].n, 0);
    bootstrap(['--role-only']);
    assert.equal((await admin.query("SELECT to_regclass('public.max_users') AS table_name")).rows[0].table_name, null);
  } else if (phase === 'restored') {
    assert.equal((await admin.query('SELECT count(*)::int n FROM max_users')).rows[0].n, 2);
    assert.equal((await admin.query("SELECT count(*)::int n FROM __omnia_migrations WHERE name='0002_row_level_security.sql'")).rows[0].n, 1);
    bootstrap();
    const url = new URL(process.env.DATABASE_URL);
    url.username = 'omnia_core_runtime'; url.password = process.env.CORE_RUNTIME_PASSWORD;
    runtime = new pg.Client({ connectionString: url.toString() });
    await runtime.connect();
    assert.equal((await runtime.query('SELECT count(*)::int n FROM max_users')).rows[0].n, 0);
    await runtime.query('BEGIN');
    await runtime.query("SELECT set_config('app.max_user_id','qa-a',true)");
    assert.deepEqual((await runtime.query('SELECT max_user_id FROM max_users')).rows, [{ max_user_id: 'qa-a' }]);
    assert.equal((await runtime.query('SELECT count(*)::int n FROM max_business_actions')).rows[0].n, 1);
    await runtime.query('COMMIT');
    assert.equal((await runtime.query('SELECT count(*)::int n FROM max_users')).rows[0].n, 0);
    // A separate project database still supports unrestricted agent-owned DDL.
    await admin.query('CREATE DATABASE project_unrestricted');
    const projectUrl = new URL(process.env.DATABASE_URL); projectUrl.pathname = '/project_unrestricted';
    const project = new pg.Client({ connectionString: projectUrl.toString() });
    await project.connect();
    try {
      await project.query("CREATE SCHEMA arbitrary; CREATE TABLE arbitrary.records(id int primary key, document jsonb); INSERT INTO arbitrary.records VALUES (1,'{}'); ALTER TABLE arbitrary.records ADD COLUMN label text; UPDATE arbitrary.records SET label='preserved'; CREATE INDEX custom_idx ON arbitrary.records(label); DROP INDEX arbitrary.custom_idx");
      assert.deepEqual((await project.query('SELECT id,label FROM arbitrary.records')).rows, [{ id: 1, label: 'preserved' }]);
      assert.equal((await project.query('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')).rows[0].rolsuper, true);
    } finally { await project.end(); }
  } else throw new Error('unknown phase');
  console.log(JSON.stringify({ passed: true, phase, node_version: process.version,
    pg_version: createRequire(import.meta.url)('pg/package.json').version,
    server_version_num: (await admin.query("SELECT current_setting('server_version_num') AS version")).rows[0].version }));
} catch (error) {
  console.log(JSON.stringify({ passed: false, error_class: error?.constructor?.name ?? 'Error' }));
  process.exitCode = 1;
} finally { await runtime?.end(); await admin.end(); }
