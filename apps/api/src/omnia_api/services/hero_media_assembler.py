from __future__ import annotations

from html import escape

from omnia_api.schemas.hero_media import HeroMediaBundlePublic, HeroMediaDecision, HeroMediaPlanKind

_MODE_LABELS: dict[HeroMediaPlanKind, str] = {
    "static": "Сильный первый кадр",
    "product-demo": "Продукт в действии",
    "motion": "Тонкая кинетика",
    "video": "Видео-подача",
    "cinematic": "Кинематографичная сцена",
}


def build_hero_bundle(
    *,
    decision: HeroMediaDecision,
    mode: HeroMediaPlanKind,
    poster_url: str,
    video_url: str | None,
) -> HeroMediaBundlePublic:
    badge = escape(_MODE_LABELS[mode])
    headline = escape(decision.hero_headline)
    subheadline = escape(decision.hero_subheadline)
    cta = escape(decision.primary_cta_label)
    visual = escape(decision.visual_style)
    poster = escape(poster_url, quote=True)
    video = escape(video_url, quote=True) if video_url else None

    media_block = _media_block(mode=mode, poster_url=poster, video_url=video, visual=visual)
    html = f"""
<section
  id="omnia-hero-media-root"
  class="omnia-hm omnia-hm--{mode}"
  data-hero-shell
  data-plan-kind="{mode}"
  data-media-mode="still"
>
  <div class="omnia-hm__stage">
    {media_block}
    <div class="omnia-hm__veil" aria-hidden="true"></div>
    <div class="omnia-hm__grain" aria-hidden="true"></div>
    <div class="omnia-hm__content">
      <div class="omnia-hm__copy">
        <div class="omnia-hm__eyebrow">
          <span class="omnia-hm__eyebrow-mark" aria-hidden="true"></span>
          {badge}
        </div>
        <h1 class="omnia-hm__headline">{headline}</h1>
        <p class="omnia-hm__subheadline">{subheadline}</p>
        <div class="omnia-hm__actions">
          <a href="#omnia-hero-media-next" class="omnia-hm__cta">
            <span>{cta}</span>
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              width="20"
              height="20"
              fill="none"
            >
              <path d="M5 10h10M11 6l4 4-4 4" stroke="currentColor" stroke-width="1.5" />
            </svg>
          </a>
        </div>
      </div>
    </div>
    <div class="omnia-hm__scroll-cue" aria-hidden="true">
      <span>Листайте</span>
      <i></i>
    </div>
  </div>
</section>
<span id="omnia-hero-media-next" class="omnia-hm__next-anchor" aria-hidden="true"></span>
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
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>{bundle.css}</style>
  </head>
  <body data-omnia-hero-media="true">
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
<div class="omnia-hm__media" data-hero-layer data-visual-style="{visual_attr}">
  <img
    class="omnia-hm__poster"
    src="{poster_url}"
    alt=""
    fetchpriority="high"
    decoding="async"
  />
  <video
    class="omnia-hm__video"
    data-hero-video
    data-src="{video_url}"
    poster="{poster_url}"
    muted
    loop
    playsinline
    preload="none"
    aria-hidden="true"
    tabindex="-1"
  ></video>
</div>
""".strip()
    return f"""
<div class="omnia-hm__media" data-hero-layer data-visual-style="{visual_attr}">
  <img
    class="omnia-hm__poster"
    src="{poster_url}"
    alt=""
    fetchpriority="high"
    decoding="async"
  />
</div>
""".strip()


_CSS = """
body[data-omnia-hero-media="true"] {
  display: block !important;
  min-height: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow-x: hidden !important;
}
body[data-omnia-hero-media="true"] > main {
  margin-inline: auto;
}
#omnia-hero-media-root,
#omnia-hero-media-root * {
  box-sizing: border-box;
}
#omnia-hero-media-root {
  --omnia-hm-ink: #f7f0e6;
  --omnia-hm-muted: rgba(247, 240, 230, 0.78);
  --omnia-hm-line: rgba(247, 240, 230, 0.28);
  --omnia-hm-paper: #eee2d2;
  --omnia-hm-dark: #15130f;
  --omnia-hm-pointer-x: 0px;
  --omnia-hm-pointer-y: 0px;
  --omnia-hm-scroll-y: 0px;
  --omnia-hm-scale: 1.035;
  position: relative;
  isolation: isolate;
  width: 100%;
  max-width: none;
  min-height: 100svh;
  overflow: clip;
  background: #17140f;
  color: var(--omnia-hm-ink);
  font-family: "DM Sans", "Avenir Next", "Segoe UI", sans-serif;
  text-align: left;
}
#omnia-hero-media-root .omnia-hm__stage {
  position: relative;
  min-height: 100svh;
  overflow: hidden;
}
#omnia-hero-media-root .omnia-hm__media {
  position: absolute;
  z-index: 1;
  inset: -3%;
  overflow: hidden;
  background: #201b15;
  transform:
    translate3d(
      var(--omnia-hm-pointer-x),
      calc(var(--omnia-hm-pointer-y) + var(--omnia-hm-scroll-y)),
      0
    )
    scale(var(--omnia-hm-scale));
  transform-origin: center;
  transition: transform 700ms cubic-bezier(0.22, 1, 0.36, 1);
  will-change: transform;
}
#omnia-hero-media-root .omnia-hm__poster,
#omnia-hero-media-root .omnia-hm__video {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 64% center;
}
#omnia-hero-media-root .omnia-hm__poster {
  opacity: 1;
  transition: opacity 900ms ease;
}
#omnia-hero-media-root .omnia-hm__video {
  opacity: 0;
  transition: opacity 900ms ease;
}
#omnia-hero-media-root[data-media-mode="video"] .omnia-hm__poster {
  opacity: 0;
}
#omnia-hero-media-root[data-media-mode="video"] .omnia-hm__video {
  opacity: 1;
}
#omnia-hero-media-root .omnia-hm__veil {
  position: absolute;
  z-index: 2;
  inset: 0;
  background:
    linear-gradient(
      90deg,
      rgba(14, 13, 11, 0.9) 0%,
      rgba(14, 13, 11, 0.64) 38%,
      rgba(14, 13, 11, 0.12) 72%
    ),
    linear-gradient(
      180deg,
      rgba(8, 8, 7, 0.2) 0%,
      rgba(8, 8, 7, 0.06) 48%,
      rgba(8, 8, 7, 0.62) 100%
    );
  pointer-events: none;
}
#omnia-hero-media-root .omnia-hm__grain {
  position: absolute;
  z-index: 3;
  inset: 0;
  opacity: 0.12;
  pointer-events: none;
  background-image:
    repeating-radial-gradient(
      circle at 17% 23%,
      rgba(255,255,255,0.32) 0 0.45px,
      transparent 0.6px 3px
    );
  background-size: 7px 7px;
  mix-blend-mode: soft-light;
}
#omnia-hero-media-root .omnia-hm__content {
  position: relative;
  z-index: 4;
  display: flex;
  align-items: flex-end;
  width: min(100%, 1600px);
  min-height: 100svh;
  margin: 0 auto;
  padding: clamp(28px, 5.6vw, 96px);
}
#omnia-hero-media-root .omnia-hm__copy {
  width: min(800px, 66vw);
  padding: clamp(120px, 18vh, 210px) 0 clamp(72px, 11vh, 120px);
}
#omnia-hero-media-root .omnia-hm__eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  margin-bottom: clamp(24px, 4vh, 48px);
  color: rgba(247, 240, 230, 0.92);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}
#omnia-hero-media-root .omnia-hm__eyebrow-mark {
  width: 28px;
  height: 1px;
  background: currentColor;
}
#omnia-hero-media-root .omnia-hm__headline {
  max-width: 11.5ch;
  margin: 0;
  color: var(--omnia-hm-ink);
  font-family: "Space Grotesk", "Arial Narrow", "Arial", sans-serif;
  font-size: clamp(3.9rem, 7.4vw, 8.4rem);
  font-weight: 600;
  line-height: 0.88;
  letter-spacing: -0.064em;
  text-wrap: balance;
  text-shadow: 0 2px 28px rgba(0, 0, 0, 0.2);
}
#omnia-hero-media-root .omnia-hm__subheadline {
  max-width: 42rem;
  margin: clamp(28px, 4vh, 44px) 0 0;
  color: var(--omnia-hm-muted);
  font-size: clamp(1.05rem, 1.35vw, 1.28rem);
  font-weight: 400;
  line-height: 1.55;
  letter-spacing: -0.012em;
}
#omnia-hero-media-root .omnia-hm__actions {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: clamp(30px, 4.5vh, 52px);
}
#omnia-hero-media-root .omnia-hm__cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 34px;
  min-height: 56px;
  padding: 0 18px 0 24px;
  border: 1px solid rgba(255, 255, 255, 0.45);
  border-radius: 999px;
  background: var(--omnia-hm-paper);
  box-shadow: 0 14px 45px rgba(0, 0, 0, 0.18);
  color: var(--omnia-hm-dark);
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
  text-decoration: none;
  transition:
    transform 240ms ease,
    background-color 240ms ease,
    box-shadow 240ms ease;
}
#omnia-hero-media-root .omnia-hm__cta:hover {
  transform: translateY(-3px);
  background: #fffaf3;
  box-shadow: 0 20px 56px rgba(0, 0, 0, 0.26);
}
#omnia-hero-media-root .omnia-hm__cta:active {
  transform: translateY(0) scale(0.98);
}
#omnia-hero-media-root .omnia-hm__cta:focus-visible {
  outline: 3px solid #fffaf3;
  outline-offset: 4px;
}
#omnia-hero-media-root .omnia-hm__cta svg {
  transition: transform 240ms ease;
}
#omnia-hero-media-root .omnia-hm__cta:hover svg {
  transform: translateX(3px);
}
#omnia-hero-media-root .omnia-hm__scroll-cue {
  position: absolute;
  z-index: 4;
  right: clamp(28px, 5.6vw, 96px);
  bottom: clamp(34px, 6vh, 72px);
  display: flex;
  align-items: center;
  gap: 14px;
  color: rgba(247, 240, 230, 0.72);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.2em;
  text-transform: uppercase;
}
#omnia-hero-media-root .omnia-hm__scroll-cue i {
  position: relative;
  display: block;
  width: 42px;
  height: 1px;
  overflow: hidden;
  background: rgba(247, 240, 230, 0.35);
}
#omnia-hero-media-root .omnia-hm__scroll-cue i::after {
  content: "";
  position: absolute;
  inset: 0;
  background: var(--omnia-hm-ink);
  transform: translateX(-100%);
  animation: omnia-hm-cue 2.2s ease-in-out infinite;
}
#omnia-hero-media-root .omnia-hm__next-anchor {
  display: block;
  width: 1px;
  height: 1px;
  scroll-margin-top: 16px;
}
#omnia-hero-media-root.omnia-hm--product-demo {
  background:
    radial-gradient(circle at 72% 38%, rgba(196, 164, 113, 0.12), transparent 34%),
    #11110f;
}
#omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__media {
  z-index: 1;
  inset: 10% 5% 10% 48%;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: clamp(24px, 3vw, 44px);
  background: #1b1b18;
  box-shadow: 0 34px 100px rgba(0, 0, 0, 0.42);
}
#omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__poster {
  object-fit: contain;
  object-position: center;
}
#omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__veil {
  background:
    linear-gradient(
      90deg,
      rgba(17, 17, 15, 0.98) 0%,
      rgba(17, 17, 15, 0.82) 43%,
      rgba(17, 17, 15, 0.1) 65%
    );
}
@keyframes omnia-hm-cue {
  0%, 18% { transform: translateX(-100%); }
  70%, 100% { transform: translateX(100%); }
}
@media (max-width: 900px) {
  #omnia-hero-media-root .omnia-hm__copy {
    width: min(760px, 82vw);
  }
  #omnia-hero-media-root .omnia-hm__headline {
    font-size: clamp(3.4rem, 10vw, 6.6rem);
  }
  #omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__media {
    inset: 42% 5% 7%;
  }
}
@media (max-width: 700px) {
  #omnia-hero-media-root,
  #omnia-hero-media-root .omnia-hm__stage,
  #omnia-hero-media-root .omnia-hm__content {
    min-height: max(700px, 100svh);
  }
  #omnia-hero-media-root .omnia-hm__content {
    align-items: flex-end;
    padding: 24px;
  }
  #omnia-hero-media-root .omnia-hm__copy {
    width: 100%;
    padding: 112px 0 48px;
  }
  #omnia-hero-media-root .omnia-hm__eyebrow {
    margin-bottom: 24px;
    font-size: 10px;
  }
  #omnia-hero-media-root .omnia-hm__headline {
    max-width: 10ch;
    font-size: clamp(3rem, 13.4vw, 4.35rem);
    line-height: 0.91;
    letter-spacing: -0.058em;
  }
  #omnia-hero-media-root .omnia-hm__subheadline {
    max-width: 34rem;
    margin-top: 24px;
    font-size: 1rem;
    line-height: 1.5;
  }
  #omnia-hero-media-root .omnia-hm__actions {
    margin-top: 30px;
  }
  #omnia-hero-media-root .omnia-hm__cta {
    min-height: 54px;
  }
  #omnia-hero-media-root .omnia-hm__scroll-cue {
    display: none;
  }
  #omnia-hero-media-root .omnia-hm__poster,
  #omnia-hero-media-root .omnia-hm__video {
    object-position: 68% center;
  }
  #omnia-hero-media-root .omnia-hm__veil {
    background:
      linear-gradient(
        180deg,
        rgba(12, 11, 9, 0.12) 0%,
        rgba(12, 11, 9, 0.2) 32%,
        rgba(12, 11, 9, 0.94) 82%,
        rgba(12, 11, 9, 0.98) 100%
      ),
      linear-gradient(
        90deg,
        rgba(12, 11, 9, 0.25),
        rgba(12, 11, 9, 0.03)
      );
  }
  #omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__media {
    inset: 7% 5% 38%;
    border-radius: 24px;
  }
  #omnia-hero-media-root.omnia-hm--product-demo .omnia-hm__copy {
    padding-top: 54vh;
  }
}
@media (prefers-reduced-motion: reduce) {
  #omnia-hero-media-root *,
  #omnia-hero-media-root *::before,
  #omnia-hero-media-root *::after {
    scroll-behavior: auto !important;
    animation: none !important;
    transition: none !important;
  }
  #omnia-hero-media-root .omnia-hm__media {
    transform: none !important;
  }
}
""".strip()

_JS = """
(() => {
  const root = document.querySelector("#omnia-hero-media-root");
  if (!root || root.dataset.bound === "true") return;
  root.dataset.bound = "true";

  const prefersReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobile = window.matchMedia("(max-width: 767px)");
  const video = root.querySelector("[data-hero-video]");
  const plan = root.dataset.planKind || "static";
  let pointerFrame = 0;
  let scrollFrame = 0;

  function syncMode() {
    const wantsVideo = plan === "video" || plan === "cinematic";
    const allowVideo = wantsVideo && !prefersReduce.matches && !mobile.matches && !!video;
    const mode = allowVideo ? "video" : "still";
    root.dataset.mediaMode = mode;
    document.documentElement.dataset.mediaMode = mode;
    document.documentElement.dataset.planKind = plan;

    if (!video) return;
    if (allowVideo) {
      if (!video.getAttribute("src") && video.dataset.src) {
        video.setAttribute("src", video.dataset.src);
        video.load();
      }
      video.play().catch(() => {});
      return;
    }
    video.pause();
    if (video.getAttribute("src")) {
      video.removeAttribute("src");
      video.load();
    }
  }

  function commitPointer(x, y) {
    root.style.setProperty("--omnia-hm-pointer-x", `${x.toFixed(2)}px`);
    root.style.setProperty("--omnia-hm-pointer-y", `${y.toFixed(2)}px`);
  }

  root.addEventListener("pointermove", (event) => {
    if (prefersReduce.matches || mobile.matches) return;
    if (pointerFrame) cancelAnimationFrame(pointerFrame);
    pointerFrame = requestAnimationFrame(() => {
      const rect = root.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width - 0.5) * -18;
      const y = ((event.clientY - rect.top) / rect.height - 0.5) * -12;
      commitPointer(x, y);
    });
  }, { passive: true });

  root.addEventListener("pointerleave", () => {
    if (prefersReduce.matches) return;
    if (pointerFrame) cancelAnimationFrame(pointerFrame);
    pointerFrame = requestAnimationFrame(() => commitPointer(0, 0));
  }, { passive: true });

  function syncScroll() {
    scrollFrame = 0;
    if (prefersReduce.matches) {
      root.style.setProperty("--omnia-hm-scroll-y", "0px");
      root.style.setProperty("--omnia-hm-scale", "1");
      return;
    }
    const rect = root.getBoundingClientRect();
    const progress = Math.max(0, Math.min(1, -rect.top / Math.max(rect.height, 1)));
    root.style.setProperty("--omnia-hm-scroll-y", `${(progress * 34).toFixed(2)}px`);
    root.style.setProperty("--omnia-hm-scale", (1.035 + progress * 0.045).toFixed(4));
  }

  window.addEventListener("scroll", () => {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(syncScroll);
  }, { passive: true });

  syncMode();
  syncScroll();
  if (prefersReduce.addEventListener) {
    prefersReduce.addEventListener("change", () => {
      syncMode();
      syncScroll();
    });
  }
  if (mobile.addEventListener) mobile.addEventListener("change", syncMode);
})();
""".strip()
