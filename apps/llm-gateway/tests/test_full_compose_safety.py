from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "full" / "docker-compose.yml"


def _source() -> str:
    return _COMPOSE.read_text(encoding="utf-8")


def test_public_app_ports_are_loopback_only() -> None:
    source = _source()
    assert '"127.0.0.1:${GATEWAY_HOST_PORT:-8101}:8001"' in source
    assert '"127.0.0.1:${API_HOST_PORT:-8200}:8000"' in source
    assert '"127.0.0.1:${WEB_HOST_PORT:-3100}:3000"' in source


def test_api_migrates_before_it_becomes_ready() -> None:
    source = _source()
    assert "/app/.venv/bin/alembic upgrade head && exec " in source
    assert '["CMD", "curl", "-fsS", "http://localhost:8000/health"]' in source


def test_worker_waits_for_api_health() -> None:
    source = _source()
    worker = source.split("\n  worker:", maxsplit=1)[1]
    assert "api:\n        condition: service_healthy" in worker


def test_api_and_worker_never_default_production_generation_to_mock() -> None:
    source = _source()
    api, worker = source.split("\n  worker:", maxsplit=1)

    assert 'MOCK_LLM: "false"' in api
    assert 'MOCK_LLM: "false"' in worker


def test_cost_abuse_guards_are_secure_by_default() -> None:
    source = _source()
    assert "PROMPT_IP_RATE_LIMIT: ${PROMPT_IP_RATE_LIMIT:-60/hour}" in source
    assert "ALLOW_STUB_TOPUP: ${ALLOW_STUB_TOPUP:-false}" in source


def test_agent_kernel_v2_flags_reach_api_and_worker() -> None:
    source = _source()
    api, worker_and_web = source.split("\n  worker:", maxsplit=1)
    worker = worker_and_web.split("\n  web:", maxsplit=1)[0]

    for service in (api, worker):
        assert "AGENT_KERNEL_V2_ENABLED: ${AGENT_KERNEL_V2_ENABLED:-false}" in service
        assert "AGENT_KERNEL_V2_CANARY_USERS: ${AGENT_KERNEL_V2_CANARY_USERS:-}" in service
        assert "MAX_CODE_INTELLIGENCE_ENABLED: ${MAX_CODE_INTELLIGENCE_ENABLED:-false}" in service
        assert (
            "MAX_CODE_INTELLIGENCE_CANARY_USERS: ${MAX_CODE_INTELLIGENCE_CANARY_USERS:-}" in service
        )
        assert "MAX_VISUAL_SCORING_ENABLED: ${MAX_VISUAL_SCORING_ENABLED:-false}" in service
