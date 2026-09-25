"""Метки Docker на время ребрендинга: пишем обе, ожидаем старую.

Метку нельзя изменить у запущенного контейнера — только пересоздав его. Значит
между выкаткой и полным оборотом ячеек на одном хосте одновременно живут
ресурсы с одним набором меток и с другим, и любая несогласованность здесь стоит
дорого: оркестратор перестаёт видеть чужие контейнеры либо отвергает свои.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from yleum_orchestrator.core.cell_resources import identity_labels
from yleum_orchestrator.core.labels import label_value, with_both


def test_every_legacy_label_gets_a_twin() -> None:
    written = with_both({"omnia.project_id": "p1", "omnia.managed": "true"})

    assert written["yleum.project_id"] == "p1"
    assert written["yleum.managed"] == "true"
    # Старое имя остаётся: по нему нас находят версии, выкаченные до ребрендинга,
    # и по нему же работают серверные фильтры Docker в этой фазе.
    assert written["omnia.project_id"] == "p1"
    assert written["omnia.managed"] == "true"


def test_foreign_labels_are_untouched() -> None:
    written = with_both({"com.docker.compose.project": "x", "omnia.kind": "dev"})

    assert written["com.docker.compose.project"] == "x"
    assert "yleum.com.docker.compose.project" not in written
    assert written["yleum.kind"] == "dev"


def test_existing_new_value_is_not_overwritten() -> None:
    written = with_both({"omnia.kind": "старое", "yleum.kind": "новое"})

    assert written["yleum.kind"] == "новое"


def test_label_value_prefers_the_new_name() -> None:
    labels = {"omnia.fencing_epoch": "3", "yleum.fencing_epoch": "4"}

    assert label_value(labels, "fencing_epoch") == "4"


def test_label_value_falls_back_to_the_legacy_name() -> None:
    """Ячейка, созданная до ребрендинга, несёт только старое имя."""
    assert label_value({"omnia.fencing_epoch": "3"}, "fencing_epoch") == "3"
    assert label_value({}, "fencing_epoch", "0") == "0"


def test_identity_expectations_stay_on_the_legacy_prefix() -> None:
    """Главная защита этого шага.

    Сверка личности ресурса требует, чтобы КАЖДАЯ ожидаемая метка нашлась на
    ресурсе. Появись в ожиданиях новый префикс — все ячейки, созданные до
    ребрендинга, разом получили бы «несовпадение личности» (409): поменять метку
    у живого контейнера невозможно. Поэтому ожидания переводятся на новое имя
    только в фазе 2, после полного оборота ячеек.
    """
    spec = SimpleNamespace(
        workspace_id=uuid4(),
        project_id=uuid4(),
        owner_id=uuid4(),
        profile_version=1,
    )

    expected = identity_labels(spec, "workspace")

    assert expected, "набор ожидаемых меток не должен быть пустым"
    assert all(key.startswith("omnia.") for key in expected)

    # И обратная сторона: ресурс, созданный уже с двумя именами, сверку проходит,
    # потому что сверка смотрит на наличие ожидаемых меток, а не на их полноту.
    on_new_resource = with_both(expected)
    assert all(on_new_resource.get(key) == value for key, value in expected.items())
