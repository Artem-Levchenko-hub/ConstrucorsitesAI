import type { RefObject } from "react";

type Page = "chat" | "manage";
type Point = { clientX: number; clientY: number };
const mobile = () => window.matchMedia?.("(max-width: 600px)").matches === true;
const controls = 'a, button, input, textarea, select, label, [contenteditable]:not([contenteditable="false"]), [role="slider"], [role="tablist"], canvas, video';

function hasOwnGesture(target: Element, surface: Element) {
  if (target.closest(controls)) return true;
  for (let node: Element | null = target; node && node !== surface; node = node.parentElement) {
    if (node.scrollWidth > node.clientWidth + 2 && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
  }
  return false;
}

export function bindMaxMobileSwipes(nav: HTMLElement, page: Page, dialogRef?: RefObject<HTMLDivElement | null>, close?: () => void) {
  const shell = nav.closest(".max-editor, .max-project-workspace");
  if (!shell) return;
  let start: { id: number; x: number; y: number; dialog: HTMLDivElement | null; header: boolean } | null = null;
  let blockClickUntil = 0;
  const reset = () => {
    start?.dialog?.style.removeProperty("--max-preview-pull");
    start = null;
  };

  function navigate(dx: number, dy: number, dialog: HTMLDivElement | null, header: boolean) {
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return false;
    if (dialog && header && dy >= 64 && dy > 1.5 * Math.abs(dx)) { close?.(); return true; }
    if (Math.abs(dx) < 56 || Math.abs(dx) < 1.5 * Math.abs(dy)) return false;
    if (dialog && dx > 0) { close?.(); return true; }
    if (!dialog && ((page === "chat" && dx > 0) || (page === "manage" && dx < 0))) return false;
    // Follow the same Link/button as a tap, including the unsaved-draft guard.
    const tab = nav.querySelector<HTMLElement>(dialog ? '[data-max-tab="manage"]' : '[data-max-tab="preview"]');
    tab?.click();
    return !!tab;
  }

  function begin(target: EventTarget | null, id: number, point: Point) {
    reset();
    blockClickUntil = 0;
    if (!mobile() || !(target instanceof Element)) return;
    const dialog = dialogRef?.current ?? null;
    const surface = dialog ?? shell;
    if (!surface?.contains(target) || target.closest('[role="dialog"]') !== dialog) return;
    if (!nav.contains(target) && hasOwnGesture(target, surface)) return;
    start = { id, x: point.clientX, y: point.clientY, dialog, header: !!target.closest(".max-editor-modal-heading") };
  }
  function move(id: number, point: Point) {
    if (!start || start.id !== id) return;
    const dx = Math.abs(point.clientX - start.x);
    const dy = point.clientY - start.y;
    if (Math.abs(dy) <= 12 || Math.abs(dy) <= dx) return;
    if (start.dialog && start.header && dy > 0) start.dialog.style.setProperty("--max-preview-pull", Math.min(dy * 0.7, 120) + "px");
    else reset();
  }
  function end(id: number, point: Point, event: Event) {
    const gesture = start;
    reset();
    if (!gesture || gesture.id !== id || gesture.dialog !== (dialogRef?.current ?? null) || !mobile()) return;
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0 && !selection.isCollapsed) {
      const rects = selection.getRangeAt(0).getClientRects();
      // A mouse drag through blank space can also select the nearest line.
      if (event.type === "touchend" || Array.from(rects).some(rect =>
        gesture.x >= rect.left - 2 && gesture.x <= rect.right + 2 && gesture.y >= rect.top - 2 && gesture.y <= rect.bottom + 2
      )) return;
    }
    if (navigate(point.clientX - gesture.x, point.clientY - gesture.y, gesture.dialog, gesture.header)) {
      if (selection && !selection.isCollapsed) selection.removeAllRanges();
      if (event.cancelable) event.preventDefault();
      blockClickUntil = performance.now() + 400;
    }
  }

  function pointerDown(event: PointerEvent) {
    if (event.pointerType === "touch") return;
    if (event.button !== 0 || event.isPrimary === false) { reset(); return; }
    begin(event.target, event.pointerId, event);
    // A drag starting on the header can finish over a different-origin iframe.
    if (start?.header && event.target instanceof Element) event.target.setPointerCapture?.(event.pointerId);
  }
  function pointerMove(event: PointerEvent) { if (event.pointerType !== "touch") move(event.pointerId, event); }
  function pointerUp(event: PointerEvent) { if (event.pointerType !== "touch") end(event.pointerId, event, event); }
  function pointerCancel(event: PointerEvent) { if (event.pointerType !== "touch") reset(); }
  function touchStart(event: TouchEvent) {
    const touch = event.touches[0];
    if (event.touches.length === 1) begin(event.target, touch.identifier, touch);
    else reset();
  }
  function touchMove(event: TouchEvent) {
    const touch = event.touches[0];
    if (event.touches.length === 1) move(touch.identifier, touch);
    else reset();
  }
  function touchEnd(event: TouchEvent) {
    const touch = event.changedTouches[0];
    if (touch && !event.touches.length) end(touch.identifier, touch, event);
    else reset();
  }
  function suppressDragClick(event: MouseEvent) {
    if (event.detail > 0 && performance.now() < blockClickUntil) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }
  function frameSwipe(event: MessageEvent) {
    const dialog = dialogRef?.current;
    if (!dialog || !mobile() || event.data?.type !== "omnia:preview:swipe") return;
    const frame = dialog.querySelector<HTMLIFrameElement>('iframe[data-testid="max-live-iframe"]');
    const focusedDialog = document.activeElement?.closest('[role="dialog"]');
    if (!frame?.src || event.source !== frame.contentWindow || event.origin !== new URL(frame.src).origin
      || (focusedDialog && focusedDialog !== dialog)) return;
    navigate(event.data.dx, event.data.dy, dialog, false);
  }

  const listeners = new AbortController();
  const options = { capture: true, signal: listeners.signal };
  document.addEventListener("pointerdown", pointerDown, options);
  document.addEventListener("pointermove", pointerMove, options);
  document.addEventListener("pointerup", pointerUp, options);
  document.addEventListener("pointercancel", pointerCancel, options);
  document.addEventListener("touchstart", touchStart, { ...options, passive: true });
  document.addEventListener("touchmove", touchMove, { ...options, passive: true });
  document.addEventListener("touchend", touchEnd, { ...options, passive: false });
  document.addEventListener("touchcancel", reset, options);
  document.addEventListener("click", suppressDragClick, options);
  window.addEventListener("blur", reset, { signal: listeners.signal });
  window.addEventListener("message", frameSwipe, options);
  return () => { listeners.abort(); reset(); };
}
