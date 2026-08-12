from __future__ import annotations

from uuid import uuid4

from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.services.deploy_attestation import live_delete_paths
from omnia_api.services.max_project_kit import render_max_history_files


def test_generic_release_proof_deletes_every_stale_live_source() -> None:
    assert live_delete_paths(
        "blank",
        ["src/app/page.tsx", "src/stale.ts"],
        {"src/app/page.tsx": "canonical"},
    ) == ("src/stale.ts",)


def test_max_release_proof_deletes_stale_product_but_keeps_platform_base() -> None:
    assert live_delete_paths(
        "max_miniapp",
        [
            "src/components/product/ProductApp.tsx",
            "src/components/product/OldCard.tsx",
            "src/components/MaxAppProvider.tsx",
            "src/app/api/health/route.ts",
        ],
        {"src/components/product/ProductApp.tsx": "canonical"},
    ) == ("src/components/product/OldCard.tsx",)


def test_empty_max_snapshot_keeps_managed_starter_product_for_proof() -> None:
    desired = render_max_history_files(
        {},
        MaxProjectConfigPayload(app_name="Starter", app_type="custom", summary="Starter"),
        uuid4(),
    )

    assert "src/components/product/ProductApp.tsx" in desired
    assert (
        live_delete_paths(
            "max_miniapp",
            ["src/components/product/ProductApp.tsx"],
            desired,
        )
        == ()
    )
