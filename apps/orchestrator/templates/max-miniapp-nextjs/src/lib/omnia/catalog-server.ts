/** Server only: the controller projects public business data, never credentials. */
import { open } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { z } from "zod";
import type { YleumMaxConfig } from "@/lib/omnia/max-config";

const MAX_BYTES = 1024 * 1024;
// Pydantic limits count Unicode code points; JS string.length counts UTF-16
// units. Keep API-valid emoji/options readable at the exact same limits.
const text = (maximum: number, minimum = 0) => z.string().min(minimum)
  .refine(value => [...value].length <= maximum);
const contentItem = z.object({
  id: z.string().min(1).max(64).regex(/^[a-zA-Z0-9_-]+$/),
  title: text(120, 1), category: text(80),
  description: text(600), price: text(80),
  availability: z.enum(["in_stock", "on_request", "out_of_stock"]),
  options: z.array(text(40)).max(24), image_url: text(500),
  action_label: text(40, 1), active: z.boolean(),
}).passthrough();
const configSchema = z.object({
  app_name: z.string().min(1),
  app_type: z.enum(["loyalty", "catalog", "booking", "event", "education", "custom"]),
  summary: z.string().min(1), audience: z.string(), primary_action: z.string(),
  features: z.array(z.string()), style: z.enum(["brand", "clean", "bright"]),
  brand_colors: z.string(),
  content: z.array(contentItem).max(100).refine(items => new Set(items.map(item => item.id)).size === items.length),
  operator: z.object({ legal_name: z.string() }).passthrough(),
  support: z.object({ email: z.string().nullable(), response_time: z.string() }).passthrough(),
  legal: z.object({
    age_rating: z.enum(["0+", "6+", "12+", "16+", "18+"]), has_sales: z.boolean(),
    has_user_content: z.boolean(), marketing_notifications: z.boolean(),
    personal_data_consent: z.boolean(), terms_accepted: z.boolean(), policy_url: z.string(),
  }).passthrough(),
}).passthrough();
const envelopeSchema = z.object({
  config_version: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
  config: configSchema,
});

export class YleumAppConfigError extends Error {
  readonly code = "catalog_unavailable";
  constructor() {
    super("Данные каталога временно недоступны. Попробуйте ещё раз.");
    this.name = "YleumAppConfigError";
  }
}

/** Read again for each operation; validate order IDs, availability and prices here.
 * Never derive this path/URL from a request or fall back to a bundled price list.
 */
export async function getYleumServerAppConfig(): Promise<{ configVersion: number; config: YleumMaxConfig }> {
  let file: Awaited<ReturnType<typeof open>> | undefined;
  try {
    const path = process.env.YLEUM_APP_CONFIG_PATH;
    if (!path || !isAbsolute(path)) throw new YleumAppConfigError();
    file = await open(path, "r");
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > MAX_BYTES) throw new YleumAppConfigError();
    // Bound the actual read as well as stat: a replacement or malformed writer
    // must not turn a public config request into an unbounded allocation.
    const chunks: Buffer[] = [];
    let size = 0;
    while (size <= MAX_BYTES) {
      const chunk = Buffer.alloc(Math.min(64 * 1024, MAX_BYTES + 1 - size));
      const { bytesRead } = await file.read(chunk, 0, chunk.length, null);
      if (!bytesRead) break;
      size += bytesRead;
      if (size > MAX_BYTES) throw new YleumAppConfigError();
      chunks.push(chunk.subarray(0, bytesRead));
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
    const value = envelopeSchema.parse(JSON.parse(text));
    return { configVersion: value.config_version, config: value.config };
  } catch {
    // File paths, content and parser errors never cross the server boundary.
    throw new YleumAppConfigError();
  } finally {
    await file?.close().catch(() => undefined);
  }
}
