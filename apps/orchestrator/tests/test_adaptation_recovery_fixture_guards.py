"""Pure capture of actual production container vectors; no daemon/socket/network."""

import json
from types import SimpleNamespace

import docker
import pytest

from tests._task9_recovery_docker_fixture import POSTGRES_IMAGE, PYTHON_IMAGE, FixtureDocker
from tests.test_docker_machine_backend import backend
from tests.test_project_machine_manifest import payload
from yleum_orchestrator.core.project_machine import MachineManifest


class Captured(Exception):
    pass


def fixture(tmp_path):
    runtime = backend(
        tmp_path,
        base_image=PYTHON_IMAGE,
        guard_image=PYTHON_IMAGE,
        postgres_image=POSTGRES_IMAGE,
        namespace="test",
    )
    values = []
    guard = SimpleNamespace(
        id="a" * 64, status="running", labels={"omnia.resource_kind": "namespace-guard"}
    )

    def get(name):
        if name == guard.id:
            return guard
        raise docker.errors.NotFound("pure absent")

    def create(image, command, **kwargs):
        values.append((image, command, kwargs))
        raise Captured()

    raw = SimpleNamespace(
        images=SimpleNamespace(get=lambda image: SimpleNamespace(id=image)),
        containers=SimpleNamespace(get=get, create=create),
        volumes=SimpleNamespace(get=lambda name: SimpleNamespace(name=name)),
        info=lambda: {"ID": "pure"},
    )
    scope = FixtureDocker(raw, nonce="fixture-nonce", backend=runtime)
    scope.check_container = lambda value: None
    scope.check_volume = lambda value: None
    scope.guard_ids.add(guard.id)
    manifest = MachineManifest.model_validate(payload())
    runtime_options = runtime.container_options(manifest, guard.id, 1)
    pg_options = runtime._project_postgres_options(guard.id, 1)
    scope.volumes_owned = {
        name: {} for name in set(runtime_options["volumes"]) | set(pg_options["volumes"])
    }
    scope.allowed_environments = {
        "development": runtime_options["environment"],
        "project-postgres": pg_options["environment"],
    }
    return scope, runtime, runtime_options, pg_options, values


def test_actual_runtime_and_postgres_vectors_pass_offline_guards(tmp_path):
    scope, _runtime, dev, pg, values = fixture(tmp_path)
    for image, options in ((PYTHON_IMAGE, dev), (POSTGRES_IMAGE, pg)):
        with pytest.raises(Captured):
            scope.containers.create(image, **options)
    assert len(values) == 2
    assert all("pull" not in row[2] for row in values)


@pytest.mark.parametrize(
    "key,value",
    [
        ("network_mode", "host"),
        ("ports", {80: 80}),
        ("privileged", True),
        ("volumes", {"/var/run/docker.sock": {"bind": "/socket", "mode": "rw"}}),
        ("environment", {"UNTRUSTED": "never forward"}),
        ("cap_add", ["SYS_ADMIN"]),
        ("labels", {"omnia.owner_id": "foreign"}),
        ("pid_mode", "host"),
        ("mounts", ["unowned"]),
        ("extra_hosts", {"host": "host-gateway"}),
    ],
)
def test_host_foreign_or_ambient_vectors_deny_before_create(tmp_path, key, value):
    scope, _runtime, dev, _pg, values = fixture(tmp_path)
    with pytest.raises(RuntimeError):
        scope.containers.create(PYTHON_IMAGE, **{**dev, key: value})
    assert values == []


def test_transport_adapter_changes_only_guard_proxy_network_commands(tmp_path):
    scope, runtime, _dev, _pg, values = fixture(tmp_path)
    for kind, suffix in (("namespace-guard", "guard"), ("egress-proxy", "proxy")):
        labels = runtime.labels(kind)
        if kind == "namespace-guard":
            labels["omnia.policy_digest"] = "b" * 64
        with pytest.raises(Captured):
            scope.containers.create(
                PYTHON_IMAGE,
                ["unused-transport"],
                name=runtime.stem + "-" + suffix,
                labels=labels,
                network="logical-only",
                cap_drop=["ALL"],
                cap_add=["NET_ADMIN"],
                privileged=False,
                security_opt=["no-new-privileges:true"],
                read_only=True,
            )
    assert len(values) == 2
    assert all(
        row[2]["network_mode"] == "none" and not row[2]["cap_add"] and "network" not in row[2]
        for row in values
    )


def test_foreign_inventory_cannot_be_mutated(tmp_path):
    scope, _runtime, _dev, _pg, _values = fixture(tmp_path)
    calls = []
    foreign = SimpleNamespace(
        id="f" * 64, reload=lambda: None, remove=lambda **kw: calls.append(kw)
    )
    scope.raw.containers.list = lambda **kwargs: [foreign]
    scope.check_container = lambda value: (_ for _ in ()).throw(RuntimeError("foreign"))
    with pytest.raises(RuntimeError, match="foreign"):
        scope.containers.list(all=True)[0].remove(force=True)
    assert calls == []


def test_unowned_exec_never_reaches_daemon(tmp_path):
    scope, _runtime, _dev, _pg, _values = fixture(tmp_path)
    calls = []
    scope.raw.api = SimpleNamespace(exec_start=lambda *a, **kw: calls.append(a))
    with pytest.raises(RuntimeError, match="unowned exec"):
        scope.api.exec_start("f" * 64)
    assert calls == []


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("Config", "Env", ["ambient=value"]),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "PidMode", "host"),
    ],
)
def test_post_creation_manifest_drift_denies_mutation(tmp_path, section, key, value):
    from copy import deepcopy

    scope, _runtime, _dev, _pg, _values = fixture(tmp_path)
    labels = {"task9.recovery_fixture": scope.nonce}
    attrs = {
        "Image": PYTHON_IMAGE,
        "Config": {"Labels": labels, "Env": []},
        "HostConfig": {"NetworkMode": "none", "CapAdd": []},
        "Mounts": [],
    }
    scope.containers_owned["a" * 64] = {
        "name": "own",
        "image": PYTHON_IMAGE,
        "labels": labels,
        "network": "none",
        "mounts": {},
        "config": deepcopy(attrs["Config"]),
        "host_config": deepcopy(attrs["HostConfig"]),
    }
    attrs[section][key] = value
    raw = SimpleNamespace(id="a" * 64, name="own", attrs=attrs, reload=lambda: None)
    with pytest.raises(RuntimeError, match="execution manifest changed"):
        FixtureDocker.check_container(scope, raw)


async def test_halt_ack_observer_counts_new_adapter_and_injects_loss_only_once(monkeypatch):
    from tests.test_adaptation_recovery_docker import _observe_halt_ack_loss
    from yleum_orchestrator.services.machine_adapter import MachineAdapter

    effects = []

    async def actual_halt_stub(adapter, state, **kwargs):
        effects.append((adapter, state, kwargs))

    # Observe the actual class dispatch shape, with no Docker/DB effect in this pure test.
    monkeypatch.setattr(MachineAdapter, "halt", actual_halt_stub)
    calls = _observe_halt_ack_loss(monkeypatch)
    first, cold = object.__new__(MachineAdapter), object.__new__(MachineAdapter)
    state = object()
    with pytest.raises(RuntimeError, match="fixture lost halt acknowledgement"):
        await first.halt(state, capture=True)
    assert calls == [first] and effects == [(first, state, {"capture": True})]
    # A recreated adapter must reach both observer and original method, without another ACK loss.
    await cold.halt(state, capture=False)
    assert calls == [first, cold]
    assert effects == [(first, state, {"capture": True}), (cold, state, {"capture": False})]
    with pytest.raises(RuntimeError, match="unknown halt replayed"):
        from tests._task9_recovery_docker_fixture import require
        require(len(calls) == 1, "unknown halt replayed")


def test_manifest_diagnostic_reports_only_field_types_and_never_secret_values():
    from tests._task9_recovery_docker_fixture import ExecutionManifestMismatch, safe_fixture_error

    secrets = ["private-password-marker", "private-command-marker", "private-arg-marker"]
    error = ExecutionManifestMismatch(
        {"Env": [secrets[0]], "Cmd": [secrets[1]], "Args": [secrets[2]]},
        {"Env": ["different-credential"], "Cmd": [], "Args": []},
        {"Dns": None, "LogConfig": {"Config": {"token": secrets[0]}}},
        {"Dns": [], "LogConfig": {"Config": {"token": "different-credential"}}},
    )
    message = safe_fixture_error(error)
    assert "Config.Env:list->list" in message and "HostConfig.Dns:null->list" in message
    assert "Config.Cmd:list->list" in message and "Config.Args:list->list" in message
    assert "HostConfig.LogConfig:dict->dict" in message
    assert all(value not in message for value in [*secrets, "different-credential", "token"])
    assert safe_fixture_error(RuntimeError(secrets[0])) == "RuntimeError"


@pytest.mark.parametrize("before,after,allowed", [
    (False, None, True), (None, False, True), (False, False, True), (None, None, True),
    (True, None, False), (False, True, False), (True, True, False),
    (0, None, False), (False, 0, False), ("false", None, False),
])
def test_oom_inspect_default_only_canonicalization(tmp_path, before, after, allowed):
    from copy import deepcopy

    scope, _runtime, _dev, _pg, _values = fixture(tmp_path)
    labels = {"task9.recovery_fixture": scope.nonce}
    config = {"Labels": labels, "Env": []}
    host = {"NetworkMode": "none", "OomKillDisable": before}
    scope.containers_owned["a" * 64] = {
        "name": "own", "image": PYTHON_IMAGE, "labels": labels,
        "network": "none", "mounts": {}, "config": deepcopy(config),
        "host_config": deepcopy(host),
    }
    attrs = {"Image": PYTHON_IMAGE, "Config": config,
             "HostConfig": {**host, "OomKillDisable": after}, "Mounts": []}
    raw = SimpleNamespace(id="a" * 64, name="own", attrs=attrs, reload=lambda: None)
    if allowed:
        FixtureDocker.check_container(scope, raw)
        # Canonical comparison must not mutate the captured immutable evidence.
        assert scope.containers_owned[raw.id]["host_config"]["OomKillDisable"] is before
        assert attrs["HostConfig"]["OomKillDisable"] is after
        attrs["HostConfig"]["FutureField"] = None
        with pytest.raises(RuntimeError, match="execution manifest changed"):
            FixtureDocker.check_container(scope, raw)
    else:
        with pytest.raises(RuntimeError):
            FixtureDocker.check_container(scope, raw)


def test_explicit_oom_override_denied_before_create(tmp_path):
    scope, _runtime, dev, _pg, values = fixture(tmp_path)
    for value in (False, None, True):
        with pytest.raises(RuntimeError, match="unsupported container option"):
            scope.containers.create(PYTHON_IMAGE, **{**dev, "oom_kill_disable": value})
    assert values == []


@pytest.mark.parametrize("case", [
    "created_own", "created_parent", "running_parent", "exited_parent",
    "running_own", "foreign_hostname", "missing_hostname", "missing_original_hostname",
    "foreign_original_hostname", "foreign_parent", "missing_parent", "alias_parent",
    "parent_config_drift", "parent_host_drift", "parent_label_drift", "parent_wrong_kind",
    "parent_nondefault_hostname", "parent_nested_network", "child_other_config_drift",
    "child_other_host_drift", "unknown_child_kind",
])
def test_namespace_hostname_requires_exact_fresh_owned_parent(tmp_path, case):
    from copy import deepcopy

    scope, _runtime, _dev, _pg, _values = fixture(tmp_path)
    del scope.check_container  # Exercise the full real guard, including parent revalidation.
    parent_id, child_id = "a" * 64, "b" * 64

    def container(cid, kind, network, hostname):
        labels = {"task9.recovery_fixture": scope.nonce, "omnia.resource_kind": kind}
        config = {"Labels": labels, "Env": [], "Hostname": hostname}
        host = {"NetworkMode": network, "OomKillDisable": False}
        scope.containers_owned[cid] = {
            "name": cid[:12], "image": PYTHON_IMAGE, "labels": deepcopy(labels),
            "network": network, "mounts": {}, "config": deepcopy(config),
            "host_config": deepcopy(host),
        }
        return SimpleNamespace(id=cid, name=cid[:12], status="running",
            attrs={"Image": PYTHON_IMAGE, "Config": config, "HostConfig": host, "Mounts": []},
            reload=lambda: None)

    parent = container(parent_id, "namespace-guard", "none", parent_id[:12])
    child = container(child_id, "project-postgres", "container:" + parent_id, child_id[:12])
    scope.guard_ids = {parent_id}
    scope.raw.containers.get = lambda value: parent
    cfg, host = child.attrs["Config"], child.attrs["HostConfig"]
    expected = scope.containers_owned[child_id]
    cfg["Hostname"] = parent_id[:12]
    host["OomKillDisable"] = None
    if case.startswith("created_"):
        child.status = "created"
        cfg["Hostname"] = child_id[:12] if case == "created_own" else parent_id[:12]
    elif case == "exited_parent":
        child.status = "exited"
    elif case == "running_own":
        cfg["Hostname"] = child_id[:12]
    elif case == "foreign_hostname":
        cfg["Hostname"] = "foreign"
    elif case == "missing_hostname":
        cfg.pop("Hostname")
    elif case == "missing_original_hostname":
        expected["config"].pop("Hostname")
    elif case == "foreign_original_hostname":
        expected["config"]["Hostname"] = "foreign"
    elif case == "foreign_parent":
        scope.guard_ids.clear()
    elif case == "missing_parent":
        scope.raw.containers.get = lambda value: (_ for _ in ()).throw(
            docker.errors.NotFound("absent")
        )
    elif case == "alias_parent":
        parent.id = "c" * 64
    elif case == "parent_config_drift":
        parent.attrs["Config"]["Env"] = ["foreign"]
    elif case == "parent_host_drift":
        parent.attrs["HostConfig"]["FutureField"] = None
    elif case == "parent_label_drift":
        parent.attrs["Config"]["Labels"]["task9.recovery_fixture"] = "other"
    elif case == "parent_wrong_kind":
        parent.attrs["Config"]["Labels"]["omnia.resource_kind"] = "development"
    elif case == "parent_nondefault_hostname":
        parent.attrs["Config"]["Hostname"] = "foreign"
        scope.containers_owned[parent_id]["config"]["Hostname"] = "foreign"
    elif case == "parent_nested_network":
        parent.attrs["HostConfig"]["NetworkMode"] = "container:" + child_id
        scope.containers_owned[parent_id]["network"] = "container:" + child_id
    elif case == "child_other_config_drift":
        cfg["FutureField"] = None
    elif case == "child_other_host_drift":
        host["FutureField"] = None
    elif case == "unknown_child_kind":
        cfg["Labels"]["omnia.resource_kind"] = "unknown"
        expected["labels"]["omnia.resource_kind"] = "unknown"
        expected["config"]["Labels"]["omnia.resource_kind"] = "unknown"
    original_attrs, original_known = deepcopy(child.attrs), deepcopy(scope.containers_owned)
    if case in {"created_own", "created_parent", "running_parent", "exited_parent"}:
        scope.check_container(child)
        assert child.attrs == original_attrs and scope.containers_owned == original_known
    else:
        with pytest.raises((RuntimeError, docker.errors.NotFound)):
            scope.check_container(child)


@pytest.mark.parametrize("message", ["private-password-marker", "private-command-marker"])
def test_runtime_diagnostic_has_only_allowed_frame_coordinates(message):
    from tests._task9_recovery_docker_fixture import require, safe_fixture_error

    try:
        require(False, message)
    except RuntimeError as exc:
        diagnostic = safe_fixture_error(exc)
    assert diagnostic.startswith("RuntimeError at fixture_guard:")
    assert message not in diagnostic and "fixture guard:" not in diagnostic
    assert diagnostic.removeprefix("RuntimeError at fixture_guard:").isdigit()


def test_runtime_diagnostic_discards_untrusted_traceback_filename_and_message():
    from tests._task9_recovery_docker_fixture import safe_fixture_error

    code = compile("raise RuntimeError('private-payload-marker')", "private-path-marker", "exec")
    try:
        exec(code, {})
    except RuntimeError as exc:
        diagnostic = safe_fixture_error(exc)
    assert diagnostic == "RuntimeError"



def test_database_fixture_admin_probes_follow_actual_peer_only_hba():
    from tests.test_adaptation_recovery_docker import _database_evidence, _sql
    from yleum_orchestrator.services.project_database_roles import project_role_hba

    hba = project_role_hba().splitlines()
    assert hba[0] == "local all postgres peer"
    assert "host all all 0.0.0.0/0 reject" in hba
    calls = []

    def execute(argv, *, user, environment):
        calls.append((argv, user, environment))
        # Mirror the actual HBA transport boundary, not successful authentication via TCP.
        if user != "postgres" or argv[argv.index("-h") + 1] != "/tmp" or environment:
            return SimpleNamespace(exit_code=2, output=b"private-denial-never-print")
        if argv[0] == "pg_dump":
            output = b"schema fixture"
        elif "json_build_object" in argv[-1]:
            output = b'BEGIN\n{"count":3,"rows":"' + b"a" * 64 + b'"}\nCOMMIT\n'
        else:
            output = b"3|t\n"
        return SimpleNamespace(exit_code=0, output=output)

    backend = SimpleNamespace(_project_postgres=lambda: SimpleNamespace(exec_run=execute),
                              project_postgres_password="must-not-forward")
    _sql(backend, "SELECT 1")
    result = _database_evidence(backend, "fixed-private-fixture-nonce")
    assert result["rows"]["count"] == 3 and len(calls) == 4
    assert {call[0][0] for call in calls} == {"psql", "pg_dump"}
    assert all(user == "postgres" and environment == {} for _, user, environment in calls)


@pytest.mark.parametrize("component", [
    "rows", "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
])
def test_database_diagnostic_preserves_strict_failure_without_private_values(component, capsys):
    from tests.test_adaptation_recovery_docker import _assert_database_preserved

    baseline = {name: "a" * 64 for name in (
        "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
    )}
    baseline["rows"] = {"count": 3, "rows": "a" * 64}
    actual = dict(baseline)
    actual[component] = {"count": 3, "rows": "b" * 64} if component == "rows" else "b" * 64
    with pytest.raises(RuntimeError):
        _assert_database_preserved(
            baseline, actual, "c" * 64, "c" * 64,
            before_source="d" * 64, after_source="d" * 64, recopy_count=0,
        )
    output = json.loads(capsys.readouterr().out)
    assert output["database_preservation"][component]["equal"] is False
    assert output["all_volume_incarnations"]["equal"] is True
    assert output["row_count_equal"] is True


def test_database_diagnostic_volume_replacement_denied_with_equal_data(capsys):
    from tests.test_adaptation_recovery_docker import _assert_database_preserved

    evidence = {name: "a" * 64 for name in (
        "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
    )}
    evidence["rows"] = {"count": 3, "rows": "a" * 64}
    with pytest.raises(RuntimeError):
        _assert_database_preserved(
            evidence, evidence, "b" * 64, "c" * 64,
            before_source="d" * 64, after_source="d" * 64, recopy_count=0,
        )
    assert json.loads(capsys.readouterr().out)["all_volume_incarnations"]["equal"] is False


@pytest.mark.parametrize("mutation", ["unknown_key", "raw_digest", "raw_rows", "missing_key"])
def test_database_diagnostic_rejects_malformed_evidence_without_output(mutation, capsys):
    from tests.test_adaptation_recovery_docker import _assert_database_preserved

    evidence = {name: "a" * 64 for name in (
        "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
    )}
    evidence["rows"] = {"count": 3, "rows": "a" * 64}
    bad = dict(evidence)
    if mutation == "unknown_key":
        bad["secret-key"] = "secret-value"
    elif mutation == "raw_digest":
        bad["schema"] = "secret-value"
    elif mutation == "raw_rows":
        bad["rows"] = {"count": 3, "rows": "secret-value"}
    else:
        del bad["sequence"]
    with pytest.raises(RuntimeError) as caught:
        _assert_database_preserved(
            evidence, bad, "b" * 64, "b" * 64,
            before_source="d" * 64, after_source="d" * 64, recopy_count=0,
        )
    assert "secret" not in str(caught.value)
    assert capsys.readouterr().out == ""


def test_schema_diagnostic_partitions_do_not_normalize_raw_schema():
    from tests.test_adaptation_recovery_docker import _schema_diagnostics

    baseline = b"-- private-comment-one\n\\restrict private-key\nCREATE TABLE private_name();\n"
    changed = baseline.replace(b"private-comment-one", b"private-comment-two")
    first, second = _schema_diagnostics(baseline), _schema_diagnostics(changed)
    assert first["schema_comments"] != second["schema_comments"]
    assert first["schema_body"] == second["schema_body"]
    assert first["schema_restrict"] == second["schema_restrict"]
    assert "private" not in str(first)


@pytest.mark.parametrize("changed", ["none", "source", "recopy", "count", "all"])
def test_database_diagnostic_collects_all_invariants_before_failure(changed, capsys):
    from tests.test_adaptation_recovery_docker import _assert_database_preserved

    before = {name: "a" * 64 for name in (
        "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
    )}
    before["rows"] = {"count": 3, "rows": "a" * 64}
    after = dict(before)
    if changed in {"count", "all"}:
        after["rows"] = {"count": 4, "rows": "a" * 64}
    if changed == "all":
        after["schema"] = "e" * 64
    kwargs = dict(
        before_source="c" * 64,
        after_source=("d" if changed in {"source", "all"} else "c") * 64,
        recopy_count=int(changed in {"recopy", "all"}),
    )
    if changed == "none":
        _assert_database_preserved(before, after, "b" * 64, "b" * 64, **kwargs)
    else:
        with pytest.raises(RuntimeError):
            _assert_database_preserved(before, after, "b" * 64, "b" * 64, **kwargs)
    result = json.loads(capsys.readouterr().out)
    assert result["source_files"]["equal"] == (changed not in {"source", "all"})
    assert result["recopy_zero"] == (changed not in {"recopy", "all"})
    assert result["row_count_equal"] == (changed not in {"count", "all"})
    assert result["row_multiset_equal"] is True
    assert result["database_preservation"]["schema"]["equal"] == (changed != "all")


def test_empty_post_restore_database_is_reported_not_hidden_by_baseline_guard(monkeypatch, capsys):
    import tests.test_adaptation_recovery_docker as target

    monkeypatch.setattr(target, "_sql", lambda _backend, sql:
                        b'BEGIN\n{"count":0,"rows":null}\nCOMMIT\n'
                        if "json_build_object" in sql else b"3|t\n")
    monkeypatch.setattr(target, "_postgres", lambda *_: b"unchanged schema")
    after = target._database_evidence(None, "fixed-nonce")
    before = {**after, "rows": {"count": 3, "rows": "a" * 64}}
    with pytest.raises(RuntimeError):
        target._assert_database_preserved(
            before, after, "b" * 64, "b" * 64,
            before_source="c" * 64, after_source="c" * 64, recopy_count=0,
        )
    result = json.loads(capsys.readouterr().out)
    assert result["row_count_equal"] is False
    assert result["row_multiset_equal"] is False
    assert result["source_files"]["equal"] is True
    assert result["database_preservation"]["schema"]["equal"] is True


@pytest.mark.parametrize("failure", [None, "probe_owner", "probe_acl", "promoted_owner",
                                     "promoted_acl", "seed_owner", "seed_acl",
                                     "catalog_drift", "schema_drift"])
async def test_owner_seed_prelude_uses_real_bootstrap_order_and_denies_drift(monkeypatch, failure):
    import tests.test_adaptation_recovery_docker as target
    from yleum_orchestrator.services.project_database_roles import PROJECT_OWNER_ROLE

    events = []
    catalog_calls = 0
    evidence_calls = 0

    def catalog(_backend, *, probe):
        nonlocal catalog_calls
        catalog_calls += 1
        events.append(("catalog", probe))
        promoted = catalog_calls > 1
        result = {kind: {"owner_postgres": not promoted, "owner_project": promoted,
                         "runtime_acl": promoted, "owner_sha256": ("a" if promoted else "b") * 64,
                         "acl_sha256": ("c" if promoted else "d") * 64}
                  for kind in ("table", "sequence")}
        bad = {1: {"probe_owner": "owner_postgres", "probe_acl": "runtime_acl"},
               2: {"promoted_owner": "owner_project", "promoted_acl": "runtime_acl"},
               3: {"seed_owner": "owner_project", "seed_acl": "runtime_acl"}}
        field = bad.get(catalog_calls, {}).get(failure)
        if field:
            result["table"][field] = not result["table"][field]
        if catalog_calls == 4 and failure == "catalog_drift":
            result["table"]["acl_sha256"] = "e" * 64
        return result

    def evidence(_backend, _nonce):
        nonlocal evidence_calls
        evidence_calls += 1
        events.append(("evidence",))
        result = {name: "c" * 64 for name in (
            "schema", "sequence", "schema_comments", "schema_body", "schema_restrict"
        )}
        result["rows"] = {"count": 3, "rows": "a" * 64}
        if failure == "schema_drift" and evidence_calls == 2:
            result["schema"] = "d" * 64
        return result

    def sql(_backend, statement):
        if "recovery_owner_probe" in statement:
            assert "SET LOCAL ROLE" not in statement
            events.append(("probe_sql",))
        else:
            assert statement.strip().startswith("BEGIN; SET LOCAL ROLE " + PROJECT_OWNER_ROLE + ";")
            assert statement.strip().endswith("COMMIT;")
            assert "CREATE TABLE public.recovery_fixture" in statement
            events.append(("seed_sql",))
        return b""

    def bootstrap():
        events.append(("bootstrap",))

    monkeypatch.setattr(target, "_role_catalog", catalog)
    monkeypatch.setattr(target, "_database_evidence", evidence)
    monkeypatch.setattr(target, "_sql", sql)
    backend = SimpleNamespace(bootstrap_project_database_roles=bootstrap)
    if failure:
        with pytest.raises(RuntimeError):
            await target._seed_database_fixture(backend, "fixed-nonce")
    else:
        result = await target._seed_database_fixture(backend, "fixed-nonce")
        assert result["rows"]["count"] == 3
        assert events == [("probe_sql",), ("catalog", True), ("bootstrap",),
                          ("catalog", True), ("seed_sql",), ("catalog", False),
                          ("evidence",), ("bootstrap",), ("catalog", False), ("evidence",)]


@pytest.mark.parametrize("bad", [{}, {"secret-name": "secret-value"},
                                 {"table": {}, "sequence": {}},
                                 {"table": {"owner_project": "secret-value"}}])
def test_role_catalog_malformed_payload_fails_without_private_output(monkeypatch, capsys, bad):
    import tests.test_adaptation_recovery_docker as target

    monkeypatch.setattr(target, "_sql", lambda *_: b"BEGIN\n" + json.dumps(bad).encode()
                        + b"\nCOMMIT\n")
    with pytest.raises(RuntimeError) as caught:
        target._role_catalog(None, probe=True)
    assert "secret" not in str(caught.value)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("probe", [True, False])
def test_role_catalog_fixed_scope_readonly_query_and_safe_return(monkeypatch, probe):
    import tests.test_adaptation_recovery_docker as target
    from yleum_orchestrator.services.project_database_roles import (
        PROJECT_OWNER_ROLE,
        PROJECT_RUNTIME_ROLE,
    )

    expected = {kind: {"owner_postgres": False, "owner_project": True, "runtime_acl": True,
                       "owner_sha256": "a" * 64, "acl_sha256": "b" * 64}
                for kind in ("table", "sequence")}
    statements = []

    def sql(_backend, statement):
        statements.append(statement)
        return b"BEGIN\n" + json.dumps(expected).encode() + b"\nCOMMIT\n"

    monkeypatch.setattr(target, "_sql", sql)
    assert target._role_catalog(None, probe=probe) == expected
    assert len(statements) == 1
    statement = statements[0]
    table = "recovery_owner_probe" if probe else "recovery_fixture"
    assert "BEGIN READ ONLY;" in statement and statement.strip().endswith("COMMIT;")
    assert f"c.relname IN ('{table}','{table}_id_seq')" in statement
    assert f"r.rolname='{PROJECT_OWNER_ROLE}'" in statement
    assert f"has_sequence_privilege('{PROJECT_RUNTIME_ROLE}'" in statement
    assert f"has_table_privilege('{PROJECT_RUNTIME_ROLE}'" in statement
    assert "'owner_sha256',encode(sha256(" in statement
    assert "'acl_sha256',encode(sha256(" in statement


def test_role_catalog_unknown_scope_denies_before_query(monkeypatch):
    import tests.test_adaptation_recovery_docker as target

    calls = []
    monkeypatch.setattr(target, "_sql", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError):
        target._role_catalog(None, probe="private-unknown-scope")
    assert calls == []
