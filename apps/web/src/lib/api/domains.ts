/**
 * Свой домен — клиент подключения домена к проекту.
 * Обёртка над `apps/api/routers/domains.py`.
 */

import { apiFetch } from "./client";
import type { Uuid } from "./types";

export type CustomDomain = {
  id: Uuid;
  project_id: Uuid;
  host: string;
  source: "external" | "purchased";
  expected_ip: string;
  dns_status: "pending" | "ok" | "mismatch";
  cert_status: "none" | "issuing" | "active" | "failed";
  last_detail: string | null;
  created_at: string;
  verified_at: string | null;
  dns_instructions: string | null;
};

export async function listDomains(projectId: Uuid): Promise<CustomDomain[]> {
  return apiFetch<CustomDomain[]>(`/api/domains/${projectId}`);
}

export async function connectDomain(
  projectId: Uuid,
  host: string,
): Promise<CustomDomain> {
  return apiFetch<CustomDomain>(`/api/domains`, {
    method: "POST",
    json: { project_id: projectId, host },
  });
}

export async function checkDomain(domainId: Uuid): Promise<CustomDomain> {
  return apiFetch<CustomDomain>(`/api/domains/${domainId}/check`, {
    method: "POST",
  });
}

export async function issueDomainCert(domainId: Uuid): Promise<CustomDomain> {
  return apiFetch<CustomDomain>(`/api/domains/${domainId}/issue`, {
    method: "POST",
  });
}

export async function deleteDomain(domainId: Uuid): Promise<void> {
  await apiFetch<void>(`/api/domains/${domainId}`, { method: "DELETE" });
}
