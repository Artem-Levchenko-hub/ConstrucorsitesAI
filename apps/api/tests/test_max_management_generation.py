"""New management is explicit; legacy config/data never silently gains privileges."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_content_delivery import public_business_config
from yleum_api.services.max_project_kit import (
    MAX_SECURITY_LOCKED_FILES,
    render_max_managed_files,
)


def payload(**extra):
    return MaxProjectConfigPayload.model_validate(
        {
            "app_name": "Мастерская",
            "app_type": "booking",
            "summary": "Запрос на запись к мастеру",
            **extra,
        }
    )


def test_legacy_config_has_no_implicit_business_management():
    config = payload()
    assert config.management_modules is None
    assert public_business_config(config).get("management_modules") is None


@pytest.mark.parametrize(
    "modules",
    [
        ["orders"],
        ["bookings"],
        ["catalog", "orders", "orders"],
        ["private_diaries"],
        [True],
        "orders",
    ],
)
def test_invalid_management_selection_is_rejected(modules):
    with pytest.raises(ValidationError):
        payload(management_modules=modules)


def test_management_selection_is_private_and_catalog_patch_preserves_it():
    config = payload(management_modules=["bookings", "catalog", "leads"])
    assert [str(item.value) for item in config.management_modules] == [
        "catalog",
        "leads",
        "bookings",
    ]
    config.content = []
    saved = MaxProjectConfigPayload.model_validate(config.model_dump(mode="json"))
    assert saved.management_modules == config.management_modules
    assert "management_modules" not in public_business_config(config)
    files = render_max_managed_files(config, uuid4())
    public_source = files["src/lib/omnia/max-config.ts"]
    assert '"management_modules"' not in public_source
    manifest = json.loads(files[".omnia/management.json"])
    assert manifest["modules"] == ["catalog", "leads", "bookings"]
    assert [r["id"] for r in manifest["resources"]] == ["leads", "bookings"]


def test_new_runtime_manifest_and_sdk_are_platform_owned():
    files = render_max_managed_files(payload(management_modules=["catalog", "orders"]), uuid4())
    expected = {
        ".omnia/management.json",
        "src/lib/omnia/management-config.ts",
        "src/lib/omnia/management-auth.ts",
        "src/lib/omnia/management-store.ts",
        "src/lib/omnia/management-resources.ts",
        "src/lib/omnia/business-client.ts",
        "src/lib/omnia/business-server.ts",
        "src/app/api/omnia/owner/route.ts",
        "src/app/api/omnia/business/[resource]/route.ts",
        "drizzle/0003_owner_management.sql",
        "src/lib/omnia/app-profile-client.ts",
        "src/lib/omnia/app-profile-actions.ts",
    }
    assert expected <= files.keys()
    assert expected <= MAX_SECURITY_LOCKED_FILES
    assert "useYleumAppProfile" in files["src/lib/omnia/app-profile-client.ts"]


def test_legacy_managed_kit_has_no_order_capability():
    files = render_max_managed_files(payload(), uuid4())
    assert ".omnia/management.json" not in files
    assert "drizzle/0003_owner_management.sql" not in files
