"""Синтетический свидетель: полностью известное состояние, закреплённое навсегда.

Ценность свидетеля в том, что его состояние известно целиком — до последней
строки. Отсюда два требования, которые здесь и держатся.

Первое: идентификаторы строк закреплены. Свидетель должен быть сравним с самим
собой от прогона к прогону, иначе «совпало» ничего не значит.

Второе: фикстура трогает только свои таблицы. Любой SQL, дотянувшийся до чужого,
разрушает знание о том, что именно изменилось и чьё оно.

Содержимое сверяется с объявленным отпечатком: подменённая фикстура — отказ, а
не тихо другой свидетель.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "restoration_qa"

# Ключи, которых не должно быть даже в синтетике: отчёты о прогоне уходят людям
# и в журналы, и привычка «тут же всё ненастоящее» — как раз то, на чём утекают
# настоящие значения.
FORBIDDEN_OUTPUT_KEYS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "api_key",
    "dsn",
    "postgresql://",
)


@dataclass(frozen=True, slots=True)
class FixtureBundle:
    """Один профиль свидетеля: схема, строки и две версии исходника."""

    profile: str
    tables: tuple[str, ...]
    row_ids: tuple[str, ...]
    schema_sql: str
    seed_sql: str
    historical_files: dict[str, str]
    current_files: dict[str, str]
    digest: str


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_fixture_digest(
    *, schema_sql: str, seed_sql: str, historical: dict[str, str], current: dict[str, str]
) -> str:
    return hashlib.sha256(
        _canonical(
            {"schema_sql": schema_sql, "seed_sql": seed_sql, "v1": historical, "v2": current}
        ).encode()
    ).hexdigest()


def fixture_digest_mismatch(*, declared: str, observed: str) -> str | None:
    """Назвать расхождение отпечатков, не раскрывая ни одного из них."""
    return None if declared == observed else "fixture_tampered"


@lru_cache(maxsize=4)
def load_fixture(profile: str) -> FixtureBundle:
    """Прочитать профиль с диска и сверить его с объявленным отпечатком."""
    manifest = json.loads((_FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("profile") != profile:
        raise ValueError(f"unknown fixture profile: {profile!r}")

    schema_sql = (_FIXTURE_ROOT / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (_FIXTURE_ROOT / "seed.sql").read_text(encoding="utf-8")
    historical = json.loads((_FIXTURE_ROOT / "v1.json").read_text(encoding="utf-8"))
    current = json.loads((_FIXTURE_ROOT / "v2.json").read_text(encoding="utf-8"))

    digest = compute_fixture_digest(
        schema_sql=schema_sql, seed_sql=seed_sql, historical=historical, current=current
    )
    mismatch = fixture_digest_mismatch(declared=manifest["fixture_digest"], observed=digest)
    if mismatch:
        raise ValueError(mismatch)

    return FixtureBundle(
        profile=profile,
        tables=tuple(manifest["tables"]),
        row_ids=tuple(manifest["row_ids"]),
        schema_sql=schema_sql,
        seed_sql=seed_sql,
        historical_files=historical,
        current_files=current,
        digest=digest,
    )


__all__ = [
    "FORBIDDEN_OUTPUT_KEYS",
    "FixtureBundle",
    "compute_fixture_digest",
    "fixture_digest_mismatch",
    "load_fixture",
]
