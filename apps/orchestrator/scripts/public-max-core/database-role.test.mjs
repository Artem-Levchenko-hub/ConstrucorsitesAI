// Only the disposable harness supplies this synthetic database. No production DSN.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import pg from 'pg';
import { createRequire } from 'node:module';

const pgVersion = createRequire(import.meta.url)('pg/package.json').version;

if (process.env.OMNIA_DISPOSABLE_CORE_TEST !== '1') throw new Error('disposable opt-in required');
const admin = new pg.Client({ connectionString: process.env.DATABASE_URL });
const baseline = process.env.CORE_ROLE_TEST_BASELINE === '1';
const cases = [];
let runtime;
function child(script, args = [], succeeds = true) {
  const result = spawnSync(process.execPath, [script, ...args], {
    env: process.env, timeout: 45_000, stdio: 'pipe',
  });
  assert.equal(result.status === 0, succeeds, 'trusted child outcome mismatch');
}
try {
  await admin.connect();
  const serverVersion = (await admin.query("SELECT current_setting('server_version_num') AS version")).rows[0].version;
  child('scripts/apply-migrations.mjs');
  assert.equal((await admin.query("SELECT count(*)::int n FROM __omnia_migrations WHERE name='0002_row_level_security.sql'")).rows[0].n, 1);
  await admin.query("INSERT INTO max_users(max_user_id,first_name) VALUES ('qa-a','A'),('qa-b','B') ON CONFLICT DO NOTHING");
  if (baseline) {
    const role = (await admin.query('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).rows[0];
    const visible = (await admin.query('SELECT count(*)::int n FROM max_users')).rows[0].n;
    // Expected negative control: the previous server/admin DSN bypasses FORCE RLS.
    assert.equal(role.rolsuper, true); assert.equal(visible, 2);
    console.log(JSON.stringify({ baseline_negative_control: true, existing_0002: true, runtime_admin: true, no_guc_isolation: false, server_version_num: serverVersion }));
  } else {
    child('scripts/bootstrap-database.mjs', ['--role-only']);
    await admin.query('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO omnia_core_runtime');
    await admin.query('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO omnia_core_runtime');
    child('scripts/bootstrap-database.mjs');
    child('scripts/bootstrap-database.mjs');
    cases.push('existing_0002_idempotent_bootstrap');
    const url = new URL(process.env.DATABASE_URL);
    url.username = 'omnia_core_runtime'; url.password = process.env.CORE_RUNTIME_PASSWORD;
    runtime = new pg.Client({ connectionString: url.toString() });
    await runtime.connect();
    const role = (await runtime.query('SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user')).rows[0];
    assert.ok(Object.values(role).every(value => value === false));
    for (const table of ['max_users','max_business_actions','max_consents','max_analytics_events','max_bot_outbox','max_audit_log']) {
      assert.equal((await runtime.query(`SELECT count(*)::int n FROM public.${table}`)).rows[0].n, 0);
    }
    cases.push('runtime_restricted_no_guc_denial');
    await assert.rejects(runtime.query("INSERT INTO max_users(max_user_id,first_name) VALUES ('qa-c','C')"), { code: '42501' });
    await runtime.query('BEGIN');
    await runtime.query("SELECT set_config('app.max_user_id','qa-a',true)");
    assert.deepEqual((await runtime.query('SELECT max_user_id FROM max_users')).rows, [{ max_user_id: 'qa-a' }]);
    assert.equal((await runtime.query("DELETE FROM max_users WHERE max_user_id='qa-b'")).rowCount, 0);
    await runtime.query("INSERT INTO max_business_actions(max_user_id,action_type) VALUES ('qa-a','qa')");
    await assert.rejects(runtime.query("INSERT INTO max_business_actions(max_user_id,action_type) VALUES ('qa-b','qa')"), { code: '42501' });
    await runtime.query('ROLLBACK');
    assert.equal((await runtime.query('SELECT count(*)::int n FROM max_users')).rows[0].n, 0);
    cases.push('actor_AB_isolation_pool_context_rollback');
    for (const sql of ['CREATE TABLE forbidden(id int)', 'CREATE TEMP TABLE forbidden(id int)', 'SET ROLE postgres', 'ALTER TABLE max_users ADD COLUMN forbidden int', 'SELECT * FROM __omnia_migrations']) {
      await assert.rejects(runtime.query(sql), { code: '42501' });
    }
    cases.push('no_ddl_admin_or_journal');
    await admin.query('CREATE TABLE future_private(id int)');
    await admin.query("CREATE FUNCTION public.future_private_fn() RETURNS int LANGUAGE sql AS 'SELECT 1'");
    await assert.rejects(runtime.query('SELECT * FROM future_private'), { code: '42501' });
    await assert.rejects(runtime.query('SELECT future_private_fn()'), { code: '42501' });
    cases.push('default_ACL_private');
    await runtime.query("INSERT INTO max_webhook_events(event_key,event_type) VALUES ('qa-key','qa') ON CONFLICT DO NOTHING RETURNING id");
    assert.equal((await runtime.query("DELETE FROM max_webhook_events WHERE event_key='qa-key' RETURNING id")).rowCount, 1);
    assert.equal((await admin.query("SELECT count(*)::int n FROM max_users WHERE max_user_id='qa-b'")).rows[0].n, 1);
    cases.push('webhook_without_actor_and_preserved_rows');
    await admin.query('CREATE ROLE qa_forbidden_membership');
    await admin.query('GRANT qa_forbidden_membership TO omnia_core_runtime');
    child('scripts/bootstrap-database.mjs', [], false);
    await admin.query('REVOKE qa_forbidden_membership FROM omnia_core_runtime');
    await admin.query('DROP ROLE qa_forbidden_membership');
    cases.push('membership_rejected');
    await admin.query('ALTER TABLE future_private OWNER TO omnia_core_runtime');
    child('scripts/bootstrap-database.mjs', [], false);
    await admin.query('ALTER TABLE future_private OWNER TO postgres');
    cases.push('runtime_ownership_rejected');
    await admin.query('DELETE FROM max_users');
    for (const predicate of ['true', "max_user_id = nullif(current_setting('app.max_user_id',true),' ')"]) {
      await admin.query(`ALTER POLICY max_users_own_row ON max_users USING (${predicate}) WITH CHECK (${predicate})`);
      child('scripts/bootstrap-database.mjs', [], false);
    }
    await admin.query("ALTER POLICY max_users_own_row ON max_users USING (max_user_id = nullif(current_setting('app.max_user_id',true),'')) WITH CHECK (max_user_id = nullif(current_setting('app.max_user_id',true),''))");
    await admin.query('CREATE POLICY qa_extra ON max_users FOR SELECT USING (true)');
    child('scripts/bootstrap-database.mjs', [], false);
    await admin.query('DROP POLICY qa_extra ON max_users');
    child('scripts/bootstrap-database.mjs');
    cases.push('empty_database_wrong_literal_and_extra_policy_rejected');
    console.log(JSON.stringify({ passed: true, cases, server_version_num: serverVersion, node_version: process.version, pg_version: pgVersion }));
  }
} catch (error) {
  // Never emit driver messages: connection failures can include DSNs/SQL values.
  console.log(JSON.stringify({ passed: false, error_class: error?.constructor?.name ?? 'Error', cases }));
  process.exitCode = 1;
} finally {
  await runtime?.end(); await admin.end();
}
