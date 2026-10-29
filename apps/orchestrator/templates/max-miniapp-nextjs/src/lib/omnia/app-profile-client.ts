"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getYleumAppProfile, type YleumAppProfile } from "@/lib/omnia/app-profile-actions";

type ProfileState = {
  profile: YleumAppProfile | null;
  configVersion: number | null;
  status: "loading" | "ready" | "error";
  error: string | null;
};
const initial: ProfileState = { profile: null, configVersion: null, status: "loading", error: null };
const unavailable = "Данные приложения временно недоступны. Попробуйте ещё раз.";

/** Name, description and support update from owner settings without generation. */
export function useYleumAppProfile() {
  const [state, setState] = useState<ProfileState>(initial);
  const mounted = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(async (): Promise<void> => {
    if (!mounted.current) return;
    const current = ++sequence.current;
    if (navigator.onLine === false) {
      setState({ ...initial, status: "error", error: "Нет соединения. Подключитесь к интернету." });
      return;
    }
    setState(previous => ({ ...previous, status: "loading", error: null }));
    try {
      const result = await getYleumAppProfile();
      if (!mounted.current || current !== sequence.current) return;
      if (!result.ok) throw new Error(unavailable);
      setState({ profile: result.profile, configVersion: result.configVersion, status: "ready", error: null });
    } catch {
      if (mounted.current && current === sequence.current) {
        setState({ ...initial, status: "error", error: unavailable });
      }
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    const offline = () => { ++sequence.current; setState({ ...initial, status: "error", error: "Нет соединения. Подключитесь к интернету." }); };
    void refresh();
    window.addEventListener("focus", visible);
    window.addEventListener("online", visible);
    window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false; ++sequence.current;
      window.removeEventListener("focus", visible);
      window.removeEventListener("online", visible);
      window.removeEventListener("offline", offline);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  return { ...state, refresh };
}
