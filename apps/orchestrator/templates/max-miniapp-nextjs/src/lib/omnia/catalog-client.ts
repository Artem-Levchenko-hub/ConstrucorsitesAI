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

function validOption(item: YleumMaxContentItem | undefined, value: unknown): string | undefined {
  return item?.active && typeof value === "string" && item.options.includes(value) ? value : undefined;
}

/** Managed UI choices only; never replace a held price/version or confirm an order. */
export function useYleumCatalogSelection(items: readonly YleumMaxContentItem[]) {
  const [chosenCategory, setCategory] = useState<string | null>(null);
  const [chosenOptions, setOptions] = useState<Record<string, string | undefined>>({});
  // Derive public choices from this render's authoritative items. No effect can
  // expose a stale value for one render or loop when a caller maps a fresh array.
  const category = chosenCategory !== null && items.some(item => item.active && item.category === chosenCategory)
    ? chosenCategory : null;
  const selectedOptions: Record<string, string> = Object.fromEntries(items.flatMap(item => {
    const option = validOption(item, chosenOptions[item.id]);
    return option === undefined ? [] : [[item.id, option]];
  }));
  function getOption(item: Pick<YleumMaxContentItem, "id">): string | undefined {
    return validOption(items.find(current => current.id === item.id), chosenOptions[item.id]);
  }
  function selectOption(itemId: string, value: string): void {
    const option = validOption(items.find(item => item.id === itemId), value);
    setOptions(previous => ({ ...previous, [itemId]: option }));
  }
  return { category, setCategory, selectedOptions, selectOption, getOption };
}
