from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from yleum_api.services import max_behavior_proof as b

REQUEST = "Add Normal/Compact density for both list and board; persist locally."
THEME = "Add a visible header Light/Dark theme control and local persistence."


def test_only_known_named_supported_contracts_are_required():
    assert b.required_contract(REQUEST, template="max_miniapp").capabilities == ("task_density_v1",)
    assert b.required_contract(THEME, template="max_miniapp").capabilities == ("header_theme_v1",)
    assert b.required_contract("Build a task board", template="max_miniapp") is None
    assert b.required_contract(REQUEST, template="unknown") is None
    assert b.required_contract("API-only: " + REQUEST, template="max_miniapp") is None
    assert b.required_contract("Do not " + REQUEST, template="max_miniapp") is None
    assert (
        b.required_contract(
            "Build a task board without a header Light/Dark theme", template="max_miniapp"
        )
        is None
    )
    assert (
        b.required_contract(
            "Explain the existing Normal/Compact density for list and board.",
            template="max_miniapp",
        )
        is None
    )


def test_contract_exact_request_and_requirements_versions_are_bound():
    contract = b.required_contract(REQUEST, template="max_miniapp")
    assert contract.request_sha256 == hashlib.sha256(REQUEST.encode()).hexdigest()
    assert b.contract_from_json(contract.to_json()) == contract
    raw = contract.to_json()
    raw["capabilities"] = ["header_theme_v1"]
    with pytest.raises(b.BehaviorProofError):
        b.contract_from_json(raw)


def test_private_capability_never_serializes_even_in_dataclass_copy():
    private = b.PrivatePreviewCapability(NS(bootstrap_url="PRIVATE_NEVER_EXPORT"))
    assert "PRIVATE_NEVER_EXPORT" not in repr(private)
    request = b.BehaviorDriverInput(binding(), contract(), private)
    assert "PRIVATE_NEVER_EXPORT" not in repr(request)
    with pytest.raises(TypeError, match="private_preview_not_serializable"):
        asdict(request)


def contract():
    return b.required_contract(REQUEST, template="max_miniapp")


def binding(c=None):
    c = c or contract()
    return b.CandidateBehaviorBinding(
        candidate_id=uuid4(),
        project_id=uuid4(),
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=7,
        proof_key="a" * 64,
        source_revision="b" * 64,
        build_ref="build/sha256/" + "c" * 64,
        request_sha256=c.request_sha256,
        contract_digest=c.contract_digest,
        surface="owner_preview",
    )


def assets(bound):
    return b.CompiledAssetWitness(
        bound,
        (
            b.ObservedAsset("/_next/static/chunks/app/page-a.js", "d" * 64, 100),
            b.ObservedAsset("/_next/static/css/a.css", "e" * 64, 100),
        ),
        "f" * 64,
        "served_candidate_compilation_v1",
    )


@pytest.mark.parametrize(
    "path",
    [
        "/_next/static/chunks/app/(app)/dashboard/page-d383517d2c7a60b6.js",
        "/_next/static/chunks/app/api/workouts/[id]/route-1c082550ded9edf9.js",
        "/_next/static/chunks/app/api/omnia/integrations/[...path]/route-1c082550ded9edf9.js",
        "/_next/static/chunks/app/docs/[[...path]]/page-a.js",
    ],
)
def test_compiled_witness_accepts_legitimate_next_route_assets(path):
    bound = binding()
    witness = replace(assets(bound), assets=(b.ObservedAsset(path, "d" * 64, 100),))
    assert b._compiled(witness, bound) is witness


@pytest.mark.parametrize(
    "segment",
    [
        ".",
        "..",
        "",
        "foo\\bar",
        "%2f",
        "%2F",
        "%5c",
        "%2e%2e",
        "%252f",
        "foo.bar",
        "(..)",
        "()",
        "[]",
        "[..path]",
        "[....path]",
        "[[path]]",
        "[...]",
        "[[...]]",
    ],
)
def test_compiled_witness_rejects_unsafe_or_malformed_route_segments(segment):
    bound = binding()
    path = f"/_next/static/chunks/app/{segment}/page-a.js"
    witness = replace(assets(bound), assets=(b.ObservedAsset(path, "d" * 64, 100),))
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_COMPILED_ASSETS_INVALID"):
        b._compiled(witness, bound)


def measurement():
    def paint():
        return [
            {
                "tag": "BUTTON",
                "effective_opacity": 1,
                "css_visible": True,
                "display": "inline-block",
                "visibility": "visible",
                "unsupported_paint": False,
                "box": {"x": 8, "y": 8, "width": 44, "height": 44},
                "in_view": True,
                "hit": True,
            }
            for _ in range(2)
        ]

    def mode(padding, height, gap):
        return {
            "data_sha256": "1" * 64,
            "filter_sha256": "2" * 64,
            "count": 2,
            "padding": padding,
            "height": height,
            "gap": gap,
        }

    return {
        "task_density_v1": [
            {
                "viewport": {"width": 1280, "height": 900},
                "view": view,
                "normal": mode(24, 70, 24),
                "compact": mode(8, 38, 8),
                "reload": mode(8, 38, 8),
                "controls_min_width": 44,
                "controls_min_height": 44,
                "local_preference": "compact",
                "reload_preference": "compact",
                "paint": paint(),
            }
            for view in ("list", "board")
        ]
        + [
            {
                "viewport": {"width": 390, "height": 844},
                "view": view,
                "normal": mode(24, 70, 24),
                "compact": mode(8, 38, 8),
                "reload": mode(8, 38, 8),
                "controls_min_width": 44,
                "controls_min_height": 44,
                "local_preference": "compact",
                "reload_preference": "compact",
                "paint": paint(),
            }
            for view in ("list", "board")
        ]
    }


def driver(bound, *, error=None, changed_asset=False, wrong_binding=False, partial=False):
    calls = []

    async def collect(request):
        calls.append("compiled")
        if error:
            raise RuntimeError("PRIVATE_NEVER_EXPORT")
        result = assets(request.binding)
        if changed_asset and calls.count("compiled") > 1:
            result = replace(result, assets=(replace(result.assets[0], sha256="0" * 64),))
        return result

    async def observe(request, witness):
        calls.append("browser")
        data = measurement()
        if partial:
            data["task_density_v1"] = data["task_density_v1"][:1]
        actual = (
            replace(request.binding, candidate_id=uuid4()) if wrong_binding else request.binding
        )
        return b.BrowserBehaviorObservation(
            actual, witness.digest, b.PROBE_VERSION, b.PROBE_SHA256, "PASS_OBSERVED", data
        )

    return b.ControllerBehaviorDriver(collect, observe, frozenset({"task_density_v1"})), calls


def theme_measurement():
    paint = measurement()["task_density_v1"][0]["paint"]
    light = {"body_luminance": 1, "card_luminance": 0.9, "text_luminance": 0.01}
    dark = {"body_luminance": 0.02, "card_luminance": 0.03, "text_luminance": 0.95}
    return {
        "header_theme_v1": [
            {
                "viewport": {"width": width, "height": height},
                "light": dict(light),
                "dark": dict(dark),
                "reload": dict(dark),
                "header_control_visible": True,
                "controls_min_width": 44,
                "controls_min_height": 44,
                "local_preference": "dark",
                "reload_preference": "dark",
                "paint": paint,
            }
            for width, height in ((1280, 900), (390, 844))
        ]
    }


def test_visible_header_theme_computed_colors_and_local_reload_are_required():
    c = b.required_contract(THEME, template="max_miniapp")
    assert len(b.validate_measurements(c, theme_measurement())) == 64


@pytest.mark.parametrize("broken", ["header", "style", "storage", "reload", "contrast"])
def test_theme_handler_style_or_storage_missing_is_red(broken):
    c = b.required_contract(THEME, template="max_miniapp")
    data = theme_measurement()
    row = data["header_theme_v1"][0]
    if broken == "header":
        row["header_control_visible"] = False
    elif broken == "style":
        row["dark"] = dict(row["light"])
    elif broken == "storage":
        row["local_preference"] = "light"
    elif broken == "reload":
        row["reload"] = dict(row["light"])
    else:
        row["light"]["text_luminance"] = 0.8
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_NEEDS_CHANGES"):
        b.validate_measurements(c, data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective_opacity", 0),
        ("css_visible", False),
        ("visibility", "hidden"),
        ("display", "none"),
        ("hit", False),
        ("in_view", False),
        ("unsupported_paint", True),
    ],
)
def test_unpainted_or_unsupported_controls_never_pass(field, value):
    data = measurement()
    data["task_density_v1"][0]["paint"][0][field] = value
    with pytest.raises(b.BehaviorProofError):
        b.validate_measurements(contract(), data)


@pytest.mark.parametrize("broken", ["data", "filter", "reload", "storage", "viewport"])
def test_density_preserves_data_filters_and_local_reload(broken):
    data = measurement()
    row = data["task_density_v1"][0]
    if broken in {"data", "filter"}:
        row["compact"][broken + "_sha256"] = "0" * 64
    elif broken == "reload":
        row["reload"] = dict(row["normal"])
    elif broken == "storage":
        row["reload_preference"] = "normal"
    else:
        row["viewport"] = {"width": 1000, "height": 800}
    with pytest.raises(b.BehaviorProofError):
        b.validate_measurements(contract(), data)


def test_missing_controller_driver_is_red_for_required_gate():
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_DRIVER_UNAVAILABLE"):
        asyncio.run(b.observe_candidate(binding(), contract(), None, None))


@pytest.mark.parametrize("case", ["exception", "asset-race", "candidate", "partial"])
def test_unverified_evidence_cannot_satisfy_named_gate(case):
    bound = binding()
    registered, _ = driver(
        bound,
        error=case == "exception",
        changed_asset=case == "asset-race",
        wrong_binding=case == "candidate",
        partial=case == "partial",
    )
    with pytest.raises(b.BehaviorProofError) as error:
        asyncio.run(
            b.observe_candidate(
                bound,
                contract(),
                registered,
                b.PrivatePreviewCapability(NS(bootstrap_url="PRIVATE_NEVER_EXPORT")),
            )
        )
    assert "PRIVATE_NEVER_EXPORT" not in str(error.value)
    assert "PRIVATE_NEVER_EXPORT" not in repr(error.value.args)


def test_complete_controller_observation_binds_actual_candidate_assets_and_probe():
    bound = binding()
    registered, calls = driver(bound)
    receipt = asyncio.run(
        b.observe_candidate(
            bound,
            contract(),
            registered,
            b.PrivatePreviewCapability(NS(bootstrap_url="PRIVATE_NEVER_EXPORT")),
        )
    )
    assert calls == ["compiled", "browser", "compiled"]
    assert receipt["status"] == "PASS_OBSERVED"
    assert receipt["binding"]["candidate_id"] == str(bound.candidate_id)
    assert receipt["compiled_asset_digest"] == assets(bound).digest
    assert "PRIVATE_NEVER_EXPORT" not in str(receipt)
    b.validate_saved_receipt(receipt, bound, contract())
    changed = dict(receipt)
    changed["status"] = "PARTIAL"
    with pytest.raises(b.BehaviorProofError):
        b.validate_saved_receipt(changed, bound, contract())


def test_measurements_not_driver_status_alone_determine_pass():
    data = measurement()
    data["task_density_v1"][0]["compact"]["gap"] = 24
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_NEEDS_CHANGES"):
        b.validate_measurements(contract(), data)


def test_untrusted_extra_measurement_fields_are_rejected():
    data = measurement()
    data["cookie"] = "PRIVATE_NEVER_EXPORT"
    with pytest.raises(b.BehaviorProofError):
        b.validate_measurements(contract(), data)


def coordinator_harness(monkeypatch, *, registered=None, required=True, frozen=None):
    from yleum_api.services import max_finalization as f

    c = frozen or contract()
    bound = binding(c)
    run = NS(
        project_id=bound.project_id,
        user_id=uuid4(),
        prompt_hash=c.request_sha256,
        user_message_id=None,
        agent_state={b.STATE_KEY: c.to_json()} if required else {},
        status="running",
    )
    identity = NS(
        workspace_id=bound.workspace_id,
        generation_run_id=bound.generation_run_id,
        fencing_epoch=bound.fencing_epoch,
        workspace_revision=bound.source_revision,
        schema_data_digest="3" * 64,
        proof_key=bound.proof_key,
    )
    candidate = NS(
        id=bound.candidate_id,
        workspace_id=bound.workspace_id,
        generation_run_id=bound.generation_run_id,
        fencing_epoch=bound.fencing_epoch,
        source_revision=bound.source_revision,
        build_ref=bound.build_ref,
        status="prepared",
    )
    events = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            events.append("commit")

        async def refresh(self, obj):
            pass

        async def get(self, model, key):
            if model.__name__ == "Project":
                return NS(template="max_miniapp")
            if model.__name__ == "Message":
                if hasattr(run, "authoritative_message"):
                    return run.authoritative_message
                return NS(project_id=bound.project_id, role="user", content=REQUEST)
            return candidate

        def expunge(self, obj):
            pass

    async def no_op(*args, **kwargs):
        pass

    async def current():
        return identity

    async def preview():
        events.append("preview")
        return NS(bootstrap_url="PRIVATE_NEVER_EXPORT")

    executor = NS(
        workspace_id=bound.workspace_id,
        current_identity=current,
        run_role=no_op,
        runtime_probe=no_op,
        operation_status=no_op,
        create_preview_session=preview,
    )
    coordinator = f.MaxFinalizationCoordinator(
        session_factory=Session,
        generation_run_id=bound.generation_run_id,
        project_id=bound.project_id,
        project_slug="synthetic",
        executor=executor,
        behavior_driver=registered,
    )

    async def locked(_session):
        return run

    async def prepare(*args, **kwargs):
        events.append("prepare")
        return candidate

    async def promote(*args, **kwargs):
        events.append("promote")
        candidate.status = "accepted"
        return candidate

    monkeypatch.setattr(coordinator, "_locked_run", locked)
    for name in ("_phase_started", "_phase_finished", "_ensure_activity", "_finish_activity"):
        monkeypatch.setattr(coordinator, name, no_op)
    monkeypatch.setattr(f, "prepare_candidate", prepare)
    monkeypatch.setattr(f, "promote_candidate", promote)
    monkeypatch.setattr(f, "require_promotion_permit", lambda *a, **kw: events.append("fence"))
    permit = NS(build_ref=bound.build_ref, verification_ref="verification/sha256/" + "4" * 64)
    return coordinator, identity, candidate, run, events, permit


def test_actual_coordinator_missing_driver_never_promotes_required_contract(monkeypatch):
    coordinator, identity, candidate, run, events, permit = coordinator_harness(monkeypatch)
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_DRIVER_UNAVAILABLE"):
        asyncio.run(
            coordinator._prepare_and_promote(
                identity,
                NS(artifact_ref=permit.build_ref),
                NS(artifact_ref=permit.verification_ref),
                permit,
            )
        )
    assert candidate.status == "prepared" and "prepare" in events and "promote" not in events
    assert "preview" not in events
    assert run.agent_state[b.RECEIPT_KEY]["status"] == "NEEDS_REVIEW"


def test_generic_coordinator_no_dom_contract_preserves_existing_promotion(monkeypatch):
    coordinator, identity, candidate, _run, events, permit = coordinator_harness(
        monkeypatch, required=False
    )
    asyncio.run(
        coordinator._prepare_and_promote(
            identity,
            NS(artifact_ref=permit.build_ref),
            NS(artifact_ref=permit.verification_ref),
            permit,
        )
    )
    assert candidate.status == "accepted" and "promote" in events and "preview" not in events


def test_actual_coordinator_observed_receipt_promotes_then_checkpoint_reuses(monkeypatch):
    registered, calls = driver(None)
    coordinator, identity, candidate, run, events, permit = coordinator_harness(
        monkeypatch, registered=registered
    )
    asyncio.run(
        coordinator._prepare_and_promote(
            identity,
            NS(artifact_ref=permit.build_ref),
            NS(artifact_ref=permit.verification_ref),
            permit,
        )
    )
    assert candidate.status == "accepted"
    assert events.index("preview") < events.index("promote")
    assert calls == ["compiled", "browser", "compiled"]
    assert events.count("fence") == 5
    asyncio.run(
        b.require_saved_candidate_behavior(coordinator, candidate, identity, permit.build_ref)
    )
    assert "PRIVATE_NEVER_EXPORT" not in str(run.agent_state)
    run.agent_state.pop(b.RECEIPT_KEY)
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_RECEIPT_MISSING"):
        asyncio.run(
            b.require_saved_candidate_behavior(coordinator, candidate, identity, permit.build_ref)
        )


@pytest.mark.parametrize("broken", ["asset-race", "candidate", "partial", "exception"])
def test_actual_coordinator_bad_observation_cannot_promote(monkeypatch, broken):
    registered, _ = driver(
        None,
        changed_asset=broken == "asset-race",
        wrong_binding=broken == "candidate",
        partial=broken == "partial",
        error=broken == "exception",
    )
    coordinator, identity, candidate, run, events, permit = coordinator_harness(
        monkeypatch, registered=registered
    )
    with pytest.raises(b.BehaviorProofError):
        asyncio.run(
            coordinator._prepare_and_promote(
                identity,
                NS(artifact_ref=permit.build_ref),
                NS(artifact_ref=permit.verification_ref),
                permit,
            )
        )
    assert candidate.status == "prepared" and "promote" not in events
    assert run.agent_state[b.RECEIPT_KEY]["status"] != "PASS_OBSERVED"
    assert "PRIVATE_NEVER_EXPORT" not in str(run.agent_state)


def test_contract_frozen_before_turn_survives_restart_and_authoritative_recovery(monkeypatch):
    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    frozen = asyncio.run(b.freeze_for_turn(coordinator, REQUEST, template="max_miniapp"))
    assert frozen == contract()
    assert asyncio.run(b.frozen_contract(coordinator)) == frozen
    run.user_message_id = uuid4()
    assert (
        asyncio.run(
            b.freeze_for_turn(
                coordinator, "Injected model/recovery context", template="max_miniapp"
            )
        )
        == frozen
    )
    run.prompt_hash = "0" * 64
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_REQUEST_BINDING_INVALID"):
        asyncio.run(b.frozen_contract(coordinator))


def test_missing_authoritative_request_cannot_hide_a_named_contract(monkeypatch):
    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    assert run.user_message_id is None
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_REQUEST_BINDING_INVALID") as error:
        asyncio.run(b.freeze_for_finalization(coordinator, "Build a generic task board"))
    assert error.value.status == "NEEDS_REVIEW"
    assert b.STATE_KEY not in run.agent_state


def test_valid_generic_and_api_only_requests_do_not_acquire_a_dom_gate(monkeypatch):
    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    for prompt in ("Build a generic task board", "API-only: " + REQUEST):
        run.prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        assert asyncio.run(b.freeze_for_finalization(coordinator, prompt)) is None
        assert b.STATE_KEY not in run.agent_state


def test_synthesized_context_cannot_erase_the_original_user_named_requirement(monkeypatch):
    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    run.user_message_id = uuid4()
    recovered = asyncio.run(b.freeze_for_finalization(coordinator, "Continue synthetic repair"))
    assert recovered == contract()
    assert b.contract_from_json(run.agent_state[b.STATE_KEY]) == contract()


@pytest.mark.parametrize("case", ["missing", "foreign-project", "assistant", "changed-message"])
def test_authoritative_recovery_requires_original_owned_user_message(monkeypatch, case):
    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    run.user_message_id = uuid4()
    run.authoritative_message = (
        None
        if case == "missing"
        else NS(
            project_id=uuid4() if case == "foreign-project" else coordinator.project_id,
            role="assistant" if case == "assistant" else "user",
            content="Changed original request" if case == "changed-message" else REQUEST,
        )
    )
    with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_REQUEST_BINDING_INVALID") as error:
        asyncio.run(b.freeze_for_finalization(coordinator, "Synthesized continuation context"))
    assert error.value.status == "NEEDS_REVIEW"
    assert b.STATE_KEY not in run.agent_state


@pytest.mark.parametrize("case", ["valid", "wrong-operation", "wrong-owner", "wrong-project"])
def test_restoration_recovery_preserves_exact_admission_reference_identity(monkeypatch, case):
    from yleum_api.schemas.message import RestorationAdaptationReference
    from yleum_api.services.restoration_adaptation import adaptation_reservation_prompt

    coordinator, _identity, _candidate, run, _events, _permit = coordinator_harness(
        monkeypatch, required=False
    )
    reference = RestorationAdaptationReference(
        operation_id=uuid4(), expected_draft_snapshot_id=uuid4()
    )
    reserved = adaptation_reservation_prompt(REQUEST, reference)
    run.prompt_hash = hashlib.sha256(reserved.encode()).hexdigest()
    run.user_message_id = uuid4()
    run.agent_state["restoration_adaptation"] = {
        "version": 2,
        "project_id": str(coordinator.project_id),
        "owner_id": str(run.user_id),
        "adaptation_run_id": str(coordinator.generation_run_id),
        "operation_id": str(reference.operation_id),
        "base_draft_snapshot_id": str(reference.expected_draft_snapshot_id),
    }
    if case != "valid":
        field = {
            "wrong-operation": "operation_id",
            "wrong-owner": "owner_id",
            "wrong-project": "project_id",
        }[case]
        run.agent_state["restoration_adaptation"][field] = str(uuid4())
        with pytest.raises(b.BehaviorProofError, match="BEHAVIOR_REQUEST_BINDING_INVALID"):
            asyncio.run(
                b.freeze_for_finalization(coordinator, "Synthesized historical source context")
            )
        assert b.STATE_KEY not in run.agent_state
    else:
        frozen = asyncio.run(
            b.freeze_for_finalization(coordinator, "Synthesized historical source context")
        )
        assert frozen.request_sha256 == run.prompt_hash
        assert frozen.capabilities == ("task_density_v1",)


@pytest.mark.parametrize("broken", ["asset", "malformed", "measurement", "probe", "request"])
def test_checkpoint_rejects_rehashed_partial_or_other_candidate_proof(broken):
    bound = binding()
    registered, _ = driver(bound)
    receipt = asyncio.run(
        b.observe_candidate(bound, contract(), registered, b.PrivatePreviewCapability(NS()))
    )
    if broken == "asset":
        receipt["observed_assets"][0]["sha256"] = "0" * 64
    elif broken == "malformed":
        receipt["observed_assets"] = [{"secret": "PRIVATE_NEVER_EXPORT"}]
    elif broken == "measurement":
        receipt["measurement_digest"] = "PARTIAL"
    elif broken == "probe":
        receipt["probe_sha256"] = "0" * 64
    else:
        receipt["binding"]["request_sha256"] = "0" * 64
    receipt["receipt_digest"] = b.digest(
        {k: v for k, v in receipt.items() if k != "receipt_digest"}
    )
    with pytest.raises(b.BehaviorProofError) as error:
        b.validate_saved_receipt(receipt, bound, contract())
    assert "PRIVATE_NEVER_EXPORT" not in str(error.value)
