from omnia_api.routers.hero_media import _apply_hero_block
from omnia_api.schemas.hero_media import HeroMediaDecision, HeroMediaShot
from omnia_api.services.hero_media_assembler import build_hero_bundle, render_preview_document


def _decision() -> HeroMediaDecision:
    return HeroMediaDecision(
        plan_kind="cinematic",
        confidence=0.91,
        explanation="Видео уместно: продукт физический, фото сильные, hero продаёт атмосферу.",
        recommended_focus="товар и атмосферу",
        recommended_tone="кинематографичный",
        brand_fit_note="Тон hero совпадает с premium-подходом бренда.",
        performance_note="Видео только на desktop, poster остаётся fallback.",
        accessibility_note="reduced-motion и mobile должны оставаться на still poster.",
        requires_confirmation=True,
        hero_headline="Свет на материале. Движение вокруг продукта.",
        hero_subheadline="Один сильный hero вместо перегруженного первого экрана.",
        primary_cta_label="Открыть коллекцию",
        visual_style="dark editorial lighting with premium product framing",
        storyboard=[
            HeroMediaShot(
                label="hero",
                purpose="показать товар крупно и дорого",
                primary_asset_index=0,
                secondary_asset_index=1,
                use_source_as_poster=True,
                still_prompt="luxury product still life",
                motion_prompt="slow orbital camera move around the product",
                first_frame_prompt=None,
                last_frame_prompt=None,
            )
        ],
    )


def test_video_bundle_renders_video_markup() -> None:
    bundle = build_hero_bundle(
        decision=_decision(),
        mode="video",
        poster_url="https://cdn.example.com/poster.webp",
        video_url="https://cdn.example.com/clip.mp4",
    )
    doc = render_preview_document(bundle)
    assert bundle.mode == "video"
    assert "<video" in bundle.html
    assert "clip.mp4" in bundle.html
    assert 'data-src="https://cdn.example.com/clip.mp4"' in bundle.html
    assert 'preload="none"' in bundle.html
    assert _decision().explanation not in bundle.html
    assert "#omnia-hero-media-root" in bundle.css
    assert "\nbody {" not in bundle.css
    assert "\nh1 {" not in bundle.css
    assert 'data-omnia-hero-media="true"' in doc
    assert 'data-plan-kind="video"' in doc


def test_motion_bundle_stays_still_first() -> None:
    decision = _decision().model_copy(update={"plan_kind": "motion"})
    bundle = build_hero_bundle(
        decision=decision,
        mode="motion",
        poster_url="https://cdn.example.com/poster.webp",
        video_url=None,
    )
    doc = render_preview_document(bundle)
    assert bundle.video_url is None
    assert "<video" not in bundle.html
    assert "omnia-hm__poster" in bundle.html
    assert 'data-media-mode="still"' in doc


def test_apply_moves_existing_hero_to_first_body_child() -> None:
    old_block = (
        "<!-- OMNIA_HERO_MEDIA_START -->old"
        "<!-- OMNIA_HERO_MEDIA_END -->"
    )
    new_block = (
        "<!-- OMNIA_HERO_MEDIA_START -->new"
        "<!-- OMNIA_HERO_MEDIA_END -->"
    )
    source = (
        '<html><body class="flex items-center">'
        f'<main class="max-w-xl">{old_block}<p>Existing content</p></main>'
        "</body></html>"
    )

    applied = _apply_hero_block(source, new_block)
    applied_twice = _apply_hero_block(applied, new_block)

    assert '<body class="flex items-center" data-omnia-hero-media="true">' in applied
    assert applied.index(new_block) < applied.index('<main class="max-w-xl">')
    assert "old" not in applied
    assert applied_twice.count("OMNIA_HERO_MEDIA_START") == 1
    assert applied_twice.index(new_block) < applied_twice.index('<main class="max-w-xl">')
