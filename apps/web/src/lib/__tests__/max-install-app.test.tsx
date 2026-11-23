import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { MaxInstallButton, PwaInstallProvider } from "@/components/max/MaxInstallApp";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it("uses the browser install prompt only after a click and can retry after dismissal", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<PwaInstallProvider><MaxInstallButton /></PwaInstallProvider>));
  const prompt = vi.fn().mockResolvedValue(undefined);
  const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
    prompt, userChoice: Promise.resolve({ outcome: "dismissed" }),
  });
  await act(async () => window.dispatchEvent(event));
  expect(prompt).not.toHaveBeenCalled();
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(prompt).toHaveBeenCalledOnce();
  expect(host.textContent).toContain("На главный экран");
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("На экран");
  await act(async () => root.unmount());
  host.remove();
});

it("hides installation after the browser confirms that the app is installed", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(<PwaInstallProvider><MaxInstallButton /></PwaInstallProvider>));
  await act(async () => window.dispatchEvent(new Event("appinstalled")));
  expect(host.querySelector("button")).toBeNull();
  await act(async () => root.unmount());
});
