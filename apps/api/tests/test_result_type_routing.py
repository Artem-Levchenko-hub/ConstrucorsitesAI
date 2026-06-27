"""Real-backend default routing: a `web_app` builds on the real full-stack
(nextjs-postgres-drizzle) instead of the managed-CRUD `nextjs_entities`, gated by
USE_REAL_BACKEND_DEFAULT. Owner directive «ентитиз не нужны, нужен реальный бэкенд»."""

from __future__ import annotations

from omnia_api.services import discovery


class _Settings:
    def __init__(self, flag: bool) -> None:
        self.use_real_backend_default = flag


def test_web_app_routes_to_real_backend_when_on(monkeypatch) -> None:
    monkeypatch.setattr(discovery, "get_settings", lambda: _Settings(True))
    assert discovery.result_type_to_stack("web_app") == "fullstack"
    # Non-entities types are untouched by the flag.
    assert discovery.result_type_to_stack("landing") == "spa"
    assert discovery.result_type_to_stack("tool") == "spa"
    assert discovery.result_type_to_stack("code") == "code"
    assert discovery.result_type_to_stack("static") == "static"
    assert discovery.result_type_to_stack("unknown") is None


def test_web_app_keeps_entities_when_off(monkeypatch) -> None:
    monkeypatch.setattr(discovery, "get_settings", lambda: _Settings(False))
    assert discovery.result_type_to_stack("web_app") == "nextjs_entities"
