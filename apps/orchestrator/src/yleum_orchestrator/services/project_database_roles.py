"""Trusted, transactional project role bootstrap; never execute as generated code.

Call before exposing a fresh/retained/restored project and after limited migrations.
Generated SQL must LOGIN as the migrator: SET ROLE on a postgres connection is
escapable with RESET ROLE. This module does not activate or rotate running clients.
"""

PROJECT_RUNTIME_ROLE = "omnia_project_runtime"
PROJECT_MIGRATOR_ROLE = "omnia_project_migrator"
PROJECT_OWNER_ROLE = "omnia_project_owner"


def _password(value: str) -> str:
    if not isinstance(value, str) or len(value) < 24 or "\x00" in value:
        raise ValueError("project database credential is invalid")
    # E strings are independent of the session's standard_conforming_strings.
    return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def project_role_hba() -> str:
    """Complete ordered HBA, not an append fragment; no administrator TCP path.

    Runtime samenet covers the local preview and published pod subnet. Network
    policy must restrict that subnet to the project's own app. The migrator uses
    the DB's loopback namespace. Controller admin uses the postgres OS peer socket.
    """
    return (
        "local all postgres peer\n"
        "local all all reject\n"
        f"host postgres {PROJECT_RUNTIME_ROLE} samenet scram-sha-256\n"
        f"host postgres {PROJECT_MIGRATOR_ROLE} 127.0.0.1/32 scram-sha-256\n"
        f"host postgres {PROJECT_MIGRATOR_ROLE} ::1/128 scram-sha-256\n"
        "host all all 0.0.0.0/0 reject\n"
        "host all all ::/0 reject\n"
    )


def bootstrap_project_roles_sql(
    runtime_password: str,
    migrator_password: str,
    *,
    admin_password: str | None = None,
) -> str:
    """Reconcile project user objects and least privilege without changing rows.

    Extensions/catalog objects are never reowned. Existing SECURITY DEFINER
    user routines and unsupported role dependencies fail closed. Password values
    are SQL input only: callers must never log this string or driver diagnostics.
    Objects imported with --no-owner/--no-privileges require this reconciliation.
    """
    runtime = _password(runtime_password)
    migrator = _password(migrator_password)
    admin = _password(admin_password) if admin_password is not None else None
    rekey = f"ALTER ROLE postgres PASSWORD {admin};" if admin else ""
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog;
SET LOCAL password_encryption = 'scram-sha-256';
SELECT pg_advisory_xact_lock(hashtext('omnia:project:roles:v1'));
DO $roles$
DECLARE item record; role_name text;
BEGIN
  IF current_user <> 'postgres' OR NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
    THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='trusted project bootstrap required';
  END IF;
  IF EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
      AND p.prosecdef AND NOT EXISTS
      (SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND
    d.deptype='e')) THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='project SECURITY DEFINER requires explicit
    review';
  END IF;
  FOREACH role_name IN ARRAY ARRAY['{PROJECT_OWNER_ROLE}', '{PROJECT_MIGRATOR_ROLE}',
    '{PROJECT_RUNTIME_ROLE}'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=role_name) THEN
      EXECUTE format('CREATE ROLE %I', role_name);
    END IF;
    EXECUTE format('ALTER ROLE %I NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    NOINHERIT', role_name);
    EXECUTE format('ALTER ROLE %I RESET ALL', role_name);
    -- Reset role-specific defaults in this DB as well as global defaults.
    EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL', role_name, current_database());
  END LOOP;
  -- Revoke both inherited parents and grants of these identities to other roles.
  FOR item IN SELECT parent.rolname AS parent, child.rolname AS child
    FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
    JOIN pg_roles child ON child.oid=m.member
    WHERE parent.rolname IN
    ('{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}')
       OR child.rolname IN
    ('{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}') LOOP
    EXECUTE format('REVOKE %I FROM %I CASCADE', item.parent, item.child);
  END LOOP;
END $roles$;
ALTER ROLE {PROJECT_OWNER_ROLE} NOLOGIN PASSWORD NULL;
ALTER ROLE {PROJECT_RUNTIME_ROLE} LOGIN PASSWORD {runtime};
ALTER ROLE {PROJECT_MIGRATOR_ROLE} LOGIN PASSWORD {migrator};
GRANT {PROJECT_OWNER_ROLE} TO {PROJECT_MIGRATOR_ROLE} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;
DO $objects$
DECLARE item record; role_name text; kind text; schema_name text;
BEGIN
  -- Membership removal alone cannot remove direct or PUBLIC catalog ACLs.
  -- Never compare against the current PUBLIC ACL: it may already be unsafe.
  FOREACH role_name IN ARRAY
    ARRAY['{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}'] LOOP
    IF EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE (n.nspname='pg_catalog' OR EXISTS (SELECT FROM pg_depend d
        WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e'))
      AND p.proname IN ('pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file',
        'lo_import','lo_export','pg_file_write','pg_file_rename','pg_file_unlink')
      AND has_function_privilege(role_name,p.oid,'EXECUTE')) THEN
      RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unsafe project file routine privilege';
    END IF;
    IF EXISTS (SELECT FROM pg_namespace n WHERE
      (n.nspname ~ '^pg_' OR n.nspname='information_schema')
      AND has_schema_privilege(role_name,n.oid,'CREATE')) THEN
      RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unsafe project system schema privilege';
    END IF;
    IF EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      CROSS JOIN LATERAL aclexplode(p.proacl) acl JOIN pg_roles r ON r.oid=acl.grantee
      WHERE (n.nspname ~ '^pg_' OR n.nspname='information_schema') AND r.rolname=role_name)
      OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE (n.nspname ~ '^pg_' OR n.nspname='information_schema')
          AND c.relkind IN ('r','p','v','m','f')
          -- pg_settings is intentionally PUBLIC UPDATE for permitted session GUCs.
          AND (has_table_privilege(role_name,c.oid,'INSERT,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR (c.relname <> 'pg_settings' AND has_table_privilege(role_name,c.oid,'UPDATE'))
            OR (c.relname IN ('pg_authid','pg_shadow')
              AND has_table_privilege(role_name,c.oid,'SELECT'))))
      OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) acl JOIN pg_roles r ON r.oid=acl.grantee
        WHERE (n.nspname ~ '^pg_' OR n.nspname='information_schema') AND r.rolname=role_name)
      OR EXISTS (SELECT FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(a.attacl) acl
        WHERE (n.nspname ~ '^pg_' OR n.nspname='information_schema')
          AND (acl.grantee=(SELECT oid FROM pg_roles WHERE rolname=role_name)
            OR (acl.grantee=0 AND (acl.privilege_type <> 'SELECT'
              OR c.relname IN ('pg_authid','pg_shadow'))))) THEN
      RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unreviewed project system object privilege';
    END IF;
  END LOOP;
  -- A legacy/extension-owned schema is outside reconciliation. Reject effective
  -- and future ACLs there; do not silently take over another owner's objects.
  FOR schema_name IN SELECT n.nspname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
      AND (NOT (n.nspname='public' OR r.rolname IN ('postgres','{PROJECT_RUNTIME_ROLE}',
        '{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}')) OR EXISTS
        (SELECT FROM pg_depend d WHERE d.classid='pg_namespace'::regclass
          AND d.objid=n.oid AND d.deptype='e')) LOOP
    FOREACH role_name IN ARRAY
      ARRAY['{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}'] LOOP
      IF has_schema_privilege(role_name,schema_name,'USAGE,CREATE')
        OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=schema_name AND c.relkind IN ('r','p','v','m','f')
            AND has_table_privilege(role_name,c.oid,
              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
        OR EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=schema_name AND c.relkind='S'
            AND CASE WHEN c.relkind='S'
              THEN has_sequence_privilege(role_name,c.oid,'USAGE,SELECT,UPDATE') ELSE false END)
        OR EXISTS (SELECT FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=schema_name AND a.attnum>0 AND NOT a.attisdropped
            AND c.relkind IN ('r','p','v','m','f')
            AND CASE WHEN c.relkind IN ('r','p','v','m','f') AND a.attnum>0
              THEN has_column_privilege(role_name,c.oid,a.attnum,'SELECT,INSERT,UPDATE,REFERENCES')
              ELSE false END)
        OR EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname=schema_name AND has_function_privilege(role_name,p.oid,'EXECUTE'))
        OR EXISTS (SELECT FROM pg_default_acl d
          LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
          CROSS JOIN LATERAL aclexplode(d.defaclacl) acl
          WHERE (n.nspname=schema_name OR (d.defaclnamespace=0 AND d.defaclrole=
            (SELECT nspowner FROM pg_namespace WHERE nspname=schema_name)))
            AND acl.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=role_name))) THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unhandled project schema privilege';
      END IF;
    END LOOP;
  END LOOP;
  EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
  FOREACH role_name IN ARRAY
    ARRAY['{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}'] LOOP
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM %I',current_database(),role_name);
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I',current_database(),role_name);
  END LOOP;
  -- Changing the DB owner is never part of bootstrap. Fail if runtime identities own it.
  IF EXISTS (SELECT FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
    WHERE r.rolname IN
    ('{PROJECT_RUNTIME_ROLE}','{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}')) THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='project database ownership requires explicit
    recovery';
  END IF;
  EXECUTE format('GRANT CREATE ON DATABASE %I TO {PROJECT_OWNER_ROLE}',current_database());
  FOR schema_name IN SELECT n.nspname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
      AND (n.nspname='public' OR r.rolname IN ('postgres','{PROJECT_RUNTIME_ROLE}',
        '{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}'))
      AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_namespace'::regclass
        AND d.objid=n.oid AND d.deptype='e') LOOP
  EXECUTE format('ALTER SCHEMA %I OWNER TO {PROJECT_OWNER_ROLE}',schema_name);
  EXECUTE format('REVOKE ALL ON SCHEMA %I FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE},{PROJECT_MIGRATOR_ROLE}',schema_name);
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO {PROJECT_RUNTIME_ROLE}',schema_name);
  FOR item IN SELECT c.oid,c.relname,c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname=schema_name AND c.relkind IN ('r','p','v','m','S','f') AND NOT EXISTS
      (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND
    d.deptype='e')
    ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END,c.oid LOOP
    kind := CASE item.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
      WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END;
    EXECUTE format('ALTER %s %I.%I OWNER TO {PROJECT_OWNER_ROLE}',kind,schema_name,item.relname);
  END LOOP;
  FOR item IN SELECT p.oid,p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname=schema_name AND NOT EXISTS
      (SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND
    d.deptype='e') LOOP
    kind := CASE WHEN item.prokind='p' THEN 'PROCEDURE' WHEN item.prokind='a' THEN 'AGGREGATE'
    ELSE 'FUNCTION' END;
    EXECUTE format('ALTER %s %s OWNER TO {PROJECT_OWNER_ROLE}',kind,item.oid::regprocedure);
    EXECUTE format('REVOKE ALL ON ROUTINE %s FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',item.oid::regprocedure);
  END LOOP;
  FOR item IN SELECT t.oid,t.typtype FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE n.nspname=schema_name AND t.typtype IN ('e','d','r') AND NOT EXISTS
      (SELECT FROM pg_depend d WHERE d.classid='pg_type'::regclass AND d.objid=t.oid AND
    d.deptype='e') LOOP
    kind := CASE WHEN item.typtype='d' THEN 'DOMAIN' ELSE 'TYPE' END;
    EXECUTE format('ALTER %s %s OWNER TO {PROJECT_OWNER_ROLE}',kind,item.oid::regtype);
  END LOOP;
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',schema_name);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',schema_name);
  FOR item IN SELECT c.relname,string_agg(quote_ident(a.attname),',') AS columns
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
    WHERE n.nspname=schema_name AND c.relkind IN ('r','p','v','m','f')
    GROUP BY c.oid,c.relname LOOP
    EXECUTE format('REVOKE ALL (%s) ON TABLE %I.%I FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',item.columns,schema_name,item.relname);
  END LOOP;
  FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname=schema_name AND c.relkind IN ('r','p','v','m','f')
      AND NOT starts_with(c.relname,'__omnia_')
      AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND
    d.objid=c.oid AND d.deptype='e') LOOP
    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE %I.%I TO
    {PROJECT_RUNTIME_ROLE}',schema_name,item.relname);
  END LOOP;
  FOR item IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname=schema_name AND c.relkind='S' AND NOT EXISTS
      (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND
    d.deptype='e') LOOP
    EXECUTE format('GRANT USAGE,SELECT ON SEQUENCE %I.%I TO
    {PROJECT_RUNTIME_ROLE}',schema_name,item.relname);
  END LOOP;
  FOREACH role_name IN ARRAY ARRAY['postgres','{PROJECT_OWNER_ROLE}','{PROJECT_MIGRATOR_ROLE}'] LOOP
    FOREACH kind IN ARRAY ARRAY['TABLES','SEQUENCES','FUNCTIONS'] LOOP
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE ALL ON %s FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',role_name,kind);
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I REVOKE ALL ON %s FROM
    PUBLIC,{PROJECT_RUNTIME_ROLE}',role_name,schema_name,kind);
    END LOOP;
    IF role_name <> 'postgres' THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT
    SELECT,INSERT,UPDATE,DELETE ON TABLES TO {PROJECT_RUNTIME_ROLE}',role_name,schema_name);
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE,SELECT ON
    SEQUENCES TO {PROJECT_RUNTIME_ROLE}',role_name,schema_name);
    END IF;
  END LOOP;
  END LOOP;
  -- No runtime identity may retain ownership outside reconciled user schemas.
  IF EXISTS (SELECT FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid
    WHERE d.refclassid='pg_authid'::regclass AND d.deptype='o' AND
    r.rolname='{PROJECT_RUNTIME_ROLE}') THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unsupported runtime object ownership';
  END IF;
  IF EXISTS (SELECT FROM pg_shdepend d JOIN pg_roles r ON r.oid=d.refobjid
    WHERE d.refclassid='pg_authid'::regclass AND d.deptype='o'
      AND r.rolname IN ('{PROJECT_MIGRATOR_ROLE}','{PROJECT_OWNER_ROLE}')
      AND d.classid NOT IN ('pg_class'::regclass,'pg_namespace'::regclass,
        'pg_type'::regclass,'pg_proc'::regclass,'pg_default_acl'::regclass)) THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='unsupported migration role ownership';
  END IF;
END $objects$;
{rekey}
COMMIT;
"""
