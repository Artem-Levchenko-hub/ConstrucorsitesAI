/**
 * Долгоживущий HTML-бутстрап для streaming-preview iframe-а.
 *
 * Идея: iframe загружается ОДИН РАЗ с этой страницей. Дальше родительский
 * React-компонент (StreamingPreviewFrame) шлёт через postMessage events вида
 * `{type: 'omnia:render', bodyHtml, cssText}` — скрипт внутри iframe-а
 * через morphdom патчит DOM in-place, без полной перезагрузки. Новым
 * top-level элементам присваивается атрибут `data-omnia-new`, под который
 * есть CSS-анимация fade+slide-up (250ms cubic-bezier).
 *
 * Зачем не srcDoc-per-update: каждое srcDoc-обновление = browser reload =
 * Tailwind CDN перезапуск + framer-motion анимации с нуля + flicker. С
 * долгоживущим iframe старые элементы остаются на месте, новые анимируются.
 *
 * morphdom грузим через jsdelivr CDN (~5KB UMD), как и Tailwind — внутри
 * sandbox с allow-scripts всё работает.
 */

export const BOOTSTRAP_HTML = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omnia preview</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/morphdom@2.7.4/dist/morphdom-umd.min.js"></script>
<style id="omnia-css"></style>
<style id="omnia-anim">
  @keyframes omnia-in {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  /* Generic birth fade for NON-kit content (raw HTML без .reveal). Kit sections
     (.reveal/.line-rise) are born via the kit's own is-visible transition driven
     below, so they're excluded here to avoid a double animation. */
  [data-omnia-new]:not(.reveal):not(.line-rise) {
    animation: omnia-in 250ms cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  /* Brand-accent birth signature: a vertical accent bar wipes down the left edge
     of each freshly-born top-level section, then fades — so the page visibly is
     born section-by-section IN THE BRAND ACCENT (pillar 3 «магия live-рендера»).
     Uses the streamed :root --accent/--primary (kit CSS provides fallbacks);
     reduced-motion drops it entirely (no position change, no bar). */
  @media (prefers-reduced-motion: no-preference) {
    [data-omnia-born] { position: relative; }
    [data-omnia-born]::after {
      content: "";
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background: var(--accent, var(--primary, #6366f1));
      transform-origin: top;
      pointer-events: none;
      z-index: 50;
      animation: omnia-born-bar 1000ms cubic-bezier(0.16, 1, 0.3, 1) both;
    }
    @keyframes omnia-born-bar {
      0%   { opacity: 0; transform: scaleY(0); }
      30%  { opacity: 1; transform: scaleY(1); }
      100% { opacity: 0; transform: scaleY(1); }
    }
  }
  html, body { background: #ffffff; color: #0a0a0a; margin: 0; }
  .omnia-shimmer {
    background: linear-gradient(90deg, #f4f4f5 0%, #e4e4e7 50%, #f4f4f5 100%);
    background-size: 200% 100%;
    animation: omnia-shimmer 1.4s ease-in-out infinite;
    border-radius: 6px;
  }
  @keyframes omnia-shimmer {
    0%   { background-position: 200% 0; }
    100% { background-position: -200% 0; }
  }
  .omnia-placeholder {
    max-width: 720px;
    margin: 0 auto;
    padding: 48px 24px;
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
  }
  /* Anticipation accent: a slow "breathing" sheen line above the label so the
     pre-first-token wait reads as intentional build-up, not dead grey. Neutral
     graphite/slate (no hue) so it never clashes with the brand the page will
     adopt; the life comes from motion, not colour. */
  .omnia-breathe-bar {
    height: 3px;
    max-width: 220px;
    margin: 0 auto 22px;
    border-radius: 999px;
    background: linear-gradient(90deg, transparent 0%, #cbd5e1 22%, #475569 50%, #cbd5e1 78%, transparent 100%);
    background-size: 220% 100%;
    animation: omnia-sheen 2.2s ease-in-out infinite, omnia-breathe 2.6s ease-in-out infinite;
  }
  @keyframes omnia-sheen {
    0%   { background-position: 220% 0; }
    100% { background-position: -220% 0; }
  }
  @keyframes omnia-breathe {
    0%, 100% { opacity: 0.5; }
    50%      { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    .omnia-breathe-bar { animation: none; opacity: 0.7; }
  }
  .omnia-placeholder .label {
    font-size: 13px;
    color: #71717a;
    text-align: center;
    margin-bottom: 8px;
    letter-spacing: 0.02em;
  }
  .omnia-placeholder .hint {
    font-size: 11px;
    color: #a1a1aa;
    text-align: center;
    margin-bottom: 24px;
    letter-spacing: 0.01em;
  }
  .omnia-placeholder .dots::after {
    display: inline-block;
    content: "";
    animation: omnia-dots 1.4s steps(4, end) infinite;
    width: 1.2em;
    text-align: left;
  }
  @keyframes omnia-dots {
    0%   { content: ""; }
    25%  { content: "."; }
    50%  { content: ".."; }
    75%  { content: "..."; }
    100% { content: ""; }
  }
  /* Empty image frame — shimmer until the photo drops in. !important beats the
     inline gradient the generator leaves on data-omnia-gen imgs; once the src
     is set the :not([src]) selector stops matching and the real image shows. */
  img[data-omnia-gen]:not([src]),
  img[data-omnia-gen][src=""] {
    background-image: linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.09) 50%, rgba(0,0,0,0.04) 100%) !important;
    background-size: 200% 100% !important;
    background-repeat: no-repeat !important;
    animation: omnia-shimmer 1.4s ease-in-out infinite;
    border-radius: 10px;
  }
  /* Photo settling into its frame: blur+scale resolving to sharp. */
  @keyframes omnia-img-in {
    from { opacity: 0; filter: blur(14px); transform: scale(1.05); }
    to   { opacity: 1; filter: blur(0);    transform: scale(1); }
  }
  img[data-omnia-img-in] {
    animation: omnia-img-in 600ms cubic-bezier(0.16, 1, 0.3, 1) both;
  }
</style>
</head>
<body>
<div id="omnia-placeholder" class="omnia-placeholder">
  <div class="omnia-breathe-bar"></div>
  <div class="label"><span id="omnia-status">AI пишет ответ</span><span class="dots"></span></div>
  <div class="hint">Обычно 5–15 секунд. Если ответ пустой — переключусь на запасную модель автоматически.</div>
  <div class="omnia-shimmer" style="height: 52px; margin-bottom: 16px;"></div>
  <div class="omnia-shimmer" style="height: 16px; width: 80%; margin-bottom: 10px;"></div>
  <div class="omnia-shimmer" style="height: 16px; width: 65%; margin-bottom: 28px;"></div>
  <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px;">
    <div class="omnia-shimmer" style="height: 120px;"></div>
    <div class="omnia-shimmer" style="height: 120px;"></div>
    <div class="omnia-shimmer" style="height: 120px;"></div>
  </div>
</div>
<script>
(function () {
  var cssEl = document.getElementById('omnia-css');
  var placeholderRemoved = false;
  // Live section-birth (V3 #2a): the kit ships a .reveal/.is-visible transition
  // (gated on html.omnia-anim) that normally fires on scroll of the FINISHED
  // page. During the stream we drive it ourselves — each section reveals with
  // the EXACT kit cadence (+ its data-reveal-delay stagger) AS it streams in, so
  // what you watch being born is pixel-identical to the committed /p/<slug>.
  // omniaAnimOn flips html.omnia-anim once (activates the hidden start-state);
  // bornSet tracks nodes already revealed (WeakSet → survives morphdom in-place
  // patches, which otherwise strip the is-visible class to match streamed HTML).
  var omniaAnimOn = false;
  var bornSet = (typeof WeakSet === 'function') ? new WeakSet() : null;
  var rafFn = window.requestAnimationFrame
    ? window.requestAnimationFrame.bind(window)
    : function (cb) { return setTimeout(cb, 16); };

  function revealSections() {
    var htmlEl = document.documentElement;
    var nodes = [].slice.call(document.querySelectorAll('.reveal, .line-rise'));
    var fresh = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var seen = bornSet ? bornSet.has(el) : el.__omniaBorn === true;
      if (seen) {
        // Already born — re-assert is-visible every render: morphdom strips it
        // when it patches the section against the (class-less) streamed node.
        el.classList.add('is-visible');
      } else {
        el.classList.remove('is-visible');
        fresh.push(el);
      }
    }
    // Turn on the kit hidden start-state only AFTER already-born nodes were
    // re-marked visible above → flipping omnia-anim never retroactively hides a
    // section that's already on screen.
    if (!omniaAnimOn) { htmlEl.classList.add('omnia-anim'); omniaAnimOn = true; }
    if (!fresh.length) return;
    // Brand-accent birth sweep only on freshly-born TOP-LEVEL sections.
    for (var t = 0; t < fresh.length; t++) {
      if (fresh[t].parentNode === document.body) {
        fresh[t].setAttribute('data-omnia-born', '');
      }
    }
    // Reveal on the next frame so the hidden start-state paints first and the
    // transition actually plays (rather than snapping straight to visible).
    rafFn(function () {
      rafFn(function () {
        for (var j = 0; j < fresh.length; j++) {
          var node = fresh[j];
          if (bornSet) bornSet.add(node); else node.__omniaBorn = true;
          node.classList.add('is-visible');
        }
      });
    });
  }
  // idx -> resolved url. Survives morphdom patches: the streamed content keeps
  // the src-less data-omnia-gen placeholders, so we re-apply after each render.
  window.__omniaImages = window.__omniaImages || {};

  function applyImage(idx, url, animate) {
    var imgs = document.querySelectorAll('img[data-omnia-gen]');
    var el = imgs[idx];
    if (!el || !url) return;
    if (el.getAttribute('src') === url) return;
    el.setAttribute('src', url);
    if (animate) {
      el.setAttribute('data-omnia-img-in', '');
      setTimeout(function () { el.removeAttribute('data-omnia-img-in'); }, 700);
    }
  }

  function reapplyImages() {
    var map = window.__omniaImages || {};
    for (var k in map) {
      if (Object.prototype.hasOwnProperty.call(map, k)) {
        applyImage(parseInt(k, 10), map[k], false);
      }
    }
  }

  function render(bodyHtml, cssText) {
    if (cssEl && cssText != null && cssEl.textContent !== cssText) {
      cssEl.textContent = cssText;
    }

    if (typeof bodyHtml !== 'string' || !bodyHtml.length) return;

    // Парсим как полный документ — DOMParser толерантен к незакрытым тегам.
    var doc = new DOMParser().parseFromString(
      '<!doctype html><html><body>' + bodyHtml + '</body></html>',
      'text/html'
    );
    var newBody = doc.body;

    // Помечаем top-level дочерние элементы как новые для анимации, если их
    // ещё нет в текущем DOM (определяем по позиции + tagName).
    var existing = document.body.children;
    var existingSigs = {};
    for (var i = 0; i < existing.length; i++) {
      var el = existing[i];
      if (el.id === 'omnia-placeholder') continue;
      existingSigs[el.tagName + '#' + (el.id || '') + '.' + el.className] = true;
    }
    var newKids = newBody.children;
    for (var j = 0; j < newKids.length; j++) {
      var k = newKids[j];
      var sig = k.tagName + '#' + (k.id || '') + '.' + k.className;
      if (!existingSigs[sig]) {
        k.setAttribute('data-omnia-new', '');
      }
    }

    if (!placeholderRemoved) {
      var ph = document.getElementById('omnia-placeholder');
      if (ph) ph.remove();
      placeholderRemoved = true;
    }

    if (typeof window.morphdom === 'function') {
      window.morphdom(document.body, newBody, {
        childrenOnly: true,
        onBeforeElUpdated: function (fromEl, toEl) {
          // Не дёргаем idle узлы — экономим перерасчёт стилей и анимации.
          if (fromEl.isEqualNode(toEl)) return false;
          // Preserve a live-swapped image: the streamed content still carries
          // the src-less data-omnia-gen placeholder, so without this morphdom
          // would strip the src we set and the photo would flicker out.
          if (fromEl.tagName === 'IMG' &&
              fromEl.hasAttribute('data-omnia-gen') &&
              fromEl.getAttribute('src')) {
            return false;
          }
          return true;
        }
      });
    } else {
      // Фолбэк, если morphdom CDN не загрузился: грубая замена.
      document.body.innerHTML = newBody.innerHTML;
    }

    // Restore any images that resolved already (a fresh morph re-inserts the
    // src-less placeholder for not-yet-preserved nodes).
    reapplyImages();

    // Drive the kit reveal cascade for freshly-streamed sections (V3 #2a). Runs
    // after morphdom so new nodes exist; re-asserts is-visible on already-born
    // nodes that morphdom just patched. The kit's own IntersectionObserver never
    // runs here (kit JS isn't loaded), so this is the sole reveal driver.
    revealSections();

    // Анимация триггерится самим css-keyframe-ом на data-omnia-new;
    // через 400ms (250ms анимация + запас) снимаем атрибут, чтобы при
    // следующем patch старые элементы не считались «новыми».
    setTimeout(function () {
      var marked = document.querySelectorAll('[data-omnia-new]');
      for (var m = 0; m < marked.length; m++) marked[m].removeAttribute('data-omnia-new');
    }, 400);
  }

  function updateStatus(text) {
    var el = document.getElementById('omnia-status');
    if (el) el.textContent = text;
  }

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data) return;
    // Status pings let React tell the placeholder what's going on without
    // tearing it down: e.g. "переключаюсь на Claude Haiku…" when the primary
    // model returns junk, or "AI пишет ответ" baseline.
    if (data.type === 'omnia:status') {
      try { updateStatus(String(data.text || '')); } catch (_) {}
      return;
    }
    // A generated image resolved → drop it into its frame (animated).
    if (data.type === 'omnia:image') {
      try {
        var i = (data.idx | 0);
        window.__omniaImages[i] = data.url;
        applyImage(i, data.url, true);
      } catch (_) {}
      return;
    }
    // New generation → forget the previous build's images so their urls can't
    // bleed into the new frames at the same indices.
    if (data.type === 'omnia:images-reset') {
      window.__omniaImages = {};
      return;
    }
    // V3.10a — the art-director brief arrived (palette/fonts/motion/sections).
    // Stash it for the live narration layer (V3.10) to read; transport-only
    // here, no visible rendering yet.
    if (data.type === 'omnia:brief') {
      try { window.__omniaBrief = data.brief || null; } catch (_) {}
      return;
    }
    if (data.type !== 'omnia:render') return;
    try {
      render(data.bodyHtml, data.cssText);
    } catch (err) {
      // Прячем от пользователя, но оставляем в консоли iframe-а для отладки.
      console.error('[omnia preview] render failed:', err);
    }
  });

  // Сигналим родителю что bootstrap готов принимать render-сообщения.
  window.parent && window.parent.postMessage({ type: 'omnia:ready' }, '*');
})();
</script>
</body>
</html>`;

/**
 * Bootstrap HTML with the canonical omnia-kit.css linked from the API origin
 * (`/api/kit/omnia-kit.css`). This gives the streaming preview the SAME styling
 * the committed `/p/<slug>` page uses (cards, gradients, .display-fill, etc.)
 * instead of unstyled kit classes that "snap" correct only after generation.
 *
 * CSS only — NOT the kit JS: its animations split/replace DOM nodes and its
 * IntersectionObserver assumes a finished, scrollable page, both of which would
 * fight morphdom's live patching during the stream. The reveal cascade is driven
 * instead by the bootstrap itself (revealSections): it flips `html.omnia-anim`
 * to arm the kit's `.reveal` hidden start-state, then adds `.is-visible`
 * per-section as it streams in — the same transition the committed /p/<slug>
 * plays on scroll. Empty `apiOrigin` → no link (graceful: falls back to the
 * plain bootstrap; without the kit CSS the start-state never hides, so content
 * still shows).
 */
export function buildBootstrap(apiOrigin: string): string {
  if (!apiOrigin) return BOOTSTRAP_HTML;
  const kitLink = `<link rel="stylesheet" href="${apiOrigin.replace(/\/$/, "")}/api/kit/omnia-kit.css">\n`;
  return BOOTSTRAP_HTML.replace(
    '<style id="omnia-css"></style>',
    kitLink + '<style id="omnia-css"></style>',
  );
}
