"""Reviewed finite interpretations, not a natural-language completeness oracle.

Only CRUD plus reload/new authenticated-context persistence is covered. No
database durability, actual MAX identity, cross-user or subjective-design claim.
The frozen original request and every discovery obligation remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yleum_api.services import max_behavior_proof as behavior
from yleum_api.services.generation.recovery_contract import RecoveryContractError

PROFILE = "notes_acceptance_v1"
RECEIPT_KEY = "notes_acceptance_v1"
PROMPTS = (
    "Создай приложение заметок: список записей с заголовком и текстом, добавление, "
    "редактирование и удаление. Изменения должны сохраняться после обновления страницы "
    "и открытия нового окна с тем же пользователем.",
    "Мне нужен личный блокнот. Покажи список заметок; у каждой есть название и текст. "
    "Дай создать заметку, изменить её и удалить. После перезагрузки страницы и в новом "
    "окне того же пользователя записи и изменения должны оставаться.",
)
CHECKS = (
    "create",
    "read",
    "update",
    "reload",
    "fresh_context",
    "delete",
    "delete_fresh_context",
    "preserve_existing",
)
LIMITS = {
    "database_durability": "NOT_PROVEN",
    "server_restart": "NOT_PROVEN",
    "actual_max": "NOT_PROVEN",
    "second_identity": "NOT_PROVEN",
}


def need(value: object, message: str = "notes acceptance unconfirmed") -> None:
    if not value:
        raise RecoveryContractError(message)


@dataclass(frozen=True)
class NotesContract:
    template: str
    request_sha256: str
    manifest_sha256: str
    requirements: tuple[str, ...]

    @property
    def contract_digest(self) -> str:
        return behavior.digest(self.to_json())

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": PROFILE,
            "template": self.template,
            "request_sha256": self.request_sha256,
            "manifest_sha256": self.manifest_sha256,
            "coverage": [
                {"requirement_id": item, "checks": list(CHECKS)} for item in self.requirements
            ],
            "limits": LIMITS,
        }


def reviewed_contract(run: Any, template: str) -> NotesContract | None:
    """Exact deployed review registry; an extra axis/word never inherits coverage."""
    from yleum_api.services.generation.recovery_requirements import (
        STATE_KEY,
        frozen_requirements,
    )

    requirements = frozen_requirements(run)
    manifest = run.agent_state[STATE_KEY]
    if (
        template not in {"max_miniapp", "max-miniapp-nextjs"}
        or manifest["original_prompt"] not in PROMPTS
        or manifest["named"] is not None
    ):
        return None
    # These two reviewed requests have one whole-request obligation, with an
    # explicit eight-check interpretation. Discovery is NOT subtracted/ignored.
    required = tuple(r for r in requirements if r.required)
    if (
        len(required) != 1
        or required[0].check_kind != "manual"
        or required[0].text != manifest["original_prompt"]
    ):
        return None
    return NotesContract(template, run.prompt_hash, manifest["manifest_sha256"], (required[0].id,))


def validate_outcomes(raw: object) -> None:
    need(type(raw) is dict and set(raw) == set(CHECKS), "notes checks incomplete")
    assert isinstance(raw, dict)
    need(all(raw[name] is True for name in CHECKS), "notes checks failed")


def validate_receipt(
    raw: object,
    *,
    run: Any,
    bound: behavior.CandidateBehaviorBinding,
    contract: NotesContract,
    binding: dict[str, Any],
) -> dict[str, Any]:
    """Validate a private server record, never accept a caller-provided witness."""
    need(type(raw) is dict, "notes receipt missing")
    assert isinstance(raw, dict)
    expected_keys = {
        "version",
        "profile",
        "owner_id",
        "binding",
        "source_binding",
        "contract",
        "compiled",
        "outcomes",
        "runner_sha256",
        "receipt_digest",
    }
    need(
        set(raw) == expected_keys and type(raw["version"]) is int and raw["version"] == 1,
        "notes receipt shape changed",
    )
    from yleum_api.services.notes_acceptance_browser import runner_digest

    need(
        raw["profile"] == PROFILE
        and raw["owner_id"] == str(run.user_id)
        and raw["binding"] == bound.to_json()
        and raw["source_binding"] == binding
        and raw["contract"] == contract.to_json()
        and raw["runner_sha256"] == runner_digest(),
        "notes receipt binding changed",
    )
    validate_outcomes(raw["outcomes"])
    compiled = raw["compiled"]
    need(
        type(compiled) is dict
        and set(compiled) == {"assets", "platform_assets", "compilation_receipt_sha256", "digest"},
        "notes compilation missing",
    )
    try:
        witness = behavior.CompiledAssetWitness(
            bound,
            tuple(behavior.ObservedAsset(**item) for item in compiled["assets"]),
            compiled["compilation_receipt_sha256"],
            "served_candidate_compilation_v1",
            tuple(behavior.ObservedAsset(**item) for item in compiled["platform_assets"]),
        )
        behavior._compiled(witness, bound)
        need(witness.digest == compiled["digest"], "notes compilation changed")
    except (KeyError, TypeError, ValueError, behavior.BehaviorProofError) as exc:
        raise RecoveryContractError("notes compilation invalid") from exc
    need(
        raw["receipt_digest"]
        == behavior.digest({key: value for key, value in raw.items() if key != "receipt_digest"}),
        "notes receipt digest changed",
    )
    return raw
