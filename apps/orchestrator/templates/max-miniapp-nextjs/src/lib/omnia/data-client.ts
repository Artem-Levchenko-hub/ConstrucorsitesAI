"use client";

export type SecureRecord<T extends Record<string, unknown> = Record<string, unknown>> = {
  id: string;
  collection: string;
  payload: T;
  revision: number;
  createdAt: string;
  updatedAt: string;
};

export class SecureDataRequestError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string) {
    super(code); this.status = status; this.code = code;
  }
}

async function request<T>(collection: string, suffix = "", method = "GET", input?: unknown): Promise<T> {
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(collection)) throw new Error("Invalid collection");
  const response = await fetch(`/api/omnia/data/${encodeURIComponent(collection)}${suffix}`, {
    method, credentials: "same-origin", cache: "no-store",
    headers: input === undefined ? undefined : { "Content-Type": "application/json" },
    body: input === undefined ? undefined : JSON.stringify(input),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({})) as { error?: string };
    throw new SecureDataRequestError(response.status, result.error || "DATA_UNAVAILABLE");
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** Collection data is private to the authenticated MAX user. */
export function secureCollection<T extends Record<string, unknown>>(collection: string) {
  const path = (id: string) => `/${encodeURIComponent(id)}`;
  return {
    list: (options: { limit?: number; cursor?: string } = {}) => {
      const params = new URLSearchParams();
      if (options.limit !== undefined) params.set("limit", String(options.limit));
      if (options.cursor) params.set("cursor", options.cursor);
      return request<{ records: SecureRecord<T>[]; nextCursor: string | null }>(collection, `?${params}`);
    },
    get: (id: string) => request<{ record: SecureRecord<T> }>(collection, path(id)),
    create: (payload: T) => request<{ record: SecureRecord<T> }>(collection, "", "POST", { payload }),
    // Replaces payload. Carry unchanged fields forward; revision guards stale writes.
    update: (id: string, revision: number, payload: T) => request<{ record: SecureRecord<T> }>(collection, path(id), "PUT", { revision, payload }),
    delete: (id: string, revision: number) => request<void>(collection, path(id), "DELETE", { revision }),
  };
}
