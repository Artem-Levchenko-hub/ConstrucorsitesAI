"""Owner-uploaded catalog photos: ownership, limits and what reaches storage."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import max_studio
from yleum_api.services import max_content_images


def _request(content_type: str, body: bytes, chunk: int = 64 * 1024):
    async def stream():
        for start in range(0, max(len(body), 1), chunk):
            yield body[start : start + chunk]

    return SimpleNamespace(
        headers={"content-type": content_type},
        stream=stream,
    )


async def _max_project(db_session, *, owner: User | None = None) -> tuple[User, Project]:
    user = owner or User(email=f"photo-{uuid4()}@example.ru")
    if owner is None:
        db_session.add(user)
        await db_session.flush()
    project = Project(
        owner_id=user.id,
        name="Магазин одежды",
        slug=f"shop-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.commit()
    return user, project


async def test_upload_returns_a_public_url_for_the_saved_item(db_session, monkeypatch) -> None:
    user, project = await _max_project(db_session)
    stored: list[tuple[str, int, str]] = []

    def fake_store(project_id: str, data: bytes, content_type: str) -> str:
        stored.append((project_id, len(data), content_type))
        return "https://yleum.ru/minio/omnia-images/max-content/x/y.webp"

    monkeypatch.setattr(max_content_images, "store_content_image", fake_store)

    request = _request("image/png; charset=binary", b"\x89PNG" + b"0" * 4096)
    result = await max_studio.upload_max_content_image(project.id, request, db_session, user)

    assert result.url.startswith("https://")
    assert stored == [(str(project.id), 4100, "image/png")]


async def test_upload_refuses_a_file_that_is_not_an_image(db_session) -> None:
    user, project = await _max_project(db_session)
    with pytest.raises(ApiError) as failure:
        await max_studio.upload_max_content_image(
            project.id, _request("application/pdf", b"%PDF-1.7"), db_session, user
        )
    assert failure.value.status_code == 400


async def test_upload_stops_reading_a_body_over_the_limit(db_session) -> None:
    user, project = await _max_project(db_session)
    oversized = b"0" * (max_content_images.MAX_CONTENT_IMAGE_BYTES + 1)
    with pytest.raises(ApiError) as failure:
        await max_studio.upload_max_content_image(
            project.id, _request("image/jpeg", oversized), db_session, user
        )
    assert failure.value.status_code == 413


async def test_upload_is_refused_for_another_owners_project(db_session) -> None:
    _, project = await _max_project(db_session)
    stranger = User(email=f"stranger-{uuid4()}@example.ru")
    db_session.add(stranger)
    await db_session.commit()
    with pytest.raises(ApiError) as failure:
        await max_studio.upload_max_content_image(
            project.id, _request("image/png", b"\x89PNG"), db_session, stranger
        )
    assert failure.value.status_code == 404


def test_storage_refuses_a_type_a_browser_would_not_render_safely() -> None:
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("p", b"<svg/>", "image/svg+xml")


def test_animation_is_kept_as_is_while_a_photo_is_re_encoded() -> None:
    gif = b"GIF89a" + b"0" * 64
    assert max_content_images._optimized(gif, "image/gif") == (gif, "image/gif", "gif")

    pillow = pytest.importorskip("PIL.Image")
    from io import BytesIO

    buffer = BytesIO()
    pillow.new("RGB", (2400, 1200), (12, 98, 238)).save(buffer, "PNG")
    original = buffer.getvalue()
    data, content_type, extension = max_content_images._optimized(original, "image/png")
    assert (content_type, extension) == ("image/webp", "webp")
    assert len(data) < len(original)
    assert pillow.open(BytesIO(data)).size == (1280, 640)
