import { apiFetch } from "./client";
import { USE_MOCKS } from "./mocks";

/** A form submission captured from the project's public site (P-LEAD). */
export interface Lead {
  id: string;
  data: Record<string, string>;
  source: string | null;
  created_at: string;
}

export interface LeadsResponse {
  count: number;
  leads: Lead[];
}

/** Owner «Заявки» inbox — submissions the generated lead form delivered. */
export async function getLeads(projectId: string): Promise<LeadsResponse> {
  if (USE_MOCKS) return { count: 0, leads: [] };
  return apiFetch<LeadsResponse>(`/api/projects/${projectId}/leads`);
}
