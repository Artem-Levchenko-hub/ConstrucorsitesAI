// Controller-only bootstrap for the dedicated trusted-core database.
// Never place the admin DSN or CORE_RUNTIME_PASSWORD in the server container.
import { spawnSync } from 'node:child_process';
import pg from 'pg';

const role = 'omnia_core_runtime';
const grants = {
  max_users: 'SELECT, INSERT, DELETE',
  max_business_actions: 'SELECT, INSERT, UPDATE, DELETE',
  max_consents: 'SELECT, INSERT',
  max_analytics_events: 'SELECT, INSERT',
  max_bot_outbox: 'SELECT',
  max_audit_log: 'SELECT, INSERT, DELETE',
  max_webhook_events: 'SELECT, INSERT, DELETE',
};
const owned = Object.keys(grants).filter(name => name !== 'max_webhook_events');
const options = { connectionTimeoutMillis: 5000, query_timeout: 10000, statement_timeout: 10000, lock_timeout: 5000 };
let admin;
let runtime;
try {
  if (process.argv.slice(2).some(arg => arg !== '--role-only')) throw new Error('arguments');
  const password = process.env.CORE_RUNTIME_PASSWORD;
  if (!password || password.length < 24 || !process.env.DATABASE_URL) throw new Error('credentials');
  admin = new pg.Client({ ...options, connectionString: process.env.DATABASE_URL });
  await admin.connect();
  // Separate from the migration runner lock, held across migration+ACL changes.
  const lock = await admin.query("SELECT pg_try_advisory_lock(hashtext('omnia:core:runtime-role'), hashtext(current_database())) AS acquired");
  if (!lock.rows[0].acquired) throw new Error('bootstrap_busy');
  const roleOnly = process.argv.includes('--role-only');
  if (!roleOnly) {
    const migration = spawnSync(process.execPath, ['scripts/apply-migrations.mjs'], {
      env: process.env, stdio: 'pipe', timeout: 45000, maxBuffer: 1024 * 1024,
    });
    if (migration.status !== 0) throw new Error('migration_failed');
  }
  await admin.query('BEGIN');
  const existing = await admin.query('SELECT oid FROM pg_roles WHERE rolname=$1', [role]);
  if (!existing.rowCount) await admin.query(`CREATE ROLE ${role} LOGIN`);
  await admin.query(`ALTER ROLE ${role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD ${pg.escapeLiteral(password)}`);
  const unsafe = await admin.query(`SELECT
    EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=$1)
    OR EXISTS (SELECT 1 FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid WHERE r.rolname=$1 AND d.deptype='o') AS unsafe`, [role]);
  if (unsafe.rows[0].unsafe) throw new Error('runtime_ownership_or_membership');
  if (!roleOnly) {
    const identity = (await admin.query('SELECT current_database() AS db, current_user AS owner')).rows[0];
    const quote = value => '"' + value.replaceAll('"', '""') + '"';
    await admin.query(`REVOKE ALL ON DATABASE ${quote(identity.db)} FROM ${role}`);
    await admin.query(`REVOKE CREATE, TEMPORARY ON DATABASE ${quote(identity.db)} FROM PUBLIC`);
    await admin.query(`GRANT CONNECT ON DATABASE ${quote(identity.db)} TO ${role}`);
    await admin.query(`REVOKE ALL ON SCHEMA public FROM ${role}`);
    await admin.query('REVOKE CREATE ON SCHEMA public FROM PUBLIC');
    await admin.query(`GRANT USAGE ON SCHEMA public TO ${role}`);
    await admin.query(`REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ${role}, PUBLIC`);
    await admin.query(`REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ${role}, PUBLIC`);
    await admin.query(`REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM ${role}, PUBLIC`);
    // Global defaults are scoped to this database. A per-schema REVOKE cannot
    // remove PostgreSQL's global default PUBLIC EXECUTE on new functions.
    for (const kind of ['TABLES', 'SEQUENCES', 'FUNCTIONS']) {
      await admin.query(`ALTER DEFAULT PRIVILEGES FOR ROLE ${quote(identity.owner)} REVOKE ALL ON ${kind} FROM PUBLIC, ${role}`);
      await admin.query(`ALTER DEFAULT PRIVILEGES FOR ROLE ${quote(identity.owner)} IN SCHEMA public REVOKE ALL ON ${kind} FROM PUBLIC, ${role}`);
    }
    for (const [table, permissions] of Object.entries(grants)) {
      await admin.query(`GRANT ${permissions} ON TABLE public.${table} TO ${role}`);
    }
    const policies = await admin.query(`SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
      (SELECT json_agg(json_build_object('command',p.polcmd,'permissive',p.polpermissive,
       'public',p.polroles=ARRAY[0::oid], 'using',pg_get_expr(p.polqual,p.polrelid),
       'check',pg_get_expr(p.polwithcheck,p.polrelid))) FROM pg_policy p WHERE p.polrelid=c.oid) AS policies
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname=ANY($1::text[])`, [owned]);
    // PostgreSQL 16 deparser output: never normalize SQL literal bytes ('' and
    // ' ' have materially different behavior after pooled GUC reset).
    const expected = "(max_user_id = NULLIF(current_setting('app.max_user_id'::text, true), ''::text))";
    if (policies.rowCount !== owned.length || policies.rows.some(row => {
      const policy = row.policies?.[0];
      return !row.relrowsecurity || !row.relforcerowsecurity || row.policies?.length !== 1
        || policy.command !== '*' || !policy.permissive || !policy.public
        || policy.using !== expected || policy.check !== expected;
    })) throw new Error('core_rls_invalid');
  }
  await admin.query('COMMIT');
  const url = new URL(process.env.DATABASE_URL);
  url.username = role; url.password = password;
  runtime = new pg.Client({ ...options, connectionString: url.toString() });
  await runtime.connect();
  const checked = (await runtime.query('SELECT current_user AS name, rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user')).rows[0];
  if (checked.name !== role || Object.entries(checked).some(([key, value]) => key !== 'name' && value)) throw new Error('runtime_validation');
  if (!roleOnly) {
    for (const table of owned) {
      if ((await runtime.query(`SELECT EXISTS(SELECT 1 FROM public.${table}) AS visible`)).rows[0].visible) throw new Error('runtime_no_actor_rows');
    }
  }
  console.log(JSON.stringify({ protocol: 1, role_ready: true, migrations_ready: !roleOnly }));
} catch {
  // Driver errors can contain DSNs, SQL and the role password. Never print them.
  console.error('trusted core database bootstrap failed');
  process.exitCode = 1;
} finally {
  await runtime?.end().catch(() => {});
  await admin?.end().catch(() => {});
}
