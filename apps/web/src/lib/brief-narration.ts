import type { StreamBrief } from "@/lib/api/types";

/**
 * V3.10 — NARRATION-AS-CODESIGNER.
 *
 * Превращает уже-эмиттящийся art-director-бриф (V3.10a транспорт →
 * `window.__omniaBrief`) в живую «AI рисует»-наррацию: упорядоченные,
 * человекочитаемые строки, КАЖДАЯ из которых ДОСЛОВНО несёт значение из брифа
 * (HEX палитры, имя шрифта, имена секций, motion-сигнатуру). Это и есть
 * falsifiable-доказательство, что бриф SURFACED, а не выдуман клиентом: если бы
 * строки были захардкожены, они бы НЕ менялись с брифом (см. adversary-тест).
 *
 * Пустой/нулевой бриф → `[]` (fail-soft — точно то «без брифа»-состояние, что
 * гейтит V3.10a; наррация-слой тогда молчит, остаётся pre-brief эвристика).
 */

/** Валидный CSS-HEX (#rgb…#rrggbbaa). Экспортирован — V3.4 brief-swatches
 * переиспользует тот же фильтр (R-04, один источник «что считается цветом»). */
export const HEX_RE = /^#[0-9a-fA-F]{3,8}$/;

/** Приоритет роли цвета: акцент → primary → фон → остальное (строка ведёт
 * самым характерным цветом). Экспортирован — V3.4 brief-swatches сортирует
 * свотчи тем же порядком ролей (R-04). */
export function paletteRole(key: string): number {
  const u = key.toUpperCase();
  if (u.includes("АКЦЕНТ") || u.includes("ACCENT")) return 0;
  if (u.includes("PRIMARY")) return 1;
  if (u.includes("ФОН") || u.includes("BACKGROUND")) return 2;
  return 3;
}

/** До 2 различных валидных HEX из палитры, отсортированных по роли. */
function pickHexes(palette: Record<string, string>): string[] {
  const entries = Object.entries(palette ?? {})
    .filter(([, v]) => typeof v === "string" && HEX_RE.test(v.trim()))
    .sort(([a], [b]) => paletteRole(a) - paletteRole(b));
  const out: string[] = [];
  for (const [, v] of entries) {
    const hex = v.trim();
    if (!out.includes(hex)) out.push(hex);
    if (out.length >= 2) break;
  }
  return out;
}

/** Motion-сигнатура бывает длинной строкой спеки — берём первый осмысленный
 * фрагмент (до ~48 символов, по границе слова). */
function shortMotion(motion: string): string {
  const m = motion.trim();
  if (m.length <= 48) return m;
  const cut = m.slice(0, 48);
  const sp = cut.lastIndexOf(" ");
  return (sp > 24 ? cut.slice(0, sp) : cut) + "…";
}

/**
 * Упорядоченный список наррация-строк из брифа. Порядок = ход рассуждения
 * арт-директора: палитра → шрифт → каркас секций → движение. Каждая строка
 * включена ТОЛЬКО если её поле непустое; финальный список де-дублирован.
 */
export function briefNarration(
  brief: StreamBrief | null | undefined,
): string[] {
  if (!brief) return [];
  const lines: string[] = [];

  const hexes = pickHexes(brief.palette ?? {});
  if (hexes.length) {
    lines.push(`Подбираю палитру — ${hexes.join(" и ")}`);
  }

  const display = (brief.fonts?.display ?? "").trim();
  const text = (brief.fonts?.text ?? "").trim();
  if (display) {
    lines.push(`Беру шрифт «${display}» для заголовков`);
  } else if (text) {
    lines.push(`Беру шрифт «${text}» для текста`);
  }

  const names = (brief.sections ?? [])
    .map((s) => (s?.name ?? "").trim())
    .filter(Boolean);
  if (names.length) {
    const shown = names.slice(0, 4);
    const suffix = names.length > shown.length ? " …" : "";
    lines.push(`Компоную секции: ${shown.join(" → ")}${suffix}`);
  }

  const motion = (brief.motion ?? "").trim();
  if (motion) {
    lines.push(`Оживляю движением — ${shortMotion(motion)}`);
  }

  return [...new Set(lines)];
}
