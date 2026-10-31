"use client";

import dynamic from "next/dynamic";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { configureMaxShell, getMaxWebApp } from "@/lib/max/bridge";
import { installOwnerPreviewFetch, requestAuthenticatedSession } from "@/lib/max/owner-preview-renewal";
import type { MaxSessionUser } from "@/lib/max/session";
import { YleumCompliance } from "@/components/YleumCompliance";
import {
  createIdentityController, getIdentitySnapshot, getServerIdentitySnapshot, subscribeIdentityScope,
} from "@/lib/omnia/identity-scope";

const MaxUI = dynamic(
  () => import("@maxhub/max-ui").then((module) => module.MaxUI),
  { ssr: false },
);

type MaxContextValue = {
  mode: "loading" | "max" | "preview" | "error";
  user: MaxSessionUser | null;
  error: string | null;
  checking: boolean;
  previewOrigins?: string[];
};

const MaxContext = createContext<MaxContextValue>({
  mode: "loading",
  user: null,
  error: null,
  checking: true,
});

const MAX_INIT_DATA_HEADER = "X-Omnia-MAX-Init-Data";
function installAuthenticatedFetch() {
  const previous = window.fetch;
  const nativeFetch = previous.bind(window);
  let activeInitData = "";
  const authenticated = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestUrl =
      input instanceof Request
        ? new URL(input.url)
        : new URL(String(input), window.location.href);
    if (
      !activeInitData ||
      requestUrl.origin !== window.location.origin ||
      !requestUrl.pathname.startsWith("/api/")
    ) {
      return nativeFetch(input, init);
    }

    const requestHeaders = new Headers(
      input instanceof Request ? input.headers : undefined,
    );
    new Headers(init?.headers).forEach((value, key) => {
      requestHeaders.set(key, value);
    });
    requestHeaders.set(MAX_INIT_DATA_HEADER, activeInitData);

    if (input instanceof Request) {
      return nativeFetch(new Request(input, { ...init, headers: requestHeaders }));
    }
    return nativeFetch(input, { ...init, headers: requestHeaders });
  }) as typeof window.fetch;
  window.fetch = authenticated;
  return {
    fetch: authenticated,
    setInitData(value: string) { activeInitData = value; },
    dispose() {
      activeInitData = "";
      if (window.fetch === authenticated) window.fetch = previous;
    },
  };
}

export function useMaxApp() {
  return useContext(MaxContext);
}

const useBeforePaint = typeof window === "undefined" ? useEffect : useLayoutEffect;

function AuthScreen({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  const gate = useRef<HTMLDialogElement>(null);
  useBeforePaint(() => {
    const dialog = gate.current;
    if (!dialog) return;
    // A native top-layer gate also covers ordinary portals outside our DOM
    // wrapper. Keep the generated subtree mounted until identity is known.
    dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, []);
  return (
    <dialog ref={gate} aria-label="Безопасный вход" onCancel={event => event.preventDefault()}
      style={{ width: "100vw", height: "100dvh", maxWidth: "none", maxHeight: "none",
        margin: 0, border: 0, padding: 0, background: "#fff", overflow: "auto" }}>
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
    </dialog>
  );
}

export function MaxAppProvider({ children }: { children: React.ReactNode }) {
  const snapshot = useSyncExternalStore(subscribeIdentityScope, getIdentitySnapshot, getServerIdentitySnapshot);
  const controller = useRef<ReturnType<typeof createIdentityController> | null>(null);
  const transport = useRef<ReturnType<typeof installAuthenticatedFetch> | null>(null);
  const previewCleanup = useRef<(() => void) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewOrigins, setPreviewOrigins] = useState<string[] | undefined>();
  const [appearance, setAppearance] = useState<{
    platform: "ios" | "android";
    colorScheme: "light" | "dark";
  }>({ platform: "android", colorScheme: "light" });

  const authenticate = useCallback(async () => {
    const owner = controller.current;
    const authenticated = transport.current;
    if (!owner || !authenticated) return;
    const attempt = owner.begin();
    if (attempt < 0) return;
    setError(null);
    const webApp = getMaxWebApp();
    const initData = webApp?.initData || "";
    authenticated.setInitData(initData);
    if (webApp && initData) {
      configureMaxShell(webApp);
      // Some iOS MAX WebViews do not persist Set-Cookie from a fetch response.
      // Keep signed launch data in memory and attach it only to same-origin APIs.
      setAppearance({
        platform: webApp.platform === "ios" ? "ios" : "android",
        colorScheme: webApp.colorScheme === "dark" ? "dark" : "light",
      });
    }
    let denied = false;
    try {
      const result = await requestAuthenticatedSession(window, authenticated.fetch, initData,
        () => owner.isCurrent(attempt), () => { denied = true; owner.revoke(attempt); });
      if (!result || !owner.isCurrent(attempt)) return;
      const { response, recovered } = result;
      const body = (await response.json().catch(() => ({}))) as {
        user?: MaxSessionUser;
        code?: string;
        mode?: string;
        editor_origins?: string[];
      };
      if (!owner.isCurrent(attempt)) return;
      const ownerPreview = !initData && body.mode === "preview" && body.user?.id === "preview";
      const validUser = body.user && (
        ownerPreview ||
        (!recovered && body.mode !== "preview" && typeof body.user.id === "string" && /^[1-9][0-9]{0,19}$/.test(body.user.id))
      );
      if (!response.ok || !body.user || !validUser) {
        if (response.status === 401) {
          if (!initData) {
            throw new Error("Откройте приложение из чата с ботом в MAX.");
          }
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
      if (owner.commit(attempt, { actor: body.user.id, mode: ownerPreview ? "preview" : "max" })) {
        setPreviewOrigins(ownerPreview ? body.editor_origins : undefined);
      }
    } catch (failure) {
      if (owner.fail(attempt, denied)) setError(
        failure instanceof Error ? failure.message : "Проверьте соединение и попробуйте ещё раз.",
      );
    }
  }, []);

  useEffect(() => {
    const owner = createIdentityController(() => void authenticate());
    const authenticated = installAuthenticatedFetch();
    controller.current = owner;
    transport.current = authenticated;
    const revalidate = () => {
      if (document.visibilityState !== "hidden") void authenticate();
    };
    window.addEventListener("focus", revalidate);
    window.addEventListener("omnia:authchange", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    void authenticate();
    return () => {
      window.removeEventListener("focus", revalidate);
      window.removeEventListener("omnia:authchange", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
      previewCleanup.current?.();
      previewCleanup.current = null;
      authenticated.dispose();
      owner.dispose();
      if (controller.current === owner) controller.current = null;
      if (transport.current === authenticated) transport.current = null;
    };
  }, [authenticate]);

  useEffect(() => {
    if (snapshot.principal?.mode !== "preview") return;
    const generation = snapshot.generation;
    const dispose = installOwnerPreviewFetch(window, previewOrigins, () => {
      const current = getIdentitySnapshot();
      return current.generation === generation && current.principal?.mode === "preview";
    });
    previewCleanup.current = dispose;
    return () => {
      dispose();
      if (previewCleanup.current === dispose) previewCleanup.current = null;
    };
  }, [snapshot.principal?.mode, snapshot.generation, previewOrigins]);

  const user = useMemo(() => snapshot.principal ? { id: snapshot.principal.actor } : null, [snapshot.principal]);
  const value = useMemo<MaxContextValue>(() => ({
    mode: snapshot.principal?.mode || (error ? "error" : "loading"), user, error,
    checking: snapshot.checking, ...(previewOrigins ? { previewOrigins } : {}),
  }), [snapshot.principal, snapshot.checking, user, error, previewOrigins]);
  const blocked = snapshot.checking || !!error || !snapshot.principal;
  return (
    <MaxUI platform={appearance.platform} colorScheme={appearance.colorScheme}>
      <MaxContext.Provider value={value}>
        {blocked && (
          <AuthScreen error={error} onRetry={() => void authenticate()} />
        )}
        {snapshot.principal && (
          <div key={snapshot.generation} hidden={blocked}
            style={{ display: blocked ? "none" : "contents" }}>
            {children}
            <YleumCompliance />
          </div>
        )}
      </MaxContext.Provider>
    </MaxUI>
  );
}
