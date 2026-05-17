---
title: "Real-time streaming preview через morphdom + postMessage"
aliases: [streaming-preview, morphdom-preview, lovable-class-preview]
tags: [frontend, workspace, preview, iframe, morphdom, animation]
sources:
  - "daily/2026-05-17.md"
created: 2026-05-17
updated: 2026-05-17
---

# Real-time streaming preview (Lovable/Bolt-class)

Live-обновление preview iframe по мере прихода `<file>` блоков от LLM. Архитектура: ОДИН долгоживущий iframe + morphdom DOM-diff через postMessage. Старые элементы не перерисовываются, новые fade+slide-up в течение 250ms.

## Key Points

- **Один iframe загружается ОДИН раз** с `BOOTSTRAP_HTML` (см. `apps/web/src/lib/streaming-preview-bootstrap.ts`). Дальше живёт в DOM и принимает обновления через `postMessage`.
- **Без перезагрузки между chunk-ами:** альтернатива (постоянный `srcDoc=…`) на каждый chunk полностью перезагружает iframe → Tailwind CDN перезапускается → entrance-анимации обнуляются → flicker. Этот подход решает все три проблемы.
- **morphdom UMD CDN** (≈5KB) грузится прямо в bootstrap, без npm-deps в основном фронт-бандле.
- **Парсер фронта** (`apps/web/src/lib/parse-assistant.ts`) — зеркало бэкендового `apps/api/.../file_extractor.py`. `parseAssistantContent(content)` возвращает массив `{kind:'text'|'file', ..., closed:bool}` — мы используем и не закрытые `<file>` блоки (partial body).
- **CSS hot-swap:** все `.css` файлы конкатенируются в `cssText`, инжектятся в фиксированный `<style id="omnia-css">` через `textContent = ...` — без флекера. Tailwind CDN MutationObserver re-сканит новые классы автоматически.
- **Анимация:** новые top-level children body (которых нет в текущем DOM по сигнатуре `tag#id.class`) получают `data-omnia-new` атрибут. CSS keyframe `omnia-in` (opacity 0→1, translateY 8px→0, cubic-bezier 0.16,1,0.3,1, 250ms) делает анимацию. Через 400ms атрибут снимается — иначе на следующем patch старые элементы посчитались бы «новыми».
- **Дебаунс 150ms** через `useEffect` cleanup в `StreamingPreviewFrame`. Memo по `content.length` (cheap monotonic proxy, контент только дописывается).
- **Switch на committed iframe** автоматический: когда `isStreaming` становится false (после `llm.done`) и `snapshot.created` пришёл, AnimatePresence flip-ит с `<StreamingPreviewFrame>` на `<motion.iframe src={publicUrl + ?snapshot=...}>`.

## Details

### Архитектура без backend-изменений

Бэк уже стримит `llm.chunk` → frontend накапливает в React Query cache → component re-renders → парсер видит свежий `content` → собирает `bodyHtml + cssText` → дебаунс → postMessage в iframe. Бэкенду не нужны file-level WS-events (это v2 оптимизация bandwidth).

### Bootstrap HTML структура

```html
<head>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/morphdom@2.7.4/dist/morphdom-umd.min.js"></script>
  <style id="omnia-css"></style>             <!-- updated by postMessage -->
  <style id="omnia-anim">@keyframes omnia-in {...}</style>
</head>
<body>
  <div id="omnia-placeholder">Собираем структуру сайта…</div>
  <script>
    window.addEventListener('message', e => {
      if (e.data?.type !== 'omnia:render') return;
      // 1) DOMParser строит newBody из bodyHtml
      // 2) top-level кидам без существующего совпадения → data-omnia-new
      // 3) morphdom(document.body, newBody, {childrenOnly:true, onBeforeElUpdated: skip-if-equal})
      // 4) styleEl.textContent = cssText
      // 5) setTimeout(() => снять data-omnia-new, 400)
    });
    window.parent.postMessage({type: 'omnia:ready'}, '*');
  </script>
</body>
```

### Sandbox

`sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"` — без `allow-same-origin` postMessage не работает в обе стороны.

### Failure modes

1. **CDN недоступен (morphdom не загрузился):** скрипт делает fallback `document.body.innerHTML = newBody.innerHTML` — флекер, но контент виден.
2. **DOMParser кидает на битом HTML:** обёрнуто в try-catch, error в iframe-консоль, родитель не падает.
3. **Регенерация одной секции:** без stable `data-omnia-id` хэшей morphdom может считать «move» как «delete+insert» → анимация мигнёт. Приемлемо для v1; v2 — добавить хэши.
4. **Tailwind CDN flash:** при ПЕРВОМ chunk до загрузки Tailwind CDN несколько сот ms показывается неstyled HTML. Это same FOUC что в реальной загрузке страницы; не критично.

## Related Concepts

- [[knowledge/concepts/file-extractor-pipeline]] — где собираются `<file>` блоки

## Sources

- [[daily/2026-05-17.md]] — initial implementation, commit `01943b5`
