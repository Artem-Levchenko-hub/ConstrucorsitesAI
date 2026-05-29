"""generation_mode() — the single freeform/catalog/plain switch (Phase 11)."""

import pytest

from omnia_api.core import config


class _Settings:
    def __init__(self, freeform: bool, catalog: bool) -> None:
        self.use_freeform_render = freeform
        self.use_section_catalog = catalog


@pytest.mark.parametrize(
    "freeform,catalog,model,expected",
    [
        # premium tier respects the flags, freeform wins over catalog
        (True, True, "claude-opus-4-7", "freeform"),
        (False, True, "claude-opus-4-7", "catalog"),
        (False, False, "claude-opus-4-7", "plain"),
        (True, False, "gpt-5", "freeform"),
        # budget/balanced tier is ALWAYS plain — never catalog/freeform
        (True, True, "claude-haiku-4-5", "plain"),
        (False, True, "claude-haiku-4-5", "plain"),
        (True, True, "gpt-5-mini", "plain"),
        # unknown / None → default tier (balanced) → plain
        (True, True, None, "plain"),
        (True, True, "some-future-model", "plain"),
    ],
)
def test_generation_mode(monkeypatch, freeform, catalog, model, expected):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(freeform, catalog))
    assert config.generation_mode(model) == expected
