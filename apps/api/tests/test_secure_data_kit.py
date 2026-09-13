from omnia_api.schemas.max_studio import MaxProjectConfigPayload
from omnia_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES, render_max_managed_files


def test_materialized_managed_kit_contains_closed_secure_data_dependency_graph():
    config = MaxProjectConfigPayload(
        app_name="Private notes", app_type="custom", summary="Private records",
        primary_action="Save", features=[],
    )
    files = render_max_managed_files(config)
    required = {
        "src/app/api/omnia/data/[...path]/route.ts",
        "src/lib/secure-data/crypto.ts",
        "src/lib/secure-data/store.ts",
        "src/lib/secure-data/validation.ts",
        "src/lib/secure-data/http.ts",
        "src/lib/secure-data/runtime.ts",
        "src/lib/omnia/data-client.ts",
        "drizzle/0003_secure_records.sql",
    }
    assert not required.difference(files), "Managed secure data must ship atomically"
    assert required.issubset(MAX_SECURITY_LOCKED_FILES)
