"""Бюджет проверок обязан вмещать хотя бы одну проверочную ячейку.

23.09.2026 откат на проде отказывал безусловно. В журнале оставалось
`CellCapacityUnavailable`, владелец видел «Не удалось завершить проверку
восстановления», и это читалось как временная занятость сервера. Занятости не
было: восемь ядер, одна работающая ячейка, load average 0.07. Отказывала
настройка — проверочная ячейка просит 1.45 ядра, а бюджет проверок стоял 1.0,
и потому «занято» было истинным всегда, на любом хосте.

Существующие тесты этого не ловили и поймать не могли: в каждом из них менеджер
ресурсов подменён заглушкой, поэтому арифметика допуска на профиле, который
реально используется в проде, не прогонялась ни разу. Здесь она прогоняется —
профиль кандидата строится настоящей `production_manager`, а не руками.

Закрепляется различение, а не тексты сообщений: «пул занят другими проверками»
проходит сам собой, «пул меньше одного кандидата» не пройдёт никогда, и путать
их нельзя — от этого зависит, ждать или чинить настройку.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from omnia_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellVerificationBudgetTooSmall,
    HostCapacitySnapshot,
    LifecycleMutation,
)
from omnia_orchestrator.services.cell_admission import CellAdmissionGate
from omnia_orchestrator.services.cell_publication_capacity import production_manager
from omnia_orchestrator.services.cell_reservations import (
    CellCapacityReservationStore,
    ReservedCapacity,
)
from tests.test_docker_cell_resources import _make_manager

_DIGEST = "a" * 64

# Значения из /opt/omnia/apps/orchestrator/.env на core и commerce (23.09.2026).
_PROD_OWNER_CPU = {
    "bundle_cpu_cores": 2.0,
    "active_machine_cpu_cores": 1.0,
    "project_postgres_cpu_cores": 0.15,
    "helper_cpu_cores": 0.2,
    "managed_core_cpu_cores": 0.35,
    "host_cpu_reserve_cores": 1.0,
}
_PROD_PUBLIC_CPU = SimpleNamespace(
    cell_public_bundle_cpu_cores=1.0,
    cell_public_machine_cpu_cores=0.25,
    cell_public_core_cpu_cores=0.1,
    cell_public_helper_cpu_cores=0.2,
)

# Хост commerce в минуту отказа: восемь ядер, почти ничего не занято.
_IDLE_HOST = HostCapacitySnapshot(
    cpu_count=8,
    load_1m=0.07,
    active_bundle_count=0,
    memory_total_bytes=32 * 1024**3,
    memory_available_bytes=29 * 1024**3,
    disk_path="/",
    disk_total_bytes=400 * 1024**3,
    disk_free_bytes=300 * 1024**3,
    disk_total_inodes=25_000_000,
    disk_free_inodes=24_000_000,
)


@pytest.fixture
def candidate_profile(tmp_path: Path) -> Any:
    """Профиль проверочной ячейки ровно так, как его строит прод."""
    manager, _, _, _ = _make_manager(tmp_path)
    owner = replace(
        manager.profile,
        profile_version="docker-owner-cell-resources-v2",
        **_PROD_OWNER_CPU,
    )
    return production_manager(replace(manager, profile=owner), _PROD_PUBLIC_CPU).profile


def _gate(profile: Any, budget: float) -> CellAdmissionGate:
    return CellAdmissionGate(profile, workload="verification", verification_cpu_cores=budget)


def _decide(gate: CellAdmissionGate, *, cpu_reserved: float = 0.0) -> Any:
    return gate.check(
        _IDLE_HOST,
        existing_bundle=False,
        running_bundle=False,
        cpu_reserved=cpu_reserved,
        reserved=ReservedCapacity(),
        provisional=ReservedCapacity(),
    )


def test_one_candidate_costs_more_than_the_budget_that_was_configured(candidate_profile) -> None:
    """Сначала — сама цифра, из-за которой откат был невозможен."""
    asked = _gate(candidate_profile, 2.0).required().cpu_cores

    assert asked == pytest.approx(1.45)


def _shipped_default_budget() -> float:
    """Значение из настроек, а не переписанное в тест число.

    Иначе тест переживёт понижение значения по умолчанию — ровно ту правку,
    которая и сделала откат невозможным на проде.
    """
    from omnia_orchestrator.core.config import Settings

    return float(Settings.model_fields["cell_verification_cpu_cores"].default)


def test_the_shipped_default_budget_admits_one_candidate(candidate_profile) -> None:
    # Главный инвариант: со значением по умолчанию откат вообще возможен.
    gate = _gate(candidate_profile, _shipped_default_budget())

    assert _decide(gate).allowed, (
        f"кандидат просит {gate.required().cpu_cores:g} ядра, "
        f"бюджет по умолчанию {_shipped_default_budget():g}"
    )


def test_a_budget_below_one_candidate_names_the_configuration_not_the_host(
    candidate_profile,
) -> None:
    # Ровно прод-настройка, на которой откат отказывал.
    decision = _decide(_gate(candidate_profile, 1.0))

    assert not decision.allowed
    assert decision.reason == "verification_budget_too_small"


def test_a_busy_pool_stays_a_wait_not_a_configuration_fault(candidate_profile) -> None:
    """Бюджета хватает, но его занял сосед — это проходит само."""
    gate = _gate(candidate_profile, 2.0)

    decision = _decide(gate, cpu_reserved=float(gate.required().cpu_cores))

    assert not decision.allowed
    assert decision.reason == "insufficient_verification_cpu"


def test_an_idle_host_never_refuses_for_lack_of_host_cpu(candidate_profile) -> None:
    # Проверочная нагрузка идёт мимо резерва хоста; иначе отказ списали бы на
    # железо и оператор опять пошёл бы искать свободные ядра.
    assert _decide(_gate(candidate_profile, 2.0)).reason != "insufficient_cpu"


@pytest.mark.parametrize("budget", [0.5, 1.0, 1.44])
def test_every_budget_below_the_candidate_is_reported_as_configuration(
    candidate_profile, budget: float
) -> None:
    assert _decide(_gate(candidate_profile, budget)).reason == "verification_budget_too_small"


def test_the_refusal_carries_both_numbers_and_the_setting_to_change(
    candidate_profile, tmp_path: Path
) -> None:
    """Оператору нужны обе цифры: сколько просят и сколько разрешено."""
    store = CellCapacityReservationStore(tmp_path / "ledger")

    with pytest.raises(CellVerificationBudgetTooSmall) as failure:
        store.reserve(
            uuid4(),
            LifecycleMutation(uuid4(), 1, _DIGEST),
            profile=candidate_profile,
            snapshot=_IDLE_HOST,
            admission_gate=_gate(candidate_profile, 1.0),
            running_bundle=False,
        )

    message = str(failure.value)
    assert "1 ядра" in message and "1.45 ядра" in message
    assert "CELL_VERIFICATION_CPU_CORES" in message


def test_a_busy_pool_still_raises_the_waitable_capacity_error(
    candidate_profile, tmp_path: Path
) -> None:
    # Починка не должна превратить обычную занятость в ошибку настройки:
    # ожидание и приговор ведут оператора в разные стороны.
    store = CellCapacityReservationStore(tmp_path / "ledger")
    gate = _gate(candidate_profile, 2.0)
    store.reserve(
        uuid4(),
        LifecycleMutation(uuid4(), 1, _DIGEST),
        profile=candidate_profile,
        snapshot=_IDLE_HOST,
        admission_gate=gate,
        running_bundle=False,
    )

    with pytest.raises(CellCapacityUnavailable) as failure:
        store.reserve(
            uuid4(),
            LifecycleMutation(uuid4(), 1, _DIGEST),
            profile=candidate_profile,
            snapshot=_IDLE_HOST,
            admission_gate=gate,
            running_bundle=False,
        )

    assert failure.value.reason == "insufficient_verification_cpu"


def test_a_smaller_candidate_fits_a_smaller_budget(candidate_profile) -> None:
    # Связь «запрос → бюджет» проверяется на другом профиле, иначе тест
    # закрепил бы одно конкретное число, а не правило.
    lean = replace(candidate_profile, active_machine_cpu_cores=0.05)

    assert _decide(_gate(lean, 1.3)).allowed
