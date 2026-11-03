from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest


def module():
    return importlib.import_module("yleum_orchestrator.services.registry_push")


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def read(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.now += delay


class Images:
    def __init__(self, attempts: list[list[dict[str, Any]]]) -> None:
        self.attempts = attempts
        self.pushes = 0
        self.tags: list[tuple[str, str]] = []

    def get(self, image_id: str) -> SimpleNamespace:
        return SimpleNamespace(tag=lambda repository, tag: self.tags.append((repository, tag)))

    def push(self, *args: Any, **kwargs: Any):
        attempt = self.attempts[min(self.pushes, len(self.attempts) - 1)]
        self.pushes += 1
        return iter(attempt)


@pytest.mark.parametrize(
    "error",
    [
        {"error": "unsupported", "errorDetail": {"code": "UNSUPPORTED"}},
        {"error": "unexpected HTTP status: 503 Service Unavailable"},
        {"error": "registry is read-only"},
    ],
)
def test_push_waits_for_registry_maintenance_without_retagging(error: dict[str, Any]) -> None:
    publication = module()
    images = Images([[error], [{"status": "pushed"}]])
    clock = Clock()
    result = publication.push_image(
        SimpleNamespace(images=images),
        "sha256:abc",
        "registry.yleum.ru/apps/p",
        "v2",
        auth=None,
        maintenance_retry_seconds=10,
        clock=clock.read,
        sleep=clock.sleep,
    )
    assert result == "registry.yleum.ru/apps/p:v2"
    assert images.pushes == 2 and images.tags == [("registry.yleum.ru/apps/p", "v2")]
    assert clock.delays and clock.now <= 10


def test_registry_maintenance_wait_is_bounded() -> None:
    publication = module()
    images = Images([[{"error": "unsupported", "errorDetail": {"code": "UNSUPPORTED"}}]])
    clock = Clock()
    with pytest.raises(publication.RegistryPushError, match=r"обслуживан|maintenance"):
        publication.push_image(
            SimpleNamespace(images=images),
            "sha256:abc",
            "registry/apps/p",
            "v2",
            auth=None,
            maintenance_retry_seconds=3,
            clock=clock.read,
            sleep=clock.sleep,
        )
    assert clock.now == 3
    assert images.pushes <= 4


def test_authentication_failure_is_not_maintenance_and_does_not_wait() -> None:
    publication = module()
    images = Images([[{"error": "denied"}]])
    clock = Clock()
    with pytest.raises(publication.RegistryPushError, match="denied"):
        publication.push_image(
            SimpleNamespace(images=images),
            "sha256:abc",
            "registry/apps/p",
            "v2",
            auth=None,
            maintenance_retry_seconds=10,
            clock=clock.read,
            sleep=clock.sleep,
        )
    assert images.pushes == 1 and not clock.delays


def test_push_failure_does_not_expose_registry_password() -> None:
    publication = module()
    images = Images([[{"error": "denied password=private-test-registry-password"}]])
    with pytest.raises(publication.RegistryPushError) as error:
        publication.push_image(
            SimpleNamespace(images=images),
            "sha256:abc",
            "registry/apps/p",
            "v2",
            auth={"username": "max", "password": "private-test-registry-password"},
        )
    assert "private-test-registry-password" not in str(error.value)
