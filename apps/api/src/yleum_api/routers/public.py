from pathlib import Path

from fastapi import APIRouter, Response, status

from yleum_api.core.errors import ApiError
from yleum_api.services.template_materialization import read_kit_asset

# Select-mode inspector — single source of truth is static/omnia-inspector.js (a
# synced copy ships in the orchestrator Next.js template; a drift test keeps them
# identical). Read once at import — a missing file fails loudly at startup rather
# than silently per-request.
_INSPECTOR_JS = (
    Path(__file__).resolve().parent.parent / "static" / "omnia-inspector.js"
).read_text(encoding="utf-8")

# Canonical kit assets served slug-independently at /api/kit/<file>. Every
# Project Cell draft-preview vhost proxies `/_omnia/inspector.js` here (see the
# orchestrator's nginx_writer), so the MAX editor's select-mode depends on it.
# Mounted under /api (nginx already proxies that to this service). Read once at
# import; filenames whitelisted (no path traversal).
_KIT_ASSET_MIME = {
    "omnia-kit.css": "text/css; charset=utf-8",
    "omnia-kit.js": "application/javascript; charset=utf-8",
    "anime.min.js": "application/javascript; charset=utf-8",
    "omnia-inspector.js": "application/javascript; charset=utf-8",
}
_KIT_ASSETS: dict[str, bytes] = {
    name: read_kit_asset(name)
    for name in _KIT_ASSET_MIME
    if name != "omnia-inspector.js"
}
_KIT_ASSETS["omnia-inspector.js"] = _INSPECTOR_JS.encode("utf-8")

kit_router = APIRouter(prefix="/api/kit", tags=["kit"], include_in_schema=False)


@kit_router.get("/{file}", response_class=Response)
async def get_kit_asset(file: str) -> Response:
    """Serve a whitelisted Omnia-kit asset."""
    data = _KIT_ASSETS.get(file)
    if data is None:
        raise ApiError(
            "not_found", f"kit asset {file} not found", status.HTTP_404_NOT_FOUND
        )
    return Response(
        content=data,
        media_type=_KIT_ASSET_MIME[file],
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
