"use server";

import { getYleumServerAppConfig } from "@/lib/omnia/catalog-server";
import type { YleumMaxContentItem } from "@/lib/omnia/max-config";

export type YleumCatalogResult =
  | { ok: true; configVersion: number; items: YleumMaxContentItem[] }
  | { ok: false; code: "catalog_unavailable" };

/** Same file as order validation, including while a K8s projection catches up. */
export async function getYleumCatalog(): Promise<YleumCatalogResult> {
  try {
    const { configVersion, config } = await getYleumServerAppConfig();
    return { ok: true, configVersion, items: config.content.filter(item => item.active) };
  } catch {
    return { ok: false, code: "catalog_unavailable" };
  }
}
