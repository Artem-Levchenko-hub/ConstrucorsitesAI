"""Страж пространства имён после перезагрузки хоста: стартует, если лежит, и
пересоздаётся, если его политика устарела.

25.09.2026, commerce: после перезапуска машина не просыпалась никогда. Страж без
restart-политики (намеренно) лежал exited, но ensure создавал и стартовал только
ОТСУТСТВУЮЩЕГО стража — лежачий сразу проваливал готовность. А поднятый руками не
проходил: адрес прокси вшит в его argv и в digest готовности, прокси после
пересоздания получил новый адрес во внутренней сети — старый страж ждал политику,
которая уже никогда не совпадёт (10.253.78.100 → .101). Страж без состояния:
устаревший заменяем, лежачий стартуем.
"""

from __future__ import annotations

from types import SimpleNamespace

from omnia_orchestrator.services.machine_egress import GuardPolicy
from tests.test_docker_machine_backend import backend


class _Guard:
    def __init__(self, *, digest: str, status: str = "running", ready: bool = True) -> None:
        self.id = "guard-" + digest[:8] + "-" + status
        self.labels = {"omnia.policy_digest": digest}
        self.status = status
        self._ready = ready
        self.starts = 0
        self.removed = False

    def reload(self) -> None:
        pass

    def start(self) -> None:
        self.starts += 1
        self.status = "running"

    def remove(self, *, force: bool = False) -> None:
        self.removed = True

    def logs(self, tail: int = 5) -> bytes:
        return (
            f"POLICY_READY={self.labels['omnia.policy_digest']}".encode() if self._ready else b""
        )


class _Containers:
    def __init__(self, existing: _Guard | None) -> None:
        self.existing = existing
        self.created: list[_Guard] = []

    def create(self, image, command, *, labels, **_options) -> _Guard:
        guard = _Guard(digest=labels["omnia.policy_digest"], status="created")
        guard.id = "guard-fresh"
        self.created.append(guard)
        return guard


def _runtime(tmp_path, existing: _Guard | None):
    containers = _Containers(existing)
    runtime = backend(tmp_path, client=SimpleNamespace(containers=containers))
    runtime._lookup = lambda _collection, _name, _kind: containers.existing
    runtime._volume = lambda _name: None
    return runtime, containers


def test_an_exited_guard_with_the_current_policy_is_started_not_recreated(tmp_path) -> None:
    policy = GuardPolicy(workspace_id="ws", proxy_ip="10.253.78.100")
    exited = _Guard(digest=policy.digest(), status="exited")
    runtime, containers = _runtime(tmp_path, exited)

    guard = runtime._ensure_namespace_guard(policy)

    assert guard is exited and exited.starts == 1 and exited.status == "running"
    assert containers.created == [] and not exited.removed


def test_a_guard_built_for_the_old_proxy_address_is_replaced(tmp_path) -> None:
    stale_policy = GuardPolicy(workspace_id="ws", proxy_ip="10.253.78.100")
    current_policy = GuardPolicy(workspace_id="ws", proxy_ip="10.253.78.101")
    stale = _Guard(digest=stale_policy.digest(), status="running")
    runtime, containers = _runtime(tmp_path, stale)

    guard = runtime._ensure_namespace_guard(current_policy)

    assert stale.removed and stale.starts == 0
    assert containers.created == [guard]
    assert guard.labels["omnia.policy_digest"] == current_policy.digest()
    assert guard.status == "running"


def test_a_missing_guard_is_still_created(tmp_path) -> None:
    policy = GuardPolicy(workspace_id="ws", proxy_ip="10.253.78.101")
    runtime, containers = _runtime(tmp_path, None)

    guard = runtime._ensure_namespace_guard(policy)

    assert containers.created == [guard] and guard.starts == 1


def test_a_healthy_guard_is_left_alone(tmp_path) -> None:
    policy = GuardPolicy(workspace_id="ws", proxy_ip="10.253.78.101")
    healthy = _Guard(digest=policy.digest(), status="running")
    runtime, containers = _runtime(tmp_path, healthy)

    assert runtime._ensure_namespace_guard(policy) is healthy
    assert healthy.starts == 0 and not healthy.removed and containers.created == []
