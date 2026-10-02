"""Own PG16 acceptance of the actual Kubernetes shell hook, never production SQL."""

import ast
import hashlib
import io
import json
import os
import re
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_k8s_publication import _by, _container, _spec
from yleum_orchestrator.services.k8s_publication import build_objects
from yleum_orchestrator.services.project_database_credentials import ProjectDatabaseCredentials

OLD_ADMIN = "k8s-local-old-admin-synthetic-20261002"
NEW_ADMIN = "k8s-local-new-admin-synthetic-20261002"
RUNTIME = "k8s-local-runtime-synthetic-20261002-'\\"
MIGRATOR = "k8s-local-migrator-synthetic-20261002"
SECRET_PATH = "/tmp/k8s-project-role-secret"
EVIDENCE = Path("/workspace/qa-evidence/postgres-runtime-privilege-20261002/k8s-physical")


def hook_source():
    source = Path(__file__).parents[1] / "src/yleum_orchestrator/services/k8s_publication.py"
    tree = ast.parse(source.read_text())
    hook = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PROJECT_DATABASE_BOOTSTRAP_SCRIPT"
            for target in node.targets
        )
    )
    readiness = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("test -f /tmp/omnia-project-db-ready && ")
    )
    return (
        hook.replace("/omnia/project-role-bootstrap", SECRET_PATH),
        readiness,
        {
            "hook_sha256": hashlib.sha256(hook.encode()).hexdigest(),
            "k8s_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "only_path_adaptation": "/omnia/project-role-bootstrap -> " + SECRET_PATH,
        },
    )


@pytest.fixture
def owned_postgres(request):
    if os.environ.get("OMNIA_PROJECT_K8S_BOOTSTRAP_TEST") != "1":
        pytest.skip("requires explicitly enabled owned disposable PostgreSQL16")
    import docker

    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=30)
    image = client.images.get("mirror.gcr.io/library/postgres:16-alpine").id
    identity = uuid4().hex
    labels = {"omnia.qa.k8s-project-bootstrap": identity}
    objects = build_objects(
        _spec(
            postgres_image=image,
            project_postgres_password=OLD_ADMIN,
            project_credentials=ProjectDatabaseCredentials(RUNTIME, MIGRATOR, NEW_ADMIN),
        )
    )
    actual = _container(_by(objects, "StatefulSet", "project-postgres"), "postgres")
    secrets = {
        obj["metadata"]["name"]: obj["stringData"] for obj in objects if obj["kind"] == "Secret"
    }
    environment = {}
    for variable in actual["env"]:
        if "value" in variable:
            environment[variable["name"]] = variable["value"]
        else:
            ref = variable["valueFrom"]["secretKeyRef"]
            environment[variable["name"]] = secrets[ref["name"]][ref["key"]]
    container = client.containers.create(
        image,
        actual["args"],
        name="omnia-project-k8s-bootstrap-test-" + identity,
        network_mode="none",
        environment=environment,
        labels=labels,
        mem_limit=256 * 1024**2,
        detach=True,
    )
    hook, readiness, proof = hook_source()
    proof.update(
        {
            "container": container.name,
            "image_id": image,
            "network": "none",
            "published_ports": False,
            "production_access": False,
            "actual_builder_args_and_secret_env": True,
            "actual_PGHOST": environment.get("PGHOST"),
        }
    )

    def query(sql, *, role="postgres", password=None):
        args = [
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "VERBOSITY=sqlstate",
            "-U",
            role,
            "-d",
            "postgres",
        ]
        env = {}
        if password is None:
            args += ["-h", "/tmp"]
            # Trusted inspection stays usable after testing hostile legacy defaults.
            env["PGOPTIONS"] = (
                "-c search_path=pg_catalog,public -c local_preload_libraries= "
                "-c session_preload_libraries= -c log_statement=none"
            )
        else:
            args += ["-h", "127.0.0.1"]
            env["PGPASSWORD"] = password
        return container.exec_run([*args, "-c", sql], user="postgres", environment=env)

    def wait_server():
        for _ in range(100):
            # Official initdb temporarily starts a different server while PID1
            # remains its entrypoint shell. Do not mistake it for stable startup.
            main = container.exec_run(["cat", "/proc/1/comm"], user="postgres")
            if (
                main.exit_code == 0
                and main.output.strip() == b"postgres"
                and container.exec_run(
                    ["pg_isready", "-q", "-U", "postgres", "-h", "/tmp", "-d", "postgres"],
                    user="postgres",
                ).exit_code
                == 0
            ):
                return
            time.sleep(0.1)
        raise AssertionError("owned disposable PostgreSQL did not become ready")

    def install_secret():
        assert container.exec_run(["mkdir", "-p", SECRET_PATH]).exit_code == 0
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            entries = secrets["project-role-bootstrap"]
            for name, value in entries.items():
                payload = value.encode()
                entry = tarfile.TarInfo(name)
                entry.size, entry.uid, entry.gid, entry.mode = len(payload), 70, 70, 0o440
                archive.addfile(entry, io.BytesIO(payload))
        assert container.put_archive(SECRET_PATH, stream.getvalue())

    def run_hook():
        return container.exec_run(["sh", "-ec", hook], user="postgres")

    def ready():
        return container.exec_run(["sh", "-ec", readiness], user="postgres").exit_code == 0

    try:
        container.start()
        wait_server()
        install_secret()
        yield SimpleNamespace(
            container=container,
            query=query,
            hook=run_hook,
            ready=ready,
            wait=wait_server,
            proof=proof,
        )
    finally:
        container.reload()
        assert container.labels.get("omnia.qa.k8s-project-bootstrap") == identity
        container.remove(force=True, v=True)
        client.close()
        proof["owned_container_and_anonymous_pgdata_removed"] = True
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name)
        (EVIDENCE / (name + ".json")).write_text(json.dumps(proof, indent=2) + "\n")


@pytest.mark.parametrize("retained", [False, True], ids=["fresh", "retained"])
def test_actual_hook_credentials_crud_denials_and_legacy_shadow(owned_postgres, retained):
    pg = owned_postgres
    q = pg.query
    if retained:
        assert (
            q(
                "CREATE TABLE public.business(id serial PRIMARY KEY,value text);"
                "INSERT INTO public.business(value) VALUES('seeded-retained');"
                "CREATE TABLE public.shadow_probe(touches int);"
                "INSERT INTO public.shadow_probe VALUES(0);"
                "CREATE FUNCTION public.pg_reload_conf() RETURNS boolean LANGUAGE plpgsql AS "
                "'BEGIN UPDATE public.shadow_probe SET touches=touches+1; RETURN true; END';"
                f"ALTER ROLE postgres PASSWORD '{OLD_ADMIN}';"
                "ALTER ROLE postgres SET search_path='public,pg_catalog';"
                "ALTER ROLE postgres SET log_statement='all';"
                "ALTER ROLE postgres SET log_duration='on';"
                "ALTER ROLE postgres SET log_min_duration_statement=0;"
                "ALTER ROLE postgres SET log_min_error_statement='error';"
                "ALTER ROLE postgres SET local_preload_libraries='missing_legacy_test_library';"
            ).exit_code
            == 0
        )
        # Same raw PGDATA now takes the official entrypoint's retained branch.
        pg.container.restart(timeout=5)
        pg.wait()
    assert not pg.ready()
    result = pg.hook()
    assert result.exit_code == 0, "actual project-postgres shell bootstrap failed"
    assert pg.ready()
    runtime = dict(role="omnia_project_runtime", password=RUNTIME)
    migrator = dict(role="omnia_project_migrator", password=MIGRATOR)
    attrs = q(
        "SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user",
        **runtime,
    )
    assert attrs.exit_code == 0 and attrs.output.strip() == b"omnia_project_runtime|f|f|f|f|f"
    if retained:
        assert (
            q("SELECT value FROM public.business", **runtime).output.strip() == b"seeded-retained"
        )
        assert q("SELECT touches FROM public.shadow_probe").output.strip() == b"0"
    else:
        assert (
            q(
                "CREATE TABLE public.business(id serial PRIMARY KEY,value text)", **migrator
            ).exit_code
            == 0
        )
    assert (
        q(
            "INSERT INTO public.business(value) VALUES('crud');"
            "UPDATE public.business SET value='updated' WHERE value='crud';"
            "DELETE FROM public.business WHERE value='updated';",
            **runtime,
        ).exit_code
        == 0
    )
    for password in (OLD_ADMIN, NEW_ADMIN):
        denied = q("SELECT 1", role="postgres", password=password)
        assert denied.exit_code != 0 and b"pg_hba.conf rejects connection" in denied.output
    for sql in (
        "TRUNCATE public.business",
        "ALTER ROLE postgres NOSUPERUSER",
        "CREATE ROLE forbidden_k8s_role",
    ):
        denied = q(sql, **runtime)
        assert denied.exit_code != 0 and b"42501" in denied.output
    logs = pg.container.logs()
    assert all(secret.encode() not in logs for secret in (NEW_ADMIN, RUNTIME, MIGRATOR))
    pg.proof.update(
        {
            "case": "retained" if retained else "fresh",
            "hook_exit": result.exit_code,
            "actual_readiness": True,
            "runtime_login_and_crud": True,
            "old_and_new_admin_tcp_denied": True,
            "runtime_admin_sql_denied": True,
            "elevated_runtime_attributes": False,
            "legacy_shadow_touches": 0 if retained else None,
            "secret_values_absent_from_server_logs": True,
        }
    )


def test_failed_rebootstrap_removes_marker_and_readiness(owned_postgres):
    pg = owned_postgres
    assert pg.hook().exit_code == 0 and pg.ready()
    assert (
        pg.query(
            "GRANT EXECUTE ON FUNCTION pg_catalog.pg_read_file(text) TO omnia_project_runtime"
        ).exit_code
        == 0
    )
    result = pg.hook()
    assert result.exit_code != 0
    assert not pg.ready()
    assert pg.container.exec_run(["test", "-f", "/tmp/omnia-project-db-ready"]).exit_code != 0
    pg.proof.update(
        {
            "case": "unsafe-retained-ACL",
            "hook_exit": result.exit_code,
            "readiness_after_failure": False,
            "old_marker_removed": True,
        }
    )


def test_restart_retains_tmp_but_actual_poststart_reconciles_it(owned_postgres):
    pg = owned_postgres
    assert pg.hook().exit_code == 0 and pg.ready()
    pg.container.restart(timeout=5)
    pg.wait()
    # Docker does not automatically invoke the Kubernetes lifecycle handler.
    # This controlled interleaving describes stale file state, not a proven K8s race.
    marker_survives = (
        pg.container.exec_run(["test", "-f", "/tmp/omnia-project-db-ready"]).exit_code == 0
    )
    assert marker_survives
    assert pg.hook().exit_code == 0 and pg.ready()
    pg.proof.update(
        {
            "case": "container-restart-same-pod-tmp",
            "tmp_marker_survived_restart": True,
            "actual_poststart_rebootstrap": True,
            "readiness_after_hook": True,
            "real_kubernetes_readiness_race_proven": False,
        }
    )


def test_retained_alternative_hba_cannot_override_actual_builder_policy(owned_postgres):
    pg = owned_postgres
    data = pg.query("SHOW data_directory")
    assert data.exit_code == 0
    pgdata = data.output.decode().strip()
    legacy_hba = pgdata + "/legacy-permissive-hba.conf"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        payload = b"local all postgres peer\nhost all all 127.0.0.1/32 trust\n"
        entry = tarfile.TarInfo("legacy-permissive-hba.conf")
        entry.size, entry.uid, entry.gid, entry.mode = len(payload), 70, 70, 0o600
        archive.addfile(entry, io.BytesIO(payload))
    assert pg.container.put_archive(pgdata, stream.getvalue())
    assert (
        pg.query(
            "CREATE TABLE public.business(id serial PRIMARY KEY,value text);"
            "INSERT INTO public.business(value) VALUES('seeded-retained-hba')"
        ).exit_code
        == 0
    )
    assert pg.query(f"ALTER SYSTEM SET hba_file='{legacy_hba}'").exit_code == 0
    pg.container.restart(timeout=5)
    pg.wait()
    assert pg.hook().exit_code == 0 and pg.ready()
    runtime = dict(role="omnia_project_runtime", password=RUNTIME)
    assert (
        pg.query("SELECT value FROM public.business", **runtime).output.strip()
        == b"seeded-retained-hba"
    )
    attrs = pg.query(
        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=current_user",
        **runtime,
    )
    assert attrs.exit_code == 0 and attrs.output.strip() == b"f|f|f|f|f"
    assert (
        pg.query(
            "INSERT INTO public.business(value) VALUES('crud');"
            "UPDATE public.business SET value='updated' WHERE value='crud';"
            "DELETE FROM public.business WHERE value='updated';",
            **runtime,
        ).exit_code
        == 0
    )
    for password in (OLD_ADMIN, NEW_ADMIN):
        denied = pg.query("SELECT 1", role="postgres", password=password)
        assert denied.exit_code != 0 and b"pg_hba.conf rejects connection" in denied.output
    assert pg.query("SHOW hba_file").output.strip() == (pgdata + "/pg_hba.conf").encode()
    denied = pg.query("TRUNCATE public.business", **runtime)
    assert denied.exit_code != 0 and b"42501" in denied.output
    pg.proof.update(
        {
            "case": "retained-alternative-hba",
            "legacy_alternative_hba_configured_before_restart": True,
            "canonical_hba_overrides_retained_configuration": True,
            "actual_readiness": True,
            "seeded_retained_row_preserved": True,
            "runtime_login_and_crud": True,
            "elevated_runtime_attributes": False,
            "old_and_new_admin_tcp_denied": True,
            "runtime_truncate_denied": True,
        }
    )
