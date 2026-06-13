import type { StreamBrief } from "@/lib/api/types";
import { HEX_RE, paletteRole } from "@/lib/brief-narration";

/**
 * V3.4 — SURFACING the art-director brief as visible "AI рисует" swatches.
 *
 * The brief (V3.10a транспорт → `["stream-brief"]` cache) arrives BEFORE the
 * first writer-HTML token. These pure helpers turn its палитра/секции into the
 * chat-side reveal cards (`PassProgressBar`): real colour chips carrying the
 * brief's OWN HEX, plus the section plan. Because the swatches are extracted
 * from `brief.palette` — not from a preset palette — a build's chosen colours
 * surface live. Empty/null brief → `[]` (fail-soft; the reveal stays silent).
 *
 * Falsifiable (adversary): swap the body for a hardcoded preset palette and the
 * unit test fails — the returned HEX must equal THIS brief's HEX, not a fixed
 * set (see brief-swatches.test.ts).
 */

export type BriefSwatch = { hex: string; label: string };

/** Человекочитаемая роль палитры (акцент/основной/фон/текст), иначе сам ключ. */
function roleLabel(key: string): string {
  const u = key.toUpperCase();
  if (u.includes("АКЦЕНТ") || u.includes("ACCENT")) return "Акцент";
  if (u.includes("PRIMARY")) return "Основной";
  if (u.includes("ФОН") || u.includes("BACKGROUND")) return "Фон";
  if (u.includes("ТЕКСТ") || u.includes("FOREGROUND") || u.includes("TEXT"))
    return "Текст";
  return key.trim();
}

/**
 * Валидные HEX-свотчи из брифа, упорядоченные по роли (акцент → primary → фон →
 * остальное), дедуплицированные по цвету, каждый с человекочитаемой ролью.
 * Пустой/нулевой бриф → `[]`.
 */
export function briefSwatches(
  brief: StreamBrief | null | undefined,
): BriefSwatch[] {
  if (!brief) return [];
  const entries = Object.entries(brief.palette ?? {})
    .filter(([, v]) => typeof v === "string" && HEX_RE.test(v.trim()))
    .sort(([a], [b]) => paletteRole(a) - paletteRole(b));
  const out: BriefSwatch[] = [];
  const seen = new Set<string>();
  for (const [k, v] of entries) {
    const hex = v.trim();
    const norm = hex.toLowerCase();
    if (seen.has(norm)) continue;
    seen.add(norm);
    out.push({ hex, label: roleLabel(k) });
  }
  return out;
}

/** Имена секций из плана брифа, дедуплицированные, fail-soft `[]`. */
export function briefSectionPlan(
  brief: StreamBrief | null | undefined,
): string[] {
  if (!brief) return [];
  const names = (brief.sections ?? [])
    .map((s) => (s?.name ?? "").trim())
    .filter(Boolean);
  return [...new Set(names)];
}
