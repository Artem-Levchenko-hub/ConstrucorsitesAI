"use client";

async function post(path: string, payload: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function trackMaxEvent(
  eventName: string,
  properties: Record<string, unknown> = {},
): Promise<void> {
  const response = await post("/api/omnia/events", { eventName, properties });
  if (!response.ok && response.status !== 401) throw new Error("Analytics event failed");
}

export async function saveMaxConsent(
  consentType: string,
  granted: boolean,
  policyVersion = "1",
): Promise<void> {
  const response = await post("/api/omnia/consents", {
    consentType,
    granted,
    policyVersion,
  });
  if (!response.ok) throw new Error("Consent save failed");
}

export async function createMaxAction(
  actionType: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const response = await post("/api/omnia/actions", { actionType, payload });
  if (!response.ok) throw new Error("Action save failed");
  return response.json() as Promise<Record<string, unknown>>;
}
