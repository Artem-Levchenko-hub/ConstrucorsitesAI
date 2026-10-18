"""A reviewed ordinary request is not a model-authored acceptance receipt."""

import hashlib
from types import SimpleNamespace

import pytest

from yleum_api.services import behavior_driver_configuration as configuration


def test_separate_installed_notes_profile_is_available_without_widening_named_driver(monkeypatch):
    monkeypatch.setattr(configuration, "_pin", lambda *_args: None)
    settings = SimpleNamespace(
        env="test",
        max_behavior_browser_executable="/trusted/chromium",
        max_behavior_browser_sha256="a" * 64,
        max_behavior_adapter_registry='{"max-miniapp-nextjs":{"preset":"notes_acceptance_v1"}}',
        max_behavior_vendor_asset_registry="",
    )
    driver = configuration.configured_behavior_driver(object(), settings)
    assert driver is not None
    assert driver.supported_capabilities == frozenset({"notes_acceptance_v1"})


def frozen_notes(prompt, spec=None):
    from uuid import uuid4

    from yleum_api.services.build_spec import FidelitySpec
    from yleum_api.services.generation.recovery_requirements import store_frozen_requirements

    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        agent_state={},
    )
    store_frozen_requirements(run, prompt, spec or FidelitySpec(), None)
    return run


@pytest.mark.parametrize("index", [0, 1])
def test_two_ordinary_reviewed_requests_keep_whole_obligation(index):
    from yleum_api.services.generation.notes_acceptance import PROMPTS, reviewed_contract
    from yleum_api.services.generation.recovery_requirements import frozen_requirements

    run = frozen_notes(PROMPTS[index])
    before = tuple(frozen_requirements(run))
    contract = reviewed_contract(run, "max_miniapp")
    assert contract is not None
    assert contract.requirements == tuple(r.id for r in before if r.required)
    assert all(r.check_kind == "manual" for r in before)
    assert frozen_requirements(run) == before


@pytest.mark.parametrize(
    "extra",
    [" Отправляй email.", " Храни в PostgreSQL.", " Сохраняй после перезапуска сервера.", " "],
)
def test_unreviewed_extra_obligation_never_inherits_coverage(extra):
    from yleum_api.services.generation.notes_acceptance import PROMPTS, reviewed_contract

    assert reviewed_contract(frozen_notes(PROMPTS[0] + extra), "max_miniapp") is None


@pytest.mark.parametrize(
    "axis",
    [
        {"dark_mode": False},
        {"sections": ("notes",)},
        {"tone": "friendly"},
        {"primary_family": "violet"},
    ],
)
def test_uncovered_discovery_axis_is_not_subtracted(axis):
    from yleum_api.services.build_spec import FidelitySpec
    from yleum_api.services.generation.notes_acceptance import PROMPTS, reviewed_contract

    assert reviewed_contract(frozen_notes(PROMPTS[0], FidelitySpec(**axis)), "max_miniapp") is None


@pytest.mark.parametrize("damage", ["missing_reload", "missing_context", "false_read", "integer"])
def test_aggregate_requires_every_actual_persistence_check(damage):
    from yleum_api.services.generation.notes_acceptance import CHECKS, validate_outcomes
    from yleum_api.services.generation.recovery_contract import RecoveryContractError

    outcomes = dict.fromkeys(CHECKS, True)
    if damage == "missing_reload":
        del outcomes["reload"]
    elif damage == "missing_context":
        del outcomes["fresh_context"]
    else:
        outcomes["read"] = False if damage == "false_read" else 1
    with pytest.raises(RecoveryContractError):
        validate_outcomes(outcomes)


@pytest.mark.parametrize("version", [1, 2, True, 3])
def test_seal_version_and_old_reader_are_strict(version):
    import json
    from uuid import uuid4

    from pydantic import ValidationError

    from tests.test_generation_recovery_requirements import current_binding
    from yleum_api.services.generation.recovery_acceptance import AcceptanceSeal, AcceptanceSealV2

    value = {
        "version": version,
        "profile": "notes_acceptance_v1",
        "contract_digest": "f" * 64,
        "run_id": str(uuid4()),
        "project_id": str(uuid4()),
        "user_id": str(uuid4()),
        "prompt_hash": "a" * 64,
        "manifest_sha256": "b" * 64,
        "binding": current_binding().model_dump(mode="json"),
        "requirements": ["c" * 64],
        "candidate_id": str(uuid4()),
        "proof_key": "d" * 64,
        "build_ref": "build-ref",
        "receipt_digest": "e" * 64,
    }
    with pytest.raises(ValidationError):
        AcceptanceSeal.model_validate_json(json.dumps(value), strict=True)
    if type(version) is int and version == 2:
        assert AcceptanceSealV2.model_validate_json(json.dumps(value), strict=True).version == 2
    else:
        with pytest.raises(ValidationError):
            AcceptanceSealV2.model_validate_json(json.dumps(value), strict=True)
