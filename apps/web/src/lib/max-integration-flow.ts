import type { MaxIntegration } from "@/lib/api/types";

export function canActivateMaxWebhook(
  integration: Pick<MaxIntegration, "connected" | "app_url"> | null | undefined,
): boolean {
  return Boolean(integration?.connected && integration.app_url);
}
