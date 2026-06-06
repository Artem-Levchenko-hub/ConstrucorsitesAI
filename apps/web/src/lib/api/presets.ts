import { apiFetch } from "./client";
import type { DesignPresetPublic } from "./types";

/**
 * The 8 design presets the generator can ride on — rendered by the onboarding
 * quiz as style/palette cards. Public endpoint (no auth); the catalog is static
 * so React Query can cache it for the whole session.
 */
export async function getDesignPresets(): Promise<DesignPresetPublic[]> {
  return apiFetch<DesignPresetPublic[]>("/api/design-presets");
}

/**
 * Set (or clear, with null) the project's design preset override. Effective on
 * the next prompt, so the quiz calls this right before submitting the build.
 */
export async function setProjectDesignPreset(
  projectId: string,
  presetId: string | null,
): Promise<{ preset_id: string | null }> {
  return apiFetch<{ preset_id: string | null }>(
    `/api/projects/${projectId}/design-preset`,
    { method: "PUT", json: { preset_id: presetId } },
  );
}
