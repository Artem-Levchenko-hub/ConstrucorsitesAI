"""Explicit process configuration for isolated legacy Docker IO unit fixtures."""

import pytest

from yleum_orchestrator.core.config import Settings
from yleum_orchestrator.services import generation_reader_inventory as readers


@pytest.fixture(autouse=True)
def legacy_reader_process_settings(monkeypatch):
    # Imported only by the legacy IO test modules. Keep the real dispatch guard;
    # provide its process Settings rather than bypassing a readiness outcome.
    settings = Settings(
        _env_file=None,
        database_url="postgresql://synthetic:synthetic@127.0.0.1/synthetic",
        internal_token="synthetic-controller-unit-token",
        generation_recovery_inventory_dir="",
    )
    monkeypatch.setattr(readers, "get_settings", lambda: settings)
    return settings
