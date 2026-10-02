"""Quiesce scales replicas without replaying a full server GET object as SSA.

The real paired API dry-run supplies the transport evidence. These regressions
exercise narrow merge-patch transport and preserve the guest-death wait gate.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes.client.exceptions import ApiException

from yleum_orchestrator.services import k8s_publication as kp

NAMESPACE = "app-scoped-test"


def deployment():
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "app",
            "namespace": NAMESPACE,
            "uid": "deployment-owned-uid",
            "resourceVersion": "41",
            "generation": 1,
            "managedFields": [{"manager": kp.FIELD_MANAGER, "operation": "Apply"}],
            "annotations": {"qa-existing": "preserved"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app.kubernetes.io/component": "app"}},
            "strategy": {"type": "Recreate"},
            "revisionHistoryLimit": 7,
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/component": "app"}},
                "spec": {
                    "containers": [{"name": "app", "image": "registry/scoped-app@sha256:abc",
                                    "env": [{"name": "PGUSER", "value": "postgres"}]}],
                    "volumes": [{"name": "business-data", "persistentVolumeClaim":
                                 {"claimName": "owned-data"}}],
                },
            },
        },
        "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1},
    }


class QuiesceApi:
    def __init__(self, *, obj=None, absent=False, post_status=None, pods=None):
        self.obj = None if absent else deepcopy(obj or deployment())
        self.post_status = post_status or {
            "observedGeneration": 2, "replicas": 0, "readyReplicas": 0,
            "terminatingReplicas": 0,
        }
        self.pods = [] if pods is None else pods
        self.scales = []
        self.gets = 0
        self.lists = 0

    def get(self, version, kind, name, namespace):
        assert (version, kind, name, namespace) == ("apps/v1", "Deployment", "app", NAMESPACE)
        self.gets += 1
        return deepcopy(self.obj)

    def apply(self, _obj):
        # The old path cannot be treated as a successful fake API operation.
        # Actual API status/cause evidence is recorded separately by Root.
        pytest.fail("quiesce must not replay full GET metadata/spec through server-side apply")

    def scale_deployment(self, name, namespace, replicas, *, resource_version=None):
        assert self.obj is not None
        assert (name, namespace, replicas, resource_version) == ("app", NAMESPACE, 0, "41")
        self.scales.append((name, namespace, replicas, resource_version))
        self.obj["spec"]["replicas"] = 0
        self.obj["metadata"]["generation"] = 2
        self.obj["metadata"]["resourceVersion"] = "42"
        self.obj["status"] = deepcopy(self.post_status)

    def list_objects(self, version, kind, namespace, selector):
        assert (version, kind, namespace, selector) == (
            "v1", "Pod", NAMESPACE, "app.kubernetes.io/component=app",
        )
        self.lists += 1
        return deepcopy(self.pods)


def test_quiesce_scales_only_replicas_and_waits_without_full_object_apply():
    original = deployment()
    api = QuiesceApi(obj=original)
    kp.KubernetesPublishedRuntime(api, ready_timeout_seconds=0).quiesce_project_app(NAMESPACE)
    assert api.scales == [("app", NAMESPACE, 0, "41")]
    expected_spec = deepcopy(original["spec"])
    expected_spec["replicas"] = 0
    assert api.obj["spec"] == expected_spec
    for key in ("uid", "managedFields", "annotations", "name", "namespace"):
        assert api.obj["metadata"][key] == original["metadata"][key]
    assert api.gets >= 2 and api.lists >= 1


@pytest.mark.parametrize("resource_version", [None, "41"])
def test_cluster_scale_transport_uses_narrow_json_merge_patch(resource_version):
    resource = SimpleNamespace(patch=Mock(), server_side_apply=Mock())
    api = object.__new__(kp.KubernetesClusterApi)
    api._resource = Mock(return_value=resource)
    api.scale_deployment("app", NAMESPACE, 0, resource_version=resource_version)
    body = {"spec": {"replicas": 0}}
    if resource_version is not None:
        body["metadata"] = {"resourceVersion": resource_version}
    api._resource.assert_called_once_with("apps/v1", "Deployment")
    resource.patch.assert_called_once_with(
        body=body, name="app", namespace=NAMESPACE,
        content_type="application/merge-patch+json",
    )
    resource.server_side_apply.assert_not_called()


def test_cluster_scale_conflict_propagates_without_force_or_retry():
    conflict = ApiException(status=409, reason="Conflict")
    resource = SimpleNamespace(patch=Mock(side_effect=conflict), server_side_apply=Mock())
    api = object.__new__(kp.KubernetesClusterApi)
    api._resource = Mock(return_value=resource)
    with pytest.raises(ApiException) as observed:
        api.scale_deployment("app", NAMESPACE, 0, resource_version="41")
    assert observed.value is conflict
    assert resource.patch.call_count == 1
    resource.server_side_apply.assert_not_called()


@pytest.mark.parametrize("unsafe_status", [
    {"observedGeneration": 1, "replicas": 0, "readyReplicas": 0, "terminatingReplicas": 0},
    {"observedGeneration": 2, "replicas": 0, "readyReplicas": 1, "terminatingReplicas": 0},
    {"observedGeneration": 2, "replicas": 0, "readyReplicas": 0, "terminatingReplicas": 1},
])
def test_quiesce_does_not_advance_on_unobserved_or_live_guest_status(unsafe_status):
    api = QuiesceApi(post_status=unsafe_status)
    with pytest.raises(kp.PublicationPlacementError, match="quiesce"):
        kp.KubernetesPublishedRuntime(api, ready_timeout_seconds=0).quiesce_project_app(NAMESPACE)
    assert len(api.scales) == 1 and api.lists >= 1


def test_absent_deployment_still_waits_for_existing_guest_pods():
    api = QuiesceApi(absent=True, pods=[{"metadata": {"name": "terminating-owned-app"}}])
    with pytest.raises(kp.PublicationPlacementError, match="quiesce"):
        kp.KubernetesPublishedRuntime(api, ready_timeout_seconds=0).quiesce_project_app(NAMESPACE)
    assert api.scales == [] and api.lists >= 1
