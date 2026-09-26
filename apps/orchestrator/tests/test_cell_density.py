"""Сколько пользователей могут собирать приложение одновременно на одном хосте.

Владелец 26.09.2026: «множество пользователей должны иметь возможность
запускать генерацию одновременно, а не по очереди». В тот день он сам не смог
создать приложение: оркестратор отвечал insufficient_cpu на каждую попытку,
хотя хост простаивал — 8 ядер, загрузка 1.06, свободно 27 ГБ из 32.

Причина была в учёте: ячейка бронировала свой ПОТОЛОК (3.2 ядра, 6.1 ГБ),
а не то, что занимает. Две служебные ячейки держали 6.4 из 8 ядер, и третья
не помещалась никогда: 6.4 + 3.2 + резерв хоста 1 = 10.6.

Этот файл закрепляет числа, ради которых правка делалась, на прод-настройках
core и commerce. Он намеренно проверяет не «код вызвался», а сколько ячеек
влезает — если кто-то вернёт бронь потолков, тест назовёт новое число.
"""

from __future__ import annotations

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceProfile
from yleum_orchestrator.services.cell_admission import AdmissionDecision, CellAdmissionGate
from yleum_orchestrator.services.cell_reservations import ReservedCapacity

from .test_cell_admission import HostCapacitySnapshot

HOST_CORES = 8
HOST_MEMORY = 32 * 1024**3


def _production_profile() -> CellResourceProfile:
    """Настройки боевых хостов ячеек (apps/orchestrator/.env на core и commerce)."""
    digest = "img@sha256:" + "0" * 64
    return CellResourceProfile(
        profile_version="docker-owner-cell-resources-v2",
        postgres_image=digest,
        redis_image=digest,
        backup_image=digest,
        bundle_cpu_cores=2.0,
        bundle_memory_bytes=4 * 1024**3,
        host_cpu_reserve_cores=1.0,
        host_memory_reserve_bytes=3 * 1024**3,
        required_free_disk_bytes=20 * 1024**3,
        host_disk_reserve_bytes=10 * 1024**3,
        required_free_inodes=100_000,
        host_inode_reserve=50_000,
        state_path="/opt/omnia-runtime/state/project-cells.json",
        active_machine_cpu_cores=1.0,
        active_machine_memory_bytes=2 * 1024**3,
    )


def _snapshot() -> HostCapacitySnapshot:
    return HostCapacitySnapshot(
        cpu_count=HOST_CORES,
        load_1m=1.06,
        memory_available_bytes=27 * 1024**3,
        memory_total_bytes=HOST_MEMORY,
        disk_free_bytes=360 * 1024**3,
        disk_total_bytes=484 * 1024**3,
        disk_free_inodes=10_000_000,
        disk_total_inodes=30_000_000,
        active_bundle_count=0,
        disk_path="/var/lib/docker",
    )


def _fits(profile: CellResourceProfile) -> int:
    """Сколько ячеек подряд допустит шлюз на пустом боевом хосте."""
    gate = CellAdmissionGate(profile)
    booked = ReservedCapacity()
    admitted = 0
    for _ in range(64):
        decision = gate.check(
            _snapshot(), existing_bundle=False, running_bundle=False, reserved=booked
        )
        if decision != AdmissionDecision(True, "admitted"):
            return admitted
        admitted += 1
        booked = booked.plus(ReservedCapacity.from_profile(profile))
    raise AssertionError("шлюз допускает бесконечно много ячеек — защита исчезла")


def test_a_production_host_now_serves_seven_simultaneous_users() -> None:
    profile = _production_profile()
    assert _fits(profile) == 7, (
        "на боевом хосте должно помещаться семь одновременных ячеек; "
        "два — это старое правило «бронируем потолок», из-за которого владелец "
        "не мог создать приложение"
    )


def test_the_old_rule_fit_only_two_and_that_is_what_broke() -> None:
    """Обратная проверка: верните бронь потолков — и хост вместит две ячейки."""
    profile = _production_profile()
    full = profile.full_quota
    gate = CellAdmissionGate(profile)
    booked = ReservedCapacity()
    admitted = 0
    for _ in range(64):
        decision = gate.check(
            _snapshot(), existing_bundle=False, running_bundle=False, reserved=booked
        )
        if decision != AdmissionDecision(True, "admitted"):
            break
        admitted += 1
        booked = booked.plus(
            ReservedCapacity(
                cpu_cores=full.cpu_cores,
                memory_bytes=full.memory_bytes,
                disk_bytes=full.disk_bytes,
                inodes=full.inodes,
            )
        )
    assert admitted == 2


def test_container_ceilings_are_untouched() -> None:
    """Правка меняет только учёт: пределы контейнеров остаются прежними.

    Одинокая ячейка по-прежнему может разогнаться до полного объёма — процессор
    делится по времени, и запрещать разгон, когда хост пуст, незачем.
    """
    profile = _production_profile()
    assert profile.full_quota.cpu_cores == pytest.approx(3.2)
    assert profile.full_quota.memory_bytes == 6 * 1024**3 + 128 * 1024**2
    assert profile.active_machine_quota.cpu_cores == pytest.approx(1.0)
    assert profile.active_machine_quota.memory_bytes == 2 * 1024**3


def test_booking_is_the_working_set_not_the_ceiling() -> None:
    profile = _production_profile()
    booking = ReservedCapacity.from_profile(profile)
    assert booking.cpu_cores == pytest.approx(1.0), "по процессору считает машина приложения"
    assert booking.memory_bytes == 3 * 1024**3, "машина, её ядро и её база"
    # Диск делить нельзя: он остаётся по полной броне.
    assert booking.disk_bytes == profile.full_quota.disk_bytes


def test_explicit_settings_win_over_the_derived_working_set() -> None:
    """Владелец должен уметь задать объём руками, не трогая код."""
    from dataclasses import replace

    profile = replace(
        _production_profile(), admission_cpu_cores=0.5, admission_memory_bytes=1024**3
    )
    booking = ReservedCapacity.from_profile(profile)
    assert booking.cpu_cores == pytest.approx(0.5)
    assert booking.memory_bytes == 1024**3
    assert _fits(profile) == 14


def test_a_working_set_larger_than_the_ceiling_is_clamped() -> None:
    """Опечатка в настройке не должна бронировать больше, чем ячейке разрешено."""
    from dataclasses import replace

    profile = replace(
        _production_profile(), admission_cpu_cores=99.0, admission_memory_bytes=99 * 1024**3
    )
    booking = ReservedCapacity.from_profile(profile)
    assert booking.cpu_cores == profile.full_quota.cpu_cores
    assert booking.memory_bytes == profile.full_quota.memory_bytes


def test_a_verification_candidate_is_still_measured_by_its_full_size() -> None:
    """Две мерки нельзя смешивать, и вот почему.

    Проверочный кандидат (кандидат отката) живёт минуты и всё это время
    действительно считает: ставит зависимости, собирает, прогоняет проверки.
    У него отдельный бюджет процессора, и этот бюджет сравнивается с размером
    ОДНОГО кандидата. Если мерить кандидата рабочим объёмом долгоживущей ячейки,
    проверка «бюджет меньше одного кандидата» перестанет срабатывать там, где
    она спасала: на проде бюджет 1.0 при кандидате 1.45 отказывал КАЖДОМУ откату
    на пустом хосте, и отказ читался как «сейчас занято, повторите позже».
    """
    profile = _production_profile()
    runtime_gate = CellAdmissionGate(profile)
    verification_gate = CellAdmissionGate(
        profile, workload="verification", verification_cpu_cores=4.0
    )

    runtime_required = runtime_gate.required()
    verification_required = verification_gate.required()

    assert isinstance(runtime_required, ReservedCapacity)
    assert isinstance(verification_required, ReservedCapacity)
    assert runtime_required.cpu_cores == pytest.approx(1.0)
    assert verification_required.cpu_cores == pytest.approx(profile.full_quota.cpu_cores)
