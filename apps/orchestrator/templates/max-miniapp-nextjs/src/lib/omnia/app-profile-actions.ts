"use server";

import { getYleumServerAppConfig } from "@/lib/omnia/catalog-server";

export type YleumAppProfile = {
  app_name: string;
  summary: string;
  support: { email: string | null; response_time: string };
};
export type YleumAppProfileResult =
  | { ok: true; configVersion: number; profile: YleumAppProfile }
  | { ok: false; code: "profile_unavailable" };

/** Only the supported public profile. No owner contract or credentials. */
export async function getYleumAppProfile(): Promise<YleumAppProfileResult> {
  try {
    const { configVersion, config } = await getYleumServerAppConfig();
    return { ok: true, configVersion, profile: {
      app_name: config.app_name, summary: config.summary, support: {
        email: config.support.email, response_time: config.support.response_time,
      },
    } };
  } catch {
    return { ok: false, code: "profile_unavailable" };
  }
}
