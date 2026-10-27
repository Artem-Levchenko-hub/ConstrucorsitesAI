"""Фоновый процесс платформы: `python -m yleum_api.workers.run`.

Он не обслуживает очередь задач — очередь ушла вместе с отложенным рендером
превью конструктора сайтов. Остались долгоживущие циклы, которым нужен процесс
рядом с API: сердцебиение воркера (его видит `/api/health`), тик подписок,
уборка вложений доски задач, письма поддержки и безопасности аккаунта,
досведение восстановлений версий.
Удаление приложений также завершается здесь после закрытия браузера.
Процесс держится на этих потоках и завершается только по сигналу.
"""

from __future__ import annotations

import asyncio
import threading

from yleum_api.core.config import get_settings
from yleum_api.services.account_security_email import run_account_security_email_forever
from yleum_api.services.project_deletion import run_project_deletion_forever
from yleum_api.services.readiness import run_worker_heartbeat_forever
from yleum_api.services.restoration_reconciliation import run_restoration_reconciliation_forever
from yleum_api.services.subscription_lifecycle import run_subscription_lifecycle_forever
from yleum_api.services.support_email import run_support_email_forever
from yleum_api.services.task_board_attachment_cleanup import run_attachment_cleanup_forever


def _run_worker_heartbeat() -> None:
    asyncio.run(run_worker_heartbeat_forever())


def _run_billing_lifecycle() -> None:
    asyncio.run(run_subscription_lifecycle_forever())


def _run_attachment_cleanup() -> None:
    asyncio.run(run_attachment_cleanup_forever())


def _run_support_email() -> None:
    asyncio.run(run_support_email_forever())


def _run_account_security_email() -> None:
    asyncio.run(run_account_security_email_forever())


def _run_restoration_reconciliation() -> None:
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    asyncio.run(run_restoration_reconciliation_forever(before_tick=wait_for_readers))


def _run_project_deletion() -> None:
    from yleum_api.services.generation.recovery_readers import wait_for_readers

    asyncio.run(run_project_deletion_forever(before_tick=wait_for_readers))


def _start_workers() -> None:
    workers = [
        ("task-board-attachment-cleanup", _run_attachment_cleanup),
        # AV19.1: restoration cancel/apply/prepare finish without a client GET.
        ("restoration-reconciliation", _run_restoration_reconciliation),
        ("project-deletion", _run_project_deletion),
        ("support-email", _run_support_email),
        ("account-security-email", _run_account_security_email),
    ]
    if get_settings().billing_lifecycle_enabled:
        workers.insert(0, ("subscription-lifecycle", _run_billing_lifecycle))
    for name, target in workers:
        threading.Thread(
            target=target,
            name=name,
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
