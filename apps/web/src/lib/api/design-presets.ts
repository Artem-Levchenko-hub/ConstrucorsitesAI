/**
 * V2 — design presets client.
 *
 * Wraps the two endpoints in `apps/api/routers/design_presets.py`:
 * - GET  /api/design-presets               → catalog
 * - PUT  /api/projects/:id/design-preset   → manual override
 *
 * Full prompt-builder internals (layout_signatures, kit_classes,
 * copywriting_examples, anti_patterns) intentionally stay server-side —
 * the picker only needs swatch + font-pair + name + reference URL.
 */

import { apiFetch } from "./client";
import type { Uuid } from "./types";

/** Frontend-facing view of a design preset — matches `routers/design_presets.DesignPresetPublic`. */
export type DesignPreset = {
  id: string;
  name: string;
  one_liner: string;
  reference_url: string;
  palette: Record<string, string>;
  fonts: Record<string, string>;
  hero_type: string;
  industries: string[];
};

export async function listDesignPresets(): Promise<DesignPreset[]> {
  return apiFetch<DesignPreset[]>("/api/design-presets");
}

/**
 * Override (or clear) the project's auto-classified design preset. Pass
 * `presetId === null` to unset and let auto-classify run again on the
 * next prompt. Effective on the NEXT prompt — already-generated snapshots
 * keep whichever preset was used when they were rendered.
 */
export async function setProjectDesignPreset(
  projectId: Uuid,
  presetId: string | null,
): Promise<{ preset_id: string | null }> {
  return apiFetch<{ preset_id: string | null }>(
    `/api/projects/${projectId}/design-preset`,
    {
      method: "PUT",
      json: { preset_id: presetId },
    },
  );
}
