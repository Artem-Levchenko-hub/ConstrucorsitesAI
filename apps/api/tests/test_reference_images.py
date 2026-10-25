import io
import uuid

import pytest
from PIL import Image
from pydantic import ValidationError

from yleum_api.schemas.message import PromptRequest


def test_reference_ids_are_not_silently_discarded():
    image_id = uuid.uuid4()
    payload = PromptRequest(prompt="Reference", reference_image_ids=[image_id])
    assert payload.model_dump().get("reference_image_ids") == [image_id]


def test_reference_count_is_bounded():
    with pytest.raises(ValidationError):
        PromptRequest(prompt="Reference", reference_image_ids=[uuid.uuid4() for _ in range(5)])


def test_durable_dispatch_retains_reference_identity():
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_runs import GenerationDispatch, load_generation_dispatch

    run = GenerationRun(project_id=uuid.uuid4(), user_id=uuid.uuid4())
    references = [{"id": str(uuid.uuid4()), "sha256": "a" * 64}]
    dispatch = GenerationDispatch(
        schema_version=1,
        project_id=run.project_id,
        user_id=run.user_id,
        user_message_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        current_snapshot_id=None,
        prompt_text="Use reference",
        model_id="test",
        force_model=None,
        is_free=False,
        orchestrate=True,
        selected_elements=None,
        reference_images=references,
    )
    run.agent_state = {"dispatch": dispatch.to_json()}
    assert load_generation_dispatch(run).reference_images == references


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,capabilities", [
    ("Проанализируй переключение светлой и тёмной темы в шапке", None),
    ("Добавь переключение светлой и тёмной темы в шапке", ("header_theme_v1",)),
])
async def test_reference_binding_keeps_semantics_on_raw_text(monkeypatch, prompt, capabilities):
    import hashlib
    from types import SimpleNamespace

    from tests.test_max_behavior_proof import coordinator_harness
    from yleum_api.services import max_behavior_proof as behavior
    from yleum_api.services.reference_images import request_identity

    coordinator, _, _, run, _, _ = coordinator_harness(monkeypatch, required=False)
    run.user_message_id = uuid.uuid4()
    refs = [{"id": str(uuid.uuid4()), "sha256": "a" * 64}]
    run.authoritative_message = SimpleNamespace(
        project_id=coordinator.project_id, role="user", content=prompt, reference_images=refs,
    )
    run.prompt_hash = hashlib.sha256(request_identity(prompt, refs).encode()).hexdigest()
    async with coordinator.session_factory() as session:
        assert await behavior.original_request(session, run, "Synthetic continuation",
            project_id=coordinator.project_id, run_id=coordinator.generation_run_id) == prompt
    frozen = await behavior.freeze_for_turn(coordinator, prompt, template="max_miniapp")
    assert (frozen.capabilities if frozen else None) == capabilities
    if frozen:
        assert frozen.request_sha256 == run.prompt_hash
    refs[0]["sha256"] = "b" * 64
    with pytest.raises(behavior.BehaviorProofError, match="BEHAVIOR_REQUEST_BINDING_INVALID"):
        await behavior.freeze_for_turn(coordinator, prompt, template="max_miniapp")


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_both_agent_paths_send_reference_bytes(monkeypatch, native):
    import base64

    from yleum_api.services import agent_builder, agent_native

    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret")
    from yleum_api.core.config import get_settings

    get_settings.cache_clear()
    raw = png_bytes()
    data = base64.b64encode(raw).decode()
    images = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
    ]
    seen = []

    async def execute(action):
        return {"ok": True}

    if native:

        async def capture(client, url, convo, system, **kwargs):
            seen.append(convo)
            return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}

        monkeypatch.setattr(agent_native, "_call_messages", capture)
        await agent_native.run_native_build(
            system="sys",
            task="Follow reference",
            execute=execute,
            reference_images=images,
            max_steps=1,
        )
        assert seen[0][0]["content"][1] == images[0]
    else:

        async def capture(messages, model, **kwargs):
            seen.append(messages)
            return '<omnia:action name="done">{"summary":"done"}</omnia:action>'

        await agent_builder.run_agent_build(
            system_prompt="sys",
            user_prompt="Follow reference",
            execute=execute,
            model="test",
            complete=capture,
            reference_images=images,
            max_steps=1,
        )
        assert seen[0][1]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{data}"},
        }


@pytest.mark.asyncio
async def test_private_upload_history_and_cross_owner_denial(
    client, db_session, test_engine, monkeypatch,
):
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.message import Message
    from yleum_api.models.project import Project
    from yleum_api.models.user import User
    from yleum_api.services import reference_images

    objects = {}
    monkeypatch.setattr(
        reference_images, "_store", lambda key, raw, mime: objects.__setitem__(key, raw)
    )
    monkeypatch.setattr(reference_images, "_load", lambda row: objects[row.object_key])

    owner, project = await reference_owner(db_session)
    stranger = User(email=f"ref-{uuid.uuid4()}@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        response = await client.post(
            f"/api/projects/{project.id}/reference-images",
            files={"file": ("screen.png", png_bytes(), "image/png")},
        )
        assert response.status_code == 201
        image = response.json()
        assert "object_key" not in image
        assert image["width"] == 8 and image["height"] == 6
        fetched = await client.get(image["url"])
        assert fetched.status_code == 200 and fetched.content == png_bytes()
        assert fetched.headers["cache-control"] == "private, no-store"
        message = Message(
            project_id=project.id, role="user", content="Use this", reference_images=[image]
        )
        db_session.add(message)
        await db_session.commit()
        message_id = message.id
        db_session.expire(message)
        history = await client.get(f"/api/projects/{project.id}/messages")
        assert history.json()[0]["reference_images"] == [image]
        blocks = await reference_images.message_images(db_session, project.id, owner.id, message_id)
        import base64

        assert base64.b64decode(blocks[0]["source"]["data"]) == png_bytes()
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from yleum_api.core.config import get_settings
        from yleum_api.models.generation_run import GenerationRun
        from yleum_api.services.generation import acceptance
        from yleum_api.services.generation_runs import load_generation_dispatch

        settings = get_settings().model_copy(
            update={
                "use_generation_worker": False,
                "use_progressive_discovery": False,
                "use_clarify_interview": False,
                "use_result_type_router": False,
                "use_followup_appification": False,
            }
        )
        monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
        spawned = []
        monkeypatch.setattr(
            acceptance, "_spawn_process_prompt", lambda **kwargs: spawned.append(kwargs)
        )
        monkeypatch.setattr(acceptance, "get_redis", lambda: SimpleNamespace(publish=AsyncMock()))
        payload = {
            "prompt": "Собери приложение по изображению; task_density_v1",
            "skip_clarify": True,
            "reference_image_ids": [image["id"]],
            "idempotency_key": "reference-submit-1",
        }
        accepted = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        assert accepted.status_code == 202, accepted.text
        from yleum_api.models.reference_image import ReferenceImage
        bound_image = await db_session.get(ReferenceImage, uuid.UUID(image["id"]))
        assert bound_image.is_bound is True
        replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        # An accepted receipt must replay even if mutable storage resolution is
        # unavailable; a missing-image code would authorize a new UI identity.
        with monkeypatch.context() as replay_patch:
            async def unavailable(*args, **kwargs):
                raise AssertionError("accepted replay must not resolve draft images")
            replay_patch.setattr(reference_images, "resolve_images", unavailable)
            replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
            assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert len(spawned) == 1
        run = await db_session.get(GenerationRun, uuid.UUID(accepted.json()["run_id"]))
        dispatch = load_generation_dispatch(run)
        assert dispatch.reference_images == [{"id": image["id"], "sha256": image["sha256"]}]
        from yleum_api.services.generation.recovery_requirements import (
            freeze_for_run,
            frozen_requirements,
        )

        # Exercise the real pipeline freeze boundary on the real accepted run,
        # before any model call, then reload the same manifest in a new session.
        await freeze_for_run(
            db_session,
            run_id=run.id,
            project_id=project.id,
            user_id=owner.id,
            prompt=payload["prompt"],
            template="max_miniapp",
        )
        requirements = frozen_requirements(run)
        assert requirements and all(
            item.origin_prompt_sha256 == run.prompt_hash for item in requirements
        )
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from yleum_api.services.generation.agent_pipeline import _load_turn_reference_images
        from yleum_api.services.generation.contracts import GenerationIds

        async with async_sessionmaker(db_session.bind, expire_on_commit=False)() as cold:
            await freeze_for_run(
                cold,
                run_id=run.id,
                project_id=project.id,
                user_id=owner.id,
                prompt="Synthesized continuation context",
                template="max_miniapp",
            )
            cold_run = await cold.get(GenerationRun, run.id)
            assert frozen_requirements(cold_run) == requirements
        restored = await _load_turn_reference_images(
            async_sessionmaker(db_session.bind, expire_on_commit=False),
            GenerationIds(
                run.id, project.id, owner.id, run.user_message_id, run.assistant_message_id
            ),
        )
        assert base64.b64decode(restored[0]["source"]["data"]) == png_bytes()
        second = await client.post(
            f"/api/projects/{project.id}/reference-images",
            files={"file": ("second.png", png_bytes(), "image/png")},
        )
        conflict = await client.post(
            f"/api/projects/{project.id}/prompt",
            json={**payload, "reference_image_ids": [second.json()["id"]]},
        )
        assert conflict.status_code == 409
        await db_session.refresh(project)
        await db_session.refresh(owner)
        await db_session.refresh(stranger)
        other = Project(
            owner_id=owner.id, name="Other", slug=f"ref-{uuid.uuid4().hex}", template="blank"
        )
        db_session.add(other)
        await db_session.commit()
        foreign_submit = await client.post(f"/api/projects/{other.id}/prompt", json=payload)
        assert foreign_submit.status_code == 404
        assert foreign_submit.json()["error"]["code"] == "reference_images_expired"
        assert (
            await client.get(f"/api/projects/{other.id}/reference-images/{image['id']}")
        ).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: stranger
        assert (await client.get(image["url"])).status_code == 404
        assert (
            await client.post(
                f"/api/projects/{project.id}/reference-images",
                files={"file": ("screen.png", png_bytes(), "image/png")},
            )
        ).status_code == 404
        from pydantic import SecretStr

        from yleum_api.services.generation import recovery_diagnostic

        diagnostic_settings = get_settings()
        with monkeypatch.context() as diagnostic_patch:
            for name, value in {
                "generation_recovery_mode": "canary",
                "generation_recovery_execution_enabled": True,
                "generation_provider_attempts_enabled": True,
                "generation_response_retention_enabled": True,
                "generation_recovery_canary_project_ids": str(project.id),
                "generation_response_signing_secret": SecretStr("synthetic-reference-test-" * 3),
            }.items():
                diagnostic_patch.setattr(diagnostic_settings, name, value)
            await recovery_diagnostic.arm_diagnostic_output_budget(
                db_session, run_id=run.id, project_id=project.id, user_id=owner.id,
                attempt_id=uuid.uuid4(),
            )
            await db_session.commit()
        await _accepted_reference_reaches_publication(db_session, test_engine, monkeypatch, run)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def _accepted_reference_reaches_publication(db_session, test_engine, monkeypatch, run):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_generation_recovery_acceptance import _canonical
    from yleum_api.services.generation import agent_publication
    from yleum_api.services.generation import recovery_execution as execution
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
    from yleum_api.services.generation.recovery_acceptance import RequirementAcceptanceIncomplete

    harness, run, message, state, _ = await _canonical(
        db_session, test_engine, monkeypatch, admitted_run=run,
    )
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    driver = execution.ExecutionDriver(factory, state, {}, None)
    async with test_engine.connect() as owner:
        await owner.execute(text("SELECT pg_advisory_lock(1195724366,:key)"),
            {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)})
        await owner.commit()
        token = execution._SESSION_OWNER.set(execution.SessionOwnership(run.id, owner))
        try:
            # Real admission, frozen scope, recovery state, owner/binding reads.
            # Images do not waive executable acceptance: without a real receipt
            # publication must reach that gate, not fail the request hash first.
            with pytest.raises(RequirementAcceptanceIncomplete):
                await agent_publication._begin_publication(
                    driver, baseline=SourceBaseline(None, None, {}), files=harness.files,
                    ids=GenerationIds(run.id, run.project_id, run.user_id,
                                      run.user_message_id, message.id),
                    model_id="synthetic",
                    prompt_text="Собери приложение по изображению; task_density_v1",
                )
        finally:
            execution._SESSION_OWNER.reset(token)
            await owner.execute(text("SELECT pg_advisory_unlock(1195724366,:key)"),
                {"key": int.from_bytes(run.id.bytes[-4:], "big", signed=True)})
            await owner.commit()


@pytest.mark.parametrize(
    "mime,raw",
    [
        ("image/svg+xml", b"<svg/>"),
        ("image/jpeg", b"bad"),
        ("image/png", b"x" * (8 * 1024 * 1024 + 1)),
    ],
    ids=["svg", "malformed", "oversize"],
)
def test_invalid_images_are_rejected_before_storage(mime, raw):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.reference_images import validate_image

    with pytest.raises(ApiError) as error:
        validate_image(raw, mime)
    assert error.value.status_code == 422


def test_mime_mismatch_truncated_pixels_and_animation_rejected():
    from yleum_api.core.errors import ApiError
    from yleum_api.services.reference_images import validate_image

    with pytest.raises(ApiError):
        validate_image(png_bytes(), "image/jpeg")
    with pytest.raises(ApiError):
        validate_image(png_bytes()[:40], "image/png")
    for image in [Image.new("RGB", (8193, 1)), Image.new("RGB", (5000, 4001))]:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        with pytest.raises(ApiError):
            validate_image(buf.getvalue(), "image/png")
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        buf, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    with pytest.raises(ApiError):
        validate_image(buf.getvalue(), "image/png")


@pytest.mark.parametrize("policy,allowed", [
    ("absent", True), ("disabled", True), ("multipart", True),
    ("expiration", False), ("noncurrent", False),
])
def test_private_storage_obeys_real_minio_lifecycle_contract(monkeypatch, policy, allowed):
    from types import SimpleNamespace

    from minio import Minio
    from minio.error import S3Error
    from minio.lifecycleconfig import (
        AbortIncompleteMultipartUpload,
        Expiration,
        LifecycleConfig,
        NoncurrentVersionExpiration,
        Rule,
    )
    from minio.xml import marshal

    from yleum_api.services import reference_images

    policies = {
        "absent": None,
        "disabled": Rule("Disabled", expiration=Expiration(days=1)),
        "multipart": Rule("Enabled", abort_incomplete_multipart_upload=(
            AbortIncompleteMultipartUpload(days_after_initiation=1)
        )),
        "expiration": Rule("Enabled", expiration=Expiration(days=1)),
        "noncurrent": Rule("Enabled", noncurrent_version_expiration=(
            NoncurrentVersionExpiration(noncurrent_days=1)
        )),
    }
    client = Minio("localhost:9000", secure=False)

    def response(*args, **kwargs):
        rule = policies[policy]
        if rule is None:
            raise S3Error(
                response=None, code="NoSuchLifecycleConfiguration", message="synthetic",
                resource="qa", request_id="qa", host_id="qa",
            )
        return SimpleNamespace(data=marshal(LifecycleConfig([rule])))

    monkeypatch.setattr(client, "_execute", response)
    monkeypatch.setattr(reference_images, "_ensure_private_bucket", lambda: (client, "private"))
    if allowed:
        assert reference_images._storage() == (client, "private")
    else:
        with pytest.raises(RuntimeError, match="retention"):
            reference_images._storage()


def test_private_storage_refuses_expiration_and_integrity_changes(monkeypatch):
    from types import SimpleNamespace

    from yleum_api.services import reference_images

    client = SimpleNamespace(
        get_bucket_lifecycle=lambda bucket: SimpleNamespace(
            rules=[
                SimpleNamespace(
                    status="Enabled", expiration=object(), noncurrent_version_expiration=None
                )
            ]
        )
    )
    monkeypatch.setattr(reference_images, "_ensure_private_bucket", lambda: (client, "private"))
    with pytest.raises(RuntimeError, match="retention"):
        reference_images._storage()
    response = io.BytesIO(b"changed-private-image")
    response.release_conn = lambda: None
    client = SimpleNamespace(get_object=lambda bucket, key: response)
    monkeypatch.setattr(reference_images, "_storage", lambda: (client, "private"))
    with pytest.raises(RuntimeError, match="integrity"):
        reference_images._load(
            SimpleNamespace(object_key="private-key", size_bytes=21, sha256="a" * 64)
        )


def test_decompression_bomb_is_rejected(monkeypatch):
    from yleum_api.core.errors import ApiError
    from yleum_api.services.reference_images import validate_image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(ApiError):
        validate_image(png_bytes(), "image/png")


def test_reference_limits_leave_provider_encoding_headroom():
    from yleum_api.core.errors import ApiError
    from yleum_api.services.reference_images import validate_image

    with pytest.raises(ApiError):
        validate_image(png_bytes() + bytes(5 * 1024 * 1024), "image/png")
    buffer = io.BytesIO()
    Image.new("RGB", (8001, 1)).save(buffer, format="PNG")
    with pytest.raises(ApiError):
        validate_image(buffer.getvalue(), "image/png")


def test_reference_identity_changes_when_image_changes():
    from yleum_api.services.reference_images import request_identity

    first = [{"id": str(uuid.uuid4()), "sha256": "a" * 64}]
    other = [{"id": str(uuid.uuid4()), "sha256": "b" * 64}]
    assert request_identity("same", first) != request_identity("same", other)
    assert request_identity("same", []) == "same"


async def reference_owner(session):
    from yleum_api.models.billing import FREE_PLAN_ID, BillingAccount, Subscription
    from yleum_api.models.project import Project
    from yleum_api.models.user import User

    user = User(email=f"quota-{uuid.uuid4()}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    account = BillingAccount(personal_user_id=user.id, created_by_user_id=user.id)
    session.add(account)
    await session.flush()
    session.add(Subscription(
        billing_account_id=account.id, user_id=user.id, plan_id=FREE_PLAN_ID, status="active",
    ))
    project = Project(
        owner_id=user.id, name="Quota", slug=f"quota-{uuid.uuid4().hex}", template="blank"
    )
    session.add(project)
    await session.commit()
    return user, project


@pytest.mark.parametrize("with_image", [False, True])
async def test_redacted_credential_prompt_replays_original_identity(
    client, db_session, monkeypatch, with_image,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from yleum_api.core.config import get_settings
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.services import reference_images as images
    from yleum_api.services.generation import acceptance

    owner, project = await reference_owner(db_session)
    app.dependency_overrides[get_current_user] = lambda: owner
    monkeypatch.setattr(images, "_store", lambda *args: None)
    spawned = []
    monkeypatch.setattr(acceptance, "_spawn_text_turn",
                        lambda *args, **kwargs: spawned.append(kwargs))
    monkeypatch.setattr(acceptance, "get_redis", lambda: SimpleNamespace(publish=AsyncMock()))
    settings = get_settings().model_copy(update={
        "use_generation_worker": False, "use_progressive_discovery": False,
        "use_clarify_interview": False, "use_result_type_router": False,
        "use_followup_appification": False,
    })
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    try:
        refs = []
        if with_image:
            uploaded = await client.post(f"/api/projects/{project.id}/reference-images",
                files={"file": ("reference.png", png_bytes(), "image/png")})
            assert uploaded.status_code == 201
            refs = [uploaded.json()["id"]]
        synthetic = "sk-" + "synthetic_only_not_a_real_credential"
        payload = {"prompt": "Подключи провайдера " + synthetic,
                   "reference_image_ids": refs, "idempotency_key": "credential-replay",
                   "skip_clarify": True}
        first = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        assert first.status_code == 202
        run = await db_session.get(GenerationRun, uuid.UUID(first.json()["run_id"]))
        message = await db_session.get(Message, run.user_message_id)
        assert synthetic not in message.content and "CREDENTIAL REDACTED" in message.content
        replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert replay.json()["run_id"] == first.json()["run_id"] and len(spawned) == 1
        # Both secrets redact to the same display text, but are distinct inputs.
        changed = await client.post(f"/api/projects/{project.id}/prompt", json={
            **payload, "prompt": payload["prompt"] + "_changed",
        })
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "idempotency_conflict"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize("native", [False, True])
async def test_retry_checkpoint_retains_original_vision_payload(db_session, monkeypatch, native):
    import base64
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.message import Message
    from yleum_api.services import agent_builder, agent_native
    from yleum_api.services import reference_images as images
    from yleum_api.services.generation import source_checkpoint as checkpoint
    from yleum_api.services.generation.agent_pipeline import _load_turn_reference_images
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

    user, project = await reference_owner(db_session)
    objects = {}
    monkeypatch.setattr(images, "_store", lambda key, raw, mime: objects.__setitem__(key, raw))
    monkeypatch.setattr(images, "_load", lambda row: objects[row.object_key])
    image = await images.create_image(db_session, project.id, user.id,
                                     png_bytes(), "image/png", "original.png")
    refs = [images.public_image(image)]
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    baseline = SourceBaseline(None, None, {})
    saved = {}
    def put(project_id, run_id, data):
        digest = hashlib.sha256(data).hexdigest()
        saved[run_id, digest] = data
        return digest
    monkeypatch.setattr(checkpoint, "_put_checkpoint", put)
    monkeypatch.setattr(checkpoint, "_get_checkpoint",
                        lambda project_id, run_id, digest: saved.get((run_id, digest)))

    async def admitted(prompt, references, ordinal):
        message = Message(project_id=project.id, role="user", content=prompt,
                          reference_images=references)
        assistant = Message(project_id=project.id, role="assistant", content="")
        db_session.add_all([message, assistant])
        await db_session.flush()
        run = GenerationRun(project_id=project.id, user_id=user.id, status="running",
            response_mode="build", user_message_id=message.id, assistant_message_id=assistant.id,
            idempotency_key=str(uuid.uuid4()), agent_state={},
            prompt_hash=hashlib.sha256(
                images.request_identity(prompt, references).encode()).hexdigest(),
            created_at=datetime.now(UTC) + timedelta(seconds=ordinal))
        db_session.add(run)
        await db_session.commit()
        return run, GenerationIds(run.id, project.id, user.id, message.id, assistant.id)

    original, original_ids = await admitted("Собери по фото", refs, 0)
    await checkpoint.persist_checkpoint(factory=factory, ids=original_ids, baseline=baseline,
        files={"src/app/page.tsx": "draft"}, original_prompt="Собери по фото")
    original.status = "failed"
    await db_session.commit()
    for ordinal in (1, 2):
        retry, ids = await admitted("Попробуй ещё раз", [], ordinal)
        assert await checkpoint.adjacent_checkpoint(factory=factory, ids=ids, baseline=baseline)
        restored = await _load_turn_reference_images(factory, ids)
        seen = []
        async def execute(action):
            return {"ok": True}
        if native:
            async def capture(client, url, convo, system, **kwargs):
                seen.extend(convo[0]["content"])
                return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}
            monkeypatch.setattr(agent_native, "_call_messages", capture)
            await agent_native.run_native_build(system="sys", task="Собери по фото",
                execute=execute, reference_images=restored, max_steps=1)
            image_block = next((block for block in seen if isinstance(block, dict)
                                and block.get("type") == "image"), None)
            assert image_block and base64.b64decode(image_block["source"]["data"]) == png_bytes()
        else:
            async def capture(messages, model, **kwargs):
                seen.extend(messages[1]["content"])
                return '<omnia:action name="done">{"summary":"done"}</omnia:action>'
            await agent_builder.run_agent_build(system_prompt="sys", user_prompt="Собери по фото",
                execute=execute, model="test", complete=capture,
                reference_images=restored, max_steps=1)
            image_block = next((block for block in seen if isinstance(block, dict)
                                and block.get("type") == "image_url"), None)
            assert image_block and base64.b64decode(
                image_block["image_url"]["url"].split(",", 1)[1]) == png_bytes()
        # The next retry may start from this run's own checkpoint, whose user
        # text is only "retry". Its original visual provenance must survive too.
        await checkpoint.persist_checkpoint(factory=factory, ids=ids, baseline=baseline,
            files={"src/app/page.tsx": "next draft"}, original_prompt="Собери по фото")
        retry.status = "failed"
        await db_session.commit()
    original_message = await db_session.get(Message, original.user_message_id)
    original_message.reference_images = [{**refs[0], "sha256": "f" * 64}]
    await db_session.commit()
    with pytest.raises(ValueError, match="identity conflicts"):
        await _load_turn_reference_images(factory, ids)


async def test_reference_user_quota_is_checked_before_object_write(db_session, monkeypatch):
    from yleum_api.core.errors import ApiError
    from yleum_api.services import reference_images as images

    user, project = await reference_owner(db_session)
    raw = png_bytes()
    monkeypatch.setattr(images, "MAX_USER_BYTES", len(raw), raising=False)
    writes = []
    monkeypatch.setattr(images, "_store", lambda *args: writes.append(args[0]))
    await images.create_image(db_session, project.id, user.id, raw, "image/png", "one.png")
    with pytest.raises(ApiError) as error:
        await images.create_image(db_session, project.id, user.id, raw, "image/png", "two.png")
    assert error.value.code == "too_large" and len(writes) == 1


async def test_failed_reference_upload_keeps_durable_quota_reservation(db_session, monkeypatch):
    from sqlalchemy import select

    from yleum_api.core.errors import ApiError
    from yleum_api.models.reference_image import ReferenceImage
    from yleum_api.services import reference_images as images

    user, project = await reference_owner(db_session)

    def fail(*args):
        raise RuntimeError("private-storage-response")

    monkeypatch.setattr(images, "_store", fail)
    with pytest.raises(ApiError):
        await images.create_image(
            db_session, project.id, user.id, png_bytes(), "image/png", "one.png"
        )
    await db_session.rollback()
    rows = list((await db_session.scalars(select(ReferenceImage))).all())
    assert len(rows) == 1 and rows[0].status == "pending"


async def test_reference_quota_is_atomic_and_shared_with_cleanup(client, db_session, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from yleum_api.core.errors import ApiError
    from yleum_api.models.task_board import TaskBoardAttachmentCleanup
    from yleum_api.services import reference_images as images

    user, project = await reference_owner(db_session)
    uid, pid, raw = user.id, project.id, png_bytes()
    monkeypatch.setattr(images, "MAX_USER_BYTES", len(raw))
    writes = []
    monkeypatch.setattr(images, "_store", lambda *args: writes.append(args[0]))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def upload():
        async with factory() as session:
            try:
                await images.create_image(session, pid, uid, raw, "image/png", "same.png")
                return True
            except ApiError:
                await session.rollback()
                return False

    assert sorted(await asyncio.gather(upload(), upload())) == [False, True]
    assert len(writes) == 1
    from yleum_api.routers import task_board
    monkeypatch.setattr(task_board, "_BOARD_ATTACHMENT_BYTES_LIMIT", len(raw))
    task = await client.post("/api/task-board/tasks", json={"title": "Quota", "assignee": "artem"})
    blocked_board = await client.post(
        f"/api/task-board/tasks/{task.json()['id']}/attachments", content=b"x",
        headers={"X-File-Name": "must-not-upload.bin"},
    )
    assert blocked_board.status_code == 409
    await db_session.rollback()
    monkeypatch.setattr(images, "MAX_USER_BYTES", len(raw) * 10)
    monkeypatch.setattr(images, "PRIVATE_BUCKET_BYTES_LIMIT", len(raw) * 2)
    db_session.add(TaskBoardAttachmentCleanup(object_key="tasks/pending", size=len(raw)))
    await db_session.commit()
    with pytest.raises(ApiError):
        await images.create_image(db_session, pid, uid, raw, "image/png", "over.png")
    assert len(writes) == 1


async def test_reference_cleanup_retains_bound_history_and_charges_failed_deletes(
    db_session, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select, update

    from yleum_api.models.reference_image import ReferenceImage
    from yleum_api.models.task_board import TaskBoardAttachmentCleanup
    from yleum_api.services import reference_images as images
    from yleum_api.services import task_board_attachment_cleanup as cleanup
    from yleum_api.services.private_attachment_quota import private_attachment_bytes
    from yleum_api.services.task_board_attachments import AttachmentStorageError

    user, project = await reference_owner(db_session)
    monkeypatch.setattr(images, "_store", lambda *args: None)
    rows = [
        await images.create_image(
            db_session, project.id, user.id, png_bytes(), "image/png", f"{i}.png"
        )
        for i in range(3)
    ]
    await images.resolve_images(db_session, project.id, user.id, [rows[0].id], bind=True)
    rows[0].is_bound = True
    rows[0].bound_at = datetime.now(UTC) - timedelta(hours=48)
    for row in rows:
        row.created_at = datetime.now(UTC) - timedelta(hours=48)
    rows[2].status = "pending"
    await db_session.commit()

    def fail(key):
        raise AttachmentStorageError("unavailable")

    monkeypatch.setattr(cleanup, "_delete_attachment", fail)
    assert await cleanup.drain_attachment_cleanup_batch(db_session) == 2
    assert await private_attachment_bytes(db_session) == 3 * len(png_bytes())
    await db_session.refresh(rows[0])
    assert rows[0].status == "ready" and rows[0].bound_at is not None
    await db_session.execute(
        update(TaskBoardAttachmentCleanup).values(
            next_attempt_at=func.now() - timedelta(seconds=1)
        )
    )
    await db_session.commit()
    deleted = []
    monkeypatch.setattr(cleanup, "_delete_attachment", deleted.append)
    assert await cleanup.drain_attachment_cleanup_batch(db_session) == 2
    assert rows[0].object_key not in deleted
    assert await private_attachment_bytes(db_session) == len(png_bytes())
    await db_session.delete(project)
    await db_session.commit()
    assert await private_attachment_bytes(db_session) == len(png_bytes())
    assert await cleanup.drain_attachment_cleanup_batch(db_session) == 1
    assert await private_attachment_bytes(db_session) == 0
    assert (await db_session.scalars(select(ReferenceImage))).all() == []
