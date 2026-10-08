"""Controller-owned evidence for explicitly requested, supported UI behaviors.

No browser driver is installed by default. Model output, tool counters, source
matches and owner-preview authorization alone are never behavioral evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, SupportsIndex, cast
from uuid import UUID

from yleum_api.services.behavior_platform_assets import PLATFORM_ASSET_LOCATIONS

CONTRACT_VERSION = "max-named-behavior-v2"
PROBE_VERSION = "ac-coffee-process-browser-v3"
PROBE_SHA256 = "919427c0c28f42a418dfae768c540a3a99bdc3800b665f692ff035c703616d28"
STATE_KEY = "max_named_behavior_contract"
RECEIPT_KEY = "max_named_behavior_receipt"
_HEX = re.compile("[0-9a-f]{64}")
_SUPPORTED_TEMPLATES = frozenset({"max_miniapp", "max-miniapp-nextjs"})
_CAPABILITIES = frozenset({"task_density_v1", "header_theme_v1", "coffee_local_summary_v1"})
_SAFE_CODES = frozenset(
    {
        "BEHAVIOR_CONTRACT_INVALID",
        "BEHAVIOR_REQUEST_BINDING_INVALID",
        "BEHAVIOR_DRIVER_UNAVAILABLE",
        "BEHAVIOR_DRIVER_FAILED",
        "BEHAVIOR_CONFIGURATION_UNAVAILABLE",
        "BEHAVIOR_COMPILATION_UNAVAILABLE",
        "BEHAVIOR_COMPILATION_IDENTITY_CHANGED",
        "BEHAVIOR_PLATFORM_ASSETS_INVALID",
        "BEHAVIOR_BROWSER_DEADLINE",
        "BEHAVIOR_BROWSER_CANCELLED",
        "BEHAVIOR_BROWSER_CLEANUP_FAILED",
        "BEHAVIOR_ADAPTER_UNSUPPORTED",
        "BEHAVIOR_PREVIEW_UNAVAILABLE",
        "BEHAVIOR_MEASUREMENTS_INVALID",
        "BEHAVIOR_MEASUREMENTS_PARTIAL",
        "BEHAVIOR_VIEWPORT_UNVERIFIED",
        "BEHAVIOR_VIEW_UNVERIFIED",
        "BEHAVIOR_PAINT_UNVERIFIED",
        "BEHAVIOR_NEEDS_CHANGES",
        "BEHAVIOR_COMPILED_BINDING_INVALID",
        "BEHAVIOR_COMPILED_ASSETS_MISSING",
        "BEHAVIOR_COMPILED_ASSETS_INVALID",
        "BEHAVIOR_COMPILED_ASSETS_CHANGED",
        "BEHAVIOR_OBSERVATION_BINDING_INVALID",
        "BEHAVIOR_OBSERVATION_UNVERIFIED",
        "BEHAVIOR_RECEIPT_MISSING",
        "BEHAVIOR_RECEIPT_UNVERIFIED",
        "BEHAVIOR_CANDIDATE_BINDING_INVALID",
        "BEHAVIOR_ADAPTATION_ADAPTER_UNSUPPORTED",
    }
)


class BehaviorProofError(RuntimeError):
    def __init__(self, code: str, status: str = "NEEDS_REVIEW") -> None:
        self.code = code if code in _SAFE_CODES else "BEHAVIOR_DRIVER_FAILED"
        self.status = (
            status if status in {"NEEDS_REVIEW", "NEEDS_CHANGES", "NOT_RUN"} else "NEEDS_REVIEW"
        )
        super().__init__(self.code)


def need(ok: object, code: str, status: str = "NEEDS_REVIEW") -> None:
    if not ok:
        raise BehaviorProofError(code, status)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class NamedBehaviorContract:
    version: str
    template: str
    scope: str
    request_sha256: str
    capabilities: tuple[str, ...]
    probe_version: str
    probe_sha256: str
    contract_digest: str

    def to_json(self) -> dict[str, object]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        return result


def required_contract(prompt: str, *, template: str) -> NamedBehaviorContract | None:
    """Conservative named-intent selection, never an acceptance proof.

    Unknown templates/requests stay on their existing path. Explicit API-only
    requests do not acquire a DOM requirement. Actual pass needs the driver.
    """
    if template not in _SUPPORTED_TEMPLATES:
        return None
    text = prompt.casefold()
    if "api-only" in text or "api only" in text or "только api" in text:
        return None
    if re.match(r"\s*(explain|inspect|review|investigate|объясни|исследуй|проанализируй)\b", text):
        return None
    coffee_requested = (
        "проверить заявку" in text
        and "резюме" in text
        and "без отправ" in text
        and not re.search(r"не (добав\w*|нуж\w*).*проверить заявку", text)
        and not re.search(r"\b(?:don't|do not|avoid|without)\b[^;\n.!?]*проверить заявку", text)
    )
    caps = []
    # This selects requirements only; semantic acceptance remains browser-owned.
    # Conservatively exclude explicit negative clauses rather than invent gates.
    clauses = re.split(r"[;\n.]", text)
    positive = " ".join(
        clause
        for clause in clauses
        if not re.search(r"\b(don't|do not|avoid|without|не добав\w*|не нуж\w*|без)\b", clause)
    )
    text = positive
    density = ("normal" in text and "compact" in text) or ("обычн" in text and "компактн" in text)
    if "task_density_v1" in text or (
        density
        and ("density" in text or "плотност" in text)
        and ("list" in text or "списк" in text)
        and ("board" in text or "доск" in text)
    ):
        caps.append("task_density_v1")
    colors = ("light" in text and "dark" in text) or (
        "светл" in text and ("темн" in text or "тёмн" in text)
    )
    if "header_theme_v1" in text or (
        colors and ("theme" in text or "тем" in text) and ("header" in text or "шапк" in text)
    ):
        caps.append("header_theme_v1")
    if "coffee_local_summary_v1" in text or coffee_requested:
        caps.append("coffee_local_summary_v1")
    if not caps:
        return None
    payload: dict[str, Any] = dict(
        version=CONTRACT_VERSION,
        template=template,
        scope="ui",
        request_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        capabilities=tuple(caps),
        probe_version=PROBE_VERSION,
        probe_sha256=PROBE_SHA256,
    )
    return NamedBehaviorContract(**payload, contract_digest=digest(payload))


def contract_from_json(raw: object) -> NamedBehaviorContract:
    try:
        need(
            isinstance(raw, dict) and set(raw) == set(NamedBehaviorContract.__dataclass_fields__),
            "BEHAVIOR_CONTRACT_INVALID",
        )
        payload = dict(cast(dict[str, Any], raw))
        payload["capabilities"] = tuple(payload["capabilities"])
        result = NamedBehaviorContract(**payload)
        unsigned = asdict(result)
        unsigned.pop("contract_digest")
        need(
            result.version == CONTRACT_VERSION
            and result.template in _SUPPORTED_TEMPLATES
            and result.scope == "ui"
            and result.probe_version == PROBE_VERSION
            and result.probe_sha256 == PROBE_SHA256
            and _HEX.fullmatch(result.request_sha256)
            and bool(result.capabilities)
            and set(result.capabilities) <= _CAPABILITIES
            and len(set(result.capabilities)) == len(result.capabilities)
            and digest(unsigned) == result.contract_digest,
            "BEHAVIOR_CONTRACT_INVALID",
        )
        return result
    except BehaviorProofError:
        raise
    except Exception:
        raise BehaviorProofError("BEHAVIOR_CONTRACT_INVALID") from None


class PrivatePreviewCapability:
    """In-process credential capability; repr/copy/pickle never contain secrets."""

    __slots__ = ("_session",)

    def __init__(self, session: Any) -> None:
        self._session = session

    @property
    def session(self) -> Any:
        return self._session

    def __repr__(self) -> str:
        return "PrivatePreviewCapability(<withheld>)"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Any:
        raise TypeError("private_preview_not_serializable")


@dataclass(frozen=True, slots=True)
class CandidateBehaviorBinding:
    candidate_id: UUID
    project_id: UUID
    workspace_id: UUID
    generation_run_id: UUID
    fencing_epoch: int
    proof_key: str
    source_revision: str
    build_ref: str
    request_sha256: str
    contract_digest: str
    surface: str

    def to_json(self) -> dict[str, object]:
        return {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class BehaviorDriverInput:
    binding: CandidateBehaviorBinding
    contract: NamedBehaviorContract
    preview: PrivatePreviewCapability


@dataclass(frozen=True, slots=True)
class ObservedAsset:
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class CompiledAssetWitness:
    binding: CandidateBehaviorBinding
    assets: tuple[ObservedAsset, ...]
    compilation_receipt_sha256: str
    provenance: str
    platform_assets: tuple[ObservedAsset, ...] = ()

    @property
    def digest(self) -> str:
        return digest(
            {
                "binding": self.binding.to_json(),
                "assets": [asdict(x) for x in self.assets],
                "compilation_receipt_sha256": self.compilation_receipt_sha256,
                "provenance": self.provenance,
                "platform_assets": [asdict(x) for x in self.platform_assets],
            }
        )


@dataclass(frozen=True, slots=True)
class BrowserBehaviorObservation:
    binding: CandidateBehaviorBinding
    compiled_asset_digest: str
    probe_version: str
    probe_sha256: str
    status: str
    measurements: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ControllerBehaviorDriver:
    # Privileged controller callbacks, never populated from run.agent_state.
    collect_compiled: Callable[[BehaviorDriverInput], Awaitable[CompiledAssetWitness]]
    observe_browser: Callable[
        [BehaviorDriverInput, CompiledAssetWitness], Awaitable[BrowserBehaviorObservation]
    ]
    supported_capabilities: frozenset[str]


def _number(value: object) -> bool:
    return (
        isinstance(value, (float, int))
        and type(value) is not bool
        and math.isfinite(value)
        and 0 <= value <= 100000
    )


def _mode(raw: object) -> None:
    need(
        isinstance(raw, dict)
        and set(raw) == {"data_sha256", "filter_sha256", "count", "padding", "height", "gap"},
        "BEHAVIOR_MEASUREMENTS_INVALID",
    )
    raw = cast(dict[str, Any], raw)
    need(
        _HEX.fullmatch(raw["data_sha256"])
        and _HEX.fullmatch(raw["filter_sha256"])
        and type(raw["count"]) is int
        and 2 <= raw["count"] <= 100
        and all(_number(raw[k]) for k in ("padding", "height", "gap")),
        "BEHAVIOR_MEASUREMENTS_INVALID",
    )


def _paint(raw: object, viewport: dict[str, int]) -> None:
    need(isinstance(raw, list) and 2 <= len(raw) <= 32, "BEHAVIOR_PAINT_UNVERIFIED")
    fields = {
        "tag",
        "effective_opacity",
        "css_visible",
        "display",
        "visibility",
        "unsupported_paint",
        "box",
        "in_view",
        "hit",
    }
    raw = cast(list[dict[str, Any]], raw)
    for witness in raw:
        need(isinstance(witness, dict) and set(witness) == fields, "BEHAVIOR_MEASUREMENTS_INVALID")
        need(witness["unsupported_paint"] is False, "BEHAVIOR_PAINT_UNVERIFIED")
        box = witness["box"]
        need(
            isinstance(box, dict)
            and set(box) == {"x", "y", "width", "height"}
            and all(_number(v) for v in box.values()),
            "BEHAVIOR_PAINT_UNVERIFIED",
        )
        need(
            witness["tag"] in {"BUTTON", "SELECT"}
            and _number(witness["effective_opacity"])
            and 0 < witness["effective_opacity"] <= 1
            and witness["css_visible"] is True
            and witness["visibility"] == "visible"
            and witness["display"] in {"block", "inline-block", "flex", "inline-flex", "grid"}
            and witness["in_view"] is True
            and witness["hit"] is True
            and box["width"] >= 44
            and box["height"] >= 44
            and box["x"] + box["width"] <= viewport["width"] + 0.5
            and box["y"] + box["height"] <= viewport["height"] + 0.5,
            "BEHAVIOR_NEEDS_CHANGES",
            "NEEDS_CHANGES",
        )


def validate_measurements(contract: NamedBehaviorContract, measurements: object) -> str:
    """Validate measured dimensions/colors; a driver's PASS label is insufficient."""
    try:
        need(
            isinstance(measurements, dict) and set(measurements) == set(contract.capabilities),
            "BEHAVIOR_MEASUREMENTS_INVALID",
        )
        measurements = cast(dict[str, Any], measurements)
        for cap in contract.capabilities:
            rows = measurements[cap]
            need(
                isinstance(rows, list) and len(rows) == (4 if cap == "task_density_v1" else 2),
                "BEHAVIOR_MEASUREMENTS_PARTIAL",
            )
            keys = set()
            for row in rows:
                need(isinstance(row, dict), "BEHAVIOR_MEASUREMENTS_INVALID")
                viewport = row["viewport"]
                need(
                    viewport in ({"width": 1280, "height": 900}, {"width": 390, "height": 844}),
                    "BEHAVIOR_VIEWPORT_UNVERIFIED",
                )
                _paint(row["paint"], viewport)
                need(
                    _number(row["controls_min_width"])
                    and _number(row["controls_min_height"])
                    and min(row["controls_min_width"], row["controls_min_height"]) >= 44,
                    "BEHAVIOR_NEEDS_CHANGES",
                    "NEEDS_CHANGES",
                )
                if cap == "task_density_v1":
                    need(
                        set(row)
                        == {
                            "viewport",
                            "view",
                            "normal",
                            "compact",
                            "reload",
                            "controls_min_width",
                            "controls_min_height",
                            "local_preference",
                            "reload_preference",
                            "paint",
                        },
                        "BEHAVIOR_MEASUREMENTS_INVALID",
                    )
                    need(row["view"] in {"list", "board"}, "BEHAVIOR_VIEW_UNVERIFIED")
                    keys.add((viewport["width"], row["view"]))
                    for mode in ("normal", "compact", "reload"):
                        _mode(row[mode])
                    normal, compact = row["normal"], row["compact"]
                    need(
                        all(
                            normal[k] == compact[k]
                            for k in ("data_sha256", "filter_sha256", "count")
                        )
                        and all(compact[k] < normal[k] for k in ("padding", "height", "gap"))
                        and row["reload"] == compact
                        and row["local_preference"] == "compact"
                        and row["reload_preference"] == "compact",
                        "BEHAVIOR_NEEDS_CHANGES",
                        "NEEDS_CHANGES",
                    )
                elif cap == "header_theme_v1":
                    need(
                        set(row)
                        == {
                            "viewport",
                            "light",
                            "dark",
                            "reload",
                            "header_control_visible",
                            "controls_min_width",
                            "controls_min_height",
                            "local_preference",
                            "reload_preference",
                            "paint",
                        },
                        "BEHAVIOR_MEASUREMENTS_INVALID",
                    )
                    keys.add(viewport["width"])
                    light, dark = row["light"], row["dark"]
                    need(
                        all(
                            isinstance(x, dict)
                            and set(x) == {"body_luminance", "card_luminance", "text_luminance"}
                            and all(_number(v) and v <= 1 for v in x.values())
                            for x in (light, dark)
                        ),
                        "BEHAVIOR_MEASUREMENTS_INVALID",
                    )
                    need(
                        row["header_control_visible"] is True
                        and row["reload"] == dark
                        and row["local_preference"] == "dark"
                        and row["reload_preference"] == "dark"
                        and all(
                            light[k] >= 0.75 and dark[k] <= 0.18
                            for k in ("body_luminance", "card_luminance")
                        )
                        and light["text_luminance"] != dark["text_luminance"],
                        "BEHAVIOR_NEEDS_CHANGES",
                        "NEEDS_CHANGES",
                    )
                    for mode in (light, dark):
                        x, y = mode["card_luminance"], mode["text_luminance"]
                        need(
                            (max(x, y) + 0.05) / (min(x, y) + 0.05) >= 4.5,
                            "BEHAVIOR_NEEDS_CHANGES",
                            "NEEDS_CHANGES",
                        )
                else:
                    need(
                        set(row)
                        == {
                            "viewport",
                            "rounds",
                            "controls_min_width",
                            "controls_min_height",
                            "paint",
                        }
                        and isinstance(row["rounds"], list)
                        and len(row["rounds"]) == 2,
                        "BEHAVIOR_MEASUREMENTS_INVALID",
                    )
                    keys.add(viewport["width"])
                    nonce_hashes = set()
                    for observed in row["rounds"]:
                        fields = {
                            "input_sha256",
                            "echoed_input_sha256",
                            "summary_before_sha256",
                            "summary_after_sha256",
                            "form_before_sha256",
                            "form_after_sha256",
                            "business_writes",
                            "provider_requests",
                        }
                        need(
                            isinstance(observed, dict) and set(observed) == fields,
                            "BEHAVIOR_MEASUREMENTS_INVALID",
                        )
                        need(
                            all(
                                _HEX.fullmatch(observed[k]) for k in fields if k.endswith("sha256")
                            ),
                            "BEHAVIOR_MEASUREMENTS_INVALID",
                        )
                        need(
                            observed["input_sha256"] == observed["echoed_input_sha256"]
                            and observed["summary_before_sha256"]
                            != observed["summary_after_sha256"]
                            and observed["form_before_sha256"] == observed["form_after_sha256"]
                            and type(observed["business_writes"]) is int
                            and observed["business_writes"] == 0
                            and type(observed["provider_requests"]) is int
                            and observed["provider_requests"] == 0,
                            "BEHAVIOR_NEEDS_CHANGES",
                            "NEEDS_CHANGES",
                        )
                        nonce_hashes.add(observed["input_sha256"])
                    need(len(nonce_hashes) == 2, "BEHAVIOR_MEASUREMENTS_PARTIAL")
            need(len(keys) == len(rows), "BEHAVIOR_MEASUREMENTS_PARTIAL")
        return digest(measurements)
    except BehaviorProofError:
        raise
    except Exception:
        raise BehaviorProofError("BEHAVIOR_MEASUREMENTS_INVALID") from None


def _compiled(witness: object, binding: CandidateBehaviorBinding) -> CompiledAssetWitness:
    need(
        type(witness) is CompiledAssetWitness
        and witness.binding == binding
        and witness.provenance == "served_candidate_compilation_v1"
        and _HEX.fullmatch(witness.compilation_receipt_sha256),
        "BEHAVIOR_COMPILED_BINDING_INVALID",
    )
    witness = cast(CompiledAssetWitness, witness)
    need(
        1 <= len(witness.assets) <= 128
        and len({x.path for x in witness.assets}) == len(witness.assets),
        "BEHAVIOR_COMPILED_ASSETS_MISSING",
    )
    for x in witness.assets:
        need(
            type(x) is ObservedAsset
            and re.fullmatch(
                r"/_next/static/(chunks/[A-Za-z0-9_/-]+\.js|css/[A-Za-z0-9_/-]+\.css|"
                r"media/[A-Za-z0-9_.-]+\.(woff2?|ttf|otf|png|jpg|jpeg|webp|avif|gif|ico)|"
                r"[A-Za-z0-9_-]+/_(buildManifest|ssgManifest)\.js)",
                x.path,
            )
            and ".." not in x.path
            and _HEX.fullmatch(x.sha256)
            and type(x.bytes) is int
            and 0 < x.bytes <= 8388608,
            "BEHAVIOR_COMPILED_ASSETS_INVALID",
        )
    need(
        len(witness.platform_assets) <= len(PLATFORM_ASSET_LOCATIONS)
        and len({x.path for x in witness.platform_assets}) == len(witness.platform_assets),
        "BEHAVIOR_PLATFORM_ASSETS_INVALID",
    )
    for x in witness.platform_assets:
        need(
            type(x) is ObservedAsset
            and x.path in PLATFORM_ASSET_LOCATIONS
            and _HEX.fullmatch(x.sha256)
            and type(x.bytes) is int
            and 0 < x.bytes <= 8388608,
            "BEHAVIOR_PLATFORM_ASSETS_INVALID",
        )
    need(
        sum(x.bytes for x in witness.assets) <= 67108864
        and any(x.path.endswith(".js") for x in witness.assets),
        "BEHAVIOR_COMPILED_ASSETS_INVALID",
    )
    return witness


def _binding(binding: CandidateBehaviorBinding, contract: NamedBehaviorContract) -> None:
    need(
        type(binding) is CandidateBehaviorBinding
        and all(
            type(getattr(binding, k)) is UUID
            for k in ("candidate_id", "project_id", "workspace_id", "generation_run_id")
        )
        and type(binding.fencing_epoch) is int
        and binding.fencing_epoch > 0
        and _HEX.fullmatch(binding.proof_key)
        and _HEX.fullmatch(binding.source_revision)
        and re.fullmatch(r"build/sha256/[0-9a-f]{64}", binding.build_ref)
        and binding.request_sha256 == contract.request_sha256
        and binding.contract_digest == contract.contract_digest
        and binding.surface in {"owner_preview", "genuine_signed_max"},
        "BEHAVIOR_CANDIDATE_BINDING_INVALID",
    )


async def observe_candidate(
    binding: CandidateBehaviorBinding,
    contract: NamedBehaviorContract,
    driver: ControllerBehaviorDriver | None,
    preview: PrivatePreviewCapability | None,
) -> dict[str, object]:
    _binding(binding, contract)
    need(driver is not None, "BEHAVIOR_DRIVER_UNAVAILABLE")
    need(
        type(driver) is ControllerBehaviorDriver
        and set(contract.capabilities) <= driver.supported_capabilities,
        "BEHAVIOR_ADAPTER_UNSUPPORTED",
    )
    need(type(preview) is PrivatePreviewCapability, "BEHAVIOR_PREVIEW_UNAVAILABLE")
    driver = cast(ControllerBehaviorDriver, driver)
    preview = cast(PrivatePreviewCapability, preview)
    request = BehaviorDriverInput(binding, contract, preview)
    try:
        before = _compiled(await driver.collect_compiled(request), binding)
        observed = await driver.observe_browser(request, before)
        need(
            type(observed) is BrowserBehaviorObservation
            and observed.binding == binding
            and observed.compiled_asset_digest == before.digest
            and observed.probe_version == contract.probe_version
            and observed.probe_sha256 == contract.probe_sha256,
            "BEHAVIOR_OBSERVATION_BINDING_INVALID",
        )
        need(
            observed.status == "PASS_OBSERVED",
            "BEHAVIOR_OBSERVATION_UNVERIFIED",
            "NEEDS_CHANGES" if observed.status == "NEEDS_CHANGES" else "NEEDS_REVIEW",
        )
        measured_digest = validate_measurements(contract, observed.measurements)
        after = _compiled(await driver.collect_compiled(request), binding)
        need(after == before, "BEHAVIOR_COMPILED_ASSETS_CHANGED")
    except asyncio.CancelledError:
        raise
    except BehaviorProofError:
        raise
    except Exception:
        # Driver exceptions can include bootstrap credentials, customer values,
        # browser URLs or command arguments. Export only this fixed code.
        raise BehaviorProofError("BEHAVIOR_DRIVER_FAILED") from None
    result: dict[str, object] = {
        "version": CONTRACT_VERSION,
        "status": "PASS_OBSERVED",
        "binding": binding.to_json(),
        "contract_digest": contract.contract_digest,
        "probe_version": contract.probe_version,
        "probe_sha256": contract.probe_sha256,
        "compiled_asset_digest": before.digest,
        "compilation_receipt_sha256": before.compilation_receipt_sha256,
        "observed_assets": [asdict(x) for x in before.assets],
        "observed_platform_assets": [asdict(x) for x in before.platform_assets],
        "measurement_digest": measured_digest,
        "capabilities": list(contract.capabilities),
    }
    result["receipt_digest"] = digest(result)
    return result


def validate_saved_receipt(
    receipt: object, binding: CandidateBehaviorBinding, contract: NamedBehaviorContract
) -> None:
    try:
        _validate_saved_receipt(receipt, binding, contract)
    except BehaviorProofError:
        raise
    except Exception:
        raise BehaviorProofError("BEHAVIOR_RECEIPT_UNVERIFIED") from None


def _validate_saved_receipt(
    receipt: object, binding: CandidateBehaviorBinding, contract: NamedBehaviorContract
) -> None:
    _binding(binding, contract)
    need(isinstance(receipt, dict), "BEHAVIOR_RECEIPT_MISSING")
    receipt = cast(dict[str, Any], receipt)
    expected = {
        "version",
        "status",
        "binding",
        "contract_digest",
        "probe_version",
        "probe_sha256",
        "compiled_asset_digest",
        "compilation_receipt_sha256",
        "observed_assets",
        "observed_platform_assets",
        "measurement_digest",
        "capabilities",
        "receipt_digest",
    }
    need(
        set(receipt) == expected
        and receipt["version"] == CONTRACT_VERSION
        and receipt["status"] == "PASS_OBSERVED"
        and receipt["binding"] == binding.to_json()
        and receipt["contract_digest"] == contract.contract_digest
        and receipt["probe_version"] == contract.probe_version
        and receipt["probe_sha256"] == contract.probe_sha256
        and receipt["capabilities"] == list(contract.capabilities)
        and receipt["receipt_digest"]
        == digest({k: v for k, v in receipt.items() if k != "receipt_digest"}),
        "BEHAVIOR_RECEIPT_UNVERIFIED",
    )
    need(
        isinstance(receipt["observed_assets"], list)
        and isinstance(receipt["observed_platform_assets"], list)
        and _HEX.fullmatch(receipt["measurement_digest"]),
        "BEHAVIOR_RECEIPT_UNVERIFIED",
    )
    witness = _compiled(
        CompiledAssetWitness(
            binding,
            tuple(ObservedAsset(**item) for item in receipt["observed_assets"]),
            receipt["compilation_receipt_sha256"],
            "served_candidate_compilation_v1",
            tuple(ObservedAsset(**item) for item in receipt["observed_platform_assets"]),
        ),
        binding,
    )
    need(witness.digest == receipt["compiled_asset_digest"], "BEHAVIOR_RECEIPT_UNVERIFIED")


async def freeze_for_turn(
    coordinator: Any, prompt: str, *, template: str
) -> NamedBehaviorContract | None:
    """Freeze from the authoritative exact user turn before any model call."""
    from yleum_api.models.message import Message

    async with coordinator.session_factory() as session:
        run = await coordinator._locked_run(session)
        original = prompt
        if hashlib.sha256(original.encode()).hexdigest() != run.prompt_hash:
            message = (
                await session.get(Message, run.user_message_id) if run.user_message_id else None
            )
            if (
                message is not None
                and message.project_id == coordinator.project_id
                and message.role == "user"
            ):
                original = message.content
                if hashlib.sha256(original.encode()).hexdigest() != run.prompt_hash:
                    # Adaptation admission hashes the canonical user request plus
                    # its typed operation/snapshot reference, while Message stores
                    # raw user text. Recover that exact existing admission format;
                    # historical files and synthesized model context are not intent.
                    from yleum_api.schemas.message import RestorationAdaptationReference
                    from yleum_api.services.restoration_adaptation import (
                        adaptation_reservation_prompt,
                    )

                    raw = run.agent_state.get("restoration_adaptation")
                    try:
                        if (
                            isinstance(raw, dict)
                            and raw.get("version") in {1, 2}
                            and raw.get("project_id") == str(coordinator.project_id)
                            and raw.get("owner_id") == str(run.user_id)
                            and raw.get("adaptation_run_id") == str(coordinator.generation_run_id)
                        ):
                            reference = RestorationAdaptationReference(
                                operation_id=UUID(raw["operation_id"]),
                                expected_draft_snapshot_id=UUID(raw["base_draft_snapshot_id"]),
                            )
                            original = adaptation_reservation_prompt(original, reference)
                    except Exception:
                        raise BehaviorProofError("BEHAVIOR_REQUEST_BINDING_INVALID") from None
        need(
            hashlib.sha256(original.encode()).hexdigest() == run.prompt_hash,
            "BEHAVIOR_REQUEST_BINDING_INVALID",
            "NEEDS_REVIEW",
        )
        selected = required_contract(original, template=template)
        root = dict(run.agent_state)
        saved = root.get(STATE_KEY)
        if selected is None and saved is None:
            return None
        if saved is not None:
            existing = contract_from_json(saved)
            need(
                existing == selected and existing.request_sha256 == run.prompt_hash,
                "BEHAVIOR_REQUEST_BINDING_INVALID",
            )
            return existing
        assert selected is not None
        root[STATE_KEY] = selected.to_json()
        run.agent_state = root
        await session.commit()
        return selected


async def frozen_contract(coordinator: Any) -> NamedBehaviorContract | None:
    async with coordinator.session_factory() as session:
        run = await coordinator._locked_run(session)
        raw = run.agent_state.get(STATE_KEY)
        if raw is None:
            return None
        result = contract_from_json(raw)
        need(result.request_sha256 == run.prompt_hash, "BEHAVIOR_REQUEST_BINDING_INVALID")
        return result


async def freeze_for_finalization(coordinator: Any, prompt: str) -> NamedBehaviorContract | None:
    """Protect direct coordinator entry points as well as the agent-turn hook."""
    from yleum_api.models.project import Project

    async with coordinator.session_factory() as session:
        project = await session.get(Project, coordinator.project_id)
    template = str(getattr(project, "template", ""))
    return await freeze_for_turn(coordinator, prompt, template=template)


def candidate_binding(
    coordinator: Any, candidate: Any, identity: Any, build_ref: str, contract: NamedBehaviorContract
) -> CandidateBehaviorBinding:
    need(
        candidate.workspace_id == identity.workspace_id
        and candidate.generation_run_id == identity.generation_run_id
        and candidate.fencing_epoch == identity.fencing_epoch
        and candidate.source_revision == identity.workspace_revision
        and candidate.build_ref == build_ref,
        "BEHAVIOR_CANDIDATE_BINDING_INVALID",
    )
    return CandidateBehaviorBinding(
        candidate.id,
        coordinator.project_id,
        identity.workspace_id,
        identity.generation_run_id,
        identity.fencing_epoch,
        identity.proof_key,
        identity.workspace_revision,
        build_ref,
        contract.request_sha256,
        contract.contract_digest,
        "owner_preview",
    )


async def require_candidate_behavior(
    coordinator: Any, candidate: Any, identity: Any, build_ref: str
) -> dict[str, object] | None:
    contract = await frozen_contract(coordinator)
    if contract is None:
        return None
    bound = candidate_binding(coordinator, candidate, identity, build_ref, contract)
    try:
        driver = coordinator.behavior_driver
        need(driver is not None, "BEHAVIOR_DRIVER_UNAVAILABLE")
        try:
            async with asyncio.timeout(120):
                session = await coordinator.executor.create_preview_session()
                receipt = await observe_candidate(
                    bound, contract, driver, PrivatePreviewCapability(session)
                )
        except asyncio.CancelledError:
            raise
        except BehaviorProofError:
            raise
        except Exception:
            raise BehaviorProofError("BEHAVIOR_PREVIEW_UNAVAILABLE") from None
    except BehaviorProofError as error:
        receipt = {
            "status": error.status,
            "code": error.code,
            "binding": bound.to_json(),
            "contract_digest": contract.contract_digest,
        }
        async with coordinator.session_factory() as session:
            run = await coordinator._locked_run(session)
            root = dict(run.agent_state)
            root[RECEIPT_KEY] = receipt
            run.agent_state = root
            await session.commit()
        raise
    async with coordinator.session_factory() as session:
        run = await coordinator._locked_run(session)
        need(
            contract_from_json(run.agent_state[STATE_KEY]) == contract,
            "BEHAVIOR_REQUEST_BINDING_INVALID",
        )
        root = dict(run.agent_state)
        root[RECEIPT_KEY] = receipt
        run.agent_state = root
        await session.commit()
    return receipt


async def require_saved_candidate_behavior(
    coordinator: Any, candidate: Any, identity: Any, build_ref: str
) -> None:
    contract = await frozen_contract(coordinator)
    if contract is None:
        return
    bound = candidate_binding(coordinator, candidate, identity, build_ref, contract)
    async with coordinator.session_factory() as session:
        run = await coordinator._locked_run(session)
        receipt = run.agent_state.get(RECEIPT_KEY)
    validate_saved_receipt(receipt, bound, contract)
