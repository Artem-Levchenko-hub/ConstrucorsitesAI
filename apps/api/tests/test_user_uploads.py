from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from omnia_api.services import user_uploads


def _image_bytes(
    *,
    size: tuple[int, int] = (1200, 800),
    mode: str = "RGB",
    fmt: str = "PNG",
) -> bytes:
    color = (120, 80, 40, 128) if mode == "RGBA" else (120, 80, 40)
    image = Image.new(mode, size, color)
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_sanitize_image_reencodes_png_as_webp() -> None:
    raw = _image_bytes()

    data, width, height = user_uploads._sanitize_image(raw)

    assert (width, height) == (1200, 800)
    assert len(data) < len(raw)
    with Image.open(BytesIO(data)) as image:
        assert image.format == "WEBP"
        assert image.size == (1200, 800)


def test_sanitize_image_preserves_alpha() -> None:
    data, _, _ = user_uploads._sanitize_image(_image_bytes(mode="RGBA"))

    with Image.open(BytesIO(data)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 128


def test_sanitize_image_downscales_longest_side() -> None:
    data, width, height = user_uploads._sanitize_image(
        _image_bytes(size=(3000, 1000), fmt="JPEG")
    )

    assert (width, height) == (2560, 853)
    with Image.open(BytesIO(data)) as image:
        assert image.size == (2560, 853)


def test_sanitize_image_rejects_unsupported_raster() -> None:
    with pytest.raises(user_uploads.UploadRejected, match="неподдерживаемый формат"):
        user_uploads._sanitize_image(_image_bytes(fmt="BMP"))


def test_upload_record_uses_webp_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    uploaded: dict[str, object] = {}

    class FakeMinio:
        def bucket_exists(self, bucket: str) -> bool:
            return True

        def put_object(
            self,
            bucket: str,
            key: str,
            stream: BytesIO,
            *,
            length: int,
            content_type: str,
        ) -> None:
            uploaded.update(
                bucket=bucket,
                key=key,
                data=stream.read(),
                length=length,
                content_type=content_type,
            )

    monkeypatch.setattr(
        user_uploads,
        "get_settings",
        lambda: SimpleNamespace(
            minio_bucket_images="images",
            minio_public_url="https://media.example.test",
        ),
    )
    monkeypatch.setattr(user_uploads, "get_minio_client", FakeMinio)

    asset = user_uploads.sanitize_and_upload_record(
        _image_bytes(size=(640, 480)), "project-1"
    )

    assert asset.mime_type == "image/webp"
    assert asset.storage_key.endswith(".webp")
    assert asset.url.endswith(asset.storage_key)
    assert asset.bytes_size == uploaded["length"]
    assert uploaded["content_type"] == "image/webp"
