"""Fail-closed restore admission and bounded legacy preview compensation.

MAX activation requires migration-free isolated compatibility proof, which the
current Project Cell restore API does not provide. Never substitute arbitrary
manifest builds (with business database access) for that missing contract.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from omnia_api.core.errors import ApiError
from omnia_api.services import orchestrator_client


def ensure_restore_supported(template: str) -> None:
    if template == "max_miniapp":
        raise ApiError(
            "conflict",
            "Восстановление этой версии пока недоступно: среда не прошла "
            "проверку совместимости. Текущая версия сохранена.",
            409,
        )


def _patch(target: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    return {
        **{path: "" for path in current if path not in target},
        **{path: value for path, value in target.items() if current.get(path) != value},
    }


def _check_result(result: dict[str, Any], expected_writes: int) -> None:
    if result.get("ok") is False or result.get("dropped"):
        raise ValueError("Runtime rejected restore files")
    for field in (
        "package_exit_code",
        "drizzle_exit_code",
        "migration_exit_code",
        "build_exit_code",
    ):
        if field in result and str(result[field]) != "0":
            raise ValueError("Runtime restore application failed")
    if result.get("state", "hot_reloaded") != "hot_reloaded":
        raise ValueError("Runtime failed restore")
    if int(result.get("written", -1)) < expected_writes:
        raise ValueError("Runtime omitted complete restore confirmation")


async def apply_legacy_restore(
    project_id: UUID, slug: str, target: dict[str, str], current: dict[str, str]
) -> None:
    patch = _patch(target, current)
    if not patch:
        return
    try:
        result = await orchestrator_client.hot_reload(
            project_id=project_id,
            slug=slug,
            files=patch,
            empty_files=tuple(path for path in patch if path in target and target[path] == ""),
        )
        _check_result(result, sum(path in target for path in patch))
    except asyncio.CancelledError:
        # Do not publish a new HEAD on caller cancellation. The legacy transport
        # has no operation identity with which to promise remote cancellation.
        raise
    except Exception as exc:
        recovered = False
        if current:
            try:
                recovery = _patch(current, target)
                result = await orchestrator_client.hot_reload(
                    project_id=project_id,
                    slug=slug,
                    files=recovery,
                    empty_files=tuple(
                        path for path in recovery if path in current and current[path] == ""
                    ),
                )
                _check_result(result, sum(path in current for path in recovery))
                recovered = True
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        message = (
            "Не удалось применить выбранную версию. История и текущая версия сохранены."
            if recovered
            else "Не удалось применить выбранную версию. История сохранена, "
            "но рабочее превью требует восстановления."
        )
        raise ApiError("conflict", message, 409, details={"runtime_recovered": recovered}) from exc
