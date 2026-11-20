export type GenerationEngine = "claude" | "sol";
export const DEFAULT_GENERATION_ENGINE: GenerationEngine = "sol";

export function isGenerationEngine(value: unknown): value is GenerationEngine {
  return value === "claude" || value === "sol";
}
