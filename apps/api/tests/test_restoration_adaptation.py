import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_version import ProjectVersion
from yleum_api.models.restoration import Restoration
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.message import RestorationAdaptationReference


def test_restoration_probe_source_contract_accepts_real_qa_tasks_shape() -> None:
    from yleum_api.services.restoration_adaptation import restoration_probe_source_gap

    files = {
        ".omnia/restoration-probe.json": json.dumps(
            {
                "version": 1,
                "endpoint": "/api/restoration-probe",
                "witnesses": [
                    {
                        "entity": "qa_tasks",
                        "id_column": "id",
                        "owner_column": "max_user_id",
                        "value_column": "title",
                        "create_values": {"status": "pending"},
                    }
                ],
                "max_payload_bytes": 4096,
            }
        ),
        "src/app/api/restoration-probe/route.ts": (
            "export async function GET() {}\nexport async function POST() {}"
        ),
        "src/app/api/restoration-probe/[...path]/route.ts": (
            "export async function GET() {}\n"
            "export async function PATCH() {}\n"
            "export async function DELETE() {}"
        ),
    }

    assert restoration_probe_source_gap(files) is None
    assert restoration_probe_source_gap({}) == (
        "restoration_probe_missing: .omnia/restoration-probe.json"
    )


@pytest.mark.parametrize(
    "path",
    [
        "src/app/api/max/session/route.ts",
        "src/app/api/omnia/integrations/[...path]/route.ts",
        "src/app/api/omnia/preview-session/route.ts",
        "src/lib/max/bot-api.ts",
    ],
)
def test_canonical_environment_credentials_are_source_references(path):
    from yleum_api.services.max_project_kit import _template_file
    from yleum_api.services.restoration_adaptation import _source_files

    content = _template_file(path)
    files, excluded = _source_files({path: content})
    assert files == {path: content}
    assert excluded == []


@pytest.mark.parametrize(
    "content",
    [
        "const token = process.env.MAX_BOT_TOKEN;",
        "  const secret = process.env.AUTH_SECRET; // read configured value\n",
        "const url = `https://example.test/${method}`;\nconst token = process.env.MAX_BOT_TOKEN;",
    ],
)
def test_complete_environment_reference_declarations_preserve_source(content):
    from yleum_api.services.restoration_adaptation import _source_files
    from yleum_api.services.secret_safety import contains_provider_secret

    # The general prompt detector remains conservative; only source has syntax context.
    assert contains_provider_secret(content)
    assert _source_files({"source.ts": content}) == ({"source.ts": content}, [])


@pytest.mark.parametrize(
    "content",
    [
        'const text = "token = process.env.MAX_BOT_TOKEN";',
        "const text = `\nconst token = process.env.MAX_BOT_TOKEN;\n`;",
        "/*\nconst token = process.env.MAX_BOT_TOKEN;\n*/",
        "// token = process.env.MAX_BOT_TOKEN",
        "token = process.env.MAX_BOT_TOKEN",
        "const token = process.env.MAX_BOT_TOKEN.secretCredential1234;",
        "const token = process.env.MAX_BOT_TOKEN + suffix;",
        "const token = process.env.MAX_BOT_TOKEN; // password: abcdefghijklmnop123456",
        "const token = process.env.MAX_BOT_TOKEN;\npassword: abcdefghijklmnop123456",
        "const token = process.env.MAX_BOT_TOKEN;\n"
        "const key = 'sk-' + 'not-a-literal';\nsecret: abcdefghijklmnop123456",
        "const token = process.env.MAX_BOT_TOKEN;\nconst value = 'sk-" + "a" * 24 + "';",
        "const text = `outer ${`inner`}\nconst token = process.env.MAX_BOT_TOKEN;\n`;",
        "const regex = /abc/; const text = `\nconst token = process.env.MAX_BOT_TOKEN;\n`;",
        'const number = 10 / fn("x/"); const text = `\n'
        "const token = process.env.MAX_BOT_TOKEN;\n`;",
    ],
)
def test_source_environment_exception_does_not_hide_other_secret_matches(content):
    from yleum_api.services.restoration_adaptation import _source_files

    with pytest.raises(ApiError) as caught:
        _source_files({"source.ts": content})
    assert caught.value.code == "conflict"


def test_environment_reference_exception_does_not_apply_to_plain_text():
    from yleum_api.services.restoration_adaptation import _source_files

    with pytest.raises(ApiError):
        _source_files({"README.md": "const token = process.env.MAX_BOT_TOKEN;"})


@pytest.mark.parametrize("path", ["source.jsx", "source.tsx", "source.js"])
def test_jsx_text_cannot_authorize_environment_reference_exception(path):
    from yleum_api.services.restoration_adaptation import _source_files

    with pytest.raises(ApiError):
        _source_files(
            {
                path: "const page = <pre>\nconst token = process.env.MAX_BOT_TOKEN;\n</pre>;",
            }
        )


@pytest.mark.parametrize("separator", ["\r", "\u2028", "\u2029"])
def test_unrecognized_js_line_terminator_leaves_secret_check_conservative(separator):
    from yleum_api.services.restoration_adaptation import _source_files

    content = "// comment" + separator + "const text = `\n"
    content += "const token = process.env.MAX_BOT_TOKEN;\n`;"
    with pytest.raises(ApiError):
        _source_files({"source.ts": content})


def test_source_budget_rejects_before_allocating_lexical_scan(monkeypatch):
    from yleum_api.services import restoration_adaptation as service

    monkeypatch.setattr(
        service, "_source_secret_scan_text", lambda *_: pytest.fail("oversized scan")
    )
    with pytest.raises(ApiError):
        service._source_files({"large.ts": "x" * (service.MAX_SOURCE_BYTES + 1)})


@pytest.fixture
def source_case(monkeypatch):
    from yleum_api.services import restoration_adaptation as service

    project = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), template="max_miniapp", current_snapshot_id=uuid4()
    )
    snapshot = SimpleNamespace(id=uuid4(), project_id=project.id, commit_sha="a" * 40)
    version = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        snapshot_id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        generation_run_id=None,
        status="ready",
        base_snapshot_id=None,
    )
    operation = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        owner_id=project.owner_id,
        state="cancelled",
        selected_branch=None,
        adaptation_run_id=None,
        phase="cancelled",
        error=None,
        revision=1,
        updated_at=None,
        source_version_id=version.id,
        source_snapshot_id=snapshot.id,
        target_commit_sha=snapshot.commit_sha,
        base_draft_snapshot_id=project.current_snapshot_id,
        report={
            "revision": 1,
            "mode": "adapted",
            "database_state": "present",
            "format": 2,
            "blockers": ["Новое обязательное поле требует совместимой записи."],
            "retained_data": ["Текущие фамилии клиентов остаются в базе."],
            "checks": [
                {
                    "code": "column_became_required",
                    "status": "unknown",
                    "severity": "blocking",
                    "operation": "clients.insert",
                    "object": "public.clients.surname",
                    "evidence": "structural_rule",
                    "explanation": "Current catalog requires surname.",
                    "resolution": "Keep the current column and adapt historical writes.",
                },
                {
                    "code": "owner_rule_unchanged",
                    "status": "compatible",
                    "severity": "info",
                    "operation": "clients.access",
                    "object": "public.clients.owner_id",
                    "evidence": "structural_rule",
                    "explanation": "Owner binding is unchanged.",
                    "resolution": None,
                },
            ],
        },
    )
    run = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        user_id=project.owner_id,
        status="pending",
        agent_state={},
    )
    rows = {
        (Restoration, operation.id): operation,
        (Snapshot, snapshot.id): snapshot,
        (ProjectVersion, version.id): version,
        (Project, project.id): project,
        (GenerationRun, run.id): run,
    }

    class Session:
        real_reader = staticmethod(service.repo.read_files)
        data = rows

        def __init__(self) -> None:
            self.refresh_calls = []

        async def refresh(self, row, **kwargs):
            self.refresh_calls.append((row, kwargs))

        async def get(self, model, identity, **kwargs):
            return rows.get((model, identity))

    historical = {
        "src/components/OldCalendar.tsx": "export const OldCalendar = () => 'historic';",
        "src/app/page.tsx": "import { OldCalendar } from '../components/OldCalendar';",
    }
    reads = []
    monkeypatch.setattr(
        service.repo,
        "read_files",
        lambda project_id, sha: reads.append((project_id, sha)) or historical,
    )
    reference = RestorationAdaptationReference(
        operation_id=operation.id, expected_draft_snapshot_id=project.current_snapshot_id
    )
    return service, Session(), project, operation, run, reference, historical, reads


async def test_adaptation_admission_binds_locked_operation_to_exact_run(source_case):
    service, session, project, operation, run, reference, _, _ = source_case

    first = await service.prepare_adaptation(
        session, project, project.owner_id, reference, run
    )
    replay = await service.prepare_adaptation(
        session, project, project.owner_id, reference, run
    )

    assert replay == first
    assert first["adaptation_run_id"] == str(run.id)
    assert operation.selected_branch == "adaptive"
    assert operation.adaptation_run_id == run.id
    assert operation.state == "adapting"
    assert operation.phase == "generation"
    assert operation.revision == 2
    assert any(
        row is operation and kwargs.get("with_for_update") is True
        for row, kwargs in session.refresh_calls
    )

    other = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        user_id=project.owner_id,
        status="pending",
    )
    with pytest.raises(ApiError):
        await service.prepare_adaptation(
            session, project, project.owner_id, reference, other
        )


def _observed_catalog_report(report):
    return {
        **(report or {}),
        "database_state": "present",
        "format": 2,
        "checks": [
            {
                "code": "catalog_observed_for_adaptation_fixture",
                "status": "compatible",
                "severity": "info",
                "operation": "catalog.read",
                "object": "public",
                "evidence": "observed_catalog",
                "explanation": "The current catalog was observed before adaptation admission.",
                "resolution": None,
            }
        ],
    }


async def test_db_adaptation_admission_is_atomic_and_blocks_competing_work(
    db_session, monkeypatch
):
    from types import SimpleNamespace

    from tests.test_restorations import FakeRuntime, restoration_fixture
    from yleum_api.services import project_cell_runtime, restorations
    from yleum_api.services import restoration_adaptation as service
    from yleum_api.services.generation_runs import (
        _finalize_generation_run,
        reserve_generation_run,
    )

    async def ready_resources(_workspace_id):
        return SimpleNamespace(state="resources_ready")

    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", ready_resources)

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = FakeRuntime()
    operation = await restorations.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    operation = await restorations.cancel_restoration(
        db_session, project.id, owner.id, operation.id, runtime
    )
    operation_row = await db_session.get(Restoration, operation.id)
    assert operation_row is not None
    operation_row.report = _observed_catalog_report(operation_row.report)
    await db_session.flush()
    run, replayed = await reserve_generation_run(
        db_session,
        project_id=project.id,
        user_id=owner.id,
        idempotency_key=f"adapt-{uuid4().hex}",
        prompt="adapt selected version",
    )
    assert replayed is False
    reference = RestorationAdaptationReference(
        operation_id=operation.id,
        expected_draft_snapshot_id=current.id,
    )

    first = await service.prepare_adaptation(
        db_session, project, owner.id, reference, run
    )
    await db_session.commit()
    replay = await service.prepare_adaptation(
        db_session, project, owner.id, reference, run
    )

    assert replay == first
    bound = await db_session.get(Restoration, operation.id, populate_existing=True)
    assert bound is not None and bound.state == "adapting"
    assert bound.adaptation_run_id == run.id
    with pytest.raises(ApiError) as competing:
        await reserve_generation_run(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            idempotency_key=f"competing-{uuid4().hex}",
            prompt="competing generation",
        )
    assert competing.value.status_code == 409

    run.agent_state = {"restoration_adaptation": first}
    assert await _finalize_generation_run(db_session, run.id) == "completed"
    await db_session.refresh(run)
    await db_session.refresh(bound)
    assert bound.state == "adapting"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_pending"

    async def notified(**_kwargs):
        return None

    monkeypatch.setattr(
        "yleum_api.services.orchestrator_client."
        "project_cell_update_restoration_adaptation_owner_status",
        notified,
    )
    from yleum_api.services.generation_runs import retry_terminal_adaptation_notifications

    assert await retry_terminal_adaptation_notifications(db_session) == 1
    await db_session.refresh(bound)
    assert bound.state == "failed"
    await restorations.assert_no_active_restoration(db_session, project.id)


@pytest.mark.parametrize("cancel_timeout", [False, True])
async def test_delayed_legacy_cancel_response_cannot_overwrite_adapting(
    db_session, test_engine, monkeypatch, cancel_timeout
):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_restorations import FakeRuntime, restoration_fixture
    from yleum_api.services import project_cell_runtime, restoration_adaptation, restorations
    from yleum_api.services.generation_runs import reserve_generation_run

    class DelayedCancelRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.cancel_entered = asyncio.Event()
            self.release_cancel = asyncio.Event()

        async def cancel(self, request):
            self.cancel_calls += 1
            delayed = self.result.model_copy(
                update={
                    "state": "cancelled",
                    "phase": "cancelled",
                    "revision": 2,
                    "can_apply": False,
                    "can_cancel": False,
                }
            )
            self.cancel_entered.set()
            await self.release_cancel.wait()
            if cancel_timeout:
                raise TimeoutError("cancel response lost after admission")
            return delayed

    async def ready_resources(_workspace_id):
        return SimpleNamespace(state="resources_ready")

    monkeypatch.setattr(project_cell_runtime, "_get_cell_resources", ready_resources)

    owner, project, _, current, _, _, request = await restoration_fixture(db_session)
    runtime = DelayedCancelRuntime()
    public = await restorations.create_restoration(
        db_session, project.id, owner.id, request, runtime
    )
    operation = await db_session.get(Restoration, public.id)
    assert operation is not None
    operation.report = _observed_catalog_report(operation.report)
    operation.state, operation.phase = "reconciling", "cancel"
    await db_session.commit()

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as delayed_session:
        delayed = asyncio.create_task(
            restorations._dispatch(
                delayed_session,
                project.id,
                owner.id,
                operation.id,
                runtime,
                "cancel",
            )
        )
        await runtime.cancel_entered.wait()

        await db_session.refresh(operation)
        operation.state, operation.phase = "cancelled", "cancelled"
        await db_session.commit()
        run, replayed = await reserve_generation_run(
            db_session,
            project_id=project.id,
            user_id=owner.id,
            idempotency_key=f"delayed-cancel-{uuid4().hex}",
            prompt="adapt selected version",
        )
        assert replayed is False
        bundle = await restoration_adaptation.prepare_adaptation(
            db_session,
            project,
            owner.id,
            RestorationAdaptationReference(
                operation_id=operation.id,
                expected_draft_snapshot_id=current.id,
            ),
            run,
        )
        run.agent_state = {"restoration_adaptation": bundle}
        await db_session.commit()

        runtime.release_cancel.set()
        stale_result = await delayed

    await db_session.refresh(operation)
    assert stale_result.state == "adapting"
    assert operation.state == "adapting"
    assert operation.phase == "generation"
    assert operation.adaptation_run_id == run.id


async def test_startup_recovery_retries_terminal_adaptation_status(
    db_session, monkeypatch
):
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.models.user import User
    from yleum_api.services import orchestrator_client
    from yleum_api.services.generation_runs import recover_interrupted_generation_runs

    owner = User(email=f"adapt-recovery-{uuid4().hex}@example.test", password_hash="fixture")
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id,
        name="Adaptation recovery",
        slug=f"adapt-recovery-{uuid4().hex}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    operation_id = uuid4()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="interrupted-adaptation",
        prompt_hash="a" * 64,
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(operation_id),
            "adaptation_run_id": str(run.id),
        }
    }
    workspace = ProjectCellWorkspace(
        project_id=project.id,
        owner_id=owner.id,
        provider="docker_owner_canary",
        state="ready",
        generation_run_id=run.id,
        fencing_epoch=3,
    )
    db_session.add(workspace)
    await db_session.commit()
    calls: list[dict[str, object]] = []

    async def notify(**kwargs: object) -> None:
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("transient orchestrator outage")

    monkeypatch.setattr(
        orchestrator_client,
        "project_cell_update_restoration_adaptation_owner_status",
        notify,
    )

    assert await recover_interrupted_generation_runs(db_session) == 1
    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.agent_state["restoration_adaptation_owner_status"] == "terminal_notified"
    assert len(calls) == 2
    assert calls[-1]["workspace_id"] == workspace.id
    assert calls[-1]["operation_id"] == operation_id
    assert calls[-1]["state"] == "terminal"

    assert await recover_interrupted_generation_runs(db_session) == 0
    assert len(calls) == 2


async def test_startup_recovery_keeps_sealed_proof_for_bounded_activation_handoff(
    db_session, monkeypatch
):
    from yleum_api.models.project_cell import ProjectCellWorkspace
    from yleum_api.models.user import User
    from yleum_api.services import orchestrator_client
    from yleum_api.services.generation_runs import recover_interrupted_generation_runs

    owner = User(email=f"sealed-recovery-{uuid4().hex}@example.test", password_hash="fixture")
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id,
        name="Sealed adaptation recovery",
        slug=f"sealed-recovery-{uuid4().hex}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    operation_id = uuid4()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        idempotency_key="sealed-interrupted-adaptation",
        prompt_hash="a" * 64,
        status="running",
    )
    db_session.add(run)
    await db_session.flush()
    run.agent_state = {
        "restoration_adaptation": {
            "operation_id": str(operation_id),
            "adaptation_run_id": str(run.id),
        },
        "max_finalization": {
            "restoration_adaptation_proof": {
                "state": "proof_ready",
                "proof_digest": "b" * 64,
            }
        },
    }
    db_session.add(
        ProjectCellWorkspace(
            project_id=project.id,
            owner_id=owner.id,
            provider="docker_owner_canary",
            state="ready",
            generation_run_id=run.id,
            fencing_epoch=3,
        )
    )
    await db_session.commit()
    calls: list[dict[str, object]] = []

    async def notify(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        orchestrator_client,
        "project_cell_update_restoration_adaptation_owner_status",
        notify,
    )

    assert await recover_interrupted_generation_runs(db_session) == 1
    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.agent_state["restoration_adaptation_owner_status"] == (
        "sealed_proof_retained"
    )
    assert calls == []

    assert await recover_interrupted_generation_runs(db_session) == 0
    assert calls == []


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "run_owner",
        "run_project",
        "missing_run",
        "failed",
        "unchanged",
        "resolved_source",
        "operation_sha",
        "version_project",
    ],
)
async def test_generated_version_uses_resolved_completed_source(source_case, invalid):
    service, session, project, operation, run, reference, historical, reads = source_case
    source = await session.get(Snapshot, operation.source_snapshot_id)
    base = Snapshot(id=uuid4(), project_id=project.id, commit_sha="b" * 40)
    assistant = Message(
        id=uuid4(), project_id=project.id, role="assistant", content="Done", snapshot_id=source.id
    )
    generated = GenerationRun(
        id=uuid4(),
        project_id=project.id,
        user_id=project.owner_id,
        status="completed",
        assistant_message_id=assistant.id,
        agent_state={},
    )
    version = ProjectVersion(
        id=operation.source_version_id,
        project_id=project.id,
        number=1,
        generation_run_id=generated.id,
        snapshot_id=base.id,
        base_snapshot_id=base.id,
        commit_sha=base.commit_sha,
        status="queued",
    )
    session.data.update(
        {
            (Snapshot, base.id): base,
            (Message, assistant.id): assistant,
            (GenerationRun, generated.id): generated,
            (ProjectVersion, version.id): version,
        }
    )
    if invalid == "run_owner":
        generated.user_id = uuid4()
    elif invalid == "run_project":
        generated.project_id = uuid4()
    elif invalid == "missing_run":
        session.data.pop((GenerationRun, generated.id))
    elif invalid == "failed":
        generated.status = "failed"
    elif invalid == "unchanged":
        base.commit_sha = source.commit_sha
    elif invalid == "resolved_source":
        assistant.snapshot_id = base.id
    elif invalid == "operation_sha":
        operation.target_commit_sha = "c" * 40
    elif invalid == "version_project":
        version.project_id = uuid4()
    if invalid is not None:
        with pytest.raises(ApiError):
            await service.prepare_adaptation(session, project, project.owner_id, reference, run)
        assert reads == []
        return
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    assert bundle["source_snapshot_id"] == str(source.id)
    assert bundle["source_commit_sha"] == source.commit_sha
    assert bundle["files"] == historical
    assert reads == [(project.id, source.commit_sha)]
    assert version.status == "queued" and version.snapshot_id == base.id


@pytest.mark.parametrize("execution_backend", ["worker", "api"])
async def test_historical_component_absent_from_current_code_reaches_model_context(
    source_case, execution_backend
):
    service, session, project, _, run, reference, historical, reads = source_case
    run.execution_backend = execution_backend
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    run.agent_state = {"restoration_adaptation": bundle}
    text = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "Adapt the old UI",
    )
    assert reads == [(project.id, "a" * 40)]
    assert "src/components/OldCalendar.tsx" in text
    assert historical["src/components/OldCalendar.tsx"] in text
    assert "CURRENT business database" in text
    assert ".omnia/restoration-probe.json" in text
    assert "value_column" in text
    assert "__Host-max_session" in text
    assert text.startswith("Adapt the old UI")


async def test_adaptation_bundle_carries_explicit_contract_diff_and_immutable_proof_contract(
    source_case,
):
    service, session, project, _, run, reference, _, _ = source_case

    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    run.agent_state = {"restoration_adaptation": bundle}
    context = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "Adapt",
    )

    assert bundle["version"] == 2
    assert bundle["data_contract_diff"] == {
        "version": 1,
        "historical_source": "selected_historical_code",
        "current_source": "controller_observed_catalog",
        "findings": [
            {
                "code": "column_became_required",
                "status": "unknown",
                "severity": "blocking",
                "operation": "clients.insert",
                "object": "public.clients.surname",
                "evidence": "structural_rule",
                "resolution": "Keep the current column and adapt historical writes.",
            },
            {
                "code": "owner_rule_unchanged",
                "status": "compatible",
                "severity": "info",
                "operation": "clients.access",
                "object": "public.clients.owner_id",
                "evidence": "structural_rule",
                "resolution": None,
            },
        ],
        "blockers": ["Новое обязательное поле требует совместимой записи."],
    }
    assert bundle["preservation_contract"] == {
        "version": 1,
        "immutable": True,
        "database_target": "isolated_copy_only",
        "requirements": [
            "preserve_existing_ids",
            "preserve_existing_business_values",
            "preserve_unknown_and_hidden_fields",
            "preserve_owner_isolation",
            "additive_schema_only",
        ],
        "required_proofs": [
            "current_schema",
            "create_read_update_delete",
            "per_id_hidden_field_preservation",
            "reload_persistence",
            "cross_owner_denial",
        ],
    }
    assert '"data_contract_diff"' in context
    assert '"isolated_copy_only"' in context
    assert "proof is mandatory before promotion" in context


async def test_v2_bundle_tampering_with_proof_contract_is_rejected(source_case):
    service, session, project, _, run, reference, _, _ = source_case
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    bundle["preservation_contract"]["database_target"] = "live"
    run.agent_state = {"restoration_adaptation": bundle}

    with pytest.raises(ApiError, match="целостности"):
        await service.append_adaptation_context(
            session,
            run.id,
            project.id,
            project.owner_id,
            project.current_snapshot_id,
            "Adapt",
        )


@pytest.mark.parametrize(
    ("database_state", "current_source"),
    [
        ("present", "controller_observed_catalog"),
        ("empty", "controller_observed_catalog"),
        ("unknown", "controller_report_unavailable"),
    ],
)
def test_contract_diff_provenance_requires_an_observed_database_state(
    database_state,
    current_source,
):
    from yleum_api.services.restoration_adaptation import _data_contract_diff

    report = {
        "database_state": database_state,
        "checks": [
            {
                "code": "required_field_missing_on_create",
                "status": "incompatible",
                "severity": "blocking",
                "operation": "tasks.create",
                "object": "public.tasks.email",
                "evidence": "structural_rule",
                "resolution": "adapt",
            }
        ],
        "blockers": ["new_required_column:tasks.email"],
    }

    assert _data_contract_diff(report)["current_source"] == current_source


@pytest.mark.parametrize("invalid", ["owner", "project", "state", "head", "snapshot", "sha"])
async def test_admission_rejects_untrusted_or_stale_reference_before_git_read(source_case, invalid):
    service, session, project, operation, run, reference, _, reads = source_case
    if invalid == "owner":
        operation.owner_id = uuid4()
    elif invalid == "project":
        operation.project_id = uuid4()
    elif invalid == "state":
        operation.state = "needs_changes"
    elif invalid == "head":
        project.current_snapshot_id = uuid4()
    elif invalid == "snapshot":
        operation.source_snapshot_id = uuid4()
    else:
        operation.target_commit_sha = "b" * 40
    with pytest.raises(ApiError):
        await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    assert reads == []


async def test_dispatch_rejects_new_head_after_queue_without_exposing_bundle(source_case):
    service, session, project, _, run, reference, _, _ = source_case
    run.agent_state = {
        "restoration_adaptation": await service.prepare_adaptation(
            session, project, project.owner_id, reference, run
        )
    }
    old_head = project.current_snapshot_id
    project.current_snapshot_id = uuid4()
    with pytest.raises(ApiError):
        await service.append_adaptation_context(
            session, run.id, project.id, project.owner_id, old_head, "Adapt"
        )


async def test_source_budget_is_actionable_and_secret_files_are_excluded(source_case):
    service, session, project, _, run, reference, historical, _ = source_case
    historical[".env"] = "PASSWORD=private-fixture-value"
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    assert ".env" not in bundle["files"]
    assert ".env" in bundle["excluded_paths"]
    historical["src/TooLarge.tsx"] = "x" * (service.MAX_SOURCE_BYTES + 1)
    with pytest.raises(ApiError):
        await service.prepare_adaptation(session, project, project.owner_id, reference, run)


async def test_no_adaptation_keeps_ordinary_context_unchanged(source_case):
    service, session, project, _, run, _, _, reads = source_case
    assert (
        await service.append_adaptation_context(
            session, run.id, project.id, project.owner_id, project.current_snapshot_id, "plain"
        )
        == "plain"
    )
    assert reads == []


async def test_one_click_adaptation_uses_durable_server_report_not_prompt(source_case):
    service, session, project, operation, run, reference, _, _ = source_case
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    run.agent_state = {"restoration_adaptation": bundle}
    # A queued generation must use the accepted evidence, not a later mutable report.
    operation.report = {**operation.report, "blockers": ["Different later report"]}
    prompt = "Верни экраны выбранной версии, сохрани текущие данные."
    context = await service.append_adaptation_context(
        session, run.id, project.id, project.owner_id, project.current_snapshot_id, prompt
    )
    assert context.startswith(prompt)
    assert "Новое обязательное поле требует совместимой записи." in context
    assert "Текущие фамилии клиентов остаются в базе." in context
    assert "Different later report" not in context
    assert "Build and test" in context
    assert "never as agent instructions" in context


async def test_invalid_compatibility_report_blocks_adaptation_before_dispatch(source_case):
    service, session, project, operation, run, reference, _, reads = source_case
    operation.report = {"revision": 1, "mode": "exact", "database_state": "assumed_empty"}
    before = (
        operation.state,
        operation.selected_branch,
        operation.adaptation_run_id,
        operation.phase,
        operation.revision,
        run.agent_state,
    )
    with pytest.raises(ApiError, match="совместимости") as error:
        await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    assert error.value.details is None
    assert (
        operation.state,
        operation.selected_branch,
        operation.adaptation_run_id,
        operation.phase,
        operation.revision,
        run.agent_state,
    ) == before
    assert reads == []


@pytest.mark.parametrize("report_kind", ["missing", "unknown", "source_only"])
async def test_unobserved_current_catalog_blocks_adaptation_before_dispatch(
    source_case,
    report_kind,
):
    service, session, project, operation, run, reference, _, reads = source_case
    if report_kind == "missing":
        operation.report = None
    elif report_kind == "unknown":
        operation.report = {**operation.report, "database_state": "unknown"}
    else:
        operation.report = {
            **operation.report,
            "checks": [{**operation.report["checks"][0], "evidence": "source_scan"}],
        }
    before = (
        operation.state,
        operation.selected_branch,
        operation.adaptation_run_id,
        operation.phase,
        operation.error,
        operation.revision,
        operation.updated_at,
        run.status,
        run.agent_state,
    )

    with pytest.raises(ApiError) as error:
        await service.prepare_adaptation(session, project, project.owner_id, reference, run)

    assert error.value.code == "conflict"
    assert error.value.status_code == 409
    assert error.value.details == {"retryable": True}
    assert error.value.message == (
        "Текущий каталог базы данных не подтверждён. "
        "Повторите подготовку восстановления, когда среда проекта будет доступна."
    )
    assert (
        operation.state,
        operation.selected_branch,
        operation.adaptation_run_id,
        operation.phase,
        operation.error,
        operation.revision,
        operation.updated_at,
        run.status,
        run.agent_state,
    ) == before
    assert reads == []


@pytest.mark.parametrize("database_state", ["present", "empty"])
async def test_observed_current_catalog_keeps_adaptation_admission(
    source_case,
    database_state,
):
    service, session, project, operation, run, reference, _, reads = source_case
    operation.report = {**operation.report, "database_state": database_state}

    bundle = await service.prepare_adaptation(
        session, project, project.owner_id, reference, run
    )

    assert bundle["compatibility_report"]["database_state"] == database_state
    assert bundle["data_contract_diff"]["current_source"] == (
        "controller_observed_catalog"
    )
    assert operation.state == "adapting"
    assert operation.selected_branch == "adaptive"
    assert reads == [(project.id, operation.target_commit_sha)]


async def test_reads_selected_git_commit_not_current_tree(source_case, monkeypatch, tmp_path):
    service, session, project, operation, run, reference, historical, _ = source_case
    monkeypatch.setattr(service.repo, "read_files", session.real_reader)
    source = tmp_path / "source"
    source.mkdir()
    initial = service.repo.init_repo(project.id, source, "code")
    old = service.repo.commit_files(project.id, historical, "Historic UI", parent_sha=initial)
    current = service.repo.commit_files(
        project.id,
        {"src/app/page.tsx": "export default () => 'current'"},
        "Remove old calendar",
        parent_sha=old,
        exact_tree=True,
    )
    assert "src/components/OldCalendar.tsx" not in service.repo.read_files(project.id, current)
    snapshot = await session.get(Snapshot, operation.source_snapshot_id)
    version = await session.get(ProjectVersion, operation.source_version_id)
    snapshot.commit_sha = version.commit_sha = operation.target_commit_sha = old
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference, run)
    assert bundle["files"] == historical
    run.agent_state = {"restoration_adaptation": bundle}
    # Dispatch/retry uses the accepted private bundle, never another Git read.
    monkeypatch.setattr(service.repo, "read_files", lambda *_: pytest.fail("unexpected Git reread"))
    first = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "Adapt",
    )
    assert (
        await service.append_adaptation_context(
            session,
            run.id,
            project.id,
            project.owner_id,
            project.current_snapshot_id,
            "Adapt",
        )
        == first
    )
    assert historical["src/components/OldCalendar.tsx"] in first


def test_reference_is_part_of_reservation_identity(source_case):
    service, _, _, _, _, reference, _, _ = source_case
    prompt = "Same editable user prompt"
    assert service.adaptation_reservation_prompt(prompt, None) == prompt
    first = service.adaptation_reservation_prompt(prompt, reference)
    assert first == service.adaptation_reservation_prompt(prompt, reference.model_copy())
    assert first != service.adaptation_reservation_prompt(
        prompt,
        reference.model_copy(update={"operation_id": uuid4()}),
    )


async def test_actual_dispatch_context_statements_keep_public_prompt_separate(source_case):
    """Execute the real caller's context seam, not a model or external worker.

    Both dispatch paths enter lifecycle. Original statements from lifecycle,
    agent_preparation and agent_prompt cover the context handoff and edit input;
    no copied prompt logic or replacement production records.
    """
    import ast
    import asyncio
    from pathlib import Path

    from yleum_api.services.generation import agent_preparation, agent_prompt, lifecycle
    from yleum_api.services.generation.contracts import ProjectGenerationFacts

    service, session, project, _, run, reference, historical, _ = source_case
    run.agent_state = {
        "restoration_adaptation": await service.prepare_adaptation(
            session,
            project,
            project.owner_id,
            reference,
            run,
        )
    }
    tree = ast.parse(await asyncio.to_thread(Path(lifecycle.__file__).read_text, encoding="utf-8"))
    process = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_process_prompt"
    )
    load = [
        n
        for n in ast.walk(process)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and isinstance(n.value.value.func, ast.Name)
        and n.value.value.func.id == "append_adaptation_context"
    ]
    facts = [
        n
        for n in ast.walk(process)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "ProjectGenerationFacts"
    ]
    preparation = ast.parse(
        await asyncio.to_thread(
            Path(agent_preparation.__file__).read_text,
            encoding="utf-8",
        )
    )
    prompt = ast.parse(
        await asyncio.to_thread(
            Path(agent_prompt.__file__).read_text,
            encoding="utf-8",
        )
    )
    inject = [
        n
        for n in ast.walk(preparation)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Attribute)
        and isinstance(n.test.value, ast.Name)
        and n.test.value.id == "project_info"
        and n.test.attr == "restoration_context"
    ]
    edit = [
        n
        for n in ast.walk(prompt)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_agent_user" for t in n.targets)
        and "ТОЧЕЧНОЕ" in ast.unparse(n)
    ]
    assert len(load) == len(facts) == len(inject) == len(edit) == 1
    wrapper = ast.AsyncFunctionDef(
        name="context_seam",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        decorator_list=[],
        body=[
            ast.Assign(
                targets=[ast.Name(id="_seed_block", ctx=ast.Store())],
                value=ast.Constant(value="CURRENT FILES"),
            ),
            *load,
            *facts,
            *inject,
            *edit,
            ast.Return(
                ast.Call(
                    func=ast.Name(id="locals", ctx=ast.Load()),
                    args=[],
                    keywords=[],
                )
            ),
        ],
    )
    scope = {
        "append_adaptation_context": service.append_adaptation_context,
        "session": session,
        "ProjectGenerationFacts": ProjectGenerationFacts,
        "project_template": project.template,
        "project_slug": "adaptation",
        "project_name": "Adaptation",
        "project_design_preset_id": None,
        "project_discovery_spec": None,
        "project_image_gen_enabled": False,
        "project_language": "ru",
        "project_is_imported": False,
        "project_memory_context": "",
        "run_id": run.id,
        "project_id": project.id,
        "user_id": project.owner_id,
        "current_snapshot_id": project.current_snapshot_id,
        "prompt_text": "Adapt my UI",
        "_seed_block": "CURRENT FILES",
        "_sel_block": "",
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            str(lifecycle.__file__),
            "exec",
        ),
        scope,
    )
    result = await scope["context_seam"]()
    assert scope["prompt_text"] == "Adapt my UI"
    assert "prompt_text" not in result  # The public prompt was never reassigned.
    assert historical["src/components/OldCalendar.tsx"] in result["_agent_user"]
    assert "CURRENT FILES" in result["_agent_user"]


async def test_disposable_db_worker_reads_private_accepted_bundle(
    db_session, test_engine, monkeypatch
):
    """Real worker dispatch/DB; replace the model body with its real context loader."""
    import asyncio
    import hashlib
    import json
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.models.message import Message
    from yleum_api.models.user import User
    from yleum_api.schemas.message import GenerationRunPublic
    from yleum_api.services import restoration_adaptation as service
    from yleum_api.services.generation import lifecycle, supervisor
    from yleum_api.services.generation_runs import GenerationDispatch, store_generation_dispatch
    from yleum_api.workers import generation

    owner = User(email=f"adapt-{uuid4().hex}@example.test", password_hash="fixture")
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id, name="Adaptation", slug=f"adapt-{uuid4().hex}", template="max_miniapp"
    )
    db_session.add(project)
    await db_session.flush()
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="Current UI")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    user_message = Message(project_id=project.id, role="user", content="Adapt calendar")
    assistant = Message(project_id=project.id, role="assistant", content="")
    db_session.add_all([user_message, assistant])
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant.id,
        idempotency_key=f"adapt-{uuid4().hex}",
        prompt_hash="a" * 64,
        status="queued_for_capacity",
        execution_backend="worker",
    )
    db_session.add(run)
    await db_session.flush()
    bundle = {
        "version": 1,
        "project_id": str(project.id),
        "owner_id": str(owner.id),
        "operation_id": str(uuid4()),
        "adaptation_run_id": str(run.id),
        "source_version_id": str(uuid4()),
        "source_snapshot_id": str(uuid4()),
        "source_commit_sha": "b" * 40,
        "base_draft_snapshot_id": str(snapshot.id),
        "files": {"src/OldCalendar.tsx": "export const OldCalendar = 'historical';"},
        "excluded_paths": [],
    }
    digest = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    run.agent_state = {"restoration_adaptation": {**bundle, "sha256": digest}}
    store_generation_dispatch(
        run,
        GenerationDispatch(
            schema_version=1,
            project_id=project.id,
            user_id=owner.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant.id,
            current_snapshot_id=snapshot.id,
            prompt_text=user_message.content,
            model_id="fixture",
            force_model=None,
            is_free=False,
            orchestrate=False,
            selected_elements=[],
        ),
    )
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    inputs = []

    async def model_body(**kwargs):
        assert kwargs["prompt_text"] == "Adapt calendar"
        async with factory() as session:
            inputs.append(
                await service.append_adaptation_context(
                    session,
                    kwargs["run_id"],
                    kwargs["project_id"],
                    kwargs["user_id"],
                    kwargs["current_snapshot_id"],
                    kwargs["prompt_text"],
                )
            )
            current_run = await session.get(GenerationRun, run.id)
            current_run.status = "completed"
            current_run.finished_at = datetime.now(UTC)
            await session.commit()

    async def tracked(work, **_kwargs):
        await work

    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(lifecycle, "_process_prompt", model_body)
    monkeypatch.setattr(supervisor, "_run_tracked_prompt", tracked)
    assert await asyncio.wait_for(generation.execute_dispatch(run.id), 5)
    assert len(inputs) == 1 and "export const OldCalendar = 'historical';" in inputs[0]
    await db_session.refresh(run)
    assert "OldCalendar" not in GenerationRunPublic.model_validate(run).model_dump_json()
    await db_session.refresh(user_message)
    await db_session.refresh(snapshot)
    assert user_message.content == "Adapt calendar"
    assert snapshot.prompt_text == "Current UI"
