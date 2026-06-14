import type { StreamBrief } from "@/lib/api/types";
import { briefSwatches } from "@/lib/brief-swatches";

/**
 * V3.8 — JOY-MOMENT.
 *
 * NORTH STAR столп: «серьёзные продукты ощущаются как игра/кайф». До этого у
 * creator-facing `apps/web` было НОЛЬ reward-кода — билд завершался молча. Здесь
 * рождается ровно ОДНА бренд-цветная success-нота на КАЖДЫЙ завершённый билд,
 * привязанная к реальному `llm.done`-событию (не таймеру, не эвристике).
 *
 * Дисциплина анти-спама (V3.3 гейтит ОТСУТСТВИЕ reward-спама): нота фаярит
 * ТОЛЬКО на полной генерации (`build`), НИКОГДА на хирургической правке (`edit`)
 * и НИКОГДА на ошибке (error-путь даже не зовёт `buildJoyTrigger`). «Бренд-цвет»
 * = акцент ЭТОГО билда из art-director-брифа (а не фикс-токен), поэтому
 * празднование само окрашено в палитру, которую юзер только что увидел рождаться.
 *
 * Falsifiable-ядро (см. joy-moment.test.ts): build с брифом-акцентом `#112233`
 * вернёт ИМЕННО `#112233`, а не фолбэк — захардкоженный цвет провалит тест;
 * `edit`-ход вернёт `null` (нет спама); тот же `id` второй раз → `joyShouldShow`
 * `false` (ровно 1×); reduced-motion → `false` (подавлено).
 */

/** Продуктовый акцент (#6E5BE8 = rgb(110,91,232), тот же, что светится в
 * PassProgressBar) — фолбэк, когда бриф не нёс валидного цвета. */
export const JOY_FALLBACK_ACCENT = "#6E5BE8";

/** Полная длительность ноты, мс. Гейт: ушла < 2.5s — держим запас. */
export const JOY_DURATION_MS = 2200;

export type JoyTrigger = {
  /** message_id завершённого билда — ключ дедупликации «ровно 1× на билд». */
  id: string;
  /** HEX-акцент ЭТОГО билда (из брифа) для окраски празднования. */
  accent: string;
};

/**
 * Бренд-акцент для success-ноты: СОБСТВЕННЫЙ HEX-акцент билда из art-director-
 * брифа (первый свотч — порядок по роли, акцент ведёт; R-04 reuse `briefSwatches`).
 * Фолбэк на продуктовый акцент, когда бриф пуст/без валидного цвета.
 */
export function joyAccent(brief: StreamBrief | null | undefined): string {
  return briefSwatches(brief)[0]?.hex ?? JOY_FALLBACK_ACCENT;
}

/**
 * Reward-нота положена на build-complete, НЕ на хирургической правке: иначе
 * каждый мелкий твик спамит празднование. Неизвестный ход (`undefined`/`null`)
 * трактуем как `build` — совпадает с дефолтом `resp.mode ?? "build"` в
 * usePromptStream, так что реконнект к завершённому билду тоже празднуется.
 */
export function joyFiresForTurn(mode: string | null | undefined): boolean {
  return mode !== "edit";
}

/**
 * Одноразовый триггер, который usePromptStream пишет в кэш `["joy"]` на
 * `llm.done`. Единый источник решения build-vs-edit: `edit` → `null` (нет ноты).
 */
export function buildJoyTrigger(
  messageId: string,
  turnMode: string | null | undefined,
  brief: StreamBrief | null | undefined,
): JoyTrigger | null {
  if (!joyFiresForTurn(turnMode)) return null;
  return { id: messageId, accent: joyAccent(brief) };
}

/**
 * Показать ли ноту прямо сейчас. Единое ядро дедупликации + a11y-подавления
 * (тот же код-путь, что исполняет JoyBurst): `true` ТОЛЬКО когда пришёл НОВЫЙ
 * build-триггер и motion не подавлен. Тот же `id` второй раз → `false`
 * («ровно 1× на билд»); reduced-motion → `false` (празднование — чистая
 * декорация, под opt-out оно молчит целиком).
 */
export function joyShouldShow(
  trigger: JoyTrigger | null | undefined,
  lastShownId: string | null,
  reducedMotion: boolean,
): boolean {
  if (reducedMotion) return false;
  if (!trigger) return false;
  return trigger.id !== lastShownId;
}
