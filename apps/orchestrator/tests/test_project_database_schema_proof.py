"""Proof transitions never authorize an unobserved historical schema."""

import hashlib
import importlib
import json
import os
import stat
import subprocess
import time
from uuid import uuid4

import pytest

ROLES = ("omnia_project_owner", "omnia_project_migrator", "omnia_project_runtime")
BASE = (
    "CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER LOGIN;\n"
    "\\connect postgres\nCREATE TABLE public.business(id integer, value text);\n"
)
ADDED = (
    'CREATE ROLE "omnia_project_runtime";\n'
    "ALTER ROLE omnia_project_runtime WITH NOSUPERUSER NOINHERIT LOGIN;\n"
    "ALTER ROLE omnia_project_runtime SET search_path TO 'public';\n"
    "ALTER ROLE omnia_project_runtime IN DATABASE postgres RESET ALL;\n"
    "CREATE ROLE omnia_project_owner;\nCREATE ROLE omnia_project_migrator;\n"
    "GRANT omnia_project_owner TO omnia_project_migrator "
    "WITH INHERIT TRUE, SET TRUE, ADMIN FALSE GRANTED BY postgres;\n"
)
BINDING = {
    "identity": "owned-project",
    "reference": "release-sha",
    "volume": "owned-pvc-uid",
    "epoch": 7,
}


def module():
    spec = importlib.util.find_spec("yleum_orchestrator.services.project_database_schema_proof")
    assert spec is not None, "strict schema proof bridge is missing"
    return importlib.import_module(spec.name)


def pending(tmp_path):
    m = module()
    path = tmp_path / "private" / "bridge.json"
    receipt = m.begin_schema_bridge(
        path, binding=BINDING, accepted_digest=m.legacy_schema_digest(BASE), before_dump=BASE
    )
    return m, path, receipt


def test_legacy_digest_is_exact_existing_algorithm():
    dump = "-- header\n\\restrict random\n\nCREATE ROLE postgres;\n\\unrestrict random\n"
    expected = hashlib.sha256(b"\nCREATE ROLE postgres;").hexdigest()
    assert module().legacy_schema_digest(dump) == expected


def test_managed_roles_only_projection_retains_postgres_and_business():
    m = module()
    assert m.legacy_schema_digest(BASE) != m.legacy_schema_digest(ADDED + BASE)
    assert m.schema_proof(BASE) == m.schema_proof(ADDED + BASE)
    assert m.schema_proof(BASE) != m.schema_proof(BASE.replace("SUPERUSER", "NOSUPERUSER"))
    assert m.schema_proof(BASE) != m.schema_proof(BASE.replace("integer", "bigint"))


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE ROLE omnia_project_runtime_extra;",
        'CREATE ROLE "OMNIA_PROJECT_RUNTIME";',
        "ALTER ROLE other SET search_path TO 'omnia_project_runtime';",
        "GRANT other TO third GRANTED BY omnia_project_owner;",
        "CREATE TABLE public.omnia_project_runtime(id int);",
        "COMMENT ON ROLE omnia_project_runtime IS 'business metadata';",
    ],
)
def test_similar_identifiers_and_other_statement_kinds_are_not_discarded(statement):
    m = module()
    assert m.schema_proof(BASE + statement) != m.schema_proof(BASE)


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER ROLE omnia_project_runtime RENAME TO other;",
        "CREATE USER omnia_project_runtime;",
        "ALTER ROLE omnia_project_runtime UNKNOWN_ATTRIBUTE;",
        "GRANT other, omnia_project_owner TO third;",
        "GRANT other TO third, omnia_project_runtime;",
    ],
)
def test_unknown_or_mixed_managed_role_statements_fail_closed(statement):
    m = module()
    with pytest.raises(m.SchemaProofError):
        m.schema_proof(BASE + statement)


@pytest.mark.parametrize(
    "statement",
    [
        "GRANT other TO omnia_project_runtime GRANTED BY postgres;",
        "GRANT omnia_project_owner TO other GRANTED BY postgres;",
        "REVOKE other FROM omnia_project_runtime CASCADE;",
        "ALTER ROLE omnia_project_runtime CONNECTION LIMIT 5 VALID UNTIL 'infinity';",
    ],
)
def test_recognized_managed_membership_and_attributes_are_projected(statement):
    m = module()
    assert m.schema_proof(BASE + statement) == m.schema_proof(BASE)


def test_whole_statements_preserve_strings_dollar_bodies_nested_comments_and_connections():
    m = module()
    business = r"""
-- dump header
CREATE FUNCTION public.f() RETURNS text LANGUAGE sql AS $body$
-- CREATE ROLE omnia_project_runtime;
SELECT 'ALTER ROLE omnia_project_owner LOGIN; /* text */';
$body$;
COMMENT ON TABLE public.business IS E'quoted \' ; CREATE ROLE omnia_project_runtime;';
CREATE TABLE "semi;quote" ("omnia_project_runtime" text DEFAULT 'it''s; safe');
/* outer /* nested ; */ still comment */
"""
    # The function's stored comment and string content are business schema.
    assert m.schema_proof(BASE + business) == m.schema_proof(ADDED + BASE + business)
    assert m.schema_proof(BASE + business) != m.schema_proof(
        BASE + business.replace("-- CREATE ROLE", "-- different CREATE ROLE")
    )
    assert m.schema_proof(BASE + business) != m.schema_proof(
        BASE + business.replace("ALTER ROLE omnia_project_owner LOGIN", "different string")
    )
    assert m.schema_proof(BASE) != m.schema_proof(
        BASE.replace("\\connect postgres", "\\connect other")
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "SELECT 'unfinished;",
        "DO $$BEGIN NULL; END;",
        "/* open",
        "\\! echo ignored\n",
        "CREATE TABLE broken(id int)",
    ],
)
def test_truncated_or_unknown_dump_commands_fail_closed(suffix):
    m = module()
    with pytest.raises(m.SchemaProofError):
        m.schema_proof(BASE + suffix)


def test_complete_bridge_resolves_only_observed_old_proof(tmp_path):
    m, path, receipt = pending(tmp_path)
    assert receipt["state"] == "pending"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    old = m.legacy_schema_digest(BASE)
    with pytest.raises(m.SchemaProofError):
        m.resolve_schema_proof(
            accepted_digest=old, binding=BINDING, dump=ADDED + BASE, receipt_path=path
        )
    complete = m.complete_schema_bridge(
        path, binding=BINDING, after_dump=ADDED + BASE, policy_verified=True
    )
    assert complete["before_digest"] == old
    assert complete["after_digest"] == m.legacy_schema_digest(ADDED + BASE)
    assert m.resolve_schema_proof(
        accepted_digest=old, binding=BINDING, dump=ADDED + BASE, receipt_path=path
    ) == m.schema_proof(BASE)
    assert m.resolve_schema_proof(
        accepted_digest=old, binding=BINDING, dump=BASE, receipt_path=path
    ) == m.schema_proof(BASE)
    assert (
        m.complete_schema_bridge(
            path, binding=BINDING, after_dump=ADDED + BASE, policy_verified=True
        )
        == complete
    )


def test_failed_preproof_does_not_create_receipt(tmp_path):
    m = module()
    path = tmp_path / "bridge.json"
    with pytest.raises(m.SchemaProofError):
        m.begin_schema_bridge(path, binding=BINDING, accepted_digest="0" * 64, before_dump=BASE)
    assert not path.exists()


def test_business_change_and_unverified_policy_keep_pending(tmp_path):
    m, path, receipt = pending(tmp_path)
    for dump, verified in [
        (ADDED + BASE, False),
        (ADDED + BASE + "CREATE TABLE bad(id int);", True),
    ]:
        with pytest.raises(m.SchemaProofError):
            m.complete_schema_bridge(
                path, binding=BINDING, after_dump=dump, policy_verified=verified
            )
        assert json.loads(path.read_text()) == receipt


@pytest.mark.parametrize(
    "key,value", [("identity", "other"), ("reference", "other"), ("volume", "other"), ("epoch", 8)]
)
def test_binding_mismatch_cannot_complete_or_resolve(tmp_path, key, value):
    m, path, _ = pending(tmp_path)
    other = {**BINDING, key: value}
    with pytest.raises(m.SchemaProofError):
        m.complete_schema_bridge(path, binding=other, after_dump=ADDED + BASE, policy_verified=True)
    m.complete_schema_bridge(path, binding=BINDING, after_dump=ADDED + BASE, policy_verified=True)
    with pytest.raises(m.SchemaProofError):
        m.resolve_schema_proof(
            accepted_digest=m.legacy_schema_digest(BASE),
            binding=other,
            dump=ADDED + BASE,
            receipt_path=path,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda x: {**x, "epoch": True},
        lambda x: {**x, "epoch": -1},
        lambda x: {**x, "volume": ""},
        lambda x: {k: v for k, v in x.items() if k != "identity"},
    ],
)
def test_invalid_binding_rejected(tmp_path, change):
    m = module()
    with pytest.raises(m.SchemaProofError):
        m.begin_schema_bridge(
            tmp_path / "bridge.json",
            binding=change(BINDING),
            accepted_digest=m.legacy_schema_digest(BASE),
            before_dump=BASE,
        )


def test_completed_receipt_cannot_be_rebound_or_overwritten(tmp_path):
    m, path, _ = pending(tmp_path)
    completed = m.complete_schema_bridge(
        path, binding=BINDING, after_dump=ADDED + BASE, policy_verified=True
    )
    before = path.read_bytes()
    with pytest.raises(m.SchemaProofError):
        m.begin_schema_bridge(
            path,
            binding=BINDING,
            accepted_digest=m.legacy_schema_digest(BASE + "CREATE TABLE changed(id int);"),
            before_dump=BASE + "CREATE TABLE changed(id int);",
        )
    assert path.read_bytes() == before
    assert json.loads(before) == completed


def test_directory_sync_failure_remains_fail_closed_on_retry(tmp_path, monkeypatch):
    m = module()
    path = tmp_path / "private" / "bridge.json"
    real_fsync = os.fsync

    def fault(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic directory persistence failure")
        real_fsync(fd)

    # Create secure parent first so the fault targets receipt publication.
    path.parent.mkdir(mode=0o700)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fault)
        with pytest.raises(m.SchemaProofError):
            m.begin_schema_bridge(
                path,
                binding=BINDING,
                accepted_digest=m.legacy_schema_digest(BASE),
                before_dump=BASE,
            )
        assert path.is_file()
        for _ in range(2):
            with pytest.raises(m.SchemaProofError):
                m.begin_schema_bridge(
                    path,
                    binding=BINDING,
                    accepted_digest=m.legacy_schema_digest(BASE),
                    before_dump=BASE,
                )
    assert (
        m.begin_schema_bridge(
            path, binding=BINDING, accepted_digest=m.legacy_schema_digest(BASE), before_dump=BASE
        )["state"]
        == "pending"
    )


def test_corrupted_receipt_and_symlink_are_rejected(tmp_path):
    m, path, _ = pending(tmp_path)
    path.write_text('{"version":1,"state":"completed"}')
    with pytest.raises(m.SchemaProofError):
        m.resolve_schema_proof(
            accepted_digest=m.legacy_schema_digest(BASE),
            binding=BINDING,
            dump=ADDED + BASE,
            receipt_path=path,
        )
    path.unlink()
    target = path.parent / "target"
    target.write_text("{}")
    target.chmod(0o600)
    path.symlink_to(target)
    with pytest.raises(m.SchemaProofError):
        m.begin_schema_bridge(
            path, binding=BINDING, accepted_digest=m.legacy_schema_digest(BASE), before_dump=BASE
        )


def test_receipt_boolean_epoch_cannot_alias_integer_binding(tmp_path):
    m = module()
    binding = {**BINDING, "epoch": 1}
    path = tmp_path / "private" / "bridge.json"
    m.begin_schema_bridge(
        path, binding=binding, accepted_digest=m.legacy_schema_digest(BASE), before_dump=BASE
    )
    receipt = json.loads(path.read_text())
    receipt["binding"]["epoch"] = True
    path.write_text(json.dumps(receipt))
    with pytest.raises(m.SchemaProofError):
        m.complete_schema_bridge(
            path, binding=binding, after_dump=ADDED + BASE, policy_verified=True
        )


def test_validate_pending_pre_effect_rejects_replaced_volume_and_business_ddl(tmp_path):
    m, path, receipt = pending(tmp_path)
    assert hasattr(m, "validate_schema_bridge"), "pre-effect schema bridge validation missing"
    old = m.legacy_schema_digest(BASE)
    assert (
        m.validate_schema_bridge(path, binding=BINDING, accepted_digest=old, dump=ADDED + BASE)
        == receipt
    )
    for binding, dump in [
        ({**BINDING, "volume": "replacement-pvc"}, ADDED + BASE),
        (BINDING, ADDED + BASE + "ALTER TABLE business ADD COLUMN x text;"),
    ]:
        with pytest.raises(m.SchemaProofError):
            m.validate_schema_bridge(path, binding=binding, accepted_digest=old, dump=dump)
    assert json.loads(path.read_text()) == receipt


def test_validate_completed_pre_effect_checks_accepted_hash_without_overwriting(tmp_path):
    m, path, _ = pending(tmp_path)
    assert hasattr(m, "validate_schema_bridge"), "pre-effect schema bridge validation missing"
    completed = m.complete_schema_bridge(
        path, binding=BINDING, after_dump=ADDED + BASE, policy_verified=True
    )
    old = m.legacy_schema_digest(BASE)
    assert (
        m.validate_schema_bridge(path, binding=BINDING, accepted_digest=old, dump=ADDED + BASE)
        == completed
    )
    with pytest.raises(m.SchemaProofError):
        m.validate_schema_bridge(path, binding=BINDING, accepted_digest="0" * 64, dump=ADDED + BASE)
    assert json.loads(path.read_text()) == completed


@pytest.mark.skipif(
    os.environ.get("OMNIA_SCHEMA_PROOF_PG16_TEST") != "1", reason="owned PG16 opt-in"
)
def test_real_pg16_role_transition_and_business_change(tmp_path):
    """Only this test creates/executes/removes its unique network-none container."""
    from yleum_orchestrator.services.project_database_roles import bootstrap_project_roles_sql

    m = module()
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOCKER_")}
    name = "omnia-schema-proof-test-" + uuid4().hex
    image = "sha256:81bd698b4594e751a3269e4dcd3e03a4a0ec0daf7b72e7aa1abd43cce9887542"

    def run(args, *, sql=None):
        return subprocess.run(
            [*docker, *args],
            input=sql,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
            check=False,
        )

    def query(sql):
        return run(
            [
                "exec",
                "-u",
                "postgres",
                "-i",
                name,
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "/var/run/postgresql",
                "-U",
                "postgres",
                "-d",
                "postgres",
            ],
            sql=sql,
        )

    def dump():
        result = run(
            [
                "exec",
                "-u",
                "postgres",
                name,
                "pg_dumpall",
                "--schema-only",
                "--no-role-passwords",
                "--no-owner",
                "--no-privileges",
                "-h",
                "/var/run/postgresql",
                "-U",
                "postgres",
            ]
        )
        assert result.returncode == 0
        return result.stdout

    created = run(
        [
            "run",
            "--detach",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--label",
            "omnia.qa.schema-proof=owned",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_PASSWORD=owned-schema-proof-synthetic",
            "-e",
            "POSTGRES_INITDB_ARGS=--auth-local=peer --auth-host=scram-sha-256",
            image,
        ]
    )
    assert created.returncode == 0, "owned PostgreSQL fixture creation failed"
    try:
        deadline = time.monotonic() + 20
        while (
            run(["exec", name, "cat", "/proc/1/comm"]).stdout.strip() != "postgres"
            or query("SELECT 1").stdout.strip() != "1"
        ):
            assert time.monotonic() < deadline, "owned PostgreSQL fixture readiness failed"
            time.sleep(0.2)
        assert (
            query("SELECT current_setting('server_version_num')::int / 10000").stdout.strip()
            == "16"
        )
        assert (
            query(
                "CREATE TABLE public.business(id int); INSERT INTO business VALUES(17);"
            ).returncode
            == 0
        )
        before = dump()
        old = m.legacy_schema_digest(before)
        path = tmp_path / "private" / "bridge.json"
        m.begin_schema_bridge(path, binding=BINDING, accepted_digest=old, before_dump=before)
        assert (
            query(
                bootstrap_project_roles_sql("r" * 32, "m" * 32, admin_password="a" * 32)
            ).returncode
            == 0
        )
        after = dump()
        assert old != m.legacy_schema_digest(after)
        assert m.schema_proof(before) == m.schema_proof(after)
        assert query("SELECT id FROM business").stdout.strip() == "17"
        m.complete_schema_bridge(path, binding=BINDING, after_dump=after, policy_verified=True)
        assert m.resolve_schema_proof(
            accepted_digest=old, binding=BINDING, dump=after, receipt_path=path
        ) == m.schema_proof(before)
        assert query("ALTER TABLE business ADD COLUMN changed text;").returncode == 0
        with pytest.raises(m.SchemaProofError):
            m.resolve_schema_proof(
                accepted_digest=old, binding=BINDING, dump=dump(), receipt_path=path
            )
    finally:
        assert run(["rm", "--force", name]).returncode == 0
