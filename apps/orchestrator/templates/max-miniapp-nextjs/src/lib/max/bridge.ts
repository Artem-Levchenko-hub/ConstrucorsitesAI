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

type MaxStorage = {
  setItem?: (key: string, value: string) => Promise<void> | void;
  getItem?: (key: string) => Promise<string | null> | string | null;
  removeItem?: (key: string) => Promise<void> | void;
};

type MaxHapticFeedback = {
  impactOccurred?: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred?: (type: "error" | "success" | "warning") => void;
  selectionChanged?: () => void;
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
  openLink?: (url: string) => void;
  openMaxLink?: (url: string) => void;
  downloadFile?: (params: { url: string; file_name?: string }) => Promise<boolean> | void;
  shareContent?: (payload: { text?: string; link?: string }) => Promise<boolean> | void;
  shareMaxContent?: (payload: { text?: string; link?: string }) => Promise<boolean> | void;
  openCodeReader?: (params?: { text?: string }) => Promise<string | null> | void;
  requestContact?: () => Promise<unknown> | void;
  getViewportSize?: () => { width: number; height: number } | Promise<{ width: number; height: number }>;
  BackButton?: MaxBackButton;
  DeviceStorage?: MaxStorage;
  SecureStorage?: MaxStorage;
  HapticFeedback?: MaxHapticFeedback;
};

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

export function openExternalLink(url: string): void {
  const webApp = getMaxWebApp();
  if (url.startsWith("https://max.ru/")) webApp?.openMaxLink?.(url);
  else if (webApp?.openLink) webApp.openLink(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

export function shareInMax(payload: { text?: string; link?: string }): void {
  void getMaxWebApp()?.shareContent?.(payload);
}

export function maxHaptic(
  type: "success" | "warning" | "error" | "selection" = "selection",
): void {
  const haptic = getMaxWebApp()?.HapticFeedback;
  if (type === "selection") haptic?.selectionChanged?.();
  else haptic?.notificationOccurred?.(type);
}

export async function requestMaxContact(): Promise<unknown> {
  return getMaxWebApp()?.requestContact?.();
}

export async function readSecureValue(key: string): Promise<string | null> {
  return (await getMaxWebApp()?.SecureStorage?.getItem?.(key)) ?? null;
}

export async function writeSecureValue(key: string, value: string): Promise<void> {
  await getMaxWebApp()?.SecureStorage?.setItem?.(key, value);
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
