from uuid import uuid4

from omnia_api.models.hero_media_render import HeroMediaRender
from omnia_api.routers.hero_media import _prepare_render_retry
from omnia_api.services.hero_media_pipeline import _locked_media_plan, _storage_key_from_url


def _render(*, media_plan: str = "motion") -> HeroMediaRender:
    return HeroMediaRender(
        project_id=uuid4(),
        owner_id=uuid4(),
        brief_id=uuid4(),
        status="completed",
        media_plan=media_plan,
        status_detail="Hero готов",
        provider_summary="plan=video; video_model=provider",
        poster_asset_id=uuid4(),
        video_asset_id=uuid4(),
        applied_snapshot_id=uuid4(),
        bundle={"mode": "video"},
        progress_log=[{"status": "completed", "detail": "Hero готов"}],
        error="old failure",
        retry_count=2,
    )


def test_retry_keeps_the_media_plan_locked_to_the_render() -> None:
    render = _render(media_plan="motion")

    assert _locked_media_plan(render) == "motion"


def test_prepare_retry_resets_terminal_output_and_counts_user_retry() -> None:
    render = _render()

    _prepare_render_retry(render)

    assert render.status == "queued"
    assert render.status_detail == "Повторно поставлено в очередь"
    assert render.provider_summary is None
    assert render.error is None
    assert render.bundle is None
    assert render.poster_asset_id is None
    assert render.video_asset_id is None
    assert render.applied_snapshot_id is None
    assert render.applied_at is None
    assert render.started_at is None
    assert render.finished_at is None
    assert render.retry_count == 3
    assert render.progress_log[-1]["status"] == "queued"


def test_storage_key_supports_nginx_prefixed_public_media_url() -> None:
    url = "https://constructor.example/minio/omnia-videos/project/video.mp4"

    assert _storage_key_from_url(url, "omnia-videos") == "project/video.mp4"


def test_storage_key_supports_direct_bucket_url() -> None:
    url = "https://minio.example/omnia-images/uploads/project/poster.webp"

    assert _storage_key_from_url(url, "omnia-images") == "uploads/project/poster.webp"


def test_storage_key_rejects_another_bucket() -> None:
    url = "https://constructor.example/minio/omnia-videos/project/video.mp4"

    assert _storage_key_from_url(url, "omnia-images") is None
