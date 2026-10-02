from types import SimpleNamespace

from fastapi import Response

from yleum_api.routers import auth


async def test_logout_clears_browser_cache_without_clearing_other_site_data():
    response = Response()

    await auth.logout(response, SimpleNamespace())

    assert response.headers.get("clear-site-data") == '"cache"'
    assert response.headers.get("cache-control") == "no-store"
    assert "omnia_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
