"""Exercise production workflow origins through the canary's HTTP boundaries."""

from pathlib import Path

import httpx
import pytest
import yaml

from tests import production_canary_fakes as fakes
from yleum_api.ops.production_canary import (
    CanaryConfig,
    CanaryConfigurationError,
    CanaryFailure,
    ProductionCanary,
)


def _workflow_env(monkeypatch):
    fakes.canary_env(monkeypatch)
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github/workflows/production-generation-canary.yml")
        .read_text(encoding="utf-8")
    )
    step = next(
        step for step in workflow["jobs"]["canary"]["steps"]
        if step.get("run") == "uv run python scripts/production_generation_canary.py"
    )
    for name in ("PRODUCTION_CANARY_BASE_URL", "PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES"):
        monkeypatch.delenv(name, raising=False)
        if name in step["env"]:
            monkeypatch.setenv(name, step["env"][name])
    monkeypatch.delenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", raising=False)


def _run_preview(monkeypatch, host):
    _workflow_env(monkeypatch)
    monkeypatch.setattr(
        fakes,
        "SIGNED_PREVIEW_URL",
        f"https://{host}/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    production = fakes.FakeProduction()
    origins = []

    def handler(request):
        origins.append(request.url.host)
        return production.handle(request)

    canary = ProductionCanary(
        CanaryConfig.from_env(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )
    return canary, origins, production


@pytest.mark.parametrize("host", ["demo.dev.yleum.ru", "demo.dev2.yleum.ru"])
def test_production_workflow_completes_canary_on_either_registered_preview_host(monkeypatch, host):
    canary, origins, _ = _run_preview(monkeypatch, host)

    result = canary.run()

    assert result.cleanup_complete
    assert set(origins) == {"yleum.ru", host}


@pytest.mark.parametrize(
    "host",
    [
        "demo.unknown.yleum.ru", "demo.dev.yleum.ru.attacker.example",
        "demo.notdev.yleum.ru", "dev.yleum.ru", "demo.preview.lead-generator.ru",
    ],
)
def test_production_workflow_rejects_unlisted_preview_before_http_request(monkeypatch, host):
    canary, origins, production = _run_preview(monkeypatch, host)

    with pytest.raises(CanaryFailure, match="preview session URL is invalid"):
        canary.run()

    assert host not in origins
    assert production.paths("DELETE") == [f"/api/projects/{fakes.PROJECT_ID}"]


@pytest.mark.parametrize(
    "suffixes",
    [".dev.yleum.ru,.yleum.ru", ".com", ".dev.yleum.ru,", "", ".dev.yleum.ru,*.dev2.yleum.ru"],
)
def test_preview_allowlist_rejects_every_broad_or_invalid_entry(monkeypatch, suffixes):
    fakes.canary_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES", suffixes)

    with pytest.raises(CanaryConfigurationError):
        CanaryConfig.from_env()


def test_singular_preview_configuration_remains_supported(monkeypatch):
    fakes.canary_env(monkeypatch)
    monkeypatch.delenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIXES", raising=False)
    monkeypatch.setenv("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".preview.lead-generator.ru")

    result = ProductionCanary(
        CanaryConfig.from_env(),
        transport=fakes.FakeProduction().transport(),
        sleep=lambda _seconds: None,
    ).run()

    assert result.cleanup_complete
