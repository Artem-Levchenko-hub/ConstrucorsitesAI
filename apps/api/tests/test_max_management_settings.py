import pytest

from yleum_api.core.errors import ApiError
from yleum_api.routers.max_studio import _prepare_config_change
from yleum_api.schemas.max_studio import MaxProjectConfigPayload


def config(**extra):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Мастерская",
            "app_type": "custom",
            "summary": "Описание",
            **extra,
        }
    )


def test_settings_require_current_revision_without_changing_catalog():
    current = config()
    desired = config(app_name="Новое название")
    with pytest.raises(ApiError) as caught:
        _prepare_config_change(current, desired, current_version=5, expected_version=4)
    assert caught.value.status_code == 409
    assert (
        _prepare_config_change(current, desired, current_version=5, expected_version=5) == desired
    )


def test_unchanged_retry_converges_and_new_config_needs_no_revision():
    current = config()
    assert (
        _prepare_config_change(current, current, current_version=5, expected_version=4) == current
    )
    desired = config(app_name="Первый проект")
    assert (
        _prepare_config_change(current, desired, current_version=0, expected_version=None)
        == desired
    )


def test_older_settings_client_cannot_erase_the_explicit_module_selection():
    current = config(management_modules=["catalog", "orders"])
    desired = config(app_name="Новое название")
    actual = _prepare_config_change(current, desired, current_version=5, expected_version=5)
    assert actual.management_modules == current.management_modules
    assert actual.app_name == "Новое название"
