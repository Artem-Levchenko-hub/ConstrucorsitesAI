import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it, vi } from "vitest";

it("relays one-finger swipes from the embedded app only while its trusted parent enables them", () => {
  const frame = document.createElement("iframe");
  document.body.append(frame);
  const app = frame.contentWindow as Window & typeof globalThis;
  const page = app.document;
  page.body.innerHTML = "<main><p>Меню</p><button>Заказать</button></main>";
  const script = page.createElement("script");
  script.setAttribute("data-omnia-parent-origin", location.origin);
  Object.defineProperty(page, "currentScript", { configurable: true, value: script });
  const parent = app.parent;
  const post = vi.spyOn(parent, "postMessage").mockImplementation(() => {});
  const gestures = () => post.mock.calls.filter(([payload]) => payload?.type === "omnia:preview:swipe");
  const source = readFileSync(resolve(process.cwd(), "../api/src/yleum_api/static/omnia-inspector.js"), "utf8");
  function configure(allow: boolean, origin = location.origin) {
    app.dispatchEvent(new app.MessageEvent("message", { source: parent, origin, data: { type: "omnia:preview:chrome", hideScrollbar: true, mobileNavigation: allow } }));
  }
  function swipe(el: Element, dx: number, dy: number, fingers = 1) {
    for (const type of ["touchstart", "touchmove", "touchend"]) {
      const atStart = type === "touchstart";
      const touch = { identifier: 4, clientX: atStart ? 50 : 50 + dx, clientY: atStart ? 80 : 80 + dy };
      const evt = new app.Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(evt, { touches: { value: type === "touchend" ? [] : Array(fingers).fill(touch) }, changedTouches: { value: [touch] } });
      el.dispatchEvent(evt);
    }
  }
  try {
    new Function("window", "document", "getComputedStyle", "AbortController", source)(
      app, page, app.getComputedStyle.bind(app), app.AbortController,
    );
    expect(post).toHaveBeenCalledWith(expect.objectContaining({ type: "omnia:inspect:ready" }), location.origin);
    const text = page.querySelector("p")!;
    configure(true, "https://other.example");
    swipe(text, 110, 0);
    expect(gestures()).toHaveLength(0);
    configure(true);
    expect(page.getElementById("omnia-preview-chrome")).not.toBeNull();
    swipe(text, 108, 4);
    expect(gestures()[0]?.[0]).toEqual({ type: "omnia:preview:swipe", dx: 108, dy: 4 });
    swipe(text, 4, 110);
    swipe(text, 110, 0, 2);
    swipe(page.querySelector("button")!, 110, 0);
    configure(false);
    swipe(text, 110, 0);
    expect(gestures()).toHaveLength(1);
  } finally {
    post.mockRestore();
    frame.remove();
  }
});
