"""Адреса кабинета, которым разрешено встраивать превью владельца.

Политика встраивания (CSP frame-ancestors) — единственное место, где браузер
решает, покажет он превью в кабинете или нет. Адрес кабинета менялся при
переименовании, поэтому он берётся из настроек, а запасной адрес перечисляется
рядом: пока старый кабинет жив, превью должно открываться в обоих.
"""

from __future__ import annotations

import os
import re

from yleum_orchestrator.core.config import Settings, get_settings

_ORIGIN_RE = re.compile(r"^https://[a-z0-9.-]{1,253}(:\d{1,5})?$")


def _configured() -> tuple[str, str]:
    """Настроенные адреса, а без пригодного окружения — значения по умолчанию.

    Сборщик превью намеренно не читает настройки платформы, и его тесты живут
    без окружения. Их не должен ронять заголовок: без настроек берём умолчания
    полей, то есть текущий кабинет и запасной, а не устаревший адрес.
    """
    try:
        settings = get_settings()
    except Exception:
        fields = Settings.model_fields
        return (
            str(fields["workspace_origin"].default),
            str(fields["workspace_legacy_origins"].default),
        )
    return settings.workspace_origin, settings.workspace_legacy_origins


def platform_api_url() -> str:
    """Адрес платформы, который получают контейнеры приложений.

    Тот же переезд: вшитый старый домен отправлял запросы приложения на
    запасной сервер, где его проекта нет. Значение переопределяется
    переменной окружения, умолчание — текущий кабинет.
    """
    configured = os.getenv("OMNIA_PLATFORM_API_URL") or os.getenv("YLEUM_PLATFORM_API_URL")
    if configured:
        return configured
    origins = studio_origins()
    return origins[0] if origins else str(Settings.model_fields["workspace_origin"].default)


def studio_origins() -> list[str]:
    """Проверенные https-адреса кабинета: основной, затем запасные."""
    primary, legacy = _configured()
    origins: list[str] = []
    for value in [primary, *re.split(r"[\s,]+", legacy or "")]:
        origin = (value or "").strip().rstrip("/").lower()
        if _ORIGIN_RE.match(origin) and origin not in origins:
            origins.append(origin)
    return origins
