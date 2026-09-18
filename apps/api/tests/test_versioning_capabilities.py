"""AV06 source check: which draft functions an adapted result dropped."""

from omnia_api.services.versioning_capabilities import (
    capability_gap,
    lost_capabilities,
    route_capabilities,
)

ROUTE = "export async function GET() {}\nexport function DELETE() {}\n"


def test_route_capabilities_cover_methods_groups_and_platform_routes():
    files = {
        "src/app/api/visits/route.ts": ROUTE,
        "src/app/(shop)/api/orders/[id]/route.ts": "export const PATCH = async () => null",
        "src/app/api/omnia/config/route.ts": ROUTE,
        "src/app/api/health/route.ts": ROUTE,
        "src/app/visits/page.tsx": "export default function Page() {}",
    }
    assert route_capabilities(files) == {
        ("GET", "/api/visits"), ("DELETE", "/api/visits"), ("PATCH", "/api/orders/[id]"),
    }


def test_lost_methods_are_listed_precisely():
    before = {"src/app/api/visits/route.ts": ROUTE}
    after = {"src/app/api/visits/route.ts": "export async function GET() {}"}
    assert lost_capabilities(before, after) == [("DELETE", "/api/visits")]
    gap = capability_gap(before, after)
    assert gap is not None and "DELETE /api/visits" in gap


def test_added_functions_are_not_a_gap():
    assert capability_gap({}, {"src/app/api/visits/route.ts": ROUTE}) is None
