"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getYleumCatalog } from "@/lib/omnia/catalog-actions";
import type { YleumMaxContentItem } from "@/lib/omnia/max-config";

type CatalogState = {
  items: YleumMaxContentItem[];
  configVersion: number | null;
  status: "loading" | "ready" | "error";
  error: string | null;
};
const unavailable = "Каталог временно недоступен. Попробуйте ещё раз.";
const initial: CatalogState = { items: [], configVersion: null, status: "loading", error: null };
const disconnected: CatalogState = { ...initial, status: "error", error: "Нет соединения. Каталог обновится после подключения." };

/** Public visible positions; owner changes require no code generation or release. */
export function useYleumCatalog() {
  const [state, setState] = useState<CatalogState>(initial);
  const mounted = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(async (): Promise<void> => {
    if (!mounted.current) return;
    const current = ++sequence.current;
    if (navigator.onLine === false) { setState(disconnected); return; }
    setState(previous => ({ ...previous, status: "loading", error: null }));
    try {
      const result = await getYleumCatalog();
      if (!mounted.current || current !== sequence.current) return;
      if (!result.ok) throw new Error(unavailable);
      setState({ items: result.items, configVersion: result.configVersion, status: "ready", error: null });
    } catch {
      if (mounted.current && current === sequence.current) {
        setState({ items: [], configVersion: null, status: "error", error: unavailable });
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const visibleRefresh = () => {
      if (document.visibilityState === "visible" && navigator.onLine !== false) void refresh();
    };
    const offline = () => {
      ++sequence.current;
      setState(disconnected);
    };
    void refresh();
    window.addEventListener("focus", visibleRefresh);
    window.addEventListener("online", visibleRefresh);
    window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", visibleRefresh);
    return () => {
      mounted.current = false;
      ++sequence.current;
      window.removeEventListener("focus", visibleRefresh);
      window.removeEventListener("online", visibleRefresh);
      window.removeEventListener("offline", offline);
      document.removeEventListener("visibilitychange", visibleRefresh);
    };
  }, [refresh]);

  return { ...state, refresh };
}
