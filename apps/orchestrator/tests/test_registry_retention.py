"""Registry policy tests against the real HTTP client and an in-memory registry."""

from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from yleum_orchestrator.services.registry_retention import RegistryPlan, RegistryRetention

BASE = "https://registry.example.test"
REPO = "max-app/12345678-1234-4234-8234-123456789abc"
OTHER_REPO = "max-app/22345678-1234-4234-8234-123456789abc"
NOW = datetime(2026, 11, 3, 10, tzinfo=UTC)
CONFIG = "sha256:" + "1" * 64
OTHER_CONFIG = "sha256:" + "2" * 64
MANIFEST_TYPE = "application/vnd.docker.distribution.manifest.v2+json"
CONFIG_TYPE = "application/vnd.docker.container.image.v1+json"
PASSWORD = "never-expose-this-registry-password"


@dataclass(frozen=True)
class Image:
    content: bytes
    media_type: str = MANIFEST_TYPE

    @property
    def digest(self) -> str:
        return "sha256:" + sha256(self.content).hexdigest()


def image(config: str = CONFIG, *, marker: str = "old") -> Image:
    return Image(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": MANIFEST_TYPE,
                "config": {"mediaType": CONFIG_TYPE, "size": 100, "digest": config},
                "layers": [],
                "annotations": {"test.marker": marker},
            },
            sort_keys=True,
        ).encode()
    )


class Registry:
    """Only the network boundary is replaced; policy, persistence and auth are real."""

    def __init__(self, repositories: dict[str, dict[str, Image]] | None = None) -> None:
        self.repositories = repositories if repositories is not None else {REPO: {"old": image()}}
        self.requests: list[httpx.Request] = []
        self.override: Callable[[httpx.Request], httpx.Response | None] | None = None
        self.deletions: list[tuple[str, str]] = []

    def request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET", "the policy must never send a public registry mutation"
        if self.override is not None:
            response = self.override(request)
            if response is not None:
                return response
        path = request.url.path
        if path == "/v2/_catalog":
            return httpx.Response(200, json={"repositories": sorted(self.repositories)})
        for repository, tags in self.repositories.items():
            if path == f"/v2/{repository}/tags/list":
                return httpx.Response(200, json={"name": repository, "tags": sorted(tags)})
            prefix = f"/v2/{repository}/manifests/"
            if path.startswith(prefix):
                reference = path.removeprefix(prefix)
                value = tags.get(reference)
                if value is None:
                    value = next((item for item in tags.values() if item.digest == reference), None)
                if value is not None:
                    return httpx.Response(
                        200,
                        content=value.content,
                        headers={
                            "Content-Type": value.media_type,
                            "Docker-Content-Digest": value.digest,
                        },
                    )
        return httpx.Response(404, json={"errors": [{"code": "MANIFEST_UNKNOWN"}]})

    def delete(self, repository: str, digest: str) -> int:
        self.deletions.append((repository, digest))
        self.repositories[repository] = {
            tag: value
            for tag, value in self.repositories[repository].items()
            if value.digest != digest
        }
        return 202


def retention(tmp_path: Path, registry: Registry) -> RegistryRetention:
    return RegistryRetention(
        BASE,
        "retention",
        PASSWORD,
        tmp_path / "retention" / "registry-seen.json",
        client=httpx.Client(transport=httpx.MockTransport(registry.request), follow_redirects=True),
    )


def aged_plan(policy: RegistryRetention, protected: set[str] | None = None) -> RegistryPlan:
    policy.plan(protected or set(), NOW)
    return policy.plan(protected or set(), NOW + timedelta(seconds=7200))


def test_first_observation_defers_deletion_until_full_two_hour_grace(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)

    assert policy.plan(set(), NOW).candidates == ()
    assert policy.plan(set(), NOW + timedelta(seconds=7199)).candidates == ()
    plan = policy.plan(set(), NOW + timedelta(seconds=7200))

    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert (candidate.repository, candidate.digest, candidate.config_digest, candidate.tags) == (
        REPO,
        image().digest,
        CONFIG,
        ("old",),
    )
    assert policy.apply(plan, set(), delete=registry.delete)["deleted"] == 1
    assert registry.deletions == [(REPO, image().digest)]
    assert registry.repositories[REPO] == {}


def test_inventory_authenticates_without_persisting_credentials(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = policy.plan(set(), NOW)
    authorization = "Basic " + b64encode(b"retention:never-expose-this-registry-password").decode()
    assert all(
        request.headers.get("Authorization") == authorization for request in registry.requests
    )
    assert PASSWORD not in repr(policy)
    assert PASSWORD not in repr(plan)
    assert PASSWORD.encode() not in (tmp_path / "retention" / "registry-seen.json").read_bytes()


@pytest.mark.parametrize(
    "protected",
    [
        f"{BASE}/{REPO}:current",
        f"registry.example.test/{REPO}:current",
        f"{REPO}:current",
        f"{BASE}/{REPO}@{image().digest}",
        image().digest,
        CONFIG,
        f"docker-pullable://registry.example.test/{REPO}@{image().digest}",
        f"containerd://{CONFIG}",
        f"REGISTRY.EXAMPLE.TEST/{REPO}:current",
        f"registry.example.test:443/{REPO}:current",
    ],
)
def test_any_protected_alias_manifest_or_config_keeps_all_digest_aliases(
    tmp_path: Path, protected: str
) -> None:
    registry = Registry({REPO: {"old": image(), "current": image()}})
    policy = retention(tmp_path, registry)

    assert aged_plan(policy, {protected}).candidates == ()


def test_unqualified_image_reference_protects_latest_tag(tmp_path: Path) -> None:
    registry = Registry({REPO: {"latest": image()}})
    policy = retention(tmp_path, registry)
    assert aged_plan(policy, {f"registry.example.test/{REPO}"}).candidates == ()


def test_foreign_registry_tag_does_not_select_this_registry(tmp_path: Path) -> None:
    policy = retention(tmp_path, Registry())
    assert len(aged_plan(policy, {f"elsewhere.example/{REPO}:old"}).candidates) == 1


def test_all_aliases_are_grouped_into_one_addressed_deletion(tmp_path: Path) -> None:
    registry = Registry({REPO: {"z-old": image(), "a-old": image()}})
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    assert len(plan.candidates) == 1
    assert plan.candidates[0].tags == ("a-old", "z-old")
    policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == [(REPO, image().digest)]


def test_new_alias_on_an_old_image_restarts_observation_grace(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    aged_plan(policy)
    registry.repositories[REPO]["newly-pushed"] = image()

    assert policy.plan(set(), NOW + timedelta(hours=24)).candidates == ()
    assert policy.plan(set(), NOW + timedelta(hours=24, seconds=7199)).candidates == ()
    plan = policy.plan(set(), NOW + timedelta(hours=26))
    assert plan.candidates[0].tags == ("newly-pushed", "old")
    assert not any("/blobs/" in request.url.path for request in registry.requests)


def test_new_manifest_with_reused_old_config_gets_its_own_grace(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    aged_plan(policy)
    registry.repositories[REPO]["new"] = image(marker="new")

    plan = policy.plan(set(), NOW + timedelta(hours=24))
    assert [candidate.tags for candidate in plan.candidates] == [("old",)]


def test_disappeared_then_reappearing_digest_gets_fresh_grace(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    aged_plan(policy)
    registry.repositories[REPO] = {}
    policy.plan(set(), NOW + timedelta(hours=3))
    registry.repositories[REPO] = {"old": image()}
    assert policy.plan(set(), NOW + timedelta(hours=24)).candidates == ()


def test_shared_and_noncanonical_application_namespaces_are_never_inspected(tmp_path: Path) -> None:
    registry = Registry(
        {
            REPO: {"old": image()},
            "platform/api": {"old": image()},
            "max-app/not-a-uuid": {"old": image()},
            REPO.upper(): {"old": image()},
            REPO + "/nested": {"old": image()},
            "max-app/12345678123442348234123456789abc": {"old": image()},
        }
    )
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    assert [candidate.repository for candidate in plan.candidates] == [REPO]
    for request in registry.requests:
        assert request.url.path == "/v2/_catalog" or request.url.path.startswith(f"/v2/{REPO}/")


@pytest.mark.parametrize(
    "unsupported",
    [
        Image(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "manifests": [
                        {"mediaType": MANIFEST_TYPE, "digest": image().digest, "size": 200}
                    ],
                }
            ).encode(),
            "application/vnd.oci.image.index.v1+json",
        ),
        Image(b'{"schemaVersion":1,"history":[]}'),
        Image(b'{"schemaVersion":2,"config":null,"layers":[]}'),
        Image(b'{"schemaVersion":2,"config":{},"config":{},"layers":[]}'),
    ],
)
def test_unknown_or_ambiguous_manifest_preserves_entire_repository(
    tmp_path: Path, unsupported: Image
) -> None:
    registry = Registry({REPO: {"child": image(), "unknown": unsupported}})
    policy = retention(tmp_path, registry)
    assert aged_plan(policy).candidates == ()


def test_valid_oci_single_manifest_can_expire(tmp_path: Path) -> None:
    content = json.loads(image().content)
    content["mediaType"] = "application/vnd.oci.image.manifest.v1+json"
    content["config"]["mediaType"] = "application/vnd.oci.image.config.v1+json"
    value = Image(json.dumps(content).encode(), "application/vnd.oci.image.manifest.v1+json")
    policy = retention(tmp_path, Registry({REPO: {"old": value}}))
    assert aged_plan(policy).candidates[0].digest == value.digest


def test_structurally_ambiguous_descriptor_preserves_repository(tmp_path: Path) -> None:
    payload = json.loads(image().content)
    payload["config"]["mediaType"] = {"unexpected": CONFIG_TYPE}
    ambiguous = Image(json.dumps(payload).encode())
    policy = retention(tmp_path, Registry({REPO: {"old": image(), "ambiguous": ambiguous}}))
    assert aged_plan(policy).candidates == ()


def test_complete_catalog_and_tag_pagination_is_inventoried(tmp_path: Path) -> None:
    registry = Registry({REPO: {"a": image(), "b": image()}, OTHER_REPO: {"old": image()}})

    def pages(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/v2/_catalog":
            if "last" not in request.url.params:
                return httpx.Response(
                    200,
                    json={"repositories": [REPO]},
                    headers={"Link": f'</v2/_catalog?n=1&last={REPO}>; rel="next"'},
                )
            return httpx.Response(200, json={"repositories": [OTHER_REPO]})
        if request.url.path == f"/v2/{REPO}/tags/list":
            if "last" not in request.url.params:
                return httpx.Response(
                    200,
                    json={"name": REPO, "tags": ["a"]},
                    headers={"Link": f'</v2/{REPO}/tags/list?n=1&last=a>; rel="next"'},
                )
            return httpx.Response(200, json={"name": REPO, "tags": ["b"]})
        return None

    registry.override = pages
    plan = aged_plan(retention(tmp_path, registry))
    assert [(candidate.repository, candidate.tags) for candidate in plan.candidates] == [
        (REPO, ("a", "b")),
        (OTHER_REPO, ("old",)),
    ]


@pytest.mark.parametrize(
    "link",
    [
        '<https://attacker.example/v2/_catalog?n=1&last=x>; rel="next"',
        '<https://registry.example.test/secret?n=1&last=x>; rel="next"',
        '<https://user:secret@registry.example.test/v2/_catalog?n=1&last=x>; rel="next"',
        '</v2/_catalog?n=1&last=x#fragment>; rel="next"',
        '</v2/_catalog?n=1&last=x&surprise=1>; rel="next"',
        '</v2/_catalog?n=1&last=wrong>; rel="next"',
        "not-a-pagination-link",
    ],
)
def test_untrusted_or_incomplete_pagination_aborts_without_state_write(
    tmp_path: Path, link: str
) -> None:
    registry = Registry()
    registry.override = lambda request: httpx.Response(
        200, json={"repositories": [REPO]}, headers={"Link": link}
    )
    policy = retention(tmp_path, registry)
    with pytest.raises(RuntimeError):
        policy.plan(set(), NOW)
    assert len(registry.requests) == 1
    assert not (tmp_path / "retention" / "registry-seen.json").exists()


def test_pagination_cycle_aborts_instead_of_accepting_partial_inventory(tmp_path: Path) -> None:
    registry = Registry()
    registry.override = lambda request: httpx.Response(
        200,
        json={"repositories": [REPO]},
        headers={"Link": f'</v2/_catalog?n=1&last={REPO}>; rel="next"'},
    )
    with pytest.raises(RuntimeError):
        retention(tmp_path, registry).plan(set(), NOW)
    assert len(registry.requests) <= 2


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 404, 500, 503])
def test_http_failure_or_redirect_never_follows_credentials_or_writes_state(
    tmp_path: Path, status: int
) -> None:
    registry = Registry()
    registry.override = lambda request: httpx.Response(
        status,
        headers={"Location": "https://attacker.example/steal"},
        text=PASSWORD,
    )
    policy = retention(tmp_path, registry)
    with pytest.raises(RuntimeError) as error:
        policy.plan(set(), NOW)
    assert PASSWORD not in str(error.value)
    assert PASSWORD not in repr(policy)
    assert len(registry.requests) == 1
    assert registry.requests[0].url.host == "registry.example.test"
    assert not (tmp_path / "retention" / "registry-seen.json").exists()


def test_transport_exception_text_is_sanitized(tmp_path: Path) -> None:
    registry = Registry()

    def failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(PASSWORD, request=request)

    registry.override = failure
    with pytest.raises(RuntimeError) as error:
        retention(tmp_path, registry).plan(set(), NOW)
    assert PASSWORD not in str(error.value)


def test_unexpected_client_exception_text_is_sanitized(tmp_path: Path) -> None:
    registry = Registry()

    def failure(request: httpx.Request) -> httpx.Response:
        raise ValueError(PASSWORD)

    registry.override = failure
    with pytest.raises(RuntimeError) as error:
        retention(tmp_path, registry).plan(set(), NOW)
    assert PASSWORD not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [{}, {"repositories": None}, {"repositories": [REPO, REPO]}, {"repositories": [None]}],
)
def test_malformed_catalog_aborts_inventory(tmp_path: Path, payload: object) -> None:
    registry = Registry()
    registry.override = lambda request: httpx.Response(200, json=payload)
    with pytest.raises(RuntimeError):
        retention(tmp_path, registry).plan(set(), NOW)


def test_catalog_with_embedded_errors_cannot_authorize_deletion(tmp_path: Path) -> None:
    registry = Registry()
    registry.override = lambda request: (
        httpx.Response(
            200,
            json={"repositories": [REPO], "errors": [{"code": "UNKNOWN", "message": PASSWORD}]},
        )
        if request.url.path == "/v2/_catalog"
        else None
    )
    with pytest.raises(RuntimeError) as error:
        retention(tmp_path, registry).plan(set(), NOW)
    assert PASSWORD not in str(error.value)


def test_unrecognized_bare_protected_digest_aborts_instead_of_being_ignored(tmp_path: Path) -> None:
    registry = Registry()
    with pytest.raises(RuntimeError):
        aged_plan(retention(tmp_path, registry), {"sha256:invalid-digest"})
    assert registry.deletions == []


@pytest.mark.parametrize(
    "payload",
    [{"name": OTHER_REPO, "tags": ["old"]}, {"name": REPO}, {"name": REPO, "tags": ["../x"]}],
)
def test_malformed_tag_inventory_aborts(tmp_path: Path, payload: object) -> None:
    registry = Registry()
    registry.override = lambda request: (
        httpx.Response(200, json=payload) if request.url.path.endswith("/tags/list") else None
    )
    with pytest.raises(RuntimeError):
        retention(tmp_path, registry).plan(set(), NOW)


def test_failed_inventory_does_not_replace_previously_complete_observations(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    policy.plan(set(), NOW)
    path = tmp_path / "retention" / "registry-seen.json"
    before = path.read_bytes()
    registry.override = lambda request: (
        httpx.Response(503) if "/manifests/" in request.url.path else None
    )
    with pytest.raises(RuntimeError):
        policy.plan(set(), NOW + timedelta(hours=24))
    assert path.read_bytes() == before


def test_apply_rechecks_every_repository_before_the_first_delete(tmp_path: Path) -> None:
    registry = Registry({REPO: {"old": image()}, OTHER_REPO: {"old": image()}})
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    registry.override = lambda request: (
        httpx.Response(503) if f"/{OTHER_REPO}/manifests/" in request.url.path else None
    )
    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == []


@pytest.mark.parametrize("change", ["alias-added", "alias-retargeted", "new-repository"])
def test_changed_inventory_aborts_before_deletion(tmp_path: Path, change: str) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    if change == "alias-added":
        registry.repositories[REPO]["new"] = image()
    elif change == "alias-retargeted":
        registry.repositories[REPO]["old"] = image(marker="changed")
    else:
        registry.repositories[OTHER_REPO] = {"old": image()}
    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == []


def test_new_protected_root_is_honored_at_apply(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    result = policy.apply(plan, {CONFIG}, delete=registry.delete)
    assert result["deleted"] == 0
    assert result["protected"] == 1
    assert registry.deletions == []


def test_protected_set_changed_during_recheck_aborts(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    protected: set[str] = set()

    def change_roots(request: httpx.Request) -> None:
        if "/manifests/" in request.url.path:
            protected.add(CONFIG)

    registry.override = change_roots
    with pytest.raises(RuntimeError):
        policy.apply(plan, protected, delete=registry.delete)
    assert registry.deletions == []


def test_immutable_manifest_is_rechecked_before_injected_delete(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    registry.override = lambda request: (
        httpx.Response(404) if request.url.path.endswith(image().digest) else None
    )
    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == []


def test_alias_added_during_immutable_manifest_recheck_aborts(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)

    def retag(request: httpx.Request) -> None:
        if request.url.path.endswith(image().digest):
            registry.repositories[REPO]["new-current"] = image()

    registry.override = retag
    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == []


def test_multiple_candidates_in_one_repository_can_be_deleted(tmp_path: Path) -> None:
    registry = Registry({REPO: {"a": image(), "b": image(OTHER_CONFIG)}})
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    assert policy.apply(plan, set(), delete=registry.delete)["deleted"] == 2
    assert len(registry.deletions) == 2


def test_superseded_observation_receipt_cannot_be_applied(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    policy.plan(set(), NOW + timedelta(hours=3))
    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=registry.delete)
    assert registry.deletions == []


def test_forged_candidate_cannot_expand_a_plan(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    forged = replace(plan, candidates=(replace(plan.candidates[0], repository="platform/api"),))
    with pytest.raises(RuntimeError):
        policy.apply(forged, set(), delete=registry.delete)
    assert registry.deletions == []


def test_corrupted_observations_are_not_treated_as_an_empty_history(tmp_path: Path) -> None:
    registry = Registry()
    policy = retention(tmp_path, registry)
    path = tmp_path / "retention" / "registry-seen.json"
    path.parent.mkdir()
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        policy.plan(set(), NOW)
    assert registry.requests == []
    assert path.read_text(encoding="utf-8") == "not-json"


def test_symlinked_observation_path_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "important.json"
    target.write_text("preserve", encoding="utf-8")
    path = tmp_path / "retention" / "registry-seen.json"
    path.parent.mkdir()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("this environment does not grant symlink creation")
    with pytest.raises(RuntimeError):
        retention(tmp_path, Registry()).plan(set(), NOW)
    assert target.read_text(encoding="utf-8") == "preserve"


def test_naive_clock_cannot_age_observations(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        retention(tmp_path, Registry()).plan(set(), NOW.replace(tzinfo=None))


def test_failed_delete_stops_remaining_candidates_and_sanitizes_errors(tmp_path: Path) -> None:
    registry = Registry({REPO: {"a": image(), "b": image(OTHER_CONFIG)}})
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    attempts: list[tuple[str, str]] = []

    def failed_delete(repository: str, digest: str) -> int:
        attempts.append((repository, digest))
        raise RuntimeError(PASSWORD)

    with pytest.raises(RuntimeError) as error:
        policy.apply(plan, set(), delete=failed_delete)
    assert PASSWORD not in str(error.value)
    assert len(attempts) == 1


def test_nonaccepted_delete_status_stops_remaining_candidates(tmp_path: Path) -> None:
    registry = Registry({REPO: {"a": image(), "b": image(OTHER_CONFIG)}})
    policy = retention(tmp_path, registry)
    plan = aged_plan(policy)
    attempts: list[tuple[str, str]] = []

    def refused_delete(repository: str, digest: str) -> int:
        attempts.append((repository, digest))
        return 500

    with pytest.raises(RuntimeError):
        policy.apply(plan, set(), delete=refused_delete)
    assert len(attempts) == 1
