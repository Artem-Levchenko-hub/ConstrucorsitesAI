import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.services.project_machine import write_controller_json
from omnia_orchestrator.services.restoration_data_contract import DataContract
from omnia_orchestrator.services.restoration_database import load_policy, stage_policy
from tests.test_restoration_machine_policy import backend


def test_fresh_database_admission_is_available():
    assert importlib.util.find_spec("omnia_orchestrator.services.fresh_database_protection"), (
        "New databases lack default protection admission"
    )


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    name = "omnia_orchestrator.services.fresh_database_protection"
    assert importlib.util.find_spec(name), "New databases lack default protection admission"
    module = importlib.import_module(name)
    runtime = backend(tmp_path)
    resources = {"volume": None, "product": None, "postgres": None}
    runtime.client = SimpleNamespace(volumes=object())
    monkeypatch.setattr(type(runtime), "_lookup", lambda *_: resources["volume"])
    monkeypatch.setattr(type(runtime), "_container", lambda _: resources["product"])
    monkeypatch.setattr(type(runtime), "_project_postgres", lambda _: resources["postgres"])
    return module, runtime, resources


def test_new_database_uses_restricted_credentials_before_postgres_or_guest(fresh):
    module, runtime, _ = fresh
    module.prepare_new_database(runtime, 7)
    policy = load_policy(runtime)
    assert policy["contract"] == {"version": 1, "tables": []}
    assert runtime.project_database_env()["PGUSER"] == "omnia_runtime"
    assert runtime.project_database_env()["PGPASSWORD"] != "old-agent-password"
    options = runtime._project_postgres_options("guard", 7)
    assert "hba_file=/etc/omnia-pg-hba.conf" in options["command"]


def test_existing_volume_is_never_admitted_as_empty_database(fresh):
    module, runtime, resources = fresh
    resources["volume"] = object()
    module.prepare_new_database(runtime, 7)
    assert load_policy(runtime) is None


def test_missing_previously_used_database_cannot_be_recreated_as_fresh(fresh):
    module, runtime, _ = fresh
    write_controller_json(runtime.metadata_path, {"manifest": {"version": 1}})
    with pytest.raises(CellResourceError, match="existing database material is missing"):
        module.prepare_new_database(runtime, 7)
    assert load_policy(runtime) is None


def test_initial_policy_failure_keeps_restricted_credentials_and_replays(fresh, monkeypatch):
    module, runtime, resources = fresh
    module.prepare_new_database(runtime, 7)
    first = load_policy(runtime)
    resources["volume"] = object()
    calls = []

    def install(_):
        calls.append(1)
        if len(calls) == 1:
            raise CellResourceError("controller database operation failed")

    monkeypatch.setattr(module, "install_policy", install)
    with pytest.raises(CellResourceError, match="controller database operation failed"):
        module.finish_new_database(runtime, 7)
    module.prepare_new_database(runtime, 7)
    assert load_policy(runtime) == first
    assert runtime.project_database_env()["PGUSER"] == "omnia_runtime"
    module.finish_new_database(runtime, 7)
    module.prepare_new_database(runtime, 8)
    module.finish_new_database(runtime, 8)
    assert len(calls) == 2
    assert load_policy(runtime) == first


def test_new_epoch_resumes_pending_initial_database_without_rotating_credentials(
    fresh, monkeypatch,
):
    module, runtime, _ = fresh
    module.prepare_new_database(runtime, 7)
    policy = load_policy(runtime)
    module.prepare_new_database(runtime, 8)
    assert load_policy(runtime) == policy
    installed = []
    monkeypatch.setattr(module, "install_policy", lambda _: installed.append(True))
    module.finish_new_database(runtime, 8)
    assert installed == [True]
    assert load_policy(runtime) == policy


def test_older_epoch_cannot_take_pending_initial_database(fresh):
    module, runtime, _ = fresh
    module.prepare_new_database(runtime, 7)
    with pytest.raises(CellIdentityConflict, match="database policy fence is newer"):
        module.prepare_new_database(runtime, 6)


def test_pending_initial_database_never_installs_policy_over_running_guest(fresh):
    module, runtime, resources = fresh
    module.prepare_new_database(runtime, 7)
    resources["product"] = object()
    with pytest.raises(CellIdentityConflict, match="initial database already has a product"):
        module.finish_new_database(runtime, 7)


def test_established_protection_is_not_replaced_or_reinstalled(fresh, monkeypatch):
    module, runtime, _ = fresh
    policy = stage_policy(runtime, DataContract(version=1), 6, blocked_deletes=[])
    monkeypatch.setattr(module, "install_policy", lambda _: pytest.fail("unexpected install"))
    module.prepare_new_database(runtime, 7)
    module.finish_new_database(runtime, 7)
    assert load_policy(runtime) == policy


@pytest.mark.parametrize("install_failure", [False, True])
def test_ensure_never_starts_guest_before_initial_policy(tmp_path, monkeypatch, install_failure):
    from omnia_orchestrator.core.project_machine import MachineManifest
    from omnia_orchestrator.services import fresh_database_protection as module
    from omnia_orchestrator.services.machine_egress import GuardPolicy
    from tests.test_project_machine_manifest import payload

    runtime = backend(tmp_path)
    events = []
    proxy_ip = "10.1.1.2"
    guard_policy = GuardPolicy(workspace_id=str(runtime.workspace_id), proxy_ip=proxy_ip)
    proxy = SimpleNamespace(
        status="running", reload=lambda: None,
        attrs={"NetworkSettings": {"Networks": {
            runtime.internal_network: {"IPAddress": proxy_ip},
        }}},
    )
    guard = SimpleNamespace(
        id="guard", status="running", reload=lambda: None,
        labels={"omnia.policy_digest": guard_policy.digest()},
        logs=lambda **_: f"POLICY_READY={guard_policy.digest()}".encode(),
    )

    def create(_image, **options):
        events.append("guest-created")
        assert events[:2] == ["postgres-ready", "policy-installed"]
        assert options["environment"]["PGUSER"] == "omnia_runtime"
        assert "old-agent-password" not in str(options["environment"])
        return SimpleNamespace(start=lambda: events.append("guest-started"))

    runtime.client = SimpleNamespace(
        volumes=object(), containers=SimpleNamespace(create=create),
        networks=SimpleNamespace(get=lambda _: SimpleNamespace(attrs={
            "Internal": True, "Labels": {"omnia.workspace_id": str(runtime.workspace_id)},
        })),
    )
    monkeypatch.setattr(runtime, "_lookup", lambda _c, _n, kind: {
        "egress-proxy": proxy, "namespace-guard": guard,
    }.get(kind))
    monkeypatch.setattr(runtime, "_container", lambda: None)
    monkeypatch.setattr(runtime, "_project_postgres", lambda: None)
    monkeypatch.setattr(runtime, "_network", lambda *_a, **_k: SimpleNamespace(name="outward"))
    monkeypatch.setattr(runtime, "_volume", lambda _: None)
    monkeypatch.setattr(runtime, "_wait_proxy_ready", lambda *_: None)
    monkeypatch.setattr(
        runtime, "_ensure_project_postgres", lambda *_: events.append("postgres-ready"),
    )

    def install(_):
        assert runtime.project_database_env()["PGUSER"] == "omnia_runtime"
        if install_failure:
            raise CellResourceError("controller database operation failed")
        events.append("policy-installed")

    monkeypatch.setattr(module, "install_policy", install)
    if install_failure:
        with pytest.raises(CellResourceError, match="controller database operation failed"):
            runtime.ensure(MachineManifest.model_validate(payload()), 7)
        assert events == ["postgres-ready"]
    else:
        runtime.ensure(MachineManifest.model_validate(payload()), 7)
        assert events == ["postgres-ready", "policy-installed", "guest-created", "guest-started"]
