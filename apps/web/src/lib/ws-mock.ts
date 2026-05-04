/**
 * Simulates the WebSocket events from docs/01-api-contract.md so the UI can
 * exercise the full prompt → stream → snapshot → preview → wallet flow without
 * a backend running. Mirrors `WsEvent` shape exactly so the swap to a real WS
 * later is a one-file change in the consumer.
 */

import type { WsEvent } from "./api/types";
import { mockApi } from "./api/mocks";

const SAMPLE_RESPONSES: string[] = [
  `Сделал минималистичный лендинг кофейни в Казани:

- Hero с большим заголовком и CTA "Забронировать столик"
- Меню с тремя категориями: кофе, десерты, завтраки
- Форма бронирования: имя, телефон, дата, время
- Карта Yandex Maps с пином
- Footer с графиком работы и соцсетями

Использовал тёплые охровые тона на тёмном фоне.`,
  `Добавил в проект:

- Адаптивную сетку (mobile / tablet / desktop)
- Sticky-навигацию с прозрачным фоном
- Плавный скролл к секциям меню
- Микроанимации на hover у карточек товаров

Всё на чистом HTML+CSS без зависимостей.`,
  `Переделал hero:

- Заменил статичную картинку на видео-fallback
- Контрастный заголовок с tracking-tight
- Большая CTA кнопка с анимацией pulse
- Подложка dark gradient для читаемости

Готово к деплою.`,
];

const previewSvg = (label: string, sub: string, hue: number): string => {
  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 300" width="480" height="300">
  <rect width="480" height="300" fill="#0a0a0a"/>
  <rect x="20" y="20" width="440" height="40" rx="6" fill="hsl(${hue} 60% 28%)"/>
  <rect x="36" y="36" width="80" height="8" rx="2" fill="#fafafa"/>
  <rect x="124" y="36" width="40" height="8" rx="2" fill="#a1a1aa"/>
  <rect x="20" y="80" width="280" height="20" rx="3" fill="#fafafa"/>
  <rect x="20" y="110" width="200" height="12" rx="2" fill="#a1a1aa"/>
  <rect x="20" y="138" width="100" height="32" rx="4" fill="#3b82f6"/>
  <rect x="320" y="80" width="140" height="120" rx="6" fill="hsl(${hue} 50% 20%)"/>
  <rect x="20" y="200" width="140" height="80" rx="4" fill="#141414" stroke="#262626"/>
  <rect x="170" y="200" width="140" height="80" rx="4" fill="#141414" stroke="#262626"/>
  <rect x="320" y="220" width="140" height="60" rx="4" fill="#141414" stroke="#262626"/>
  <text x="36" y="56" font-family="ui-monospace, monospace" font-size="9" fill="#fafafa">${escapeXml(label)}</text>
  <text x="36" y="290" font-family="ui-monospace, monospace" font-size="8" fill="#71717a">${escapeXml(sub)}</text>
</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
};

const escapeXml = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;")
    .slice(0, 60);

export function simulatePromptStream(opts: {
  projectId: string;
  projectSlug: string;
  promptText: string;
  modelId: string;
  assistantMessageId: string;
  emit: (event: WsEvent) => void;
  responseSeed?: number;
}): () => void {
  const {
    projectId,
    projectSlug,
    promptText,
    modelId,
    assistantMessageId,
    emit,
    responseSeed = Math.floor(Math.random() * SAMPLE_RESPONSES.length),
  } = opts;

  const response = SAMPLE_RESPONSES[responseSeed % SAMPLE_RESPONSES.length];
  const chunks = response.match(/.{1,8}|\s+/gs) ?? [response];

  const timers: ReturnType<typeof setTimeout>[] = [];
  let cancelled = false;

  let i = 0;
  const tick = () => {
    if (cancelled) return;
    if (i < chunks.length) {
      const delta = chunks[i];
      mockApi.appendChunk(projectId, assistantMessageId, delta);
      emit({
        type: "llm.chunk",
        data: { message_id: assistantMessageId, delta },
      });
      i += 1;
      timers.push(setTimeout(tick, 18 + Math.random() * 32));
      return;
    }

    const tokensIn = 800 + Math.floor(Math.random() * 600);
    const tokensOut = 1500 + Math.floor(Math.random() * 2500);
    const cost =
      Math.round((tokensIn * 0.0003 + tokensOut * 0.0015) * 100) / 100;
    mockApi.finalizeMessage(
      projectId,
      assistantMessageId,
      tokensIn,
      tokensOut,
    );
    mockApi.charge(cost, `Сгенерировано · ${modelId}`, assistantMessageId);

    emit({
      type: "llm.done",
      data: {
        message_id: assistantMessageId,
        tokens_in: tokensIn,
        tokens_out: tokensOut,
        cost_rub: cost,
      },
    });

    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        const snap = mockApi.registerSnapshot(projectId, promptText, modelId);
        emit({ type: "snapshot.created", data: { snapshot: snap } });

        timers.push(
          setTimeout(() => {
            if (cancelled) return;
            const preview = previewSvg(
              `${projectSlug}.omnia.ai`,
              promptText,
              140 + (responseSeed % 6) * 32,
            );
            mockApi.attachPreview(projectId, snap.id, preview);
            emit({
              type: "preview.ready",
              data: { snapshot_id: snap.id, preview_url: preview },
            });
          }, 1100 + Math.random() * 600),
        );

        timers.push(
          setTimeout(() => {
            if (cancelled) return;
            // Wallet was already deducted in mockApi.charge above; just push the
            // fresh balance to the UI so the badge animates.
            emit({
              type: "wallet.updated",
              data: { balance_rub: mockApi.currentBalance() },
            });
          }, 200),
        );
      }, 250),
    );
  };

  timers.push(setTimeout(tick, 200));

  return () => {
    cancelled = true;
    for (const t of timers) clearTimeout(t);
  };
}
