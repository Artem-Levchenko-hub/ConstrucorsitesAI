"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { entities, type ListParams, type Row } from "@/lib/sdk";

export interface UseEntity {
  rows: Row[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  create: (data: Record<string, unknown>) => Promise<Row>;
  update: (id: string, data: Record<string, unknown>) => Promise<Row>;
  remove: (id: string) => Promise<void>;
}

/**
 * Collection state for one entity, wired to the SDK. Loads on mount and after
 * every mutation, so the table the caller renders always reflects the server.
 * `params` is read by ref — pass a stable resource query; client-side
 * search/sort/paging happen in <DataTable>, not here.
 *
 *   const clients = useEntity("Client", { sort: "created_at", order: "desc" });
 *   await clients.create({ name: "ООО Ромашка" });
 */
export function useEntity(name: string, params?: ListParams): UseEntity {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await entities[name].list(paramsRef.current));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить данные");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const create = useCallback(
    async (data: Record<string, unknown>) => {
      const row = await entities[name].create(data);
      await reload();
      return row;
    },
    [name, reload],
  );

  const update = useCallback(
    async (id: string, data: Record<string, unknown>) => {
      const row = await entities[name].update(id, data);
      await reload();
      return row;
    },
    [name, reload],
  );

  const remove = useCallback(
    async (id: string) => {
      await entities[name].delete(id);
      await reload();
    },
    [name, reload],
  );

  return { rows, loading, error, reload, create, update, remove };
}
