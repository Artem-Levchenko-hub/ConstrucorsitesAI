"""Opted-in public configuration projection and read-only live confirmation."""

from __future__ import annotations

import json

import pytest

from tests.test_k8s_publication import PROJECT, FakeApi, _by, _manifest, _spec
from yleum_orchestrator.services import k8s_publication as kp


def _opted_manifest():
    return _manifest().model_copy(update={"app_config_protocol": 1})


def test_legacy_app_does_not_receive_public_config_projection() -> None:
    objects = kp.build_objects(_spec())
    app = _by(objects, "Deployment", "app")["spec"]["template"]["spec"]
    assert not any(o["metadata"]["name"] == "app-business-config" for o in objects)
    assert not any(e["name"] == "YLEUM_APP_CONFIG_PATH" for e in app["containers"][0]["env"])
    assert not any(
        m["mountPath"] == "/run/yleum-config" for m in app["containers"][0]["volumeMounts"]
    )


def test_opted_app_receives_only_public_config_without_restart_on_save() -> None:
    original = kp.build_objects(_spec(manifest=_opted_manifest(), business_config_version=7))
    updated = kp.build_objects(
        _spec(
            manifest=_opted_manifest(),
            business_config_version=8,
            business_config={"content": [{"id": "item-1", "title": "Новое"}]},
        )
    )
    config = _by(updated, "ConfigMap", "app-business-config")
    assert json.loads(config["data"]["business.json"]) == {
        "config_version": 8,
        "config": {"content": [{"id": "item-1", "title": "Новое"}]},
    }
    app = _by(updated, "Deployment", "app")["spec"]["template"]
    assert app == _by(original, "Deployment", "app")["spec"]["template"]
    container = app["spec"]["containers"][0]
    assert {
        "name": "YLEUM_APP_CONFIG_PATH",
        "value": "/run/yleum-config/business.json",
    } in container["env"]
    assert {
        "name": "app-business-config",
        "mountPath": "/run/yleum-config",
        "readOnly": True,
    } in container["volumeMounts"]
    assert {"name": "app-business-config", "configMap": {"name": "app-business-config"}} in app[
        "spec"
    ]["volumes"]
    assert not any(
        v.get("secret", {}).get("secretName") == "core-config" for v in app["spec"]["volumes"]
    )
    assert _by(updated, "NetworkPolicy", "app") == _by(original, "NetworkPolicy", "app")


@pytest.mark.parametrize("opted", [True, False])
def test_runtime_observation_is_opt_in_and_never_mutates_cluster(opted):
    class ConfigApi(FakeApi):
        def observe_config(self, namespace, release_id, expected_digest, config_version):
            assert opted
            assert namespace == f"app-{PROJECT}"
            assert release_id == "release-1"
            assert len(expected_digest) == 64 and config_version == 8
            return {"core_matches": True, "app_matches": True}

    api = ConfigApi()
    result = kp.KubernetesPublishedRuntime(api).observe_config(
        PROJECT,
        "release-1",
        _opted_manifest() if opted else _manifest(),
        {"content": []},
        8,
    )
    assert result == {"core_matches": opted, "app_matches": opted}
    assert api.applied == [] and api.execs == []
