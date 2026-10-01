"""Owner-uploaded catalog photos: ownership, limits and what reaches storage."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PIL import Image

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

    photo = _image("PNG")
    request = _request("image/png; charset=binary", photo)
    result = await max_studio.upload_max_content_image(project.id, request, db_session, user)

    assert result.url.startswith("https://")
    assert stored == [(str(project.id), len(photo), "image/png")]


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
    gif = _image("GIF", animated=True)
    assert max_content_images._optimized(gif, "image/gif") == (gif, "image/gif", "gif")

    pillow = Image

    buffer = BytesIO()
    pillow.new("RGB", (2400, 1200), (12, 98, 238)).save(buffer, "PNG")
    original = buffer.getvalue()
    data, content_type, extension = max_content_images._optimized(original, "image/png")
    assert (content_type, extension) == ("image/webp", "webp")
    assert len(data) < len(original)
    assert pillow.open(BytesIO(data)).size == (1280, 640)


def _image(format: str, *, size: tuple[int, int] = (4, 3), animated: bool = False) -> bytes:
    with Image.new("RGB", size, (12, 98, 238)) as image:
        buffer = BytesIO()
        if animated:
            with Image.new("RGB", size, (230, 12, 45)) as second:
                image.save(
                    buffer, format, save_all=True, append_images=[second], duration=100, loop=0
                )
        else:
            image.save(buffer, format)
    return buffer.getvalue()


@pytest.fixture
def storage_spy(monkeypatch):
    client = Mock()
    factory = Mock(return_value=client)
    bucket = Mock()
    monkeypatch.setattr(max_content_images, "get_minio_client", factory)
    monkeypatch.setattr(max_content_images, "ensure_public_bucket", bucket)
    monkeypatch.setattr(
        max_content_images,
        "get_settings",
        lambda: SimpleNamespace(
            minio_bucket_images="qa-images", minio_public_url="https://example.test"
        ),
    )
    return factory, bucket, client


@pytest.mark.parametrize(
    "mime,data",
    [
        ("image/gif", b"inert plaintext"),
        ("image/png", b"\x89PNG" + b"0" * 100),
        ("image/jpeg", b"<html>inert</html>"),
        ("image/webp", b"RIFFinvalidWEBP"),
    ],
)
def test_malformed_content_cannot_touch_any_storage(storage_spy, mime, data):
    factory, bucket, client = storage_spy
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", data, mime)
    factory.assert_not_called()
    bucket.assert_not_called()
    client.put_object.assert_not_called()


@pytest.mark.parametrize(
    "format,mime",
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_real_supported_images_are_stored(storage_spy, format, mime):
    data = _image(format)
    url = max_content_images.store_content_image("qa-own", data, mime)
    assert url.startswith("https://example.test/")
    call = storage_spy[2].put_object.call_args
    stored = call.args[2].getvalue()
    with Image.open(BytesIO(stored)) as image:
        image.load()
        assert image.size == (4, 3)
    assert call.kwargs["content_type"] in max_content_images.CONTENT_IMAGE_TYPES


@pytest.mark.parametrize("mime", ["image/jpeg", "image/gif", "image/webp"])
def test_actual_format_must_match_declared_type_before_storage(storage_spy, mime):
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", _image("PNG"), mime)
    storage_spy[0].assert_not_called()


def test_animation_all_frames_and_exact_original_bytes_survive(storage_spy):
    gif = _image("GIF", animated=True)
    max_content_images.store_content_image("qa-own", gif, "image/gif")
    call = storage_spy[2].put_object.call_args
    assert call.args[2].getvalue() == gif
    assert call.kwargs["content_type"] == "image/gif"
    with Image.open(BytesIO(call.args[2].getvalue())) as image:
        assert image.n_frames == 2
        image.seek(1)
        image.load()


@pytest.mark.parametrize(
    "format,mime,drop",
    [
        ("PNG", "image/png", 12),
        ("JPEG", "image/jpeg", 20),
        ("GIF", "image/gif", 1),
        ("WEBP", "image/webp", 10),
    ],
)
def test_truncated_images_never_fall_back_into_storage(storage_spy, format, mime, drop):
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", _image(format)[:-drop], mime)
    storage_spy[0].assert_not_called()


@pytest.mark.parametrize(
    "mime,body",
    [
        ("image/gif", b"inert plaintext"),
        ("image/png", b"not png"),
        ("image/jpeg", b"GIF89a"),
        ("image/png", _image("GIF", animated=True)),
    ],
)
async def test_route_reports_invalid_decoded_content_as_400_without_storage(
    db_session, storage_spy, mime, body
):
    user, project = await _max_project(db_session)
    with pytest.raises(ApiError) as failure:
        await max_studio.upload_max_content_image(
            project.id, _request(mime, body), db_session, user
        )
    assert failure.value.status_code == 400
    storage_spy[0].assert_not_called()


def test_pixel_bomb_header_is_rejected_before_decode_or_storage(storage_spy, monkeypatch):
    import struct
    import zlib

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    load = Mock(side_effect=AssertionError("pixel bomb must not decode"))
    monkeypatch.setattr(Image.Image, "load", load)
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", data, "image/png")
    load.assert_not_called()
    storage_spy[0].assert_not_called()


def test_frame_budget_rejects_before_storage(storage_spy, monkeypatch):
    monkeypatch.setattr(max_content_images, "MAX_CONTENT_IMAGE_FRAMES", 1)
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", _image("GIF", animated=True), "image/gif")
    storage_spy[0].assert_not_called()


def test_total_decoded_pixels_are_bounded_even_for_small_frames(storage_spy, monkeypatch):
    monkeypatch.setattr(max_content_images, "MAX_CONTENT_IMAGE_TOTAL_PIXELS", 20)
    with pytest.raises(max_content_images.ContentImageError):
        max_content_images.store_content_image("qa-own", _image("GIF", animated=True), "image/gif")
    storage_spy[0].assert_not_called()


def test_common_24mp_jpeg_under_8mib_is_accepted(storage_spy):
    photo = _image("JPEG", size=(6000, 4000))
    assert len(photo) < max_content_images.MAX_CONTENT_IMAGE_BYTES
    max_content_images.store_content_image("qa-own", photo, "image/jpeg")
    with Image.open(BytesIO(storage_spy[2].put_object.call_args.args[2].getvalue())) as image:
        assert image.width == 1280


def test_encoder_failure_only_falls_back_to_validated_image(storage_spy, monkeypatch):
    photo = _image("PNG")
    monkeypatch.setattr(Image.Image, "save", Mock(side_effect=OSError("codec unavailable")))
    max_content_images.store_content_image("qa-own", photo, "image/png")
    assert storage_spy[2].put_object.call_args.args[2].getvalue() == photo


async def test_real_storage_failure_remains_502(db_session, monkeypatch):
    user, project = await _max_project(db_session)
    monkeypatch.setattr(
        max_content_images,
        "store_content_image",
        Mock(side_effect=max_content_images.ContentImageError("upload failed")),
    )
    with pytest.raises(ApiError) as failure:
        await max_studio.upload_max_content_image(
            project.id, _request("image/png", _image("PNG")), db_session, user
        )
    assert failure.value.status_code == 502


@pytest.mark.parametrize("decoder_error", [OSError, IndexError])
def test_decode_failure_after_validation_never_uses_encoder_fallback(
    storage_spy, monkeypatch, decoder_error
):
    photo = _image("PNG")
    original_open = Image.open
    attempts = 0

    def open_image(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise decoder_error("decoder failed on optimization read")
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", open_image)
    with pytest.raises(max_content_images.ContentImageValidationError):
        max_content_images.store_content_image("qa-own", photo, "image/png")
    storage_spy[0].assert_not_called()


def test_corrupted_png_crc_is_not_a_valid_photo(storage_spy):
    data = bytearray(_image("PNG"))
    data[data.index(b"IDAT") + 4] ^= 1
    with pytest.raises(max_content_images.ContentImageValidationError):
        max_content_images.store_content_image("qa-own", bytes(data), "image/png")
    storage_spy[0].assert_not_called()


def test_truncated_later_gif_frame_cannot_hide_behind_valid_first_frame(storage_spy):
    gif = _image("GIF", animated=True)
    damaged = gif[:-4] + b";"
    with pytest.raises(max_content_images.ContentImageValidationError):
        max_content_images.store_content_image("qa-own", damaged, "image/gif")
    storage_spy[0].assert_not_called()


@pytest.mark.parametrize("body", [b"", b"0" * (max_content_images.MAX_CONTENT_IMAGE_BYTES + 1)])
def test_direct_storage_entry_also_checks_encoded_size(storage_spy, body):
    with pytest.raises(max_content_images.ContentImageValidationError):
        max_content_images.store_content_image("qa-own", body, "image/png")
    storage_spy[0].assert_not_called()
