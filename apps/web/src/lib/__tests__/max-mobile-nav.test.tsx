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

it("moves between adjacent mobile tabs and keeps vertical drags and form edits in place", async () => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  const shell = document.createElement("div");
  shell.className = "max-editor";
  document.body.append(shell);
  const root = createRoot(shell);
  const open = vi.fn();
  const close = vi.fn();
  const manage = vi.fn();
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  dialog.innerHTML = '<div class="max-editor-modal-heading">Превью</div><iframe data-testid="max-live-iframe" src="https://preview.example/app"></iframe><nav class="max-mobile-nav"><button>Чат</button><a data-max-tab="manage" href="/max/owned/manage">Управление</a></nav>';
  const preview = { current: null as HTMLDivElement | null };
  function drag(target: Element, dx: number, dy: number, end = "pointerup") {
    function fire(type: string, x: number, y: number) {
      const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 });
      Object.defineProperties(event, { pointerId: { value: 1 }, pointerType: { value: "mouse" }, isPrimary: { value: true } });
      target.dispatchEvent(event);
    }
    fire("pointerdown", 150, 150);
    fire("pointermove", 150 + dx, 150 + dy);
    fire(end, 150 + dx, 150 + dy);
  }
  try {
    await act(async () => root.render(<><main><p>Чат</p><input /></main><MaxMobileNav projectId="owned" active="chat" onPreview={open} previewDialog={preview} onClosePreview={close} /></>));
    const panel = shell.querySelector("p")!;
    dialog.querySelector('a[href="/max/owned/manage"]')!.addEventListener("click", event => { event.preventDefault(); manage(); });
    drag(panel, -12, 120);
    drag(panel, -8, 0);
    drag(shell.querySelector("input")!, -120, 2);
    drag(panel, -120, 2, "pointercancel");
    expect(open).not.toHaveBeenCalled();
    drag(panel, -110, 5);
    expect(open).toHaveBeenCalledOnce();
    drag(shell.querySelector("nav a")!, -110, 3);
    expect(open).toHaveBeenCalledTimes(2);

    document.body.append(dialog);
    preview.current = dialog;
    const header = dialog.firstElementChild!;
    drag(header, 0, 92);
    drag(header, 102, 3);
    expect(close).toHaveBeenCalledTimes(2);
    drag(header, -110, 2);
    expect(manage).toHaveBeenCalledOnce();
    drag(dialog.querySelector("nav button")!, -110, 2);
    expect(manage).toHaveBeenCalledTimes(2);

    const frame = dialog.querySelector("iframe")!;
    const data = { type: "omnia:preview:swipe", dx: 108, dy: 4 };
    window.dispatchEvent(new MessageEvent("message", { source: window, origin: "https://preview.example", data }));
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, origin: "https://elsewhere.example", data }));
    expect(close).toHaveBeenCalledTimes(2);
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, origin: "https://preview.example", data }));
    expect(close).toHaveBeenCalledTimes(3);

    preview.current = null;
    await act(async () => root.render(<><main /><MaxMobileNav projectId="owned" active="manage" /></>));
    const toPreview = vi.fn();
    shell.querySelector('a[href="/max/owned?preview=1"]')!.addEventListener("click", event => { event.preventDefault(); toPreview(); });
    drag(shell.querySelector("main")!, 115, 2);
    drag(shell.querySelector("main")!, -115, 2);
    expect(toPreview).toHaveBeenCalledOnce();
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
    drag(shell.querySelector("main")!, 115, 2);
    expect(toPreview).toHaveBeenCalledOnce();
  } finally {
    await act(async () => root.unmount());
    shell.remove();
    dialog.remove();
    vi.unstubAllGlobals();
  }
});
