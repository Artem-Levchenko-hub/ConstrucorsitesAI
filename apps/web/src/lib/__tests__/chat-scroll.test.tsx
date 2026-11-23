import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useChatScroll } from "@/hooks/useChatScroll";

const frames = new Map<number, FrameRequestCallback>();
const observers: ResizeObserverMock[] = [];
let nextFrame: number;
let container: HTMLDivElement;
let root: Root;

class ResizeObserverMock {
  observe = vi.fn();
  disconnect = vi.fn();
  notify: (target?: Element) => void;
  constructor(callback: ResizeObserverCallback) {
    this.notify = (target) => callback(target ? [{ target } as ResizeObserverEntry] : [], this as unknown as ResizeObserver);
    observers.push(this);
  }
}

function Harness({ content = "one", scopeKey = "p", withFooter = false }: { content?: string; scopeKey?: string; withFooter?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const footerRef = useRef<HTMLDivElement>(null);
  const scroll = useChatScroll(ref, content, { contentRef, footerRef: withFooter ? footerRef : undefined, scopeKey });
  return <>
    <div ref={ref} onScroll={scroll.onScroll} data-following={scroll.following}>
      <div ref={contentRef}>{content}</div>
      <button onClick={scroll.scrollToLatest}>Последнее сообщение</button>
      <button data-pause-follow onClick={scroll.pauseFollowing}>Читать блок</button>
    </div>
    {withFooter && <div ref={footerRef} />}
  </>;
}

function flushFrame() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach(callback => callback(performance.now()));
  });
}

function metrics() {
  const pane = container.firstElementChild as HTMLDivElement;
  let height = 1000;
  Object.defineProperties(pane, {
    scrollHeight: { get: () => height }, clientHeight: { value: 400 },
  });
  return { pane, resize: (value: number) => { height = value; } };
}

function userScroll(pane: HTMLDivElement, top: number) {
  act(() => { pane.scrollTop = top; pane.dispatchEvent(new Event("scroll")); });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  frames.clear(); observers.length = 0; nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => {
    const id = ++nextFrame; frames.set(id, callback); return id;
  }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => { frames.delete(id); }));
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount()); container.remove(); vi.unstubAllGlobals();
});

it("follows actual content growth and animated heights without a message-token change, once per frame", () => {
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics();
  expect(observers[0].observe).toHaveBeenCalledExactlyOnceWith(pane.firstElementChild);
  flushFrame();
  resize(1100); observers[0].notify();
  resize(1200); observers[0].notify(); observers[0].notify();
  expect(frames.size).toBe(1);
  expect(pane.scrollTop).toBe(1000);
  flushFrame(); expect(pane.scrollTop).toBe(1200);
  resize(1300); observers[0].notify(); flushFrame();
  expect(pane.scrollTop).toBe(1300);
});

it("reserves room for a resizing composer while respecting the user's scroll position", () => {
  act(() => root.render(<Harness withFooter />));
  const { pane, resize } = metrics();
  const footer = container.lastElementChild!;
  let footerHeight = 80;
  Object.defineProperty(footer, "offsetHeight", { get: () => footerHeight });

  resize(1080);
  observers[0].notify(footer);
  flushFrame();
  expect(pane.style.getPropertyValue("--max-chat-footer-height")).toBe("80px");
  expect(pane.scrollTop).toBe(1080);

  userScroll(pane, 120);
  footerHeight = 146;
  resize(1146);
  observers[0].notify(footer);
  flushFrame();
  expect(pane.style.getPropertyValue("--max-chat-footer-height")).toBe("146px");
  expect(pane.scrollTop).toBe(120);
});

it("retains the 72px history threshold and restores following only near the bottom or on an explicit jump", () => {
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics(); flushFrame();
  userScroll(pane, 528); // Exactly 72px from the bottom remains history mode.
  expect(pane.dataset.following).toBe("false");
  resize(1200); observers[0].notify();
  act(() => root.render(<Harness content="two" />));
  expect(frames.size).toBe(0); expect(pane.scrollTop).toBe(528);
  userScroll(pane, 729); // 71px from the bottom resumes following.
  expect(pane.dataset.following).toBe("true");
  resize(1300); observers[0].notify(); flushFrame();
  expect(pane.scrollTop).toBe(1300);
  userScroll(pane, 100);
  act(() => container.querySelector("button")!.click());
  expect(pane.scrollTop).toBe(1300); expect(pane.dataset.following).toBe("true");
  resize(1400); observers[0].notify(); flushFrame(); expect(pane.scrollTop).toBe(1400);
});

it("does not take history away if the user scrolls after a resize has already queued a frame", () => {
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics(); flushFrame();
  resize(1200); observers[0].notify();
  userScroll(pane, 100); flushFrame();
  expect(pane.scrollTop).toBe(100); expect(pane.dataset.following).toBe("false");
});

it("disconnects and cancels retired project work, ignoring stale observer and frame callbacks", () => {
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics(); flushFrame();
  observers[0].notify();
  const retiredFrame = [...frames.values()][0];
  const retiredObserver = observers[0];
  userScroll(pane, 100);
  act(() => root.render(<Harness scopeKey="next-project" />));
  expect(pane.dataset.following).toBe("true");
  expect(retiredObserver.disconnect).toHaveBeenCalledOnce();
  expect(cancelAnimationFrame).toHaveBeenCalled();
  pane.scrollTop = 0; resize(2000);
  retiredFrame(performance.now()); retiredObserver.notify();
  expect(pane.scrollTop).toBe(0); expect(frames.size).toBe(1);
  flushFrame(); expect(pane.scrollTop).toBe(2000);
  observers[1].notify();
  const lastFrame = [...frames.values()][0];
  act(() => root.unmount());
  expect(observers[1].disconnect).toHaveBeenCalledOnce(); expect(frames.size).toBe(0);
  pane.scrollTop = 100; lastFrame(performance.now()); observers[1].notify();
  expect(pane.scrollTop).toBe(100); expect(frames.size).toBe(0);
});

it("retains the text-token fallback when ResizeObserver is unavailable", () => {
  vi.stubGlobal("ResizeObserver", undefined);
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics(); flushFrame();
  resize(1100); act(() => root.render(<Harness content="two" />));
  flushFrame(); expect(pane.scrollTop).toBe(1100);
  userScroll(pane, 100);
  resize(1200); act(() => root.render(<Harness content="three" />));
  flushFrame(); expect(pane.scrollTop).toBe(100);
});

it("pauses a queued follow synchronously and preserves jump and project reset", () => {
  act(() => root.render(<Harness />));
  const { pane, resize } = metrics(); flushFrame();
  resize(1200); observers[0].notify();
  act(() => container.querySelector<HTMLButtonElement>("[data-pause-follow]")!.click());
  expect(pane.dataset.following).toBe("false");
  resize(1300); observers[0].notify(); flushFrame();
  expect(pane.scrollTop).toBe(1000);
  act(() => container.querySelector("button")!.click());
  expect(pane.dataset.following).toBe("true"); expect(pane.scrollTop).toBe(1300);
  act(() => container.querySelector<HTMLButtonElement>("[data-pause-follow]")!.click());
  act(() => root.render(<Harness scopeKey="another-project" />));
  expect(pane.dataset.following).toBe("true");
  resize(1400); observers[1].notify(); flushFrame(); expect(pane.scrollTop).toBe(1400);
});
