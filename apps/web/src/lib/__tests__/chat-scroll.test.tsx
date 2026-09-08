import { act, createElement, useRef } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { useChatScroll } from "@/hooks/useChatScroll";

it("follows new output at the bottom but preserves the user's position while reading history", () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  function Harness({ content }: { content: string }) {
    const ref = useRef<HTMLDivElement>(null);
    const scroll = useChatScroll(ref, content);
    return createElement("div", { ref, onScroll: scroll.onScroll }, content,
      createElement("button", { onClick: scroll.scrollToLatest }, "Последнее сообщение"));
  }
  try {
    act(() => root.render(createElement(Harness, { content: "one" })));
    const pane = container.firstElementChild as HTMLDivElement;
    Object.defineProperties(pane, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 400 } });
    pane.scrollTop = 600;
    act(() => pane.dispatchEvent(new Event("scroll")));
    Object.defineProperty(pane, "scrollHeight", { value: 1100 });
    act(() => root.render(createElement(Harness, { content: "two" })));
    expect(pane.scrollTop).toBe(1100);
    pane.scrollTop = 100;
    act(() => pane.dispatchEvent(new Event("scroll")));
    act(() => root.render(createElement(Harness, { content: "three" })));
    expect(pane.scrollTop).toBe(100);
    act(() => container.querySelector("button")!.click());
    expect(pane.scrollTop).toBe(1100);
  } finally { act(() => root.unmount()); container.remove(); }
});
