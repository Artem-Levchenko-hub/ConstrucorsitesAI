export type GenerationEngine = "claude" | "sol";

export function isGenerationEngine(value: unknown): value is GenerationEngine {
  return value === "claude" || value === "sol";
}
