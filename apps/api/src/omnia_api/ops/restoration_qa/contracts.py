"""Границы испытательного прогона отката: сначала доказать, потом трогать.

Этот пакет создаёт одноразовый синтетический проект и гоняет по нему настоящую
цепочку восстановления. Опасность не в том, что проверка не пройдёт, а в том,
что она пройдёт НЕ ТАМ: подцепится к чужому проекту, к рабочей базе или потратит
оплаченную генерацию до того, как свидетель достроен.

Поэтому все проверки здесь — предварительные. Они отвечают на вопрос «моё ли
это?» ДО того, как будет сделан хоть один запрос наружу: подключение к чужой
базе уже и есть то, чего нельзя допустить, и проверять его постфактум незачем.

Отказ несёт код причины и ничего больше. Ни строки подключения, ни путей, ни
чужих идентификаторов: сообщение об ошибке уходит в журналы и отчёты.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Песочница живёт только на машине прогона. Любой другой адрес означает, что
# целью оказалась чужая база, а не наша одноразовая.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_DATABASE_PREFIX = "omnia-qa-"

Sha1Hex = Field(pattern=r"^[0-9a-f]{40}$")
Sha256Hex = Field(pattern=r"^[0-9a-f]{64}$")


class QaScopeRefused(RuntimeError):
    """Цель не доказана своей — прогон не начинается.

    Это отказ, а не сбой: продолжать нельзя не потому, что что-то сломалось, а
    потому, что не подтверждено право трогать названное.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code

    def __str__(self) -> str:  # pragma: no cover - тривиально
        return self.reason_code


class QaManifest(BaseModel):
    """Единственное описание песочницы: что создано и до какого момента живо."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    run_id: UUID
    release_sha: str = Sha1Hex
    mode: Literal["isolated", "production_disposable"]
    project_id: UUID
    owner_id: UUID
    workspace_id: UUID
    fixture_digest: str = Sha256Hex
    database_identity_digest: str = Sha256Hex
    resource_manifest_digest: str = Sha256Hex
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def outlives_its_own_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("a manifest must outlive its own creation")
        return self

    @property
    def database_name(self) -> str:
        """Имя одноразовой базы выводится из прогона, а не задаётся отдельно."""
        return f"{_DATABASE_PREFIX}{self.run_id.hex}"


class CheckResult(BaseModel):
    """Одна проверка: вердикт, ревизия, на которой он получен, и доказательство."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=1, max_length=120)
    status: Literal["PASS", "FAIL", "BLOCKED_ENV", "NOT_RUN"]
    observed_release_sha: str = Sha1Hex
    evidence_digest: str = Sha256Hex
    reason_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def a_verdict_that_is_not_a_pass_names_its_reason(self) -> Self:
        # «Не прошло» без причины нечитаемо и невоспроизводимо.
        if self.status != "PASS" and not self.reason_code:
            raise ValueError("only a passing check may omit its reason")
        return self


def assert_bootstrap_scope(
    *,
    existing_project_ids: Collection[UUID],
    requested_project_id: UUID,
) -> None:
    """Песочница создаётся, а не занимается.

    Попытка взять уже существующий проект — самый дешёвый способ испортить чужую
    работу, поэтому она отвергается раньше любой записи.
    """
    if requested_project_id in set(existing_project_ids):
        raise QaScopeRefused("foreign_project")


def assert_qa_database(manifest: QaManifest, dsn: str) -> None:
    """Доказать, что база — наша одноразовая, ДО подключения к ней."""
    parts = urlsplit(dsn)
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK:
        raise QaScopeRefused("foreign_host")
    if parts.path.lstrip("/") != manifest.database_name:
        raise QaScopeRefused("foreign_database")


def assert_setup_spent_nothing(*, generation_runs: int, settlements: int) -> None:
    """Подготовка свидетеля не тратит оплаченную генерацию.

    Свидетель ещё не достроен: прогон на этом этапе ничего не доказывает, но
    деньги за него спишутся.
    """
    if generation_runs:
        raise QaScopeRefused("generation_spent")
    if settlements:
        raise QaScopeRefused("settlement_spent")


def assert_adaptive_consent(*, profile: str, explicit_prompt: str) -> None:
    """Адаптацию запускает явное согласие, а не автоматическая политика."""
    if profile == "automatic":
        raise QaScopeRefused("automatic_profile")
    if not explicit_prompt.strip():
        raise QaScopeRefused("missing_prompt")


__all__ = [
    "CheckResult",
    "QaManifest",
    "QaScopeRefused",
    "assert_adaptive_consent",
    "assert_bootstrap_scope",
    "assert_qa_database",
    "assert_setup_spent_nothing",
]
