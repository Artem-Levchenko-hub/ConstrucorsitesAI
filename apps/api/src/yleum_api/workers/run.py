"""Фоновый процесс платформы: `python -m yleum_api.workers.run`.

Он не обслуживает очередь задач — очередь ушла вместе с отложенным рендером
превью конструктора сайтов. Остались долгоживущие циклы, которым нужен процесс
рядом с API: сердцебиение воркера (его видит `/api/health`), тик подписок,
уборка вложений доски задач и досведение незавершённых восстановлений версий.
Удаление приложений также завершается здесь после закрытия браузера.
Процесс держится на этих потоках и завершается только по сигналу.
"""

from __future__ import annotations

import asyncio
import threading

from yleum_api.core.config import get_settings
from yleum_api.services.project_deletion import run_project_deletion_forever
from yleum_api.services.readiness import run_worker_heartbeat_forever
from yleum_api.services.restoration_reconciliation import run_restoration_reconciliation_forever
from yleum_api.services.subscription_lifecycle import run_subscription_lifecycle_forever
from yleum_api.services.task_board_attachment_cleanup import run_attachment_cleanup_forever


def _run_worker_heartbeat() -> None:
    asyncio.run(run_worker_heartbeat_forever())


def _run_billing_lifecycle() -> None:
    asyncio.run(run_subscription_lifecycle_forever())


def _run_attachment_cleanup() -> None:
    asyncio.run(run_attachment_cleanup_forever())


def _run_restoration_reconciliation() -> None:
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    asyncio.run(run_restoration_reconciliation_forever(before_tick=wait_for_readers))


def _run_project_deletion() -> None:
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    asyncio.run(run_project_deletion_forever(before_tick=wait_for_readers))


def _start_workers() -> None:
    if get_settings().billing_lifecycle_enabled:
        threading.Thread(
            target=_run_billing_lifecycle,
            name="subscription-lifecycle",
            daemon=True,
        ).start()
    threading.Thread(
        target=_run_attachment_cleanup,
        name="task-board-attachment-cleanup",
        daemon=True,
    ).start()
    # AV19.1: restoration cancel/apply/prepare finish without a client GET.
    threading.Thread(
        target=_run_restoration_reconciliation,
        name="restoration-reconciliation",
        daemon=True,
    ).start()
    threading.Thread(
        target=_run_project_deletion,
        name="project-deletion",
        daemon=True,
    ).start()


async def _run() -> None:
    from yleum_api.services.generation.recovery_readers import reader_lifecycle

    # Existing aggregate heartbeat is liveness only, never reader authority.
    threading.Thread(target=_run_worker_heartbeat, name="worker-heartbeat", daemon=True).start()
    async with reader_lifecycle("worker"):
        _start_workers()
        await asyncio.Event().wait()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
