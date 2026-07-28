from __future__ import annotations

from html import escape

from omnia_api.schemas.hero_media import HeroMediaBundlePublic, HeroMediaDecision, HeroMediaPlanKind


def build_hero_bundle(
    *,
    decision: HeroMediaDecision,
    mode: HeroMediaPlanKind,
    poster_url: str,
    video_url: str | None,
) -> HeroMediaBundlePublic:
    badge = escape(mode.replace("-", " ").upper())
    headline = escape(decision.hero_headline)
    subheadline = escape(decision.hero_subheadline)
    cta = escape(decision.primary_cta_label)
    explanation = escape(decision.explanation)
    visual = escape(decision.visual_style)
    poster = escape(poster_url, quote=True)
    video = escape(video_url, quote=True) if video_url else None

    media_block = _media_block(mode=mode, poster_url=poster, video_url=video, visual=visual)
    html = f"""
<section class="hero-media-shell" data-hero-shell data-plan-kind="{mode}">
  <div class="hero-media-grid hero-media-mode-{mode}">
    <div class="hero-copy">
      <div class="hero-badge">{badge}</div>
      <h1>{headline}</h1>
      <p class="hero-subheadline">{subheadline}</p>
      <div class="hero-actions">
        <a href="#contact" class="hero-cta">{cta}</a>
        <span class="hero-note">{explanation}</span>
      </div>
    </div>
    {media_block}
  </div>
</section>
""".strip()

    return HeroMediaBundlePublic(
        mode=mode,
        poster_url=poster_url,
        video_url=video_url,
        headline=decision.hero_headline,
        subheadline=decision.hero_subheadline,
        primary_cta_label=decision.primary_cta_label,
        explanation=decision.explanation,
        html=html,
        css=_CSS,
        js=_JS,
    )


def render_preview_document(bundle: HeroMediaBundlePublic) -> str:
    title = escape(bundle.headline)
    return f"""<!DOCTYPE html>
<html lang="ru" data-plan-kind="{bundle.mode}" data-media-mode="still">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>{bundle.css}</style>
  </head>
  <body>
    {bundle.html}
    <script>{bundle.js}</script>
  </body>
</html>"""


def _media_block(
    *,
    mode: HeroMediaPlanKind,
    poster_url: str,
    video_url: str | None,
    visual: str,
) -> str:
    visual_attr = escape(visual)
    if mode in {"video", "cinematic"} and video_url:
        return f"""
<div class="hero-visual hero-video-scene" data-hero-layer data-visual-style="{visual_attr}">
  <img class="hero-poster" src="{poster_url}" alt="" />
  <video
    class="hero-video"
    data-hero-video
    poster="{poster_url}"
    src="{video_url}"
    muted
    loop
    playsinline
    preload="metadata"
    aria-hidden="true"
  ></video>
  <div class="hero-overlay"></div>
</div>
""".strip()
    frame_class = "hero-demo-frame" if mode == "product-demo" else "hero-still-scene"
    return f"""
<div class="hero-visual {frame_class}" data-hero-layer data-visual-style="{visual_attr}">
  <img class="hero-poster hero-poster-static" src="{poster_url}" alt="" />
  <div class="hero-overlay"></div>
</div>
""".strip()


_CSS = """
:root {
  color-scheme: dark;
  --bg: #09090d;
  --panel: rgba(18, 18, 25, 0.78);
  --stroke: rgba(255, 255, 255, 0.12);
  --text: #f6f3ef;
  --muted: rgba(246, 243, 239, 0.7);
  --accent: #7c68f2;
  --accent-soft: rgba(124, 104, 242, 0.18);
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background:
  radial-gradient(circle at top, rgba(124, 104, 242, 0.2), transparent 38%),
  linear-gradient(180deg, #101019 0%, var(--bg) 100%);
  color: var(--text); font-family: Inter, system-ui, sans-serif; }
body { display: grid; place-items: center; padding: 24px; overflow-x: hidden; }
.hero-media-shell { width: min(1180px, 100%); }
.hero-media-grid {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 560px);
  gap: 28px;
  align-items: center;
  min-height: min(78vh, 860px);
}
.hero-copy {
  position: relative;
  z-index: 2;
  padding: 40px 0;
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-radius: 999px;
  border: 1px solid var(--stroke);
  background: rgba(255, 255, 255, 0.03);
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
h1 {
  margin: 22px 0 16px;
  font-size: clamp(2.8rem, 6vw, 5.6rem);
  line-height: 0.96;
  letter-spacing: -0.045em;
}
.hero-subheadline {
  max-width: 40rem;
  margin: 0;
  font-size: clamp(1.02rem, 1.6vw, 1.22rem);
  line-height: 1.65;
  color: var(--muted);
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  margin-top: 28px;
}
.hero-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  padding: 0 24px;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  text-decoration: none;
  font-weight: 600;
}
.hero-note {
  max-width: 28rem;
  color: var(--muted);
  font-size: 0.95rem;
  line-height: 1.5;
}
.hero-visual {
  position: relative;
  overflow: hidden;
  border-radius: 32px;
  border: 1px solid var(--stroke);
  background: linear-gradient(180deg, rgba(24, 24, 34, 0.84), rgba(8, 8, 12, 0.96));
  box-shadow: 0 30px 100px rgba(0, 0, 0, 0.35);
  min-height: 560px;
}
.hero-video-scene,
.hero-still-scene { isolation: isolate; }
.hero-demo-frame { padding: 18px; }
.hero-demo-frame::before {
  content: "";
  position: absolute;
  inset: 18px;
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(124,104,242,0.08));
}
.hero-poster,
.hero-video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.hero-poster-static {
  transform: scale(1.02);
  transition: transform 480ms ease;
}
.hero-overlay {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(7, 7, 11, 0.12), rgba(7, 7, 11, 0.62)),
    radial-gradient(circle at 78% 22%, rgba(124, 104, 242, 0.28), transparent 34%);
  pointer-events: none;
}
html[data-media-mode="video"] .hero-poster { opacity: 0; }
html[data-media-mode="still"] .hero-video { display: none; }
html[data-media-mode="still"] .hero-poster { opacity: 1; }
html[data-plan-kind="motion"] .hero-poster-static,
html[data-plan-kind="cinematic"] .hero-poster-static {
  will-change: transform;
}
@media (max-width: 960px) {
  .hero-media-grid { grid-template-columns: 1fr; }
  .hero-copy { padding-bottom: 0; }
  .hero-visual { min-height: 420px; }
}
@media (max-width: 640px) {
  body { padding: 16px; }
  .hero-media-grid { gap: 20px; min-height: auto; }
  .hero-copy { padding: 12px 0 0; }
  .hero-visual { min-height: 340px; border-radius: 24px; }
  .hero-actions { flex-direction: column; align-items: flex-start; }
  .hero-note { max-width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; animation: none !important; transition: none !important; }
}
""".strip()

_JS = """
(() => {
  const root = document.documentElement;
  const prefersReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobile = window.matchMedia("(max-width: 767px)");
  const video = document.querySelector("[data-hero-video]");
  const layer = document.querySelector("[data-hero-layer]");
  const poster = document.querySelector(".hero-poster-static");
  function syncMode() {
    const plan = root.dataset.planKind || "static";
    const wantsVideo = plan === "video" || plan === "cinematic";
    const allowVideo = wantsVideo && !prefersReduce.matches && !mobile.matches && !!video;
    root.dataset.mediaMode = allowVideo ? "video" : "still";
    if (video) {
      if (allowVideo) {
        video.play().catch(() => {});
      } else {
        video.pause();
      }
    }
  }
  syncMode();
  if (prefersReduce.addEventListener) prefersReduce.addEventListener("change", syncMode);
  if (mobile.addEventListener) mobile.addEventListener("change", syncMode);
  if (layer && poster && !prefersReduce.matches) {
    window.addEventListener("pointermove", (event) => {
      const x = (event.clientX / window.innerWidth - 0.5) * 12;
      const y = (event.clientY / window.innerHeight - 0.5) * 10;
      poster.style.transform = `scale(1.03) translate3d(${x}px, ${y}px, 0)`;
    }, { passive: true });
  }
})();
""".strip()
