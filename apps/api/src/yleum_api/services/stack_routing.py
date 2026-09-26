"""Подготовка контейнера проекта перед сборкой.

Модуль остался от автоматической маршрутизации стеков: когда продукт умел
делать сайты разных видов, он по итогам опроса переключал шаблон проекта и
пересобирал его git. Теперь создаётся только один вид проекта — приложение MAX,
и переключать не на что: все ветки «не MAX» были недостижимы.

Живой осталась одна обязанность — попросить оркестратор поднять контейнер
разработки до сборки, чтобы горячая перезагрузка после неё била в живую цель.
Вызов мягкий: заминка с контейнером не срывает сборку, снимок всё равно ложится
в git.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from yleum_api.schemas.project import orchestrator_template
from yleum_api.services import orchestrator_client

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Discovery emits its own small stack vocabulary (``services/discovery._STACKS``).
# Map it onto the project ``template`` values. ``static`` has no entry — it means
# "leave the project as the static template it already is". ``spa`` →
# ``vite-react-spa`` (the no-backend interactive escape hatch, Phase 7.2);
# ``orchestrator_template`` + ``is_fullstack`` already treat ``spa`` as a
# container stack, so the only wiring it needed was this discovery→template entry.
async def ensure_provisioned(
    project_id: UUID,
    slug: str,
    template: str,
    *,
    require_ready: bool = False,
) -> bool:
    """Provision the project's orchestrator dev container if the stack needs one.

    No-op (returns ``False``) for static templates. For container templates calls
    the orchestrator's idempotent ``provision`` (safe to call when the container
    already exists). Normal background warming remains fail-soft. Agentic builds
    pass ``require_ready=True``: they wait through cold image provisioning and
    fail before invoking the model if no running container is available.
    """
    orch_template = orchestrator_template(template)
    if orch_template is None:
        return False  # static — no container
    try:
        payload = await orchestrator_client.provision(
            project_id=project_id,
            slug=slug,
            template=orch_template,
            tier="free",
            # A stale template image can legitimately take several minutes to
            # rebuild. The prompt itself is already a background run, so waiting
            # here is safe and prevents the agent working against no container.
            timeout=420.0 if require_ready else 30.0,
        )
        if require_ready and payload.get("state") != "running":
            raise RuntimeError(f"runtime did not become ready (state={payload.get('state')!r})")
        log.info("stack_routing: provisioned %s (%s)", project_id, orch_template)
        return True
    except Exception as exc:
        if require_ready:
            log.error(
                "stack_routing: required runtime failed for %s (%s): %r",
                project_id,
                orch_template,
                exc,
            )
            raise
        log.warning(
            "stack_routing: provision failed for %s (%s) — build continues: %r",
            project_id,
            orch_template,
            exc,
        )
        return False


__all__ = ["ensure_provisioned"]
