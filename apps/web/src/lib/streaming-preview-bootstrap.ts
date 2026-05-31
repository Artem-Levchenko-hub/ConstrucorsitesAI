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
  [data-omnia-new] {
    animation: omnia-in 250ms cubic-bezier(0.16, 1, 0.3, 1) both;
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
</style>
</head>
<body>
<div id="omnia-placeholder" class="omnia-placeholder">
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
          return true;
        }
      });
    } else {
      // Фолбэк, если morphdom CDN не загрузился: грубая замена.
      document.body.innerHTML = newBody.innerHTML;
    }

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
 * CSS only — NOT the kit JS: its animations split/replace DOM nodes, which
 * would fight morphdom's live patching during the stream. `.reveal` stays
 * visible (its hidden start-state is gated on a class only the kit JS adds),
 * so loading the CSS never hides streaming content. Empty `apiOrigin` → no
 * link (graceful: falls back to the plain bootstrap).
 */
export function buildBootstrap(apiOrigin: string): string {
  if (!apiOrigin) return BOOTSTRAP_HTML;
  const kitLink = `<link rel="stylesheet" href="${apiOrigin.replace(/\/$/, "")}/api/kit/omnia-kit.css">\n`;
  return BOOTSTRAP_HTML.replace(
    '<style id="omnia-css"></style>',
    kitLink + '<style id="omnia-css"></style>',
  );
}
