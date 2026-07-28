import { apiFetch } from "./client";
import type {
  HeroMediaAsset,
  HeroMediaFocusPreference,
  HeroMediaMotionPreference,
  HeroMediaPlan,
  HeroMediaPlanKind,
  HeroMediaRender,
} from "./types";

const _API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function uploadHeroMediaAsset(
  projectId: string,
  file: File,
  options: { consentConfirmed: boolean; filename?: string } = {
    consentConfirmed: true,
  },
): Promise<HeroMediaAsset> {
  const qs = new URLSearchParams({
    consent_confirmed: options.consentConfirmed ? "true" : "false",
  });
  if (options.filename) qs.set("filename", options.filename);
  const res = await fetch(
    `${_API_BASE}/api/projects/${projectId}/hero-media/assets?${qs.toString()}`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    },
  );
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const b = (await res.json()) as { error?: { message?: string } };
      msg = b?.error?.message ?? msg;
    } catch {
      /* non-JSON body */
    }
    throw new Error(msg);
  }
  return (await res.json()) as HeroMediaAsset;
}

export async function createHeroMediaPlan(
  projectId: string,
  payload: {
    prompt: string;
    business_type?: string | null;
    style_preference?: string | null;
    focus_preference?: HeroMediaFocusPreference;
    motion_preference?: HeroMediaMotionPreference;
    asset_ids?: string[];
  },
): Promise<HeroMediaPlan> {
  return apiFetch<HeroMediaPlan>(`/api/projects/${projectId}/hero-media/plans`, {
    method: "POST",
    json: payload,
  });
}

export async function listHeroMediaAssets(
  projectId: string,
): Promise<HeroMediaAsset[]> {
  return apiFetch<HeroMediaAsset[]>(`/api/projects/${projectId}/hero-media/assets`);
}

export async function getHeroMediaPlan(
  projectId: string,
  planId: string,
): Promise<HeroMediaPlan> {
  return apiFetch<HeroMediaPlan>(
    `/api/projects/${projectId}/hero-media/plans/${planId}`,
  );
}

export async function listHeroMediaPlans(
  projectId: string,
): Promise<HeroMediaPlan[]> {
  return apiFetch<HeroMediaPlan[]>(`/api/projects/${projectId}/hero-media/plans`);
}

export async function approveHeroMediaPlan(
  projectId: string,
  planId: string,
  selected_plan_kind: HeroMediaPlanKind,
): Promise<HeroMediaPlan> {
  return apiFetch<HeroMediaPlan>(
    `/api/projects/${projectId}/hero-media/plans/${planId}/approve`,
    {
      method: "POST",
      json: { selected_plan_kind },
    },
  );
}

export async function createHeroMediaRender(
  projectId: string,
  plan_id: string,
): Promise<HeroMediaRender> {
  return apiFetch<HeroMediaRender>(`/api/projects/${projectId}/hero-media/renders`, {
    method: "POST",
    json: { plan_id },
  });
}

export async function getHeroMediaRender(
  projectId: string,
  renderId: string,
): Promise<HeroMediaRender> {
  return apiFetch<HeroMediaRender>(
    `/api/projects/${projectId}/hero-media/renders/${renderId}`,
  );
}

export async function listHeroMediaRenders(
  projectId: string,
): Promise<HeroMediaRender[]> {
  return apiFetch<HeroMediaRender[]>(`/api/projects/${projectId}/hero-media/renders`);
}

export async function retryHeroMediaRender(
  projectId: string,
  renderId: string,
): Promise<HeroMediaRender> {
  return apiFetch<HeroMediaRender>(
    `/api/projects/${projectId}/hero-media/renders/${renderId}/retry`,
    { method: "POST" },
  );
}

export async function applyHeroMediaRender(
  projectId: string,
  renderId: string,
): Promise<{ id: string; preview_url: string | null }> {
  return apiFetch<{ id: string; preview_url: string | null }>(
    `/api/projects/${projectId}/hero-media/renders/${renderId}/apply`,
    { method: "POST" },
  );
}

export function heroMediaPreviewUrl(projectId: string, renderId: string): string {
  return `${_API_BASE}/api/projects/${projectId}/hero-media/renders/${renderId}/preview`;
}
