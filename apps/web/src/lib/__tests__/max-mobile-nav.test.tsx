import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { MaxMobileNav } from "@/components/max/MaxMobileNav";
import manifest from "@/app/manifest";

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

it("opens preview without navigating away from a draft", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const preview = vi.fn();
  await act(async () => root.render(<MaxMobileNav projectId="owned" active="chat" onPreview={preview} />));
  expect(host.querySelectorAll("a,button")).toHaveLength(3);
  expect(host.querySelector('a[aria-current="page"]')?.getAttribute("href")).toBe("/max/owned");
  expect(host.querySelector('a[href="/max/owned/manage"]')).not.toBeNull();
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(preview).toHaveBeenCalledOnce();
  await act(async () => root.unmount());
});

it("provides a direct preview destination from management", async () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  await act(async () => root.render(<MaxMobileNav projectId="owned" active="manage" />));
  expect(host.querySelector('a[href="/max/owned?preview=1"]')).not.toBeNull();
  await act(async () => root.unmount());
});

it("installs as one standalone app without account or project identifiers", () => {
  const app = manifest();
  expect(app.display).toBe("standalone");
  expect(app.start_url).toBe("/max");
  expect(app.scope).toBe("/");
  expect(app.icons?.map(icon => icon.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
});
