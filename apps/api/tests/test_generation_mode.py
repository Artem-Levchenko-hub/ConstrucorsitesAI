"""generation_mode() — freeform/catalog/plain switch + rollout % (Phase 11)."""

import pytest

from omnia_api.core import config


class _Settings:
    def __init__(self, freeform: bool, catalog: bool, pct: int = 100) -> None:
        self.use_freeform_render = freeform
        self.use_section_catalog = catalog
        self.freeform_traffic_pct = pct


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
    assert config.generation_mode(model, "proj-1") == expected


def test_rollout_zero_pct_falls_back_to_catalog(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(True, True, pct=0))
    # Freeform flag on but 0% rollout → premium gets catalog, not freeform.
    assert config.generation_mode("claude-opus-4-7", "proj-1") == "catalog"


def test_rollout_zero_pct_without_catalog_is_plain(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(True, False, pct=0))
    assert config.generation_mode("claude-opus-4-7", "proj-1") == "plain"


def test_rollout_full_pct_is_freeform(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(True, True, pct=100))
    assert config.generation_mode("claude-opus-4-7", "proj-xyz") == "freeform"


def test_rollout_partial_is_deterministic(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(True, True, pct=50))
    a = config.generation_mode("claude-opus-4-7", "proj-stable")
    b = config.generation_mode("claude-opus-4-7", "proj-stable")
    assert a == b  # same project → same bucket every time
    assert a in ("freeform", "catalog")


def test_rollout_partial_splits_projects(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: _Settings(True, True, pct=50))
    modes = {config.generation_mode("claude-opus-4-7", f"p{i}") for i in range(40)}
    # At 50% across 40 projects both buckets must appear (not all-or-nothing).
    assert modes == {"freeform", "catalog"}
