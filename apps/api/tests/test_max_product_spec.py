import pytest
from pydantic import ValidationError

from omnia_api.routers.messages import (
    _fresh_agent_step_budget,
    _fresh_max_product_spec_required,
    _merge_stored_max_product_spec,
    _stored_max_product_spec,
)
from omnia_api.schemas.max_product_spec import MaxProductSpec
from omnia_api.schemas.message import PromptRequest


def _valid_spec() -> dict[str, object]:
    return {
        "purpose": "Кофейня: заказывать напитки и получать награды",
        "audience": "Гости кофейни",
        "screens": ["Главная", "Награды", "Профиль"],
        "primary_action": "Оформить заказ",
        "primary_action_kind": "managed_write",
        "capabilities": ["Каталог", "Избранное"],
        "data": ["Напитки", "Заказы пользователя"],
        "history": True,
        "integrations": ["MAX Bridge", "MAX-профиль пользователя"],
        "style": "Чистый; цвета: кофе и молочный",
        "acceptance": [
            "Заказ доступен с главного экрана.",
            "Пустая история честно отображается без вымышленных записей.",
        ],
    }


def test_product_spec_is_strict_and_bounded() -> None:
    spec = MaxProductSpec.model_validate(_valid_spec())

    assert spec.screens == ["Главная", "Награды", "Профиль"]
    incomplete = _valid_spec()
    del incomplete["primary_action_kind"]
    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate(incomplete)

    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate({**_valid_spec(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate({**_valid_spec(), "screens": ["Главная"]})
    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate({**_valid_spec(), "screens": ["Главная", " главная "]})
    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate({**_valid_spec(), "primary_action_kind": "guessed"})
    with pytest.raises(ValidationError):
        MaxProductSpec.model_validate(
            {**_valid_spec(), "history": False, "primary_action_kind": "managed_write"}
        )


def test_product_spec_transport_stays_optional_for_non_max_requests() -> None:
    assert PromptRequest(prompt="Собери приложение").product_spec is None
    assert PromptRequest(prompt="Собери приложение", product_spec=_valid_spec()).product_spec


def test_fresh_max_build_requires_product_spec_before_starting() -> None:
    assert _fresh_max_product_spec_required(
        project_template="max_miniapp",
        is_first_build=True,
        has_product_spec=False,
    )
    assert not _fresh_max_product_spec_required(
        project_template="max_miniapp",
        is_first_build=True,
        has_product_spec=True,
    )
    assert not _fresh_max_product_spec_required(
        project_template="max_miniapp",
        is_first_build=False,
        has_product_spec=False,
    )
    assert not _fresh_max_product_spec_required(
        project_template="blank",
        is_first_build=True,
        has_product_spec=False,
    )


def test_strict_product_kernel_never_inherits_large_legacy_turn_budget() -> None:
    assert _fresh_agent_step_budget(kernel_product_run=True, configured_steps=120) == 4
    assert _fresh_agent_step_budget(kernel_product_run=False, configured_steps=120) == 30


def test_failed_fresh_run_can_recover_its_strict_product_spec() -> None:
    spec = MaxProductSpec.model_validate(_valid_spec())
    stored = _merge_stored_max_product_spec({"build_plan": {"summary": "keep"}}, spec)

    assert stored["build_plan"] == {"summary": "keep"}
    assert _stored_max_product_spec(stored) == spec
    assert _stored_max_product_spec({"max_product_spec": {"purpose": "partial"}}) is None
