from sqlalchemy import select

from omnia_api.core.security import create_access_token
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.project_versions import ensure_generation_version, record_restored_version


async def fixture(db):
    user = User(email="versions@example.test")
    db.add(user)
    await db.flush()
    project = Project(owner_id=user.id, name="Versions", slug="versions", template="max_miniapp")
    db.add(project)
    await db.flush()
    snap = Snapshot(project_id=project.id, commit_sha="a" * 40)
    db.add(snap)
    await db.flush()
    project.current_snapshot_id = snap.id
    await db.commit()
    return user, project, snap


async def accepted(db, project, number, status="completed"):
    user_message = Message(project_id=project.id, role="user", content=f"Prompt {number}")
    assistant = Message(project_id=project.id, role="assistant", content="", model_id="test-model")
    db.add_all([user_message, assistant])
    await db.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=project.owner_id,
        user_message_id=user_message.id,
        assistant_message_id=assistant.id,
        idempotency_key=str(number),
        prompt_hash="hash",
        status=status,
    )
    db.add(run)
    await db.flush()
    return run, assistant


async def test_versions_are_durable_idempotent_failed_and_paginated(client, db_session):
    owner, project, base = await fixture(db_session)
    run1, _ = await accepted(db_session, project, 1)
    first = await ensure_generation_version(db_session, run1, project)
    again = await ensure_generation_version(db_session, run1, project)
    assert first.id == again.id
    run2, _ = await accepted(db_session, project, 2, "failed")
    second = await ensure_generation_version(db_session, run2, project)
    await db_session.commit()
    assert (first.number, second.number) == (1, 2)
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"
    result = await client.get(f"/api/projects/{project.id}/versions?limit=1")
    assert result.status_code == 200, result.text
    assert result.json()["versions"][0]["status"] == "failed"
    assert result.json()["versions"][0]["is_current"] is False
    assert result.json()["next_cursor"] == 2
    previous = (await client.get(f"/api/projects/{project.id}/versions?before=2")).json()
    row = previous["versions"][0]
    assert row["number"] == 1 and row["status"] == "unchanged"
    assert row["snapshot_id"] == str(base.id) and row["is_current"]
    assert row["source_message_id"] == str(run1.user_message_id)
    assert row["previews"] == [] and row["preview_status"] == "missing"
    assert previous["next_cursor"] is None
    assert len((await db_session.scalars(select(ProjectVersion))).all()) == 2
    client.headers.pop("Authorization")
    assert (await client.get(f"/api/projects/{project.id}/versions")).status_code == 401


async def test_owner_scope_and_final_snapshot_provenance(client, db_session):
    owner, project, base = await fixture(db_session)
    run, assistant = await accepted(db_session, project, 1)
    await ensure_generation_version(db_session, run, project)
    final = Snapshot(
        project_id=project.id,
        commit_sha="b" * 40,
        preview_status="ready",
        preview_commit_sha="b" * 40,
        preview_manifest=[{"key": "pending", "width": 390, "height": 844, "route": "/"}],
    )
    db_session.add(final)
    await db_session.flush()
    final.preview_manifest = [
        {
            "key": f"snapshot-previews/{project.id}/{final.id}/" + "d" * 64 + ".png",
            "width": 390,
            "height": 844,
            "route": "/",
        }
    ]
    assistant.snapshot_id = final.id
    project.current_snapshot_id = final.id
    await db_session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"
    row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert row["status"] == "ready" and row["is_current"]
    assert row["can_restore"] is False
    project.template = "landing"
    await db_session.commit()
    static_row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert static_row["can_restore"] is True
    assert row["commit_sha"] == final.commit_sha
    assert row["previews"] == [
        {
            "url": f"/api/projects/{project.id}/snapshots/{final.id}/previews/0?v=" + "d" * 64,
            "width": 390,
            "height": 844,
            "route": "/",
            "reconstructed": False,
        }
    ]
    final.preview_commit_sha = base.commit_sha
    await db_session.commit()
    row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert row["previews"] == [] and row["preview_status"] == "missing"
    stranger = User(email="stranger@example.test")
    db_session.add(stranger)
    await db_session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(stranger.id)}"
    assert (await client.get(f"/api/projects/{project.id}/versions")).status_code == 404


async def test_restoration_creates_next_version_once(db_session):
    _, project, base = await fixture(db_session)
    run, _ = await accepted(db_session, project, 1)
    await ensure_generation_version(db_session, run, project)
    restored = Snapshot(project_id=project.id, commit_sha="c" * 40)
    db_session.add(restored)
    await db_session.flush()
    version = await record_restored_version(db_session, project, restored, base)
    repeated = await record_restored_version(db_session, project, restored, base)
    assert version.id == repeated.id and version.number == 2
    assert version.snapshot_id == restored.id
    assert version.generation_run_id is None
    await db_session.commit()


async def test_concurrent_allocators_serialize_project_number(db_session):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    _, project, _ = await fixture(db_session)
    first, _ = await accepted(db_session, project, 1)
    second, _ = await accepted(db_session, project, 2)
    await db_session.commit()
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def allocate(run_id):
        async with sessions() as session:
            local_project = await session.get(Project, project.id)
            local_run = await session.get(GenerationRun, run_id)
            result = await ensure_generation_version(session, local_run, local_project)
            await session.commit()
            return result.number

    assert sorted(await asyncio.gather(allocate(first.id), allocate(second.id))) == [1, 2]


async def test_unchanged_run_keeps_base_image_when_technical_snapshot_reuses_commit(
    client, db_session
):
    owner, project, base = await fixture(db_session)
    base.preview_status = "ready"
    base.preview_commit_sha = base.commit_sha
    base.preview_manifest = [
        {
            "key": f"snapshot-previews/{project.id}/{base.id}/" + "e" * 64 + ".png",
            "width": 390,
            "height": 844,
            "route": "/",
        }
    ]
    run, assistant = await accepted(db_session, project, 1)
    await ensure_generation_version(db_session, run, project)
    technical = Snapshot(project_id=project.id, commit_sha=base.commit_sha)
    db_session.add(technical)
    await db_session.flush()
    assistant.snapshot_id = technical.id
    project.current_snapshot_id = technical.id
    await db_session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"
    row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert row["status"] == "unchanged" and row["is_current"]
    assert row["snapshot_id"] == str(base.id)
    assert row["preview_status"] == "ready" and len(row["previews"]) == 1


async def test_current_version_survives_managed_technical_child_commit(client, db_session):
    owner, project, base = await fixture(db_session)
    run, _ = await accepted(db_session, project, 1)
    version = await ensure_generation_version(db_session, run, project)
    technical = Snapshot(project_id=project.id, parent_id=base.id, commit_sha="f" * 40)
    db_session.add(technical)
    await db_session.flush()
    project.current_snapshot_id = technical.id
    await db_session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"
    row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert row["id"] == str(version.id) and row["is_current"]
    assert row["snapshot_id"] == str(base.id)


def test_reconstructed_preview_label_is_explicit_and_backward_compatible():
    from uuid import uuid4

    from omnia_api.schemas.project_version import VersionPreview
    from omnia_api.services.project_versions import preview_fields

    sid, pid = uuid4(), uuid4()
    item = {
        "key": f"snapshot-previews/{pid}/{sid}/" + "a" * 64 + ".png",
        "width": 390,
        "height": 844,
        "route": "/",
    }
    snap = Snapshot(
        id=sid,
        project_id=pid,
        commit_sha="b" * 40,
        preview_commit_sha="b" * 40,
        preview_status="ready",
        preview_manifest=[item],
    )
    _, previews = preview_fields(snap)
    assert VersionPreview.model_validate(previews[0]).reconstructed is False
    snap.preview_manifest = [item | {"reconstructed": True}]
    _, previews = preview_fields(snap)
    assert VersionPreview.model_validate(previews[0]).reconstructed is True
    snap.preview_manifest = [item | {"reconstructed": "true"}]
    _, previews = preview_fields(snap)
    assert VersionPreview.model_validate(previews[0]).reconstructed is False


def test_unfinished_versions_never_present_base_version_image():
    from uuid import uuid4

    from omnia_api.services.project_versions import version_preview_fields

    sid, pid = uuid4(), uuid4()
    base = Snapshot(
        id=sid,
        project_id=pid,
        commit_sha="b" * 40,
        preview_commit_sha="b" * 40,
        preview_status="ready",
        preview_manifest=[
            {
                "key": f"snapshot-previews/{pid}/{sid}/" + "a" * 64 + ".png",
                "width": 390,
                "height": 844,
                "route": "/",
            }
        ],
    )
    for state in ("queued", "running"):
        assert version_preview_fields(state, base) == ("pending", [])
    for state in ("failed", "cancelled"):
        assert version_preview_fields(state, base) == ("missing", [])
    for state in ("ready", "unchanged"):
        preview_status, previews = version_preview_fields(state, base)
        assert preview_status == "ready" and len(previews) == 1


async def test_failed_version_api_hides_verified_base_preview(client, db_session):
    owner, project, base = await fixture(db_session)
    base.preview_status = "ready"
    base.preview_commit_sha = base.commit_sha
    base.preview_manifest = [
        {
            "key": f"snapshot-previews/{project.id}/{base.id}/" + "a" * 64 + ".png",
            "width": 390,
            "height": 844,
            "route": "/",
        }
    ]
    run, _ = await accepted(db_session, project, 1, "failed")
    await ensure_generation_version(db_session, run, project)
    await db_session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id)}"
    row = (await client.get(f"/api/projects/{project.id}/versions")).json()["versions"][0]
    assert row["status"] == "failed"
    assert row["preview_status"] == "missing" and row["previews"] == []
