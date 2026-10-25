"""Real DB/API boundaries; synthetic font bytes, never a live object store/provider."""

import base64
import hashlib
import io
import json
import os
import shutil
import string
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_reference_images import reference_owner
from yleum_api.core.deps import get_current_user
from yleum_api.core.errors import ApiError
from yleum_api.models.font_asset import FontAsset
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.user import User
from yleum_api.schemas.message import FontSelection
from yleum_api.services import font_assets
from yleum_api.services.font_validation_worker import normalize


def fixture_font(family="Yleum Test Font"):
    """Build a tiny original TTF, including real Cyrillic and ruble cmap entries."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    points = sorted(
        set(map(ord, string.ascii_letters + string.digits))
        | set(range(0x410, 0x450))
        | {0x401, 0x451, 0x20BD, 0x20}
    )
    mapping = {point: f"uni{point:04X}" for point in points}
    glyph_order = [".notdef", *mapping.values()]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(mapping)
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        if name != "uni0020":
            pen.moveTo((50, 0))
            pen.lineTo((550, 0))
            pen.lineTo((550, 700))
            pen.lineTo((50, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(dict.fromkeys(glyph_order, (600, 0)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": family,
            "fullName": family + " Regular",
            "psName": family.replace(" ", "") + "-Regular",
            "version": "Version 1.000",
            "licenseDescription": "Original synthetic test fixture",
        }
    )
    builder.setupOS2(
        sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200, usWeightClass=400
    )
    builder.setupPost()
    builder.setupMaxp()
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def ots_available():
    # _validate intentionally replaces PATH, so match its actual execution path.
    return shutil.which("ots-sanitize", path=os.defpath) is not None


@pytest.fixture
def font_storage(monkeypatch):
    objects = {}

    def store(key, raw, mime):
        assert key.startswith("fonts/") and mime == "application/json"
        objects[key] = raw

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    class Storage:
        def get_object(self, bucket, key):
            assert bucket == "isolated-font-tests"
            return Response(objects[key])

    monkeypatch.setattr(font_assets, "_store", store)
    monkeypatch.setattr(font_assets, "_storage", lambda: (Storage(), "isolated-font-tests"))
    if not ots_available():
        # Windows has no OTS. Still use real FontTools parsing/WOFF2 encoding;
        # CI with OTS exercises the unchanged isolated production subprocess.
        monkeypatch.setattr(font_assets, "_validate", lambda raw: normalize(raw, sanitize=False))
    return objects


def test_runtime_worker_real_ots():
    if not ots_available():
        pytest.skip("ots-sanitize absent from isolated worker PATH; required in Linux CI")
    face = font_assets._validate(fixture_font())
    assert face["family"] == "Yleum Test Font"
    assert face["has_cyrillic"] is True and face["has_ruble"] is True
    assert base64.b64decode(face["data"]).startswith(b"wOF2")
    with pytest.raises(ApiError) as error:
        font_assets._validate(b"not a font")
    assert error.value.code == "validation_failed"


async def test_uncommitted_font_admission_expires_but_message_binding_is_retained(
    db_session,
    monkeypatch,
    font_storage,
):
    from datetime import UTC, datetime, timedelta

    from yleum_api.services import task_board_attachment_cleanup as cleanup

    owner, project = await reference_owner(db_session)
    owner_id, project_id = owner.id, project.id
    assets = [
        await font_assets.create_font(
            db_session, project_id, owner_id, fixture_font(), f"{index}.ttf"
        )
        for index in range(2)
    ]
    bindings = await font_assets.resolve_selections(
        db_session,
        project_id,
        owner_id,
        [FontSelection(asset_id=row.id, role="all") for row in assets],
        bind=True,
    )
    await db_session.commit()  # Same boundary as policy admission, before Message.
    assert all(row.is_bound is False and row.bound_at is not None for row in assets)
    await font_assets.bind_message_fonts(db_session, project_id, owner_id, bindings[1:])
    await db_session.rollback()  # Message transaction never committed.
    for row in assets:
        await db_session.refresh(row)
    assert all(row.is_bound is False for row in assets)
    await font_assets.bind_message_fonts(db_session, project_id, owner_id, bindings[:1])
    db_session.add(
        Message(
            project_id=project_id, role="user", content="Accepted", font_selections=bindings[:1]
        )
    )
    await db_session.commit()
    for row in assets:
        row.created_at = row.bound_at = datetime.now(UTC) - timedelta(hours=48)
    await db_session.commit()
    await font_assets.enqueue_expired_fonts(db_session, 20)
    await db_session.commit()
    assert assets[0].status == "ready" and assets[0].is_bound is True
    assert assets[1].status == "deleting" and assets[1].is_bound is False
    monkeypatch.setattr(cleanup, "_delete_attachment", lambda key: font_storage.pop(key))
    assert await cleanup.drain_attachment_cleanup_batch(db_session) == 1
    assert await db_session.get(FontAsset, assets[0].id) is not None


def test_synthetic_font_fixture_uses_real_woff2_and_cyrillic():
    face = normalize(fixture_font(), sanitize=False)
    assert face["has_cyrillic"] is True and face["has_ruble"] is True
    assert base64.b64decode(face["data"]).startswith(b"wOF2")
    assert face["weight"] == 400 and face["style"] == "normal"


async def test_owner_upload_binding_and_history_survive_cold_session(
    client,
    db_session,
    monkeypatch,
    font_storage,
):
    from yleum_api.core.config import get_settings
    from yleum_api.main import app
    from yleum_api.services.generation import acceptance
    from yleum_api.services.generation_runs import load_generation_dispatch

    owner, project = await reference_owner(db_session)
    app.dependency_overrides[get_current_user] = lambda: owner
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
    monkeypatch.setattr(acceptance, "_spawn_process_prompt", lambda **kw: spawned.append(kw))
    monkeypatch.setattr(acceptance, "get_redis", lambda: SimpleNamespace(publish=AsyncMock()))
    uploaded = await client.post(
        f"/api/projects/{project.id}/font-assets",
        files={
            "file": ("owner.ttf", fixture_font(), "font/ttf"),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    public = uploaded.json()
    assert set(public) == {
        "id",
        "family",
        "filename",
        "sha256",
        "size_bytes",
        "weight",
        "style",
        "source",
        "license",
    }
    assert public["family"] == "Yleum Test Font" and public["source"] == "upload"
    assert public["license"] is None
    stored = next(iter(font_storage.values()))
    assert public["sha256"] == hashlib.sha256(stored).hexdigest()
    assert public["size_bytes"] == len(stored)
    payload = {
        "prompt": "Примени приложенное оформление к приложению",
        "skip_clarify": True,
        "font_selections": [{"asset_id": public["id"], "role": "heading"}],
        "idempotency_key": "font-submit-owner-1",
    }
    accepted = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    assert accepted.status_code == 202, accepted.text
    run = await db_session.get(GenerationRun, uuid.UUID(accepted.json()["run_id"]))
    binding = {**public, "role": "heading"}
    assert load_generation_dispatch(run).font_selections == [
        {"id": public["id"], "sha256": public["sha256"], "role": "heading"},
    ]
    font = await db_session.get(FontAsset, uuid.UUID(public["id"]))
    assert font.is_bound is True
    async with async_sessionmaker(db_session.bind, expire_on_commit=False)() as cold:
        message = await cold.get(Message, run.user_message_id)
        assert message.font_selections == [binding]
        bundles = await font_assets.message_font_bundles(cold, project.id, owner.id, message.id)
        assert bundles[0][0] == binding
        assert base64.b64decode(bundles[0][1]["faces"][0]["data"]).startswith(b"wOF2")
    history = await client.get(f"/api/projects/{project.id}/messages")
    assert history.status_code == 200
    user_message = next(item for item in history.json() if item["id"] == str(run.user_message_id))
    assert user_message["font_selections"] == [binding]
    with monkeypatch.context() as replay_patch:

        async def no_resolution(*args, **kwargs):
            raise AssertionError("accepted replay must not resolve mutable draft assets")

        replay_patch.setattr(font_assets, "resolve_selections", no_resolution)
        replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
    changed_role = await client.post(
        f"/api/projects/{project.id}/prompt",
        json={
            **payload,
            "font_selections": [{"asset_id": public["id"], "role": "body"}],
        },
    )
    assert changed_role.status_code == 409
    assert changed_role.json()["error"]["code"] == "idempotency_conflict"
    assert len(spawned) == 1
    font_storage[font.object_key] = b"corrupt"
    with pytest.raises(ApiError) as error:
        await font_assets.message_font_bundles(
            db_session, project.id, owner.id, run.user_message_id
        )
    assert error.value.code == "conflict"


async def test_foreign_owner_is_denied_before_font_parsing_or_name_fetch(
    client,
    db_session,
    monkeypatch,
):
    from yleum_api.main import app

    _, project = await reference_owner(db_session)
    stranger = User(email=f"font-stranger-{uuid.uuid4()}@example.com", password_hash="x")
    db_session.add(stranger)
    await db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: stranger

    def forbidden(*args):
        raise AssertionError("foreign owner reached font processing")

    monkeypatch.setattr(font_assets, "_validate", forbidden)
    monkeypatch.setattr(font_assets, "fetch_family", forbidden)
    response = await client.post(
        f"/api/projects/{project.id}/font-assets",
        files={
            "file": ("broken.ttf", b"malformed-font", "font/ttf"),
        },
    )
    assert response.status_code == 404
    response = await client.post(
        f"/api/projects/{project.id}/font-assets/resolve",
        json={
            "family_name": "Montserrat",
        },
    )
    assert response.status_code == 404


async def test_cross_project_and_foreign_owner_cannot_bind_font(db_session, font_storage):
    owner, project = await reference_owner(db_session)
    stranger, other = await reference_owner(db_session)
    row = await font_assets.create_font(
        db_session, project.id, owner.id, fixture_font(), "face.ttf"
    )
    selection = [FontSelection(asset_id=row.id, role="body")]
    for project_id, user_id in ((other.id, owner.id), (project.id, stranger.id)):
        with pytest.raises(ApiError) as error:
            await font_assets.resolve_selections(
                db_session, project_id, user_id, selection, bind=True
            )
        assert error.value.code == "not_found"
    assert row.is_bound is False


async def test_failed_put_remains_pending_and_charged_before_next_write(
    db_session,
    monkeypatch,
    font_storage,
):
    from yleum_api.services.private_attachment_quota import private_attachment_bytes

    owner, project = await reference_owner(db_session)
    user_id, project_id = owner.id, project.id
    writes = []

    def failed_put(key, raw, mime):
        writes.append(key)
        raise RuntimeError("synthetic unknown storage response")

    monkeypatch.setattr(font_assets, "_store", failed_put)
    with pytest.raises(ApiError) as error:
        await font_assets.create_font(db_session, project_id, user_id, fixture_font(), "one.ttf")
    assert error.value.code == "upload_failed"
    await db_session.rollback()
    rows = list((await db_session.scalars(select(FontAsset))).all())
    assert len(rows) == 1 and rows[0].status == "pending"
    assert await private_attachment_bytes(db_session) == rows[0].size_bytes
    monkeypatch.setattr(font_assets, "PRIVATE_BUCKET_BYTES_LIMIT", rows[0].size_bytes)
    with pytest.raises(ApiError) as error:
        await font_assets.create_font(db_session, project_id, user_id, fixture_font(), "two.ttf")
    assert error.value.code == "too_large" and len(writes) == 1


@pytest.mark.parametrize("name", ["Montserrat", "https://fonts.google.com/specimen/Montserrat"])
async def test_free_name_outside_core_catalogue_is_resolved_and_reused(
    client,
    db_session,
    monkeypatch,
    font_storage,
    name,
):
    from yleum_api.main import app
    from yleum_api.services.typography import font_catalogue

    assert "montserrat" not in font_catalogue()["fonts"]
    owner, project = await reference_owner(db_session)
    app.dependency_overrides[get_current_user] = lambda: owner
    raw = fixture_font("Montserrat")
    calls = []

    def fetch(value):
        calls.append(value)
        return {
            "family": "Montserrat",
            "package": "@fontsource/montserrat",
            "version": "5.3.0",
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
            "license": "OFL-1.1",
            "license_text": "Synthetic package license fixture",
            "faces": [{"raw": raw, "unicode_range": "U+0-10FFFF"}],
        }

    monkeypatch.setattr(font_assets, "fetch_family", fetch)
    endpoint = f"/api/projects/{project.id}/font-assets/resolve"
    response = await client.post(endpoint, json={"family_name": name})
    assert response.status_code == 201, response.text
    public = response.json()
    assert public["family"] == "Montserrat" and public["source"] == "fontsource"
    assert public["license"] == "OFL-1.1" and "object_key" not in public
    again = await client.post(endpoint, json={"family_name": "Montserrat"})
    assert again.status_code == 201 and again.json()["id"] == public["id"]
    assert calls == [name]
    saved = json.loads(next(iter(font_storage.values())))
    assert saved["license_text"] == "Synthetic package license fixture"
    assert base64.b64decode(saved["faces"][0]["data"]).startswith(b"wOF2")
