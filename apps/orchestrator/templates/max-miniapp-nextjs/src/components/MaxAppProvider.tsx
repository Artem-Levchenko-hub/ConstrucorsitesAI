"use client";

import dynamic from "next/dynamic";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { configureMaxShell, getMaxWebApp } from "@/lib/max/bridge";
import type { MaxSessionUser } from "@/lib/max/session";

const MaxUI = dynamic(
  () => import("@maxhub/max-ui").then((module) => module.MaxUI),
  { ssr: false },
);

type MaxContextValue = {
  mode: "loading" | "max" | "preview" | "error";
  user: MaxSessionUser | null;
  error: string | null;
};

const MaxContext = createContext<MaxContextValue>({
  mode: "loading",
  user: null,
  error: null,
});

const previewUser: MaxSessionUser = {
  id: "preview",
  firstName: "Пользователь",
  lastName: "MAX",
  username: "preview",
  languageCode: "ru",
  photoUrl: null,
};

export function useMaxApp() {
  return useContext(MaxContext);
}

export function MaxAppProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<MaxContextValue>({
    mode: "loading",
    user: null,
    error: null,
  });
  const [appearance, setAppearance] = useState<{
    platform: "ios" | "android";
    colorScheme: "light" | "dark";
  }>({ platform: "android", colorScheme: "light" });

  useEffect(() => {
    const webApp = getMaxWebApp();
    if (!webApp?.initData) {
      setState({ mode: "preview", user: previewUser, error: null });
      return;
    }
    configureMaxShell(webApp);
    setAppearance({
      platform: webApp.platform === "ios" ? "ios" : "android",
      colorScheme: webApp.colorScheme === "dark" ? "dark" : "light",
    });
    void fetch("/api/max/session", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: webApp.initData }),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("MAX не подтвердил сессию");
        return response.json() as Promise<{ user: MaxSessionUser }>;
      })
      .then(({ user }) => setState({ mode: "max", user, error: null }))
      .catch((error: unknown) =>
        setState({
          mode: "error",
          user: null,
          error: error instanceof Error ? error.message : "Ошибка входа через MAX",
        }),
      );
  }, []);

  const value = useMemo(() => state, [state]);
  return (
    <MaxUI platform={appearance.platform} colorScheme={appearance.colorScheme}>
      <MaxContext.Provider value={value}>{children}</MaxContext.Provider>
    </MaxUI>
  );
}
