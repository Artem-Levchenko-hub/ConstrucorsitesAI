"use client";

import dynamic from "next/dynamic";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { configureMaxShell, getMaxWebApp } from "@/lib/max/bridge";
import type { MaxSessionUser } from "@/lib/max/session";
import { OmniaCompliance } from "@/components/OmniaCompliance";

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

const MAX_INIT_DATA_HEADER = "X-Omnia-MAX-Init-Data";
let nativeFetch: typeof window.fetch | null = null;
let activeInitData = "";

function installAuthenticatedFetch(initData: string) {
  activeInitData = initData;
  if (nativeFetch) return;
  nativeFetch = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestUrl =
      input instanceof Request
        ? new URL(input.url)
        : new URL(String(input), window.location.href);
    if (
      !activeInitData ||
      requestUrl.origin !== window.location.origin ||
      !requestUrl.pathname.startsWith("/api/")
    ) {
      return nativeFetch!(input, init);
    }

    const requestHeaders = new Headers(
      input instanceof Request ? input.headers : undefined,
    );
    new Headers(init?.headers).forEach((value, key) => {
      requestHeaders.set(key, value);
    });
    requestHeaders.set(MAX_INIT_DATA_HEADER, activeInitData);

    if (input instanceof Request) {
      return nativeFetch!(new Request(input, { ...init, headers: requestHeaders }));
    }
    return nativeFetch!(input, { ...init, headers: requestHeaders });
  }) as typeof window.fetch;
}

const previewUser: MaxSessionUser = {
  id: "preview",
  // A preview has no verified MAX profile. Keep identity empty so product code
  // exercises its honest neutral greeting instead of rendering a synthetic
  // person that the visual/product gates correctly reject as fake data.
  firstName: "",
  lastName: null,
  username: "preview",
  languageCode: "ru",
  photoUrl: null,
};

export function useMaxApp() {
  return useContext(MaxContext);
}

function AuthScreen({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        padding: "calc(24px + env(safe-area-inset-top)) 24px calc(24px + env(safe-area-inset-bottom))",
        background: "var(--maxui-background, #ffffff)",
        color: "var(--maxui-text-primary, #171717)",
        textAlign: "center",
      }}
    >
      <section style={{ width: "min(100%, 360px)" }} role={error ? "alert" : "status"}>
        <div
          aria-hidden="true"
          style={{
            width: 48,
            height: 48,
            margin: "0 auto 18px",
            borderRadius: 16,
            display: "grid",
            placeItems: "center",
            background: error ? "#fff0ed" : "#f2f3f5",
            color: error ? "#d94932" : "#6d7278",
            fontSize: 24,
            fontWeight: 700,
          }}
        >
          {error ? "!" : "…"}
        </div>
        <h1 style={{ margin: 0, fontSize: 22, lineHeight: 1.25 }}>
          {error ? "Не удалось войти через MAX" : "Подключаем приложение"}
        </h1>
        <p
          style={{
            margin: "10px 0 0",
            color: "var(--maxui-text-secondary, #777b80)",
            fontSize: 15,
            lineHeight: 1.5,
          }}
        >
          {error || "Проверяем безопасный запуск и загружаем ваши данные."}
        </p>
        {error ? (
          <button
            type="button"
            onClick={onRetry}
            style={{
              width: "100%",
              minHeight: 48,
              marginTop: 22,
              border: 0,
              borderRadius: 14,
              background: "#ff5c35",
              color: "#ffffff",
              font: "inherit",
              fontWeight: 700,
            }}
          >
            Повторить
          </button>
        ) : null}
      </section>
    </main>
  );
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

  const authenticate = useCallback(async () => {
    setState({ mode: "loading", user: null, error: null });
    const webApp = getMaxWebApp();
    if (!webApp?.initData) {
      const host = window.location.hostname;
      const isPreview =
        host === "localhost" ||
        host === "127.0.0.1" ||
        host.includes("-dev.preview.");
      setState(
        isPreview
          ? { mode: "preview", user: previewUser, error: null }
          : {
              mode: "error",
              user: null,
              error: "Откройте приложение из чата с ботом в MAX.",
            },
      );
      return;
    }
    configureMaxShell(webApp);
    // Some iOS MAX WebViews do not persist Set-Cookie from a fetch response.
    // Keep the signed MAX launch data in memory and attach it only to same-origin
    // API requests, so protected routes can authenticate without browser storage.
    installAuthenticatedFetch(webApp.initData);
    setAppearance({
      platform: webApp.platform === "ios" ? "ios" : "android",
      colorScheme: webApp.colorScheme === "dark" ? "dark" : "light",
    });
    try {
      const response = await fetch("/api/max/session", {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData: webApp.initData }),
      });
      const body = (await response.json().catch(() => ({}))) as {
        user?: MaxSessionUser;
        code?: string;
      };
      if (!response.ok || !body.user) {
        if (response.status === 401) {
          console.warn("[max-auth] launch rejected", body.code || "unknown");
          throw new Error(
            "Закройте приложение и откройте его снова из чата с ботом. Если ошибка повторится, напишите в поддержку.",
          );
        }
        if (response.status >= 500) {
          throw new Error("Сервис временно недоступен. Подождите немного и повторите.");
        }
        throw new Error("Не удалось завершить безопасный вход. Попробуйте ещё раз.");
      }
      setState({ mode: "max", user: body.user, error: null });
    } catch (error) {
      setState({
        mode: "error",
        user: null,
        error:
          error instanceof Error
            ? error.message
            : "Проверьте соединение и попробуйте ещё раз.",
      });
    }
  }, []);

  useEffect(() => {
    void authenticate();
  }, [authenticate]);

  const value = useMemo(() => state, [state]);
  return (
    <MaxUI
      className="omnia-max-runtime"
      platform={appearance.platform}
      colorScheme={appearance.colorScheme}
    >
      <MaxContext.Provider value={value}>
        {state.mode === "loading" || state.mode === "error" ? (
          <AuthScreen error={state.error} onRetry={() => void authenticate()} />
        ) : (
          <>
            {children}
            <OmniaCompliance fallback />
          </>
        )}
      </MaxContext.Provider>
    </MaxUI>
  );
}
