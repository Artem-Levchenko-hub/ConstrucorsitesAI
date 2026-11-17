"""Unknown SDK source shapes do not buy repairs or bypass real proof gates."""
import json

import pytest

from tests.test_max_generation_contract import _portable_coffee_files
from tests.test_max_management_generation import MISSING_SELECTION_PAGE, connection_files
from yleum_api.services import max_management_generation as management
from yleum_api.services.max_generation_contract import max_completion_gap, max_source_completion_gap


def unavailable_interpreter(*_args, **_kwargs):
    raise AssertionError("generation acceptance must not interpret product syntax")


def test_source_advisory_is_optional_and_never_implementation_proof(monkeypatch):
    files = connection_files(MISSING_SELECTION_PAGE, ("catalog",))
    assert management.management_source_advisory(files) is not None
    monkeypatch.setattr(management, "_Connections", unavailable_interpreter)

    assert management.management_source_gap(files) is None
    assert management.management_implemented_capabilities(files) == frozenset()


@pytest.mark.parametrize("damage", ["json", "hash", "version"])
def test_generation_still_rejects_a_corrupted_server_owned_contract(monkeypatch, damage):
    files = connection_files(MISSING_SELECTION_PAGE, ("catalog",))
    contract = json.loads(files[".omnia/management.json"])
    if damage == "json":
        files[".omnia/management.json"] = "{"
    else:
        contract["contract_hash" if damage == "hash" else "version"] = "invalid"
        files[".omnia/management.json"] = json.dumps(contract)
    monkeypatch.setattr(management, "_Connections", unavailable_interpreter)

    assert "Invalid .omnia/management.json" in str(management.management_source_gap(files))


def test_finalization_can_reach_real_checks_without_static_sdk_interpretation(monkeypatch):
    files = _portable_coffee_files()
    files.update(connection_files(MISSING_SELECTION_PAGE, ("catalog",)))
    # Reuse the known reachable product and styles, with an opaque helper the
    # optional interpreter cannot understand. A compiler must judge its code.
    files["src/lib/opaque.ts"] = "export class Cache { value = 1; }"
    monkeypatch.setattr(management, "_Connections", unavailable_interpreter)

    assert max_source_completion_gap("Меню кофейни", files, portable=True) is None
    assert "runtime_check" in str(max_completion_gap("Меню кофейни", files, {}, portable=True))
