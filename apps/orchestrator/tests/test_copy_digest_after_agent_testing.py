"""Достаточно ли «убери за собой», чтобы копия снова совпала с источником.

25.09.2026 прогон 0a058f14 упёрся в `candidate_business_data_changed`: деловые
данные изолированной копии разошлись с данными владельца. В задании агенту было
велено делать на копии настоящие записи и не было сказано вернуть её в исходный
вид; вчера это исправлено. Но сама по себе правка задания ничего не гарантирует,
пока не проверено, что уборка ВООБЩЕ способна вернуть отпечаток.

Здесь это и проверяется на настоящей базе и настоящей схеме проекта: отпечаток
считается по содержимому строк, поэтому вставка обратима удалением, а вот правка
существующей строки обратима только восстановлением прежних значений — включая
те, на которые агент обычно не смотрит, вроде отметки времени изменения.
"""

from __future__ import annotations

import pytest

from yleum_orchestrator.services.restoration_adaptation_workspace import (
    content_inventory_partition_digests,
)
from yleum_orchestrator.services.versioning.contracts import InventoryObject, InventoryReport

from tests._versioning_pg import pg  # noqa: F401

# Схема проекта b5c4c26d слово в слово из его миграций.
_SCHEMA = """
CREATE TABLE public.max_users (
  max_user_id text PRIMARY KEY,
  display_name text
);
CREATE TABLE public.leads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  max_user_id text NOT NULL REFERENCES public.max_users(max_user_id) ON DELETE CASCADE,
  name text NOT NULL,
  phone text NOT NULL,
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'новая'
);
INSERT INTO public.max_users VALUES ('owner-1', 'Владелец');
INSERT INTO public.leads (id, max_user_id, name, phone, note)
VALUES ('11111111-1111-1111-1111-111111111111', 'owner-1', 'Клиент 1', '+79990000001', 'Заметка 1'),
       ('22222222-2222-2222-2222-222222222222', 'owner-1', 'Клиент 2', '+79990000002', 'Заметка 2');
"""


def _inventory() -> InventoryReport:
    return InventoryReport(
        presence="present",
        coverage="complete",
        schema_analysis="complete",
        observed_on="candidate_copy",
        objects=[
            InventoryObject(
                object="public.leads",
                kind="table",
                classification="business",
                presence="present",
                row_count=2,
                count_kind="exact",
            ),
            InventoryObject(
                object="public.max_users",
                kind="table",
                classification="business",
                presence="present",
                row_count=1,
                count_kind="exact",
            ),
        ],
    )


@pytest.fixture
def digests(pg, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    from yleum_orchestrator.services import restoration_adaptation_workspace as module

    pg.run(_SCHEMA)
    monkeypatch.setattr(module, "admin_sql", lambda _b, sql, *, max_bytes: pg.run(sql))
    return pg, lambda: content_inventory_partition_digests(object(), _inventory())


def test_a_created_row_changes_the_copy(digests) -> None:
    """Иначе проверять нечего: сверка обязана замечать записи агента."""
    pg, digest = digests
    before = digest()

    pg.run(
        "INSERT INTO public.leads (id, max_user_id, name, phone) VALUES "
        "('33333333-3333-3333-3333-333333333333', 'owner-1', 'Проверка', '+70000000000');"
    )

    assert digest() != before


def test_deleting_what_the_agent_created_restores_the_copy(digests) -> None:
    """Главное: уборка ПОСЛЕ создания действительно возвращает отпечаток.

    Если бы не возвращала, вчерашнее требование «верни копию как было» было бы
    невыполнимым, и адаптация не смогла бы доказать работу с данными в принципе.
    """
    pg, digest = digests
    before = digest()

    pg.run(
        "INSERT INTO public.leads (id, max_user_id, name, phone) VALUES "
        "('33333333-3333-3333-3333-333333333333', 'owner-1', 'Проверка', '+70000000000');"
        "DELETE FROM public.leads WHERE id='33333333-3333-3333-3333-333333333333';"
    )

    assert digest() == before


def test_touching_an_existing_row_is_not_undone_by_restoring_the_visible_value(digests) -> None:
    """Ловушка: вернуть видимое значение мало, если приложение двигает отметку времени.

    Агента просят доказать изменение данных. Если он правит запись ВЛАДЕЛЬЦА и
    возвращает прежний текст, отпечаток всё равно останется другим — потому что
    отметка времени изменения уже не та. Значит правило должно быть жёстче:
    трогать чужие записи нельзя вовсе, проверять изменение надо на своей.
    """
    pg, digest = digests
    before = digest()

    pg.run(
        "UPDATE public.leads SET note='проверка', updated_at=now() "
        "WHERE id='11111111-1111-1111-1111-111111111111';"
        "UPDATE public.leads SET note='Заметка 1' "
        "WHERE id='11111111-1111-1111-1111-111111111111';"
    )

    assert digest() != before, "отпечаток вернулся — значит отметка времени не мешает"


def test_a_full_restore_of_an_existing_row_does_return(digests) -> None:
    """А полное восстановление ВСЕХ колонок отпечаток возвращает.

    То есть беда не в самой правке, а в том, что вернуть надо всё, включая
    колонки, на которые агент не смотрит. Требовать этого от него нельзя —
    проще запретить трогать чужие записи.
    """
    pg, digest = digests
    before = digest()

    pg.run(
        "CREATE TEMP TABLE kept AS SELECT * FROM public.leads "
        "WHERE id='11111111-1111-1111-1111-111111111111';"
        "UPDATE public.leads SET note='проверка', updated_at=now() "
        "WHERE id='11111111-1111-1111-1111-111111111111';"
        "DELETE FROM public.leads WHERE id='11111111-1111-1111-1111-111111111111';"
        "INSERT INTO public.leads SELECT * FROM kept;"
    )

    assert digest() == before
