"use client";

export type MaxBridgeUser = {
  id: number;
  first_name: string;
  last_name?: string | null;
  username?: string | null;
  photo_url?: string | null;
};

type MaxBackButton = {
  show?: () => void;
  hide?: () => void;
  onClick?: (handler: () => void) => void;
  offClick?: (handler: () => void) => void;
};

export type MaxWebApp = {
  initData?: string;
  initDataUnsafe?: { user?: MaxBridgeUser };
  platform?: string;
  colorScheme?: "light" | "dark";
  ready?: () => void;
  expand?: () => void;
  enableClosingConfirmation?: () => void;
  disableClosingConfirmation?: () => void;
  BackButton?: MaxBackButton;
};

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

export function getMaxWebApp(): MaxWebApp | null {
  return typeof window === "undefined" ? null : window.WebApp || null;
}

export function configureMaxShell(webApp: MaxWebApp): void {
  webApp.ready?.();
  webApp.expand?.();
}

export function setMaxClosingConfirmation(enabled: boolean): void {
  const webApp = getMaxWebApp();
  if (enabled) webApp?.enableClosingConfirmation?.();
  else webApp?.disableClosingConfirmation?.();
}

export function bindMaxBackButton(handler: (() => void) | null): () => void {
  const button = getMaxWebApp()?.BackButton;
  if (!button || !handler) return () => undefined;
  button.onClick?.(handler);
  button.show?.();
  return () => {
    button.offClick?.(handler);
    button.hide?.();
  };
}
